#!/usr/bin/env python3
"""Profile 守卫的测试 —— 证明「改 profile 提权」能被发现。

核心断言：
    1. 指纹稳定；改一个字节、增删文件都会变
    2. 备份文件（*.bak-*）、node_modules、.rollback-* 不进入指纹
    3. 只读真的生效（写会失败）
    4. **模拟攻击**：解锁 → 删掉一条 disabled → 恢复只读，退出时必须报 tampered
    5. 目录不存在时不炸（新 profile 还没建）

零依赖，可被 pytest 收集，也可直接跑。
"""

import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from qed import guard  # noqa: E402

LOCKDOWN = """- id: tool-web
  disabled: true
- id: web-search-deepseek
  disabled: true
"""


def make_profile(tmp):
    d = os.path.join(tmp, "profiles", "app-test")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as fh:
        fh.write('{"name":"dsh-profile-app-test","dsh":{"profile":{"bundles":[]}}}')
    with open(os.path.join(d, "cordis.patch.yml"), "w", encoding="utf-8") as fh:
        fh.write(LOCKDOWN)
    os.makedirs(os.path.join(d, "node_modules", "x"), exist_ok=True)
    with open(os.path.join(d, "node_modules", "x", "index.js"), "w", encoding="utf-8") as fh:
        fh.write("module.exports = 1\n")
    with open(os.path.join(d, "cordis.patch.yml.bak-20260101"), "w", encoding="utf-8") as fh:
        fh.write("[]\n")
    os.makedirs(os.path.join(d, ".rollback-20260101"), exist_ok=True)
    with open(os.path.join(d, ".rollback-20260101", "cordis.patch.yml"), "w", encoding="utf-8") as fh:
        fh.write("[]\n")
    return d


def _force_writable(path):
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)


# ------------------------------------------------------------------ 指纹
def test_fingerprint_is_stable():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        a = guard.fingerprint(d)
        b = guard.fingerprint(d)
        assert a["digest"] == b["digest"]
        assert set(a["files"]) == {"package.json", "cordis.patch.yml"}


def test_fingerprint_ignores_noise():
    """备份、node_modules、.rollback-* 都不该进入指纹 —— 否则天天误报。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        before = guard.fingerprint(d)
        with open(os.path.join(d, "cordis.patch.yml.bak-20260102"), "w", encoding="utf-8") as fh:
            fh.write("- id: hacked\n")
        with open(os.path.join(d, "node_modules", "x", "index.js"), "w", encoding="utf-8") as fh:
            fh.write("module.exports = 999\n")
        with open(os.path.join(d, ".rollback-20260101", "cordis.patch.yml"), "w", encoding="utf-8") as fh:
            fh.write("- id: hacked\n")
        assert guard.fingerprint(d)["digest"] == before["digest"]


def test_fingerprint_detects_edit_and_add_and_delete():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        base = guard.fingerprint(d)

        # 改一个字节
        p = os.path.join(d, "cordis.patch.yml")
        _force_writable(p)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("# x\n")
        edited = guard.fingerprint(d)
        assert edited["digest"] != base["digest"]
        assert "~ cordis.patch.yml" in guard.diff(base, edited)

        # 新增一个声明文件
        _force_writable(os.path.join(d, "package.json"))
        with open(os.path.join(d, "compatibility.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        added = guard.fingerprint(d)
        assert "+ compatibility.json" in guard.diff(edited, added)

        # 删除
        _force_writable(os.path.join(d, "compatibility.json"))
        os.remove(os.path.join(d, "compatibility.json"))
        assert "- compatibility.json" in guard.diff(added, guard.fingerprint(d))


# ------------------------------------------------------------------ 只读
def test_readonly_blocks_write():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        failures = guard.set_readonly(d, True)
        assert failures == []
        target = os.path.join(d, "cordis.patch.yml")
        try:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write("oops")
        except PermissionError:
            pass
        else:
            raise AssertionError("只读没生效，仍然能写")
        finally:
            guard.set_readonly(d, False)
        with open(target, "a", encoding="utf-8") as fh:      # 解锁后能写
            fh.write("ok")


# ------------------------------------------------------------------ 守卫
def test_guard_reports_clean_run():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        with guard.ProfileGuard(d) as g:
            pass
        assert not g.tampered, g.summary()
        assert "未改动" in g.summary()


def test_guard_catches_privilege_escalation():
    """模拟攻击：解锁 → 删掉 lockdown → 恢复只读。退出时必须报出来。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        with guard.ProfileGuard(d) as g:
            target = os.path.join(d, "cordis.patch.yml")
            _force_writable(target)                      # 攻击者自己解锁
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("- id: tool-web\n  disabled: false\n")   # 把 lockdown 拆了
        assert g.tampered, "改 profile 必须被发现"
        assert any("cordis.patch.yml" in c for c in g.changes), g.changes
        assert "被改动" in g.summary()


def test_guard_missing_dir_is_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "profiles", "nope")
        with guard.ProfileGuard(missing) as g:
            pass
        assert not g.tampered
        assert g.before["files"] == {}


def test_guard_restores_writable_after_exit():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_profile(tmp)
        with guard.ProfileGuard(d):
            pass
        target = os.path.join(d, "cordis.patch.yml")
        with open(target, "a", encoding="utf-8") as fh:   # 退出后必须可写
            fh.write("# ok\n")


CASES = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except AssertionError as exc:
            print("  FAIL  %s  <- %s" % (name, exc))
            failed += 1
        except Exception as exc:                    # noqa: BLE001
            print("  FAIL  %s  <- %s: %s" % (name, type(exc).__name__, exc))
            failed += 1
    print("\n%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


try:
    import pytest  # noqa: F401

    @pytest.mark.parametrize("label,fn", CASES, ids=[c[0] for c in CASES])
    def test_guard(label, fn):
        fn()
except ImportError:
    pass


if __name__ == "__main__":
    sys.exit(main())
