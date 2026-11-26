# Changelog

## 1.9.0 — 2026-11-26

传播轮：把「路人 60 秒内相信你」这件事做成产品功能。

### 新增

- **`dsh-agent-kit demo`**：60 秒演示，**零依赖 · 不联网 · 不用 API key · 不需要装 dsh**。
  全程真实执行（scaffold → 红 → 写实现 → 2/2 + 变异 4/4 → 自证成立），
  最后一步故意**把断言放松**，展示「变异逃逸 → 自证不成立」。
  这是整个仓库对外的第一屏：看到它的人 99% 没装 dsh，不能要求他先配环境。
- **`dsh-agent-kit` CLI**（`python/dsh_agent_kit/cli.py`）：`demo` / `scaffold` / `verify` / `gateway`
  四个子命令，参数完全透传，和 `python -m dsh_agent_kit.xxx` 行为一致。
- **`assets/demo.svg`**：手写终端风格首屏图（无字体依赖风险，GitHub 直接渲染）。
  之前仓库里**零图片零视频** —— GitHub 上没人会为了理解你去读 300 行。
- **README 重写**：
  - `README.md` → 英文门面（星标主要在英文圈）：一句话钩子 + SVG + 对比表 + 「它不是」
  - `README.zh.md` → 中文门面 + 原接入文档
  - 新的叙事从「怎么接入 dsh」换成**「让 AI 写的代码自己证明它对」**
- **`.github/ISSUE_TEMPLATE/`** + **`pull_request_template.md`**：
  bug 模板要求先跑 `demo` 复现；feature 模板强制回答「这加强哪把锁」「怎么验证」。
- CI 增加 `demo` 冒烟（门面坏了等于门面塌了）。

### 修复

- `verify._fmt`：变异逃逸时曾显示「用例全绿但未做变异检查（加 --mutate）」——
  明明做了检查。改成「**自证不成立：N/M 变异逃逸 —— 用例绿了，但它挡不住做错**」。
  这个 bug 是写 demo 第 5 步时暴露的。

### 实测

```
$ python -m dsh_agent_kit.demo --fast
[4/5] 发票统计 · 2/2 用例通过 · 变异 4/4 被抓 → 自证成立 ✓
[5/5] 弱断言示例 · 1/1 用例通过 · 变异 1/2 被抓 → 自证不成立：1/2 变异逃逸
```

12 项验收全绿（新增 demo 4 条）。`__version__` → `1.9.0`。

## 1.8.0 — 2026-11-26

宿主密钥库 + 一键直达。**又一处设计被实测打回**：迁移会被别的 dsh 进程覆盖。

### 新增

- **`host/keystore.go` + `host/secret_windows.go`**：宿主密钥库
  - Windows DPAPI（`CryptProtectData`）加密存 key，落到 `%LOCALAPPDATA%\dshapps\secret.bin`
  - `readRealKey()` 顺序改为：密钥库 > 环境变量 > 明文文件
  - 迁移是**行级替换**（只改 `refs.DEEPSEEK_API_KEY` 那一行），
    `records` 段一字不动 —— grant 的 payload 只有它的拥有者能解释，重写等于毁别人数据
  - UI 告警条：检测到明文 key 就提示 + 提供「迁移到密钥库」「恢复」两个动作
- **`host/keystore_test.go`**：8 条 Go 测试
- **`.github/workflows/ci.yml` 新增 go job**（windows-latest）：vet + test + GUI 构建
- **空状态一键直达（B1）**：三个示例按钮点了直接跑，不再只填输入框
- 前端加了 `error` / `unhandledrejection` 兜底 —— GUI 没有控制台，异常必须显示在界面上

### 实测把一个设计打回

第一版迁移写完就报成功。12 小时后再看，**文件里的真 key 又回来了**，
还多出两条 `deepseek-account-platform/*` records —— 是 dsh 自己运行中写的。

原因：`dsh-credentials-local` 持有 key 的**内存快照**，某些操作会把整个文件重写，
覆盖掉占位符。**只要还有别的 dsh 会话活着，改全局文件就不稳定。**

