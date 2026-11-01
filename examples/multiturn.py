#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多轮对话 REPL：会话自动续接，`/reset` 开新会话，`/session` 看当前 id，`/quit` 退出。

    python examples/multiturn.py
    > 记住代号 ORION-7
    > 代号是什么？
    ORION-7
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from dsh_agent_kit import DshClient, DshError  # noqa: E402


def main() -> int:
    client = DshClient(cwd=os.getcwd(), timeout=900)
    print("多轮对话（/reset 开新会话，/session 看 id，/quit 退出）")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "exit"):
            return 0
        if line == "/reset":
            client.reset()
            print("(已开新会话)")
            continue
        if line == "/session":
            print(client.session_id or "(尚未建立)")
            continue

        try:
            turn = client.ask(line)
        except DshError as exc:
            print("失败(%s): %s" % (exc.kind, exc))
            continue
        print(turn.text)
        if turn.usage:
            print("[usage %s]" % turn.usage.get("totalTokens"), end="  ")
        print("[sid %s]" % turn.session_id)


if __name__ == "__main__":
    sys.exit(main())
