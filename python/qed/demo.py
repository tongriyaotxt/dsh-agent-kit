#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""60 秒 demo —— **零依赖、不联网、不用 API key、不需要 dsh**，全程真实执行。

这是这个项目最重要的入口。原因很简单：
    看到这个仓库的人，99% 没有装 dsh。如果你要求他先装环境才能看到效果，
    他会在第一步就关掉页面。

所以 demo 不调用任何模型、不伪造任何事件流 —— 它**真的**生成一个 app、
**真的**跑断言、**真的**做变异检查。你看到的每一个 FAIL 都是真的 FAIL。

跑法：
    python -m qed.demo
    python -m qed.demo --fast      # 不等停顿（CI 用）
    python -m qed.demo --no-color
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

from . import scaffold, verify

FAST = False
COLOR = True


def _c(code: str, text: str) -> str:
    return text if not COLOR else "\033[%sm%s\033[0m" % (code, text)


def dim(t): return _c("2", t)
def bold(t): return _c("1", t)
def green(t): return _c("32", t)
def red(t): return _c("31", t)
def yellow(t): return _c("33", t)
def cyan(t): return _c("36", t)


def step(n, total, title):
    print()
    print(bold("[%d/%d] " % (n, total)) + title)


def pause(ms=520):
    if not FAST:
        time.sleep(ms / 1000.0)


def run_quiet(app_dir):
    """跑一次 verify，把结果收成 (report, 行文本)。"""
    report = verify.run_mutations(app_dir)
    lines = verify._fmt(report).splitlines()
    return report, lines


GOOD_LOGIC = '''def run(input, ctx):
    rows = input.get("rows") or []
    total = 0
    for r in rows:
        total += float(r.get("amount", 0))
    return {"count": len(rows), "total": round(total, 6)}
'''

#: 演示"断言太弱"用的：只断言一个恒为 0 的值，变异抓不到
WEAK_LOGIC = '''def run(input, ctx):
    return {"v": len([])}
'''

WEAK_CASES = '''{
  "app": "弱断言示例",
  "cases": [
    {"id": "w1", "input": {}, "expect": {"v": 0}}
  ]
}
'''


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    global FAST, COLOR
    argv = argv if argv is not None else sys.argv[1:]
    FAST = "--fast" in argv
    COLOR = "--no-color" not in argv

    print()
    print(bold("  qed · 60 秒 demo"))
    print(dim("  零依赖 · 不联网 · 不用 API key · 不需要装 dsh · 全程真实执行"))
    print(dim("  " + "─" * 58))

    tmp = tempfile.mkdtemp(prefix="qed-demo-")
    apps = os.path.join(tmp, "apps")
    profiles = os.path.join(tmp, "profiles")
    try:
        # ---------------------------------------------------------- 1
        step(1, 5, "生成一个 app —— 脚手架故意把它交付成" + red("红") + "的")
        app = scaffold.create_app("发票统计", "按月统计发票总额", root=apps,
                                  profiles_root=profiles)
        print(dim("      " + app))
        for name in sorted(os.listdir(app)):
            print(dim("        " + name))
        print()
        print("      " + dim("logic.py 里只有一句：") + 'return {"todo": True}')
        pause()

        # ---------------------------------------------------------- 2
        step(2, 5, "现在的状态")
        report = verify.run_cases(app)
        print("      $ " + cyan("python verify.py"))
        for line in verify._fmt(report).splitlines():
            print("      " + (red(line) if "FAIL" in line else line))
        print("      " + dim("↑ 这是故意的：生成即绿会让人以为做完了"))
        pause()

        # ---------------------------------------------------------- 3
        step(3, 5, "写实现（真实项目里这一步由 agent 做）")
        with open(os.path.join(app, "logic.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(GOOD_LOGIC)
        for line in GOOD_LOGIC.strip().splitlines():
            print("      " + green("+ " + line))
        pause()

        # ---------------------------------------------------------- 4
        step(4, 5, "自证：不只是跑通，还要证明断言不是摆设")
        print("      $ " + cyan("python verify.py --mutate"))
        report, lines = run_quiet(app)
        for line in lines:
            print("      " + (green(line) if "自证成立" in line else line))
        pause()

        # ---------------------------------------------------------- 5
        step(5, 5, "关键一步：把断言放松，看它能不能发现")
        weak = scaffold.create_app("弱断言示例", "演示断言太弱", root=apps,
                                   profiles_root=profiles)
        with open(os.path.join(weak, "logic.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(WEAK_LOGIC)
        with open(os.path.join(weak, "cases.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(WEAK_CASES)
        print("      " + dim("这次只留一条永远成立的断言：expect {\"v\": 0}"))
        print("      $ " + cyan("python verify.py --mutate"))
        report2, lines2 = run_quiet(weak)
        for line in lines2:
            print("      " + (yellow(line) if "逃逸" in line or "没抓到" in line else line))
        pause()

        # ---------------------------------------------------------- 收尾
        print()
        print(dim("  " + "─" * 58))
        print(bold("  为什么这件事值得做"))
        print()
        print("  AI 写代码最大的问题不是「写不出来」，是" + yellow("「你不知道它写对了没有」") + "。")
        print("  verify 用两把锁回答这个问题：")
        print("    " + green("① 用例全绿") + "  —— 它做了你要的事")
        print("    " + green("② 变异全抓") + "  —— 你的用例真的能发现它做错")
        print()
        print("  少一把，自证就是" + red("自欺") + "：第 5 步里那条永远成立的断言，")
        print("  变异全逃逸了 —— 代码看起来对，其实什么都没验证。")
        print()
        print(dim("  下一步："))
        print(dim("    python -m qed.scaffold 我的工具     # 生成你自己的"))
        print(dim("    python -m qed.demo                 # 再看一遍这个 demo"))
        print()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
