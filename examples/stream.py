#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""流式渲染：把 text 增量直接打到终端，thinking 可选显示，最后打印用量。

    python examples/stream.py "写一个 Python 的 LRU 缓存"
    python examples/stream.py --thinking "解释 QUIC 握手"
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from dsh_agent_kit import DshClient, DshError  # noqa: E402


def main(argv):
    show_thinking = "--thinking" in argv
    argv = [a for a in argv if a != "--thinking"]
    task = " ".join(argv) or "reply with OK"

    client = DshClient(cwd=os.getcwd())
    usage = None
    try:
        for ev in client.stream(task):
            kind = ev.get("type")
            if kind == "text":
                sys.stdout.write(ev.get("text", ""))
                sys.stdout.flush()
            elif kind == "thinking" and show_thinking and ev.get("text"):
                sys.stderr.write("\033[2m%s\033[0m" % ev["text"])
                sys.stderr.flush()
            elif kind == "tool":
                sys.stderr.write("\n[tool] %s\n" % ev.get("name", "?"))
            elif kind == "status" and ev.get("usage"):
                usage = ev["usage"]
            elif kind == "final":
                pass  # final 与 text 拼接结果一致，这里不重复打印
    except DshError as exc:
        sys.stderr.write("\n失败(%s): %s\n" % (exc.kind, exc))
        return 1

    print()
    if usage:
        print("\n[usage] %s  session=%s" % (usage, client.session_id), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
