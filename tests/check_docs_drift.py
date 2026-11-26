#!/usr/bin/env python3
"""文档漂移检测 —— 让文档里的"实测数字"不会悄悄过期。

背景（docs/09-issues.md E3）：
    docs/03 写 headless 97 包 / tui 124 包，2026-11-25 实测是 98 / 126，
    版本号却是同一个 0.2.1-rc.2。文档里的数字没有任何机制保证它还是真的。

做法：
    文档写 `≈N`（近似值），本脚本数 `dsh <profile> --dump-config` 里 `- id:` 的行数，
    差值在容差内算过。差太多说明文档过期或 profile 被改，二者都该有人看一眼。

    没有 dsh（比如 GitHub Actions 的 ubuntu runner）时 SKIP，退出码 0。

跑法：
    python tests/check_docs_drift.py
    python tests/check_docs_drift.py --tolerance 3
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "03-profiles.md"

# 文档里形如 `headless ≈98 / tui ≈126`
PATTERN = re.compile(r"headless\s*≈\s*(\d+)\s*/\s*tui\s*≈\s*(\d+)")


def find_dsh():
    """Windows 上 npm 装出来的是 dsh.cmd，shutil.which('dsh') 未必命中。"""
    for name in ("dsh", "dsh.cmd", "dsh.exe", "dsh.ps1"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def run_dsh(args):
    """返回 (stdout, 错误)。.cmd/.bat 在 Windows 上必须过 shell。"""
    exe = find_dsh()
    if not exe:
        return None, "找不到 dsh 命令"
    cmd = [exe] + args
    try:
        if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            proc = subprocess.run(
                subprocess.list2cmdline(cmd), shell=True,
                capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
        else:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
    except subprocess.TimeoutExpired:
        return None, "dsh --dump-config 超时"
    if proc.returncode != 0:
        return None, "dsh 退出码 %d：%s" % (
            proc.returncode, (proc.stderr or "").strip()[:200])
    return proc.stdout, ""


# 文档里的简称 → 真实 profile 名（`tui` 的 profile 目录叫 `dsh-tui`）
PROFILE_MAP = {"headless": "headless", "tui": "dsh-tui"}


def count_entries(profile: str):
    """返回 (条目数, 错误)。profile 传文档里的简称。"""
    out, err = run_dsh(["--profile", PROFILE_MAP.get(profile, profile), "--dump-config"])
    if out is None:
        return None, err
    return sum(1 for line in out.splitlines() if line.startswith("- id:")), ""


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    tolerance = 3
    if "--tolerance" in argv:
        tolerance = int(argv[argv.index("--tolerance") + 1])

    if not shutil.which("dsh"):
        print("  SKIP  环境里没有 dsh，跳过文档漂移检测")
        return 0

    text = DOC.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if not match:
        print("  FAIL  docs/03-profiles.md 里找不到 `headless ≈N / tui ≈M` 形式，"
              "文档格式已变更或数字被写死")
        return 1

    claimed = {"headless": int(match.group(1)), "tui": int(match.group(2))}
    failures = 0

    for profile in ("headless", "tui"):
        actual, err = count_entries(profile)
        if actual is None:
            print("  SKIP  %-9s %s" % (profile, err))
            continue
        delta = abs(actual - claimed[profile])
        ok = delta <= tolerance
        print("  %s  %-9s 文档 ≈%d  实测 %d  差 %d（容差 %d）"
              % ("PASS" if ok else "FAIL", profile, claimed[profile], actual,
                 delta, tolerance))
        if not ok:
            failures += 1

    if failures:
        print("\n文档数字已漂移：改了 profile 就同步 docs/03，或把数字改成更宽的近似。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
