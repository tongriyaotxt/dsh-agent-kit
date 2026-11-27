#!/usr/bin/env python3
"""examples 冒烟 —— 让 7 个示例不再"写完就烂"。

背景（docs/09-issues.md E5）：
    examples/ 里 7 个文件，CI 一个都没跑过。示例是最容易被复制走的代码，
    它一旦坏掉，别人拿到的是坏代码。

做法：
    用 QED_LAUNCHER 把启动器换成 tests/fake_dsh.py（零 token、不装 dsh），
    每个示例跑一遍，断言 exit 0 且输出里有可验证的字符串。

跑法：
    python tests/run_examples.py
    python tests/run_examples.py -v
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_dsh.py"


def launcher_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QED_LAUNCHER"] = __import__("json").dumps(
        [sys.executable, str(FAKE)]
    )
    return env


class Case:
    def __init__(self, label, cmd, expect, stdin=None, skip_without=None):
        self.label = label
        self.cmd = cmd
        self.expect = expect
        self.stdin = stdin
        self.skip_without = skip_without


CASES = [
    Case("hello.py", [sys.executable, "examples/hello.py", "reply with OK"], "ECHO"),
    Case("stream.py", [sys.executable, "examples/stream.py", "reply with OK"], "ECHO"),
    Case("stream.py --thinking",
         [sys.executable, "examples/stream.py", "--thinking", "reply with OK"], "ECHO"),
    Case("batch.py",
         [sys.executable, "examples/batch.py", "--workers", "2",
          "总结这个文件：{path}", "README.md"], None),
    Case("multiturn.py",
         [sys.executable, "examples/multiturn.py"], None, stdin="hi\n/quit\n"),
    Case("node_stream.mjs",
         ["node", "examples/node_stream.mjs", "reply with OK"], None,
         skip_without="node"),
]


def run(case: Case, verbose: bool):
    env = launcher_env()
    try:
        proc = subprocess.run(
            case.cmd, cwd=str(ROOT), env=env,
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
            input=case.stdin,
        )
    except subprocess.TimeoutExpired:
        return False, "超时（交互式示例可能没有读到 EOF）"
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, "exit %d · %s" % (proc.returncode, out.strip().splitlines()[-3:])
    if case.expect and case.expect not in out:
        return False, "输出里没有 %r · 尾部：%s" % (case.expect, out.strip()[-200:])
    if verbose:
        print("        " + " | ".join(out.strip().splitlines()[:2])[:200])
    return True, ""


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    verbose = "-v" in (argv if argv is not None else sys.argv[1:])
    if not FAKE.exists():
        print("  FAIL  找不到 tests/fake_dsh.py")
        return 1

    failed = 0
    for case in CASES:
        if case.skip_without and not shutil.which(case.skip_without):
            print("  SKIP  %-22s 环境里没有 %s" % (case.label, case.skip_without))
            continue
        ok, why = run(case, verbose)
        print(("  PASS  " if ok else "  FAIL  ") + "%-22s %s"
              % (case.label, "" if ok else why))
        if not ok:
            failed += 1

    total = len(CASES)
    print("\n%d/%d 个示例跑通" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
