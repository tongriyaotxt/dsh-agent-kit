# DSH Agent Kit · 把 agent 接进你的应用

> **一句话**：`dsh --profile headless --json -`，任务走 **stdin**，事件流按行回 **stdout**。
> 不用 SDK、不用鉴权、不用逆向内部 API。任何语言 30 行内接完。

这是**开发者接入 DSH agent 的标准方法**。它把「怎么接」从口头经验变成四件可交付物：

| 交付物 | 文件 | 作用 |
|---|---|---|
| **协议** | [`spec/events.schema.json`](spec/events.schema.json) + [`docs/02-protocol.md`](docs/02-protocol.md) | 事件流的权威定义与生命周期不变量 |
| **客户端** | [`python/`](python/) · [`node/`](node/) | 零依赖实现（Popen / spawn + 逐行 JSON） |
| **网关** | [`python/dsh_agent_kit/gateway.py`](python/dsh_agent_kit/gateway.py) | 一行命令把事件流转成 **SSE**，浏览器直接消费 |
| **一致性测试** | [`tests/`](tests/) | 离线回归（零 token）+ 真实 dsh 校验（`--live`） |

---

## 30 秒接入

**Python**

```python
from dsh_agent_kit import DshClient

c = DshClient(cwd="/path/to/project")
for ev in c.stream("解释这个仓库的构建流程"):
    if ev["type"] == "text":
        print(ev["text"], end="", flush=True)
```

**Node ≥ 18**

```js
import { DshClient } from "./node/dsh-client.mjs";

const c = new DshClient({ cwd: "/path/to/project" });
for await (const ev of c.stream("解释这个仓库的构建流程")) {
  if (ev.type === "text") process.stdout.write(ev.text);
}
```

**只剩 shell**

```bash
printf '%s' "解释这个仓库的构建流程" | dsh --profile headless --json - | while read -r l; do
  echo "$l" | grep -q '"type":"text"' && echo "$l"
done
```

三者走的是同一条管道。客户端只是帮你处理了 **stdin 传参、逐行解析、stderr 分流、超时 kill、会话续接** 这五件琐事。

---

## 一、先做一个决定：三层接口选哪层

| 层 | 入口 | 鉴权 | 结构化 | 选它的理由 |
|---|---|---|---|---|
| **L1 进程 + JSONL** | `dsh --profile headless --json -` | 无 | ✅ 事件流 | **默认选它**。跨语言、可隔离、可并发、可超时 |
| **L2 HTTP** | `dsh web --port N` → `/api/*` | token → cookie | ⚠️ 内部协议 | 仅当你需要**嵌入官方 UI**（iframe） |
| **L3 终端驱动** | 窗格 + SendInput | 无 | ❌ 屏幕像素 | 只在「必须让人看着 TUI 跑」时用 |

决策树：

```
要程序化拿结果？
├─ 是 ──► L1（spawn 子进程，消费 JSONL）
│         ├─ 前端要流式？──► L1 + 本仓库 gateway（SSE）
│         └─ 多用户常驻？──► L1 + 任务队列（每任务一个进程）
└─ 否 ──► 要人机同屏？──► 让用户自己开 dsh tui，别驱动它
```

L2 的实测结论：`?token=` 只能拿到页面与 cookie，`/api/*` 鉴权通过后**路径仍 404**（路径不在前端 bundle 里）。做程序化集成会陷进逆向内网协议，收益为负。

L3 的实测结论：能工作，但会抢焦点、标题被覆盖、启动 splash 吞输入、可能打错窗口。**它不是 API。**

---

## 二、协议：一行一个事件

```bash
printf '%s' "reply with OK" | dsh --profile headless --json -
```

真实输出（`dsh 0.2.1-rc.2`）：

```json
{"type":"session","sessionId":"session-ed3cbd0a-...","cwd":"D:\\proj"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"thinking","text":""}
{"type":"text","text":"OK"}
{"type":"status","phase":"step_end","turn":1,"step":1,
 "usage":{"inputTokens":15968,"outputTokens":4,"cacheReadTokens":384,"totalTokens":16356}}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}
{"type":"final","text":"OK"}
```

| `type` | 关键字段 | 用途 |
|---|---|---|
| `session` | `sessionId`, `cwd` | **第一件事**：存下 `sessionId` 用于续接 |
| `status` | `phase` ∈ `turn_start｜step_start｜step_end｜turn_end`，`usage`，`reason` | 进度条、成本面板、终止原因 |
| `thinking` | `text` | 思考增量（可选展示） |
| `text` | `text` | **正文增量，用于流式渲染** |
| `tool` | 版本相关，按原样透传 | 显示「正在写文件」 |
| `final` | `text` | **最终答案**，以它为准 |