修法：写完立刻**复读自检**，600 ms 后发现又变回真 key 就自动回滚（恢复文件、删备份、
删密钥库）并报错「请先关掉其他 dsh 会话再试」。
`TestMigrateDetectsOverwriteAndRollsBack` 模拟抢占窗口，验证必须回滚。

### 顺带核实并清理

- 上一轮实验残留（含明文 key 的备份、密钥库）已删除；用户凭据完好，`dsh` 实测可用
- `emitNow()`：`_start` 在 RPC 主线程里直接 `Eval`，不走 `Dispatch` 队列

### 实测

```
点空状态「让 AI 自己写实现并自证」
→ _START（命令行里带 --patch ...dshapps-sandbox.patch.yml）
→ SESSION → THINKING → TOOL_CALL read ×2 → TOOL_RESULT（读到 AGENT_RULES.md）
```

`go test`：8/8。Python 全量：11 项全绿。

## 1.7.0 — 2026-11-26

宿主接上 broker，并**用实测否掉了自己的一半假设**。

### 新增

- **`host/broker.go`**：内嵌凭证 broker（Go 标准库 `httputil.ReverseProxy`，流式透传）。
  - 宿主自己读真 key（环境变量或 `~/.dsh/.credentials.yaml`），只放内存
  - 启动时写「只有占位符」的凭据文件 + patch，让 dsh 的 `credentials.path` 指过去、
    `baseURL` 指向 broker
  - 日志可见：`broker: POST /anthropic/v1/messages`

### 实测结论（重要，写进 ADR 第六节）

**broker 只挡网络出口，不挡文件读取。**

```
broker 就绪 127.0.0.1:56531 → broker: POST /anthropic/v1/messages → _end code=0
但：让 agent 读真实路径 → DEEPSEEK_API_KEY: sk-XXXX…（真 key）
```

`credentials.path` 只改变 dsh 服务从哪里取凭证，不改文件系统；agent 的 `read` 工具
绕过整个配置。**所以 D1 的运行时闭环只完成一半**，另一半（把 key 从文件移走）
需要 v0.2 的宿主密钥库，且必须由用户点确认（会影响其他 dsh 会话）。

### 修掉的 bug

| 现象 | 原因 | 修法 |
|---|---|---|
| `POST /anthropic/anthropic/v1/messages` 404 | `NewSingleHostReverseProxy` 的 Director 已拼过一次路径，我又拼了一次 | 只保留 header 注入 |
| 宿主体积 3.4 → 7.57 MB | 引入 `net/http/httputil` | 可接受，记在这里 |

### 实测数据

- 宿主编译产物 **7.57 MB**（含 broker）
- 一轮对话：`broker 就绪 → 2 次 POST → stdout EOF → _end code=0` ≈ 10 s

## 1.6.0 — 2026-11-26

宿主落地：**双击 exe → 列表 → 跑起来 → 事件上屏**，全链路跑通。3 个坑都是实测撞出来的。

### 新增

- **`host/`**：Go + WebView2 宿主（3.4 MB / 500–705 ms / 26.8 MB 内存）。
  - 扫 `~/apps/*/app.json` → 左栏列表（坏 app 会标出来，不静默跳过）
  - 一次任务：`spawn dsh --profile <app> --json -`，cwd = app 目录，
    `DSH_PERMISSION_MODE` 取自 `app.json`
  - NDJSON 逐行经 `window.__onEvent` 推给 UI；`tool_call` / `tool_result` / `final` / `_raw` / `_stderr` 全渲染
  - `-cred` / `-root` 可配；`DSHAPPS_DEBUG=1` 写日志到 `%TEMP%\dshapps.log`
- **`scaffold` 现在会建 dsh profile**：`~/.dsh/profiles/app-<slug>/`
  （bundle = base + headless，patch 里开凭证 + 关 web）。
  没有它 `dsh --profile app-xxx` 直接 `profile not found`，新 app 等于跑不起来。
  **已存在的 profile 不会被覆盖**（app 目录是生成物，profile 是用户资产）。

