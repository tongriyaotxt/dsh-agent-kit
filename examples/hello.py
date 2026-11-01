#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最小同步调用：一次任务、拿到答案与用量。

    python examples/hello.py "reply with OK"
    python examples/hello.py --profile headless --cwd .. "解释这个仓库的构建流程"
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from dsh_agent_kit import DshClient, DshError  # noqa: E402


def main(argv):
    profile = "headless"
    cwd = os.getcwd()
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--profile":
            profile = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--cwd":
            cwd = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1

    task = " ".join(rest) or "reply with OK"

    client = DshClient(profile=profile, cwd=cwd)
    try:
        turn = client.ask(task)
    except DshError as exc:
        sys.stderr.write("失败(%s): %s\n" % (exc.kind, exc))
        return 1

    print(turn.text)
    print("\n--- 元信息 ---", file=sys.stderr)
    print("session: %s" % turn.session_id, file=sys.stderr)
    print("usage  : %s" % turn.usage, file=sys.stderr)
    print("耗时   : %.2fs  (事件 %d 条)" % (turn.duration, len(turn.events)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
