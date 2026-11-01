# dsh_agent_kit（Python，零依赖）

```bash
# 直接用（不需要安装）：把 python/ 加进 PYTHONPATH，或复制 dsh_agent_kit/ 到你的项目
export PYTHONPATH=/path/to/dsh-agent-kit/python
python -c "from dsh_agent_kit import DshClient; print(DshClient().ask('reply with OK').text)"

# 或者安装
pip install -e /path/to/dsh-agent-kit/python
```

## API

### `DshClient`

| 参数 | 默认 | 说明 |
|---|---|---|
| `dsh` | `"dsh"` | 可执行名或绝对路径（Windows 上会自动 `cmd /c` 包装 `.cmd`） |
| `launcher` | `None` | 直接给完整前缀，如 `[sys.executable, "wrapper.py"]`；非 `dsh` 可执行时用 |
| `profile` | `"headless"` | profile 名 |
| `cwd` | 当前目录 | 子进程工作目录 |
| `patches` | `[]` | `--patch` 列表，按顺序叠加 |
| `env` | `{}` | 追加环境变量（在启动瞬间合并到 `os.environ`） |
| `timeout` | `600.0` | 秒；到点 `kill()` |
| `strict` | `True` | 超时/非 0 退出抛 `DshError`；`False` 时产出 `error` 事件 |
| `track_session` | `True` | 自动记住并复用 `session_id` |
| `drop_session_env` | `True` | 摘除子进程继承的 `DSH_SESSION_ID`/`DSH_PROFILE`/`DSH_PROFILE_DIR` |
| `extra_launcher` / `extra_app` | `[]` | 追加启动器 flag / app 参数 |

| 方法 / 属性 | 说明 |
|---|---|
| `stream(task, session_id=…, timeout=…, extra_app=…)` | 生成器，逐条产出事件字典 |
| `ask(task, …) -> Turn` | 跑完一轮 |
| `session_id` | 当前会话 id（`None` = 尚未建立） |
| `reset()` | 忘掉会话 |
| `argv(...)` | 返回将执行的 argv（排障用） |
| `env` | 返回将传给子进程的环境（排障用） |

`session_id` 省略 → 用记住的会话；显式传 `None` → 强制开新会话。

### `Turn`

`text`（以 `final` 为准）· `session_id` · `usage` · `events` · `exit_code` · `stderr` · `duration` · `events_of(type)`
支持 `turn["text"]` 式访问。

### `DshError`

`kind` ∈ `spawn` / `timeout` / `exit`，另有 `exit_code`、`stderr`、`events`（已收到的事件，便于排障）。

### 网关

```bash
python -m dsh_agent_kit.gateway --port 8777 --cwd . [--token SECRET] [--patch FILE]
```

编程方式：

```python
from dsh_agent_kit.gateway import create_server

server, cfg = create_server(cwd=".", port=8777, token="dev-secret", max_concurrency=4)
server.serve_forever()
```

## 模块布局

```
dsh_agent_kit/
├── client.py    Popen + 逐行 JSONL + 超时 + 会话（纯 stdlib）
└── gateway.py   ThreadingHTTPServer + 手写 chunked SSE + 内置 demo 页
```
