"""零依赖 SSE 网关：把 DSH 的 JSONL 事件流转成浏览器可消费的 text/event-stream。

    python -m dsh_agent_kit.gateway --port 8777 --cwd /path/to/project
    # GET  /         内置单页 demo
    # POST /ask      {task, session_id?, cwd?} -> SSE，每帧 data: {事件JSON}
    # GET  /health   {ok, dsh, profile, cwd}
    # POST /shutdown 停止（仅回环地址）

实现要点（写自己的网关时照抄）：

1. HTTP/1.1 下必须手写 ``Transfer-Encoding: chunked`` 帧，否则 SSE 会卡住不刷；
2. 每帧后显式 flush；
3. 结束时写 ``0\\r\\n\\r\\n``；
4. ``X-Accel-Buffering: no`` 用于绕过反向代理缓冲。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from .client import DEFAULT_PROFILE, DshClient

DEMO_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>dsh-agent-kit · SSE demo</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
      background:#0d1117;color:#c9d1d9}
 header{padding:10px 16px;border-bottom:1px solid #21262d;display:flex;gap:12px;align-items:center}
 header b{color:#58a6ff}
 #meta{color:#8b949e;font-size:12px;margin-left:auto}
 main{padding:16px;display:grid;gap:12px;max-width:960px}
 textarea{width:100%;height:88px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;
          border-radius:6px;padding:10px;font:inherit;resize:vertical}
 button{background:#238636;color:#fff;border:0;border-radius:6px;padding:8px 16px;font:inherit;cursor:pointer}
 button.stop{background:#da3633}
 #out{white-space:pre-wrap;background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:12px;min-height:180px}
 #cost{color:#8b949e;font-size:12px}
</style></head><body>
<header><b>dsh-agent-kit</b><span>SSE gateway demo</span><span id="meta"></span></header>
<main>
  <textarea id="task" placeholder="输入任务，例如：解释这个仓库的构建流程"></textarea>
  <div><button id="go">运行</button> <button id="stop" class="stop">停止</button></div>
  <div id="out"></div>
  <div id="cost"></div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const out = $("#out"), cost = $("#cost"), meta = $("#meta");
let sid = null, ac = null;

fetch("/health").then(r => r.json()).then(h => {
  meta.textContent = `profile=${h.profile}  cwd=${h.cwd}`;
}).catch(() => meta.textContent = "后端未就绪");

$("#go").onclick = async () => {
  const task = $("#task").value.trim();
  if (!task) return;
  out.textContent = ""; cost.textContent = "";
  ac = new AbortController();
  const t0 = performance.now();
  try {
    const r = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, session_id: sid }),
      signal: ac.signal,
    });
    if (!r.ok) { out.textContent = "HTTP " + r.status + ": " + await r.text(); return; }

    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await rd.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });   // stream:true 必须，否则中文乱码
      let i;
      while ((i = buf.indexOf("\\n\\n")) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!frame.startsWith("data: ")) continue;
        const ev = JSON.parse(frame.slice(6));
        if (ev.type === "text") out.textContent += ev.text;
        else if (ev.type === "final") out.textContent = ev.text;
        else if (ev.type === "session") { sid = ev.sessionId; meta.textContent = "sid=" + sid; }
        else if (ev.type === "status" && ev.usage)
          cost.textContent = "usage: " + JSON.stringify(ev.usage);
        else if (ev.type === "error") out.textContent += "\\n[error] " + ev.message;
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") out.textContent += "\\n[js] " + e;
  } finally {
    cost.textContent += `  (${((performance.now() - t0) / 1000).toFixed(1)}s)`;
  }
};
$("#stop").onclick = () => ac && ac.abort();
</script></body></html>
"""


class Config:
    def __init__(
        self,
        cwd: str,
        dsh: str = "dsh",
        profile: str = DEFAULT_PROFILE,
        patches: Optional[list] = None,
        token: Optional[str] = None,
        timeout: float = 600.0,
        max_concurrency: int = 4,
        quiet: bool = False,
        log_dir: Optional[str] = None,
        launcher: Optional[list] = None,
    ) -> None:
        self.cwd = os.path.realpath(cwd)
        self.dsh = dsh
        self.launcher = list(launcher) if launcher else None
        self.profile = profile
        self.patches = list(patches or ())
        self.token = token
        self.timeout = float(timeout)
        self.max_concurrency = int(max_concurrency)
        self.quiet = quiet
        self.log_dir = os.path.realpath(log_dir) if log_dir else None


