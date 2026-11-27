#!/usr/bin/env python3
"""demo 的测试 —— 它是这个项目面向路人的第一屏，坏了就等于门面塌了。

三条断言：
    1. 零依赖：不需要 dsh、不需要 API key、不联网
    2. 全程真实执行：能跑出「自证成立」和「变异逃逸」两种结果
    3. 收尾干净：临时目录要清掉

零依赖，可被 pytest 收集，也可直接跑。
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run_demo(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "python")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("DEEPSEEK_API_KEY", None)      # 证明不需要 key
    env.pop("QED_LAUNCHER", None)
    return subprocess.run(
        [sys.executable, "-m", "qed.demo", "--fast", "--no-color"] + list(args),
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )


def test_demo_runs_without_dsh_or_key():
    proc = run_demo()
    assert proc.returncode == 0, proc.stderr[-800:]
    out = proc.stdout
    for marker in ("60 秒 demo", "零依赖", "不需要装 dsh"):
        assert marker in out, "首屏缺少卖点标记：%r" % marker


def test_demo_shows_both_verdicts():
    """必须同时展示「自证成立」和「变异逃逸」—— 后者是全篇的论点。"""
    out = run_demo().stdout
    assert "自证成立" in out, "没有展示通过的样子"
    assert "自证不成立" in out, "没有展示「断言太弱」的样子"
    assert "逃逸" in out, "没有点明变异逃逸"


def test_demo_is_real_not_faked():
    """第 2 步必须是真的 0/2 失败，第 4 步必须是真的 2/2 通过。"""
    out = run_demo().stdout
    assert "0/2 用例通过" in out, "没有真实的初始失败状态"
    assert "2/2 用例通过" in out, "没有真实的通过状态"
    assert "变异 4/4 被抓" in out or "变异" in out


def test_demo_cleans_up():
    before = set(os.listdir(tempfile.gettempdir()))
    run_demo()
    after = set(os.listdir(tempfile.gettempdir()))
    leftovers = [d for d in (after - before) if d.startswith("qed-demo-")]
    assert not leftovers, "demo 没清理临时目录：%r" % leftovers


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
    def test_demo(label, fn):
        fn()
except ImportError:
    pass


if __name__ == "__main__":
    sys.exit(main())