**三条不变量**（[一致性测试](tests/conformance.py) 就是在验这三条）：

1. 第一条事件必为 `session`，且带非空 `sessionId`；
2. 正常结束必有一条 `final`，且 `status(turn_end).reason.kind` 存在；
3. **诊断只走 stderr，stdout 只有 JSONL**；退出码 ≠ 0 即失败。

字段级定义见 [`spec/events.schema.json`](spec/events.schema.json)，版本演进策略见 [`docs/02-protocol.md`](docs/02-protocol.md#版本演进)。

---

## 三、客户端能干什么

```python
c = DshClient(cwd="/path/to/project", profile="headless", patches=[], timeout=600.0)

# 1) 同步：拿完整结果 + 用量
t = c.ask("列出三个候选方案")
t.text, t.usage, t.session_id, t.events, t.exit_code, t.stderr

# 2) 流式：边跑边渲染
for ev in c.stream("重构 utils.py"):
    ...

# 3) 多轮：session 自动记住，也可显式指定
t2 = c.ask("展开第二个")                       # 自动续上一轮
t3 = c.ask("继续", session_id=t.session_id)    # 显式续指定会话
c.reset()                                      # 开新会话

# 4) 严格模式：非 0 退出抛 DshError（默认 strict=True）
DshClient(strict=False).ask(...)               # 要「失败也拿到部分事件」时
```

同一套参数在 Node 侧一一对应（`sessionId` / `patches` / `timeoutMs`）。

**它替你挡掉的坑**（都在 [客户端源码](python/dsh_agent_kit/client.py) 里）：

- Windows 上 `dsh` 是 `dsh.cmd`，直接 `Popen` 会 `FileNotFoundError` → `shutil.which` + `cmd /c` 包装；
- 任务拼进命令行会被 shell 吃掉中文/引号/反斜杠/换行 → **任务永远走 stdin**，参数只留 `-`；
- stderr 不分离会污染 JSONL → 独立线程收，失败时截尾 800 字符附进异常；
- 超时不能指望 dsh 内部 → `deadline` + `kill()`；
- **嵌套调用**：子进程若继承 `DSH_SESSION_ID` / `DSH_PROFILE` / `DSH_PROFILE_DIR`，会串进父会话 → 客户端启动前主动摘除这三个变量。

---

## 四、网关：让前端直接吃事件流

浏览器不能 spawn 进程，所以后端加一层 SSE 就行。仓库自带 **零依赖** 网关（stdlib `http.server`）：

```bash
python -m dsh_agent_kit.gateway --port 8777 --cwd /path/to/project
# 打开 http://127.0.0.1:8777/  → 输入框里打字，答案流式返回
```

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 内置单页 demo（textarea + 流式渲染） |
| `/ask` | POST | `{task, session_id?, cwd?}` → `text/event-stream`，每帧 `data: {事件JSON}` |
| `/health` | GET | `{ok, dsh, profile, cwd}` |
| `/shutdown` | POST | 停止服务（仅回环地址可用） |

服务端要点（自己写网关时照抄）：

- 必须发 `Transfer-Encoding: chunked` 并**手写 chunk 帧**，否则 HTTP/1.1 下 SSE 会卡住不刷；
- 每帧后显式 `flush()`；结束时写 `0\r\n\r\n`；
- 加 `Access-Control-Allow-Origin` 与 `OPTIONS` 预检，前端才能跨端口；
- 想限制调用方就设 `--token`，客户端加 `Authorization: Bearer <token>`。

前端消费（[examples/browser.html](examples/browser.html) 是完整可跑的）：

```js
const r = await fetch("http://127.0.0.1:8777/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ task: "写个 README" }),
});
const rd = r.body.getReader(), dec = new TextDecoder();
for (;;) {
  const { value, done } = await rd.read();
  if (done) break;
  for (const frame of dec.decode(value).split("\n\n")) {
    if (!frame.startsWith("data: ")) continue;
    const ev = JSON.parse(frame.slice(6));
    if (ev.type === "text") out.append(ev.text);
  }
}
```

---

## 五、Profile：给 agent 配一个专属能力集

`profile` = 一组插件 bundle + 你的覆盖层。**接入最干净的做法是造一个专用 profile，只装你要的能力**，应用侧只管 spawn。

```bash
dsh --from-default-profile headless --profile myagent   # 从模板复制
dsh --profile myagent --json -                          # 用它
```

目录结构：

```
~/.dsh/profiles/myagent/
├── package.json        # dsh.profile.bundles：用哪些 bundle
├── cordis.yml          # 空 []（树由 patch 组成）
└── cordis.patch.yml    # 你的覆盖层：insert 插件 / 改 disabled / 配 MCP
```

模板见 [`configs/profile-template/`](configs/profile-template/)。

### headless 到底缺什么（实测，不是读配置）

两个 profile 装包数不同（**headless 97 / tui 124**），但配置里 `disabled` 常是 **JS 表达式**（`!!js '!ctx.get(...)'`），静态比对必然误判 —— 只能用行为验证：

| 能力 | headless | tui |
|---|---|---|
| bash / pwsh / fs / fs-search / skill | ✅ | ✅ |
| web_search / web_fetch | ✅ | ✅ |
| subagent / workflow | ✅ | ✅ |
| **MCP 工具** | ❌ → 需 `--patch` | ✅ |
| 上下文压缩 compaction | ❌ | ✅ |
| plan mode / goal / todo | ❌ | ✅ |

**真正的缺口只有 MCP**。补法见 [`docs/04-mcp.md`](docs/04-mcp.md)：

```bash
dsh --profile headless --patch configs/mcp.patch.yml --json -
```

⚠️ **两个位置的坑**：

```bash
dsh headless --patch x.yml                    # ✗ unknown option '--patch'
dsh --patch x.yml --profile headless --json - # ✓ 启动器 flag 必须在 app 参数之前
```

`dsh [--profile] <name> [options] [app-args...]` —— `--patch` / `--profile` / `--from-default-profile` 属**启动器**，`--json` / `--session-id` / 任务文本属 **app 参数**。顺序错了就是 unknown option。

💸 **成本提醒**：工具定义进上下文是要付 token 的。实测只开 `screen-use` 一个 MCP，`inputTokens` 从 **169 → 1.8 万**。所以 `configs/mcp.patch.yml` 默认只开一个，其余是注释，按需打开。

---

## 六、验证：先离线，再 live

```bash
# 1) 离线回归：用假 dsh 跑客户端/网关逻辑，零 token、零副作用
python tests/test_client.py
python tests/test_gateway.py

# 2) 真实 dsh 一致性：验协议三条不变量 + 会话续接
python tests/conformance.py --live

# 3) 冒烟：一轮真实调用
python examples/hello.py "reply with OK"
```

`tests/fake_dsh.py` 是一个**行为级**假 dsh：它像真 CLI 一样严格解析 argv（`--patch` 位置错了就报 unknown option）、按真实事件序吐 JSONL、支持故障注入（`FAKE_DSH_EXIT=3`、`FAKE_DSH_STALL=1`、`FAKE_DSH_BADLINE=1`）。所以 CI 里不需要装 dsh 也不烧 token，就能守住接入层的回归。

### 这些结论是怎么验的

| 项 | 验证方式 | 状态 |
|---|---|---|
| argv 顺序、`--patch` 位置 | 对**真实** `dsh 0.2.1-rc.2` 跑 `--dump-config`；错误顺序复现 `unknown option '--patch'` | ✅ 真 CLI |
| profile 组合与覆盖层来源 | 真 CLI `--dump-config` 的 `# == <层>` 链 | ✅ 真 CLI |
| 客户端（超时 / 会话 / stderr / 环境摘除） | 16 个离线用例 | ✅ |
| 网关（chunked / CORS / cwd 白名单 / token） | 7 个离线用例 | ✅ |
| Node 客户端与网关 | 10 个 `node --test` 用例 | ✅ |
| 事件协议三条不变量 | fake dsh 离线；真机请跑 `--live` | ✅ 离线 |
| 真实模型往返 + token 计数 | `python tests/conformance.py --live` | ⏳ 需可用凭据 |

live 那一栏没打勾，不是代码问题：验证机的 `headless` profile 当前拿不到模型凭据（`MISSING_CREDENTIAL`，凭据服务里只有浏览器会话授权），换一台有 `DEEPSEEK_API_KEY` 的机器或先 `dsh web` 写入 key，再跑一条命令即可补验：

```bash
export DEEPSEEK_API_KEY=sk-...
python tests/conformance.py --live
```

`conformance.py` 会把这两类真实故障（凭据缺失、插件版本不兼容）直接翻译成可执行建议，省得你去翻 stderr。

---

## 七、踩坑清单（全部实测）

| # | 症状 | 原因 | 修法 |
|---|---|---|---|
| 1 | `FileNotFoundError: dsh` | Windows 上 npm 装的是 `dsh.cmd` | `shutil.which()` 解析 + `cmd /c` 包装 |
| 2 | 中文任务变乱码 / 引号丢失 | 任务拼进命令行被 shell 解析 | 任务走 **stdin**，argv 只留 `-` |
| 3 | `JSON.parse` 偶发失败 | 诊断信息混进 stdout | stderr 独立收；解析失败降级成 `_raw` |
| 4 | 前端一直等不到数据 | HTTP/1.1 下没发 chunked 帧 | 手写 `Transfer-Encoding: chunked` + 显式 flush |
| 5 | `unknown option '--patch'` | 启动器 flag 写在了 app 参数之后 | `--patch` 放 `--profile` 前 / app 参数前 |
| 6 | 多轮丢上下文 | 没保存 `sessionId` | 存 `session.sessionId`，下轮 `--session-id` |
| 7 | 偶发串进别人的会话 | 子进程继承 `DSH_SESSION_ID` | 启动前摘除 `DSH_*` 会话级变量 |
| 8 | 卡死不返回 | 依赖 dsh 内部超时 | 自己算 deadline，到点 `kill()` |
| 9 | 成本暴涨 | 一次性开了多个 MCP | 只开必需的；工具定义进上下文按 token 计费 |

---

## 八、为什么不做一个「真正的 SDK」

这是有意的设计选择，不是偷懒：

1. **协议只有一个进程边界**。`dsh` 已经把「模型 + 工具 + 会话」封装完了，再加一层 SDK 只是给 `Popen` 套壳，抽象收益 < 维护成本。
2. **跨语言零成本**。JSONL 是任何语言的一等公民；SDK 要为每种语言维护一份 bug。
3. **隔离天然成立**。一个任务 = 一个进程 = 一个 cwd = 一套环境变量。崩溃、超时、内存泄漏都被操作系统兜住。
4. **不用鉴权**。L1 没有 token，就没有 token 泄露、续期、跨域 cookie 那一整套事故面。
5. **升级不破坏**。字段只增不改（见协议版本策略），消费方忽略未知 `type` 即可前向兼容。

因此本仓库的定位是：**规范 + 参考实现 + 回归用例**，而不是又一层框架。

---

## 九、接入检查表

复制到你的 PR 描述里，逐条打勾：

- [ ] 用 `--profile` 指定了专用 profile，没有依赖开发机默认配置
- [ ] 任务走 stdin，argv 里没有拼接用户输入
- [ ] 保存了 `session.sessionId`，多轮靠 `--session-id` 续接
- [ ] stderr 单独收集并截尾附进错误
- [ ] 自己实现 deadline，超时 `kill()` 并清理子进程
- [ ] 子进程环境摘除了 `DSH_SESSION_ID` / `DSH_PROFILE` / `DSH_PROFILE_DIR`
- [ ] 以 `final.text` 为答案，`text` 仅用于流式渲染
- [ ] `status.step_end.usage` 已接到成本面板
- [ ] 需要 MCP 时用 `--patch`，并只开必需的那一个
- [ ] `python tests/test_client.py` 与 `tests/conformance.py --live` 都通过

---

## 十、目录

```
dsh-agent-kit/
├── README.md                      ← 本文件（接入手册）
├── docs/
│   ├── 01-architecture.md         三层接口的实测对比与选型
│   ├── 02-protocol.md             事件协议权威表、生命周期、版本演进
│   ├── 03-profiles.md             profile 结构与覆盖层
│   ├── 04-mcp.md                  headless 补 MCP、token 成本
│   ├── 05-server-side.md          SSE/chunked 网关实现要点
│   ├── 06-troubleshooting.md      症状 → 诊断 → 修法
│   ├── 07-faq.md                  常见疑问
│   └── 08-app-contract.md         应用侧要给 agent 的三件东西
├── spec/events.schema.json        事件流 JSON Schema
├── python/dsh_agent_kit/          client.py · gateway.py（零依赖）
├── node/                          dsh-client.mjs · sse-gateway.mjs
├── examples/                      hello · stream · multiturn · batch · 网关 · 浏览器
├── configs/                       mcp.patch.yml · profile-template/
└── tests/                         fake_dsh.py · 离线测试 · conformance
```

## 兼容性

在 `dsh 0.2.1-rc.2` / Windows 11 / Python 3.9–3.13 / Node 18–24 上实测通过。
协议本身与平台无关；`cmd /c` 分支只在 Windows 触发，Linux/macOS 直接 exec。

## License

MIT