def make_handler(cfg: Config, sem: threading.BoundedSemaphore):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "dsh-agent-kit/1.0"
        config = cfg

        # -------- 基础 -------- #
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if self.config.quiet:
                return
            sys.stderr.write("[gateway] %s %s\n" % (self.address_string(), fmt % args))

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _send(self, code: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _authorized(self) -> bool:
            if not self.config.token:
                return True
            header = self.headers.get("Authorization", "")
            if header == "Bearer " + self.config.token:
                return True
            query = self.path.split("token=", 1)[1] if "token=" in self.path else ""
            return bool(query) and query.split("&", 1)[0] == self.config.token

        # -------- 路由 -------- #
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not self._authorized():
                return self._json({"error": "unauthorized"}, 401)
            if path in ("/", "/index.html"):
                return self._send(200, DEMO_HTML.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/health":
                return self._json({
                    "ok": True,
                    "dsh": self.config.dsh,
                    "profile": self.config.profile,
                    "patches": self.config.patches,
                    "cwd": self.config.cwd,
                })
            if path == "/favicon.ico":
                return self._send(204)
            return self._json({"error": "not found", "path": path}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not self._authorized():
                return self._json({"error": "unauthorized"}, 401)
            if path == "/ask":
                return self._ask()
            if path == "/shutdown":
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._json({"error": "loopback only"}, 403)
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self._json({"error": "not found", "path": path}, 404)

        # -------- /ask -------- #
        def _body(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return obj if isinstance(obj, dict) else {}

        def _safe_cwd(self, requested: Optional[str]) -> Optional[str]:
            """只允许 config.cwd 及其子目录，防目录穿越。"""
            if not requested:
                return self.config.cwd
            target = os.path.realpath(os.path.join(self.config.cwd, requested))
            if target == self.config.cwd or target.startswith(self.config.cwd + os.sep):
                return target
            return None

        def _chunk(self, payload: bytes) -> None:
            self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
            self.wfile.flush()

        def _sse_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self._cors()
            self.end_headers()

        def _emit(self, event: Dict[str, Any]) -> None:
            frame = "data: %s\n\n" % json.dumps(event, ensure_ascii=False)
            self._chunk(frame.encode("utf-8"))

        def _ask(self) -> None:
            payload = self._body()
            task = (payload.get("task") or "").strip()
            if not task:
                return self._json({"error": "task required"}, 400)

            cwd = self._safe_cwd(payload.get("cwd"))
            if cwd is None:
                return self._json({"error": "cwd outside allowed root"}, 403)

            if not sem.acquire(blocking=False):
                return self._json({"error": "busy", "max_concurrency": self.config.max_concurrency}, 429)

            session_id = payload.get("session_id") or None
            client = DshClient(
                dsh=self.config.dsh,
                profile=self.config.profile,
                cwd=cwd,
                patches=self.config.patches,
                timeout=self.config.timeout,
                strict=False,          # 失败走 error 事件，前端不需要区分 HTTP 语义
                launcher=self.config.launcher,
            )

            self.close_connection = True
            self._sse_headers()
            started = time.monotonic()
            sink = None
            try:
                if self.config.log_dir:
                    os.makedirs(self.config.log_dir, exist_ok=True)
                    name = "%s-%s.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
                    sink = open(os.path.join(self.config.log_dir, name), "a", encoding="utf-8")

                for ev in client.stream(task, session_id=session_id):
                    if sink:
                        sink.write(json.dumps(ev, ensure_ascii=False) + "\n")
                        sink.flush()
                    self._emit(ev)
            except Exception as exc:  # noqa: BLE001 - 任何异常都要变成一帧，不能只断连接
                try:
                    self._emit({"type": "error", "message": "%s: %s" % (type(exc).__name__, exc)})
                except Exception:  # noqa: BLE001
                    pass
            finally:
                if sink:
                    sink.close()
                sem.release()
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except Exception:  # noqa: BLE001
                    pass
                if not self.config.quiet:
                    sys.stderr.write("[gateway] ask done in %.1fs\n" % (time.monotonic() - started))

    return Handler


def create_server(
    cwd: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8777,
    **kwargs: Any
) -> Tuple[ThreadingHTTPServer, Config]:
    """建服务但不启动。测试直接用它。"""
    cfg = Config(cwd=cwd or os.getcwd(), **kwargs)
    sem = threading.BoundedSemaphore(cfg.max_concurrency)
    server = ThreadingHTTPServer((host, port), make_handler(cfg, sem))
    server.daemon_threads = True
    return server, cfg


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dsh_agent_kit.gateway",
        description="DSH JSONL -> SSE 网关（零依赖）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--cwd", default=os.getcwd(), help="任务工作目录（同时是 cwd 白名单根）")
    parser.add_argument("--dsh", default="dsh", help="dsh 可执行名或路径")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--patch", action="append", default=[], dest="patches",
                        help="叠加 patch 文件，可重复")
    parser.add_argument("--token", default=None, help="要求 Authorization: Bearer <token>")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--log-dir", default=None, help="把每帧另存为 jsonl")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    server, cfg = create_server(
        cwd=args.cwd,
        host=args.host,
        port=args.port,
        dsh=args.dsh,
        profile=args.profile,
        patches=args.patches,
        token=args.token,
        timeout=args.timeout,
        max_concurrency=args.max_concurrency,
        log_dir=args.log_dir,
        quiet=args.quiet,
    )
    host, port = server.server_address[:2]
    sys.stderr.write(
        "dsh-agent-kit gateway -> http://%s:%s/  (profile=%s, cwd=%s%s)\n"
        % (host, port, cfg.profile, cfg.cwd,
           ", token required" if cfg.token else "")
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
