#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一条命令行入口：`dsh-agent-kit <子命令>`。

四个子命令，对应四件不同的事：

    dsh-agent-kit demo        # 60 秒，零依赖，看到「自证成立」与「变异逃逸」两种结果
    dsh-agent-kit scaffold    # 生成一个 app（故意是红的）
    dsh-agent-kit verify      # 跑自证（用例 + 变异）
    dsh-agent-kit gateway     # 事件流转 SSE（给浏览器用）

设计原则：子命令只做转发，参数完全透传给各自的 main —— 这样 CLI 和
`python -m dsh_agent_kit.xxx` 的行为永远一致，不会出现两套参数。
"""

from __future__ import annotations

import sys

USAGE = """dsh-agent-kit — AI 写的代码，自己证明它对

用法:
  dsh-agent-kit demo                        60 秒演示（零依赖，不需要 API key）
  dsh-agent-kit scaffold <名字> [--desc ..]  生成一个 app（故意是红的）
  dsh-agent-kit verify --app <目录> [--mutate]
  dsh-agent-kit gateway [--port N]

示例:
  dsh-agent-kit demo
  dsh-agent-kit scaffold 发票统计 --desc "按月统计发票总额"
  dsh-agent-kit verify --app ~/apps/发票统计 --mutate

判据（记住这一条就够）:
  用例全绿 + 变异全抓 = 自证成立。少一个都算没做完。
"""


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    cmd, rest = args[0], args[1:]
    if cmd in ("-V", "--version", "version"):
        from . import __version__
        print(__version__)
        return 0

    if cmd == "demo":
        from . import demo
        return demo.main(rest)
    if cmd == "scaffold":
        from . import scaffold
        return scaffold.main(rest)
    if cmd == "verify":
        from . import verify
        return verify.main(rest)
    if cmd == "gateway":
        from . import gateway
        return gateway.main(rest) or 0

    print("未知子命令: %s\n" % cmd, file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