### 修掉的三个坑

| 坑 | 现象 | 修法 |
|---|---|---|
| GUI 子系统无控制台 | 工具进程挂死，60 s 卡在 thinking | `CREATE_NO_WINDOW`（`proc_windows.go`） |
| stdout 先 EOF、进程后退出 | `cmd.Wait()` 永不返回，UI 永远转圈 | **EOF 即收尾**：最多等 1.5 s 拿退出码，然后立刻 `_end` |
| taskkill 阻塞收尾 | `_end` 发不出去 | 先 emit，再异步清理 |

### 另一个 bug

- `scaffold.slugify` 对中文名生成 `app-<hash>`，`profile` 又加了一次 `app-` 前缀，
  得到 `app-app-b99cffcf`。改为 slug 不带前缀，由 profile 加。

### 实测

```
dshapps.exe -root F:\...\_slice
→ 窗口 500 ms 出现，3.4 MB
→ 点「发送」→ _START / SESSION / THINKING / 正文流 / TOOL_CALL / TOOL_RESULT
→ 输出「发票统计 · 2/2 用例通过 · 变异 4/4 被抓 · 自证成立 ✓」
→ _end code=0（日志 00:14:06.892 stdout EOF → .901 已发 _end → .073 taskkill 完成）
```

`__version__` → `1.6.0`。

## 1.5.0 — 2026-11-26

权限补完轮：D4（自我提权）有了检测层，并且**只读清单被实测打回一次**。

### 新增

- **`python/dsh_agent_kit/guard.py`**：`ProfileGuard` —— profile 指纹 + 收窄只读 + 前后比对。
  - `fingerprint()`：对 5 个能力声明文件算 sha256，排除 `node_modules` / `*.bak-*` / `.rollback-*`
  - `diff()`：输出 `+ 新增` / `- 删除` / `~ 修改`
  - `set_readonly()`：只锁 `package.json` + `cordis.patch.yml`
  - `ProfileGuard`：上下文管理器，退出时给出 `tampered` 与 `changes`
- **`tests/test_guard.py`**：8 条，含**模拟攻击**（解锁 → 拆掉 lockdown → 恢复只读，必须被抓到）。

### 实测把设计打回一次

```
锁 package.json + cordis.yml + cordis.patch.yml
→ EPERM: open '.../profiles/headless/cordis.yml'     dsh 启动即崩
```

dsh 启动时会重写 `cordis.yml`，锁它等于锁死自己。**只读清单收窄到「用户层」**
（bundle 声明 + 覆盖层），`cordis.yml` 只做指纹检测。收窄后：

```
ProfileGuard(headless) 内跑一轮 dsh → exit 0 · final.text = "OK"
守卫结论 → profile 未改动（5 个声明文件）· lock 失败项 无
```

另一处实测修正：`.rollback-<ts>/cordis.patch.yml` 曾被当成能力声明算进指纹，
一有回滚就误报「profile 被改」—— 由 `test_fingerprint_ignores_noise` 抓到，加了目录前缀跳过。

### 边界（写进 ADR）

- 同用户进程能自己 `chmod +w` → 这层是**检测**，不是阻止；阻止要 L2。
- 默认只哈希声明文件，防不了 `node_modules` 投毒；需要时 `full=True`。

`__version__` → `1.5.0`。

## 1.4.0 — 2026-11-26

协议对齐轮：**发现并修正了一处协议级错误**，并让「挂起」第一次能被 5 秒抓住。

### 修正（重大）

