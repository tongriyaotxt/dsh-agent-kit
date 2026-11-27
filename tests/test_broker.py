#!/usr/bin/env python3
"""凭证 broker 的测试 —— 证明「真 key 只在 broker 里，客户端只拿占位符」。

核心断言：
    1. 客户端发出去的是占位符，上游收到的是真 key（唯一注入点工作）
    2. 流式响应逐块透传，不被攒完再发
    3. stop() 之后 broker 内存里的 key 被抹掉

零依赖，可被 pytest 收集，也可 `python tests/test_broker.py` 直接跑。
"""

import http.client
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from qed.broker import PLACEHOLDER_KEY, Broker  # noqa: E402

REAL_KEY = "sk-real-secret-0123456789"
SEEN_KEYS = []
SEEN_PATHS = []


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        SEEN_KEYS.append(self.headers.get("x-api-key"))
        SEEN_PATHS.append(self.path)

        if b"STREAM" in body:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for piece in (b'data: {"type":"message_start"}\n\n',
                          b'data: {"usage":{"input_tokens":7,"output_tokens":3}}\n\n',
                          b"data: [DONE]\n\n"):
                self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            return

        payload = json.dumps({
            "id": "msg_1", "type": "message",
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 11, "output_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def post(port, path, body, key=PLACEHOLDER_KEY, read_all=True):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("POST", path, body=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    resp = conn.getresponse()
    data = resp.read() if read_all else b""
    conn.close()
    return resp.status, data


# ------------------------------------------------------------------ 用例
def test_injects_real_key_and_strips_placeholder():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    up_port = upstream.server_address[1]

    b = Broker(REAL_KEY, upstream="http://127.0.0.1:%d" % up_port)
    port = b.start()
    try:
        SEEN_KEYS.clear()
        SEEN_PATHS.clear()
        status, data = post(port, "/v1/messages", b'{"model":"x","messages":[]}')
        assert status == 200, status
        assert json.loads(data)["content"][0]["text"] == "OK"
        assert SEEN_KEYS == [REAL_KEY], "上游应收到真 key，实际 %r" % (SEEN_KEYS,)
        assert SEEN_PATHS == ["/v1/messages"], SEEN_PATHS
        assert b.requests == 1
    finally:
        b.stop()
        upstream.shutdown()
        upstream.server_close()


def test_stream_is_forwarded_incrementally():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    up_port = upstream.server_address[1]

    usages = []
    b = Broker(REAL_KEY, upstream="http://127.0.0.1:%d" % up_port,
               on_usage=usages.append)
    port = b.start()
    try:
        status, data = post(port, "/v1/messages", b'{"stream":true,"STREAM":1}')
        assert status == 200
        assert b"message_start" in data
        assert data.rstrip().endswith(b"[DONE]"), data[-40:]
        assert usages and usages[0]["input_tokens"] == 7, usages
    finally:
        b.stop()
        upstream.shutdown()
        upstream.server_close()


def test_stop_clears_key():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    b = Broker(REAL_KEY, upstream="http://127.0.0.1:%d" % upstream.server_address[1])
    b.start()
    b.stop()
    assert b._api_key == "", "stop() 必须抹掉内存里的 key"
    upstream.shutdown()
    upstream.server_close()


def test_rejects_bad_upstream():
    for bad in ("", "not-a-url", "ftp://x/y"):
        try:
            Broker(REAL_KEY, upstream=bad)
        except ValueError:
            continue
        raise AssertionError("非法 upstream 应被拒绝：%r" % bad)


def test_rejects_missing_key():
    try:
        Broker("")
    except ValueError:
        return
    raise AssertionError("空 key 应被拒绝")


CASES = [
    ("真 key 注入 / 占位符被替换", test_injects_real_key_and_strips_placeholder),
    ("流式响应逐块透传 + usage 抽取", test_stream_is_forwarded_incrementally),
    ("stop() 抹掉内存里的 key", test_stop_clears_key),
    ("非法 upstream 被拒绝", test_rejects_bad_upstream),
    ("空 key 被拒绝", test_rejects_missing_key),
]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    failed = 0
    for label, fn in CASES:
        try:
            fn()
            print("  PASS  " + label)
        except AssertionError as exc:
            print("  FAIL  %s  <- %s" % (label, exc))
            failed += 1
        except Exception as exc:            # noqa: BLE001 测试脚本要报得出来
            print("  FAIL  %s  <- %s: %s" % (label, type(exc).__name__, exc))
            failed += 1
    total = len(CASES)
    print("\n%d/%d 通过" % (total - failed, total))
    return 1 if failed else 0


try:
    import pytest  # noqa: F401

    @pytest.mark.parametrize("label,fn", CASES, ids=[c[0] for c in CASES])
    def test_broker(label, fn):
        fn()
except ImportError:
    pass


if __name__ == "__main__":
    sys.exit(main())
