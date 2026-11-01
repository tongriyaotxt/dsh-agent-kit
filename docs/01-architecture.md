# 01 · 架构：三层接口与选型

DSH 对外可用的接法只有三种。选错层是接入失败最常见的原因，所以先看实测结论再动手。

## 一、三层总表

| | L1 进程 + JSONL | L2 HTTP（`dsh web`） | L3 终端驱动 |
|---|---|---|---|
| 入口 | `dsh --profile headless --json -` | `dsh web --port N` → `/api/*` | 窗口 + 键盘注入 |
| 鉴权 | 无 | `?token=` → cookie `dsh-auth-<hash>` | 无 |
| 输出 | 结构化 JSONL | JSON（路径未知） | 屏幕像素 |
| 跨语言 | ✅ | ✅ | ❌ |
| 可并发 | ✅ 一任务一进程 | ⚠️ 单服务竞争 | ❌ 抢焦点 |
| 可超时隔离 | ✅ kill 进程 | ⚠️ 只能断连接 | ❌ 无法可靠中断 |
| 无人值守 | ✅ | ✅ | ❌ |
| 适合 | **默认** | 嵌入官方 UI | 人机同屏 |

## 二、L1 进程 + JSONL（首选）

```
你的应用 ──stdin(task)──►  dsh 子进程  ──stdout(JSONL)──►  你的应用
                      ◄────────────────  stderr(诊断)  ◄──
```

协议面只有三件事：

1. **argv**：启动器 flag（`--profile` / `--patch`）在前，app 参数（`--json` / `--session-id` / 任务）在后。
2. **stdin**：任务原文，写完即 close（等价于 EOF）。
3. **stdout**：一行一个 JSON 对象；**stderr 只放诊断**。

为什么这是默认答案：

- 进程边界自带隔离：cwd、环境变量、崩溃、超时全部由操作系统管理；
- 没有鉴权面：不存在 token 泄露/续期/跨域 cookie 问题；
- 天然可并发：应用要 10 个并行任务就起 10 个进程，互不干扰；
- 日志友好：把 stdout 原样落盘就是完整运行记录，便于回放与计费。

代价：每任务一次冷启动（加载 profile 插件 + 建会话），通常 1–3 秒。批量任务要复用会话而不是复用进程 —— 会话靠 `--session-id` 续接，进程仍是一任务一进程。

## 三、L2 HTTP（只用于嵌 UI）

```bash
dsh web --no-open --port 8899
# stdout: http://127.0.0.1:8899/?token=MX4y6Wm...
```

- 用 `?token=` 打开首页 → 服务端种下 cookie `dsh-auth-<hash>`；
- 带 cookie 请求 `/api/*`：**鉴权通过，但路径返回 404**。这些路径不在前端 bundle 里，属于内部协议；
- `--trusted-host` 控制 `/api` 的 browser-trust fence（防 DNS rebinding）。

结论：**能拿到页面，拿不到稳定的程序化 API**。可行用法只有 iframe 嵌入官方 UI。要程序化，回 L1。

> 如果你的场景是「多用户常驻」，正确解法不是去逆向 L2，而是 L1 + 队列：每个任务一个进程，队列控并发，会话 id 落库。

## 四、L3 终端驱动（不是 API）

实测跑通过完整链路：`wt -w -1` 开新窗 → 窗口集合差分锁定 hwnd → `AttachThreadInput` 绕前台限制后 `SetForegroundWindow` → `SendInput` 逐字符 Unicode → `Enter`。dsh-tui 确实收到并执行了命令。

但四个硬伤：

1. **抢焦点** —— 用户正在别的窗口打字会被打断；
2. **标题被覆盖** —— dsh-tui 会把窗口标题设成任务文本，无法靠标题定位；
3. **启动 splash** —— 前 ~20 秒的输入会被吞，必须补发空回车；
4. **可能打错窗口** —— 早期版本按 pid 兜底，把文字打进了用户正在用的会话。

只在「必须让人看着 TUI 跑」时用，且必须：独立窗口（`-w -1`）、窗口集合差分锁 hwnd（不猜）、注入前 `IsWindow` + 激活结果校验、失败就报错退出。

## 五、反向集成：让 DSH 调用你的工具

上面的三层都是「你的应用调 DSH」。反过来，DSH 要调你的能力，走 **MCP server**：

```
你的工具 ◄──stdio(MCP)──► dsh 子进程
```

在 profile 的 `cordis.patch.yml` 里 `insert` 一个 `@deepseek-ai/dsh-mcp-client` 条目即可，见 [04-mcp.md](04-mcp.md)。

两个方向可以同时存在：应用 spawn dsh，dsh 通过 MCP 回调应用暴露的工具 —— 这时要注意别形成调用环（工具里再 spawn dsh 会嵌套）。

## 六、组合矩阵

| 你的应用 | 传输 | 建议 |
|---|---|---|
| Web 前端 / 桌面 App / 编辑器插件 | L1 + 本仓库网关 | 后端 spawn，前端消费 SSE |
| CI / 批处理 / 定时任务 | L1 | 无状态，JSONL 落盘即可 |
| 多用户 SaaS | L1 + 队列 | 一任务一进程，会话 id 入库 |
| 已有 DSH Web 界面 | L2 | iframe 嵌入，别碰 `/api` |
| 需要人盯着看 | L3 或让用户自己开 | 别把 TUI 当 API |
| 想让 DSH 用你的私有能力 | MCP server | 反向集成 |

## 七、一张图

```
                    ┌──────────── 你的应用 ────────────┐
                    │  UI / 业务逻辑 / 状态存储         │
                    └───┬───────────────────────▲──────┘
        L1 spawn+stdin  │                       │ JSONL / SSE
                        ▼                       │
                  ┌───────────────────────────────────┐
                  │  dsh --profile <name> --json -    │
                  │  ├─ plugins (fs/bash/web/子代理)   │
                  │  ├─ MCP client ──► 你的工具        │
                  │  └─ session (可 --session-id 续接) │
                  └───────────────────────────────────┘
```