**工具事件不是单一 `type: "tool"，而是 `tool_call` + `tool_result` 两条。**

实测抓包（`tests/fixtures/live_tool.jsonl`，13 条事件，`0.2.1-rc.2`）：

```json
{"type":"tool_call","callId":"call_00_...","tool":"pwsh","input":{"command":"Write-Output 'hi'"}}
{"type":"tool_result","callId":"call_00_...","status":"completed","result":"hi\r\n"}
```

原来的 `docs/02`、`spec/events.schema.json`、`tests/fake_dsh.py` 三方**一致地错**：
都按单一 `tool` 实现。后果：按 schema 写的 UI 永远不会命中 `type === "tool"`，
**工具调用整段静默消失**；而 `fake_dsh` 发的是假形状，测试全绿 —— 典型的
「用自己造的假数据自洽，与真实世界脱节」。

改动：

- `spec/events.schema.json`：新增 `tool_call`（required `type/callId/tool`）与
  `tool_result`（required `type/callId/status`）；旧 `tool` 标 `deprecated: true` 保留兼容；
  `oneOf` 与 examples 同步。
- `docs/02-protocol.md`：类型表拆成两行，补实测样例与踩坑说明。
- `tests/fake_dsh.py`：改发真实形状的 `tool_call` + `tool_result`，用 `callId` 配对。
- `tests/test_client.py::test_tool_event_passthrough`：改为断言两条事件与配对关系。

### 新增

- **`tests/test_protocol_live.py`**（6 条）：拿真实抓包做协议快照回归。断言
  「必须含 tool_call/tool_result 且 callId 配对」「**不许出现 `type: "tool"`**」
  「docs/02 不得再声称单一 tool」「schema 的两个定义与 required 仍在」。
  这条闸门专门防「文档-schema-实现」三方再次一起漂移。
- **`tests/fixtures/live_tool.jsonl`**：2026-11-26 从真 dsh 抓的 13 条完整事件流。
- **`idle_timeout`**（A8）：连续 N 秒没有任何 stdout 即判「挂起」，抛
  `DshError(kind="idle")`，与「慢任务」区分开。构造参数与 `stream()` 均可传，默认 0 = 关闭。
- `KIND_IDLE` 导出（`__init__`）。

### 实测

```
# 真实挂起场景：dsh-tui profile + --json（此前 180 s 无输出）
DshClient(profile="dsh-tui", idle_timeout=5, timeout=120).ask(...)
→ kind=idle  elapsed=5.3s  message=空闲 5s 无任何 stdout（疑似挂起）
```

负样本从 16 条增到 18 条（新增 `tool_call 缺 callId`、`tool_result 缺 status`）。

`__version__` → `1.4.0`。

## 1.3.0 — 2026-11-25

v0.1 内核：自证 runner + 脚手架，**垂直切片首次跑通**（agent 自己 35 秒做到自证成立）。

### 新增

- **`python/dsh_agent_kit/verify.py`**：自证 runner。
  - `run_cases()`：逐条比对 `cases.json`，输出 `N/M 用例通过` + 每条差异
  - `run_mutations()`：往 `logic.py` 注入 11 类变异，要求每条用例集都能变红；
    列出**逃逸的变异**并提示"断言太弱"
  - `LocalCtx`：执行 AI 写的代码时的受限上下文 —— 只能读写 `data/ out/ state/`，
    越界抛 `CtxError`；不提供网络 / 子进程 / 环境变量
  - CLI：`python -m dsh_agent_kit.verify --app <dir> [--mutate] [--json]`
- **`python/dsh_agent_kit/scaffold.py`**：一条命令生成 app 四件套
  （`app.json` / `AGENT_RULES.md` / `logic.py` / `cases.json` / `verify.py` / `说明.md` / `state/ data/ out/ outbox/`）。
  **刻意生成"红"的项目**：stub 返回 `{"todo": True}`，一跑就红。
- **`docs/11-quickstart.md`**：五分钟路径，含"什么才算做完了"的四态表。
- **`tests/test_verify.py`**：10 条（脚手架产物、红/绿/变异、BOM、越界、弱断言逃逸）。

### 修复

- **BOM 兼容**：`verify` 读取 `logic.py` / `cases.json` 一律用 `utf-8-sig`。
  Windows 记事本与 `Set-Content -Encoding UTF8` 都会写 BOM，之前会让变异候选被全部跳过，
  报「找不到可用的变异点」（`tests/test_verify.py::test_bom_in_logic_and_cases` 覆盖）。
- **退出码语义**：用例红时返回 1（业务失败），不再误报 2（环境错误）。
- **`configs/broker.patch.yml`** 的 `baseURL` 改为 `!!js process.env.DSH_BROKER_URL ?? '...'`。
  之前写死 8787，broker 换端口就 `TRANSPORT: DeepSeek Messages transport failed`，极难排查（实测踩到）。
- **变异集扩充到 11 类**，加入"最后 return → None""第一个数字 +1""True→False""min→max"
  等通用变异 —— 简单实现（只有一行 `sum`）原本一个都命中不了。

### 实测：垂直切片

```
scaffold 生成 → python verify.py          → 0/2 用例通过（故意）
dsh 自己做（35 s，workspace-write + broker）
  → 自己读 AGENT_RULES.md / cases.json → 写 logic.py → 跑 verify --mutate
  → 自检「无残留 _mutant_*」「cases.json 未被改动」
