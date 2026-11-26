#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭证 broker —— 让 API key 只存在于宿主进程内存，不落盘、不进子进程环境。

为什么需要它（docs/09-issues.md D1，实测确认）：
    agent 用 pwsh 在 workspace-write 档位下**照样读到了** ~/.dsh/.credentials.yaml
    的完整 SHA256 —— dsh 的沙箱是写边界，不是读边界。
    所以「把 key 放在用户目录里」这个前提本身就不成立。

怎么做：
    dsh 的 llm 适配器支持 baseURL + apiKeyEnv（见 dsh-llm-deepseek 文档），
    于是可以让 dsh 指向本机 broker，并持有一个**占位符** key：

        baseURL: http://127.0.0.1:<port>
        apiKeyEnv: DSH_BROKER_PLACEHOLDER     # 值是假的，随便写

    broker 在转发时把真 key 注入 x-api-key。agent 读遍磁盘与进程环境，
    拿到的都只是占位符。真 key 只在宿主进程的堆里。

能力边界（诚实标注，别当成绝对隔离）：
    · 挡住「读文件」这条路 —— 已验证
    · 挡不住「读本进程内存」—— 同用户进程用调试权限仍可拿到，那需要
      AppContainer / 受限令牌 / 容器 / 不同用户身份才能关掉（ADR 的 L2/L3）
    · 不改变 dsh 的任何行为，只是换了通道

用法（库）：
    from dsh_agent_kit.broker import Broker
    b = Broker(api_key="sk-...", upstream="https://api.deepseek.com/anthropic")
    port = b.start()                      # 0 => 自动选端口
    ...                                   # 把 port 写进 dsh 的 baseURL
    b.stop()

用法（命令行，调试用）：
    python -m dsh_agent_kit.broker --port 8787 --api-key-env DEEPSEEK_API_KEY
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

#: dsh 侧拿到的假 key —— 值不重要，只要非空（有些 SDK 会做空值校验）
PLACEHOLDER_KEY = "broker-placeholder-not-a-secret"

#: 这些请求头由 broker 自己重写或丢弃
DROP_REQUEST_HEADERS = {
    "host", "content-length", "x-api-key", "authorization",
    "connection", "accept-encoding",
}
DROP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "content-encoding",
}


class Broker:
    """把带占位符 key 的请求转发到真上游，并注入真 key。"""

    def __init__(
        self,
        api_key: str,
        upstream: str = "https://api.deepseek.com/anthropic",
        host: str = "127.0.0.1",
        port: int = 0,
        on_usage=None,
        verbose: bool = False,
    ):
        if not api_key:
            raise ValueError("broker 需要一个真 key")
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("upstream 必须是 HTTP(S) 绝对地址：%r" % upstream)
        self._api_key = api_key
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._base_path = parts.path.rstrip("/")
        self._on_usage = on_usage
        self._verbose = verbose
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._listen_port = port
        self._listen_host = host
        self.requests = 0                     # 便于测试断言

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> int:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):      # 静音；需要时用 verbose
                if broker._verbose:
                    sys.stderr.write("[broker] " + (fmt % args) + "\n")

            def do_POST(self):
                broker._handle(self, "POST")

            def do_GET(self):
                broker._handle(self, "GET")

            def do_DELETE(self):
                broker._handle(self, "DELETE")

        self._httpd = ThreadingHTTPServer((self._listen_host, self._listen_port), Handler)
        self._httpd.daemon_threads = True
        self._listen_port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self._listen_port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._api_key = ""                    # 主动抹掉堆里的引用

    @property
    def port(self) -> int:
        return self._listen_port

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self._listen_host, self._listen_port)

    # ------------------------------------------------------------------ 转发
    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        length = int(h.headers.get("content-length") or 0)
        body = h.rfile.read(length) if length else b""

        path = self._base_path + h.path
        headers = {
            k: v for k, v in h.headers.items()
            if k.lower() not in DROP_REQUEST_HEADERS
        }
        headers["x-api-key"] = self._api_key           # ← 唯一注入点
        headers["content-length"] = str(len(body))

        conn_cls = (
            http.client.HTTPSConnection if self._scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(self._host, self._port, timeout=900)
        try:
            conn.request(method, path, body=body or None, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:                       # 上游不可达
            payload = json.dumps({
                "type": "error",
                "error": {"type": "broker_upstream_error", "message": str(exc)[:300]},
            }).encode()
            h.send_response(502)
            h.send_header("content-type", "application/json")
            h.send_header("content-length", str(len(payload)))
            h.end_headers()
            h.wfile.write(payload)
            return

        self.requests += 1
        h.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in DROP_RESPONSE_HEADERS:
                h.send_header(key, value)
        h.send_header("transfer-encoding", "chunked")
        h.end_headers()

        self._pump(h, resp)
        conn.close()

    def _pump(self, h: BaseHTTPRequestHandler, resp) -> None:
        """边读边写（SSE 必须流式透传，不能攒完）。顺手抽 usage。"""
        usage_scan: List[bytes] = []
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                h.wfile.write(_chunk(chunk))
                h.wfile.flush()
                if self._on_usage is not None and len(usage_scan) < 64:
                    usage_scan.append(chunk)
            h.wfile.write(b"0\r\n\r\n")
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # 客户端提前断开：正常
        finally:
            if self._on_usage is not None and usage_scan:
                self._report_usage(b"".join(usage_scan))

    def _report_usage(self, blob: bytes) -> None:
        """尽力抽取 usage；抽不到就算了，绝不因为统计影响主链路。"""
        try:
            for line in blob.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data in (b"", b"[DONE]"):
                    continue
                obj = json.loads(data)
                usage = obj.get("usage") or (obj.get("message") or {}).get("usage")
                if usage:
                    self._on_usage(usage)
        except Exception:
            pass


def _chunk(data: bytes) -> bytes:
    return ("%x\r\n" % len(data)).encode() + data + b"\r\n"


# ---------------------------------------------------------------------- 命令行
def _load_key(api_key_env: str) -> str:
    key = os.environ.get(api_key_env, "").strip()
    if key:
        return key
    # 退路：从 harness home 的凭证文件里读一次，只为调试方便
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    path = os.path.join(home, ".credentials.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if api_key_env in line and ":" in line:
                    return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="凭证 broker（本地 LLM 代理）")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--upstream", default=os.environ.get(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic"))
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    key = _load_key(args.api_key_env)
    if not key:
        print("找不到 key：设 %s 或写进 $DSH_HOME/.credentials.yaml" % args.api_key_env,
              file=sys.stderr)
        return 2

    usage_total: Dict[str, int] = {}

    def on_usage(usage: dict) -> None:
        for k, v in usage.items():
            if isinstance(v, int):
                usage_total[k] = usage_total.get(k, 0) + v

    broker = Broker(key, upstream=args.upstream, host=args.host,
                    port=args.port, on_usage=on_usage, verbose=args.verbose)
    port = broker.start()
    print("broker 就绪: %s" % broker.base_url)
    print("dsh 侧配置：")
    print("  baseURL:   %s" % broker.base_url)
    print("  apiKeyEnv: DSH_BROKER_PLACEHOLDER   # 值随意，真 key 在这里")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        broker.stop()
        print("\n用量累计: %s" % (usage_total or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
