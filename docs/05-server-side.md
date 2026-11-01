# 05 · 服务端：把事件流变成 SSE

前端不能 spawn 进程，所以要有一个**同机后端**把 JSONL 转成 SSE。本仓库自带零依赖实现：[`../python/dsh_agent_kit/gateway.py`](../python/dsh_agent_kit/gateway.py)。

```bash
python -m dsh_agent_kit.gateway --port 8777 --cwd /path/to/project [--token SECRET]
# GET  /            内置单页 demo
# POST /ask         {task, session_id?, cwd?}  → text/event-stream
# GET  /health      {ok, dsh, profile, cwd}
# POST /shutdown    停止（仅回环）
```

## 一、端点契约

### `POST /ask`

请求：

```json
{"task": "写个 README", "session_id": "session-...", "cwd": "/proj"}
```

`session_id` 与 `cwd` 可选；`cwd` 受白名单限制（默认只允许 `--cwd` 及其子目录，防目录穿越）。

响应：`200 text/event-stream`，每帧一个事件：

```
data: {"type":"session","sessionId":"session-...","cwd":"/proj"}

data: {"type":"text","text":"# 项目"}

data: {"type":"final","text":"# 项目\n..."}

```

客户端错误时先发一帧合成事件再收尾：

```
data: {"type":"error","message":"dsh exit 1: ..."}
```

### `GET /health`

```json
{"ok": true, "dsh": "C:\\Users\\x\\AppData\\Roaming\\npm\\dsh.cmd", "profile": "headless", "cwd": "/proj"}
```

用于探活与前端启动自检（拿不到就提示「后端没起」）。

## 二、必须手写 chunked（否则一定卡）

HTTP/1.1 下 `BaseHTTPRequestHandler` 默认按 `Content-Length` 语义工作。SSE 是无限长响应，没有长度。**不发 `Transfer-Encoding: chunked` 并手写帧，客户端会一直等首批数据。**

```python
self.send_response(200)
self.send_header("Content-Type", "text/event-stream; charset=utf-8")
self.send_header("Cache-Control", "no-cache, no-transform")
self.send_header("Connection", "close")
self.send_header("X-Accel-Buffering", "no")      # 前面有 nginx 时关缓冲
self.send_header("Transfer-Encoding", "chunked")
self.end_headers()

def put(payload: bytes):
    self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
    self.wfile.flush()

for ev in client.stream(task):
    put(("data: %s\n\n" % json.dumps(ev, ensure_ascii=False)).encode("utf-8"))

self.wfile.write(b"0\r\n\r\n")   # 结束帧
self.wfile.flush()
```

三个容易漏的点：

1. **每帧后 `flush()`** —— 不刷就进了缓冲区，前端看到的是「一次性吐完」；
2. **结束后写 `0\r\n\r\n`** —— 不写前端永远等下一帧；
3. **`X-Accel-Buffering: no`** —— 有反向代理时绕过其缓冲。

## 三、前端消费

```js
const r = await fetch("http://127.0.0.1:8777/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ task, session_id: sid ?? undefined }),
  signal: ac.signal,                      // 用 AbortController 支持「停止」
});
if (!r.ok) throw new Error(await r.text());

const rd = r.body.getReader(), dec = new TextDecoder();
let buf = "";
for (;;) {
  const { value, done } = await rd.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  let i;
  while ((i = buf.indexOf("\n\n")) >= 0) {
    const frame = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!frame.startsWith("data: ")) continue;
    const ev = JSON.parse(frame.slice(6));
    if (ev.type === "text") out.append(ev.text);
    if (ev.type === "final") sid = sid ?? ev.sessionId;
    if (ev.type === "error") showError(ev.message);
  }
}
```

注意 `dec.decode(value, {stream: true})`：UTF-8 多字节字符会被 chunk 边界切开，不加 `stream: true` 会出乱码（中文场景必踩）。

## 四、会话与并发

- 网关**不持有会话状态**，`session_id` 由前端保存、随请求带回。这样后端可重启、可多实例（用一个共享 `cwd` 卷）。
- 每个 `/ask` 就是一个 dsh 子进程，`ThreadingHTTPServer` 天然并发。
- 要限流就在 `_handle_ask` 入口加信号量（例如同时最多 4 个）：
  ```python
  if not SEM.acquire(blocking=False):
      self._json({"error": "busy"}, 429); return
  try: ... finally: SEM.release()
  ```
- 要审计就把每帧同时写一行到 `runs/<ts>-<sid>.jsonl`；这比解析 stderr 可靠。

## 五、安全边界（工程项，不是免责声明）

网关会**在本机执行任意任务**，等价于给调用方一个 shell。所以：

| 措施 | 做法 |
|---|---|
| 只绑回环 | 默认 `127.0.0.1`；不建议 `0.0.0.0` |
| Token 门禁 | `--token SECRET`，客户端带 `Authorization: Bearer` |
| cwd 白名单 | 只允许 `--cwd` 及其子目录 |
| 资源上限 | 每请求超时 + 并发信号量 |
| 只读模式 | 传 `--profile` 一个受限 profile（关 bash/fs 写工具） |

多用户场景不要把网关直接暴露；正确形态是「网关在后面 + 你的鉴权 + 每用户隔离的 cwd」。

## 六、替代传输

| 传输 | 何时 | 代价 |
|---|---|---|
| **SSE** | 单向流式（推荐） | 无需握手，浏览器原生 |
| WebSocket | 需要中途注入新指令 | 要处理双向协议、心跳 |
| 轮询 | 环境不允许长连接 | 延迟高，浪费连接 |
| 纯 HTTP 一次性返回 | 不要流式 | 简单，但用户看不到进度 |

SSE 的关键优势：**断线重连语义简单**（重新发一次任务，带 `session_id` 即可继续）。真要「接着上次的流」才需要 `Last-Event-ID`，本仓库不做，因为 dsh 会话本身可以续接，重发任务更可靠。