独立复核：2/2 用例通过 · 变异 5/5 被抓 · 自证成立 ✓
```

`__version__` → `1.3.0`，新增导出 `Broker` / `PLACEHOLDER_KEY`。

## 1.2.0 — 2026-11-25

权限轮：把 D1 从"待办"变成"已验证的缓解"，并把结论写成 ADR。

### 新增

- **`python/dsh_agent_kit/broker.py`**：凭证 broker（本地 LLM 代理，零依赖）。
  key 只在宿主进程内存，dsh 通过 `baseURL` 指向它并持有占位符；转发时注入真 key。
  支持 SSE 逐块透传与 usage 抽取，`stop()` 主动抹掉内存引用。
- **`configs/broker.patch.yml`**：把 `llm-deepseek` 指向 broker（`baseURL` + `apiKeyEnv`）。
- **`configs/lockdown.patch.yml`**：关掉 `tool-web` / `web-search-deepseek` / `web-fetch-http`。
- **`docs/10-permission-adr.md`**：权限模型 ADR —— L1/L2/L3 分层、实测证据、诚实边界。
- **`tests/test_broker.py`**：5 条断言（真 key 注入、流式透传、stop 抹除、非法输入拒绝）。

### 实测结论（写进 ADR）

- **dsh 的沙箱是写边界，不是读边界。** `DSH_PERMISSION_MODE=workspace-write` 下，
  agent 仍算出了 `~/.dsh/.credentials.yaml` 的完整 SHA256。
- **agent 会主动扩查**：目标文件不在时，它立刻列目录、发现 `.moved` 残件并推断迁移。
  → 改名/换目录不构成防护。
- **broker 可行**：把凭证文件移走后，dsh 通过 broker 仍完整跑完 3 个 step 并返回正确结果，
  同时 agent 的工具结果是 `NOT FOUND`。
- **`--dump-config` 不足以验证 lockdown**：应用后 `tool-web` 仍显示 `fetch: true`，
  但行为上 `web_search` 已 `TOOL_MISSING`。

### 变更

- `docs/09-issues.md`：D1 标为「部分缓解（L1）」，D3 标为「v0.1 已处理（关插件）」，
  并补 L2/L3 的路线。

## 1.1.0 — 2026-11-25

闭环轮：把「AI 自证」从口号变成可验证的闸门。新增 5 个检查、修 1 个真 bug。

### 新增

- `tests/test_conformance_negative.py`：16 条负样本，验证 conformance 的每条不变量**都能被违反**
  （闸门的闸门）。首跑即抓到真 bug，见下。
- `tests/mutate.py`：变异测试。注入 6 个错误，要求测试全部变红；任一逃逸即 exit 1。
- `tests/run_examples.py`：7 个示例的冒烟（`fake_dsh`，零 token）。Node 示例首次纳入 CI。
- `tests/check_docs_drift.py`：文档里的「≈N 包」与 `--dump-config` 实测比对，容差 3。
- `.github/workflows/live.yml`：真 dsh 冒烟（每日 + 手动），含凭证条目结构自检；
  没有 `DEEPSEEK_API_KEY` secret 时优雅跳过。
- `configs/cred.patch.yml`：打开 `headless` bundle 默认关闭的 `credentials` / `authorization`。
- `DSH_KIT_LAUNCHER` 环境变量：Python 与 Node 客户端行为一致，用 JSON 数组整段替换启动前缀
  （CI 接 fake dsh、无 dsh 机器上做演示）。
- `docs/09-issues.md`：76 条问题清单（P0 12 / P1 36 / P2 24 / P3 4），到商业标准为止的权威 backlog。

### 修复

- `tests/conformance.py`：`schema_check` 遇到非 dict 事件抛 `AttributeError` 而非报错
  （负样本「混入非对象行」抓到）。闸门在坏输入上崩溃，等于闸门失效。
- `docs/03-profiles.md`：补「headless 默认没有凭证」（实测 `MISSING_CREDENTIAL` → `final.text` 空串）；
  补「`dsh-tui` + `--json -` 会挂起」警告；装包数 97/124 → ≈98/≈126。

### 实测数据

- `headless` + `cred.patch.yml`：单轮 6.6 s，`inputTokens=3722` / `outputTokens=2`。
- `--dump-config` 条目数：`headless` 98 / `dsh-tui` 126（`0.2.1-rc.2`）。
- 全量验收 7 项：pytest 39 通过 · conformance `ALL PASS` · 负样本 16/16 · 变异 6/6 ·
  示例 6/6 · 文档漂移 2/2 · Node 10/10。

## 1.0.0 — 2026-11-01

首个公开版本：把「接入 DSH agent 的方法」固化成可交付物。

### 新增

- **协议规范** `spec/events.schema.json`：事件流的机器可读定义（draft 2020-12），
  含 `session / status / thinking / text / tool / final` 与本地合成的 `_raw / error`。
- **Python 客户端** `python/dsh_agent_kit/client.py`：零依赖，`stream()` / `ask()` / 会话续接 /
  自己实现的 deadline / stderr 分流 / 子进程环境摘除 `DSH_*` 会话变量。
- **Python SSE 网关** `python/dsh_agent_kit/gateway.py`：stdlib `ThreadingHTTPServer`，
  手写 chunked 帧、CORS、cwd 白名单、token 门禁、并发信号量、内置 demo 页、可选 jsonl 审计。
- **Node 客户端与网关** `node/dsh-client.mjs`、`node/sse-gateway.mjs`：行为与 Python 一一对应。
- **离线回归** `tests/fake_dsh.py`：行为级 dsh 替身（严格解析 argv 顺序、真实事件序、
  故障注入），因此 CI 不需要装 dsh、不消耗 token。
- **一致性测试** `tests/conformance.py`：三条不变量 + schema 校验，`--live` 走真实 dsh
  并验证会话记忆。
- **文档** `docs/01…08`：三层选型、协议生命周期、profile 与 `--patch` 位置陷阱、
  MCP 补法与 token 成本、SSE 服务端要点、排障矩阵、FAQ、应用侧契约（三件套 + 规则手册）。
- **示例** `examples/`：hello / stream / multiturn / batch / 网关 / Node / 浏览器端。
- **配置模板** `configs/mcp.patch.yml`、`configs/profile-template/`。

### 实测结论（写入文档，作为设计依据）

- `dsh --json` 事件流首条为 `session`，末条为 `final`，用量在 `status.step_end.usage`；
- `--patch` 属启动器 flag，写在 app 参数之后会 `unknown option`；
- `headless` 缺 MCP（需 `--patch`）、缺 compaction 与 plan/goal/todo，其余能力可用；
- 只开一个 `screen-use` MCP 会让单轮 `inputTokens` 从 169 涨到约 1.8 万；
- `disabled` 常是 JS 表达式，因此「某能力是否启用」只能行为验证，不能静态比对。
