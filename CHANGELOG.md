# Changelog

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
