#!/usr/bin/env python3
"""自证 runner 与脚手架的测试。

覆盖四类：
    1. 脚手架产物完整、且**故意是红的**
    2. 写完逻辑后：用例绿 + 变异全抓 = 自证成立
    3. 真实世界的脏输入：BOM、裸数组 cases、路径越界
    4. 断言太弱时变异能逃逸 —— 证明变异检查不是摆设

零依赖，可被 pytest 收集，也可 `python tests/test_verify.py` 直接跑。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from dsh_agent_kit import scaffold, verify  # noqa: E402

GOOD_LOGIC = '''
def run(input, ctx):
    rows = input.get("rows") or []
    total = 0
    for r in rows:
        total += float(r.get("amount", 0))
    return {"count": len(rows), "total": round(total, 6)}
'''

WEAK_LOGIC = '''
def run(input, ctx):
    return {"v": len([])}
'''

WEAK_CASES = {"app": "weak", "cases": [{"id": "w1", "input": {}, "expect": {"v": 0}}]}


def make_app(tmpdir, name="测试应用"):
    # profiles_root 必须指到临时目录，否则会往真实的 ~/.dsh/profiles 里写东西
    return scaffold.create_app(name, "测试用", root=tmpdir,
                               profiles_root=os.path.join(tmpdir, "profiles"))


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


# ------------------------------------------------------------------ 脚手架
def test_scaffold_creates_expected_tree():
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp)
        for rel in ("app.json", "AGENT_RULES.md", "logic.py", "cases.json",
                    "verify.py", "说明.md", "state/app.json",
                    "data", "out", "outbox"):
            assert os.path.exists(os.path.join(app, rel)), "缺 " + rel
        meta = json.load(open(os.path.join(app, "app.json"), encoding="utf-8"))
        assert meta["version"] == 1
        assert meta["sandbox"] == "workspace-write"
        assert meta["network"] is False
        assert meta["profile"].startswith("app-")


def test_scaffold_refuses_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        make_app(tmp)
        try:
            make_app(tmp)
        except FileExistsError:
            return
        raise AssertionError("已存在时应拒绝，除非 --force")


def test_scaffold_creates_runnable_profile():
    """没有 profile，`dsh --profile app-xxx` 直接 profile not found —— app 等于跑不起来。"""
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "带档位")
        meta = json.load(open(os.path.join(app, "app.json"), encoding="utf-8"))
        prof = os.path.join(tmp, "profiles", meta["profile"])
        assert os.path.isdir(prof), "没建 profile: " + prof
        pkg = json.load(open(os.path.join(prof, "package.json"), encoding="utf-8"))
        assert "@deepseek-ai/dsh-headless" in pkg["dsh"]["profile"]["bundles"]
        patch = open(os.path.join(prof, "cordis.patch.yml"), encoding="utf-8").read()
        assert "id: credentials" in patch, "profile 没开凭证，跑起来会 MISSING_CREDENTIAL"
        assert "id: tool-web" in patch, "profile 没关外联"


def test_scaffold_keeps_existing_profile():
    """已存在的 profile 不许被覆盖 —— 用户可能已经调过。"""
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "已有档位")
        meta = json.load(open(os.path.join(app, "app.json"), encoding="utf-8"))
        prof = os.path.join(tmp, "profiles", meta["profile"])
        marker = os.path.join(prof, "cordis.patch.yml")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("# 用户自己改过\n")
        scaffold.create_app("已有档位", "再生成一次", root=tmp, force=True,
                            profiles_root=os.path.join(tmp, "profiles"))
        assert open(marker, encoding="utf-8").read() == "# 用户自己改过\n"


def test_fresh_app_is_red():
    """新生成的 app 必须是红的 —— 绿了就说明脚手架在撒谎。"""
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp)
        report = verify.run_cases(app)
        assert report.total == 2
        assert report.passed == 0, "脚手架生成完就绿了，不可接受"
        assert report.error is None


# ------------------------------------------------------------------ 自证
def test_implemented_app_verifies():
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp)
        write(os.path.join(app, "logic.py"), GOOD_LOGIC)
        report = verify.run_mutations(app)
        assert report.green, report.summary()
        assert report.mutation_total > 0
        assert report.mutation_caught == report.mutation_total, report.mutation_survivors
        assert report.verified


def test_weak_assertion_lets_mutation_escape():
    """断言太弱时，变异必须能逃逸 —— 这是「变异检查有效」的证明。"""
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "弱断言")
        write(os.path.join(app, "logic.py"), WEAK_LOGIC)
        write(os.path.join(app, "cases.json"), json.dumps(WEAK_CASES, ensure_ascii=False))
        report = verify.run_mutations(app)
        assert report.green, report.summary()
        assert report.mutation_total > 0
        assert not report.verified, "全部变异都被抓到，说明这个用例构造得不对"
        assert report.mutation_survivors, "应有逃逸的变异"


# ------------------------------------------------------------------ 脏输入
def test_bom_in_logic_and_cases():
    """Windows 记事本/PowerShell 写文件默认带 BOM，必须能吃。"""
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "带BOM")
        for name, content in (("logic.py", GOOD_LOGIC),
                              ("cases.json", open(os.path.join(app, "cases.json"),
                                                  encoding="utf-8").read())):
            with open(os.path.join(app, name), "w", encoding="utf-8-sig", newline="\n") as fh:
                fh.write(content)
        report = verify.run_mutations(app)
        assert report.green, report.summary()
        assert report.verified, report.mutation_survivors


def test_cases_accepts_bare_list():
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "裸数组")
        cases = json.load(open(os.path.join(app, "cases.json"), encoding="utf-8"))["cases"]
        write(os.path.join(app, "cases.json"), json.dumps(cases, ensure_ascii=False))
        write(os.path.join(app, "logic.py"), GOOD_LOGIC)
        assert verify.run_cases(app).green


def test_missing_cases_reports_clearly():
    with tempfile.TemporaryDirectory() as tmp:
        app = make_app(tmp, "缺用例")
        os.remove(os.path.join(app, "cases.json"))
        report = verify.run_cases(app)
        assert report.error and "cases.json" in report.error


def test_ctx_blocks_path_escape():
    ctx = verify.LocalCtx(tempfile.mkdtemp())
    for bad in ("../outside.txt", "C:/Windows/win.ini", "data/../../x"):
        try:
            ctx.read(bad)
        except verify.CtxError:
            continue
        raise AssertionError("越界路径应被拒: " + bad)


def test_normalize_handles_order_and_float():
    assert verify.same({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert verify.same({"x": 0.1 + 0.2}, {"x": 0.3})
    assert not verify.same({"x": 1}, {"x": 2})
    assert verify.same([1, 2], (1, 2))


# ------------------------------------------------------------------ 收尾
CASES = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main():
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


if __name__ == "__main__":
    sys.exit(main())
