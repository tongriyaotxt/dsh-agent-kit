#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""以编程方式起网关：自定义 cwd 白名单、token、并发与审计目录。

    python examples/gateway_server.py --port 8777 --cwd . --token dev-secret

打开 http://127.0.0.1:8777/?token=dev-secret 即可用内置 demo 页。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from dsh_agent_kit.gateway import create_server  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--profile", default="headless")
    parser.add_argument("--patch", action="append", default=[], dest="patches")
    parser.add_argument("--token", default=None)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--log-dir", default=None, help="每帧另存为 jsonl（审计/回放）")
    args = parser.parse_args(argv)

    server, cfg = create_server(
        cwd=args.cwd,
        host=args.host,
        port=args.port,
        profile=args.profile,
        patches=args.patches,
        token=args.token,
        max_concurrency=args.max_concurrency,
        log_dir=args.log_dir,
    )
    host, port = server.server_address[:2]
    query = "?token=%s" % args.token if args.token else ""
    print("gateway -> http://%s:%s/%s" % (host, port, query))
    print("  GET  /health   探活")
    print("  POST /ask      {task, session_id?, cwd?}")
    print("  POST /shutdown 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
