#!/usr/bin/env python3
"""变异测试 —— 证明测试套件能抓住真实错误。

原理：往被测代码里注入一个**明确错误**，然后跑测试。
     测试必须变红。如果还绿，说明那层测试是摆设（docs/09-issues.md E1）。

  这不是覆盖率，是"闸门的有效性证明"。覆盖率回答"跑过没有"，
  变异测试回答"跑过的地方真的会报警吗"。

跑法：
    python tests/mutate.py          # 全部变异，约 30 秒
    python tests/mutate.py -v       # 打印每个变异的失败摘要

退出码：0 = 每个变异都被抓到；1 = 有变异逃逸（漏网的错误）。
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache"}


class Mutation:
    def __init__(self, target, needle, replacement, label, checks):
        self.target = target          # 相对路径
        self.needle = needle          # 原文（必须唯一出现）
        self.replacement = replacement
        self.label = label
        self.checks = checks          # 期望变红的检查脚本（相对路径列表）


# 覆盖两层：闸门自身（conformance）与协议实现（fake_dsh / client）
MUTATIONS = [
    Mutation(
        "tests/conformance.py",
        'found["schema"] = schema_check(events, schema)',
        'found["schema"] = []  # MUTANT: 绕过 schema 校验',
        "conformance 绕过 schema 校验",
        ["tests/test_conformance_negative.py"],
    ),
    Mutation(
        "tests/conformance.py",
        'if first.get("type") != "session":',
        'if False:  # MUTANT: 不检查首事件',
        "conformance 不检查首事件是否为 session",
        ["tests/test_conformance_negative.py"],
    ),
    Mutation(
        "tests/conformance.py",
        "    if len(finals) != 1:",
        "    if False:  # MUTANT: 不检查 final 数量",
        "conformance 不检查 final 数量",
        ["tests/test_conformance_negative.py"],
    ),
    Mutation(
        "tests/conformance.py",
        "        if not isinstance(ev, dict):\n            # 非对象行由 stream_invariants",
        "        if False:\n            # 非对象行由 stream_invariants",
        "conformance 在非对象行上崩溃（而不是报错）",
        ["tests/test_conformance_negative.py"],
    ),
    Mutation(
        "tests/fake_dsh.py",
        'emit({"type": "final", "text": answer})',
        'emit({"type": "finale", "text": answer})',
        "fake dsh 把 final 写成 finale（协议破坏）",
        ["tests/test_client.py"],
    ),
    Mutation(
        "tests/fake_dsh.py",
        'emit({"type": "session", "sessionId": sid, "cwd": os.getcwd()})',
        'emit({"type": "boot", "sessionId": sid, "cwd": os.getcwd()})',
        "fake dsh 首事件不是 session",
        ["tests/test_client.py"],
    ),
]


def copy_project(dst: Path) -> None:
    shutil.copytree(
        ROOT, dst,
        ignore=shutil.ignore_patterns(*SKIP_DIRS),
        dirs_exist_ok=True,
    )


def run_check(path: Path, script: str) -> tuple:
    proc = subprocess.run(
        [sys.executable, script],
        cwd=str(path),
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tail(text: str, lines: int = 3) -> str:
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return " | ".join(rows[-lines:])[:400]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    verbose = "-v" in argv

    survivors = []
    with tempfile.TemporaryDirectory(prefix="dshkit-mutate-") as tmp:
        work = Path(tmp) / "proj"
        copy_project(work)

        for i, mut in enumerate(MUTATIONS, 1):
            origin = ROOT / mut.target
            pristine = origin.read_text(encoding="utf-8")

            if pristine.count(mut.needle) != 1:
                print("  SKIP  %-44s 原文出现 %d 次（应为 1），变异点已失效"
                      % (mut.label, pristine.count(mut.needle)))
                survivors.append(mut.label + "（变异点失效）")
                continue

            target = work / mut.target
            try:
                target.write_text(
                    pristine.replace(mut.needle, mut.replacement, 1), encoding="utf-8"
                )
                caught = True
                detail = ""
                for script in mut.checks:
                    code, out = run_check(work, script)
                    if code == 0:
                        caught = False
                        detail = "%s 仍然 exit 0" % script
                        break
                    detail = tail(out)
            finally:
                target.write_text(pristine, encoding="utf-8")   # 必须还原

            if caught:
                print("  CAUGHT   %-44s %s" % (mut.label, detail if verbose else ""))
            else:
                print("  ESCAPED  %-44s %s" % (mut.label, detail))
                survivors.append(mut.label)

    total = len(MUTATIONS)
    caught_n = total - len(survivors)
    print("\n%d/%d 个变异被抓到" % (caught_n, total))
    if survivors:
        print("逃逸的变异（这些地方测试是摆设）：")
        for s in survivors:
            print("  - " + s)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
