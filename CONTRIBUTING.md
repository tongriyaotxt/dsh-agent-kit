# 贡献指南

这个仓库的目标很窄：**让接入 DSH agent 的方法可交接**。所以贡献的东西必须能被下一个人直接跑起来。

## 一条硬规则

**每个断言都要先在 `tests/` 里离线复现。**

- 新的坑 → 先加一个 `tests/fake_dsh.py` 能复现的用例，再改代码；
- 只依赖真实 dsh 的行为，不进 CI（CI 不装 dsh、不烧 token）；
- 真实环境专用的验证放 `tests/conformance.py --live`，手动跑。

## 开发流程

```bash
# 1) 离线回归（秒级，零 token）
python tests/test_client.py
python tests/test_gateway.py
python tests/conformance.py
node --test tests/test_node.mjs

# 2) 真机一致性（会消耗 token）
python tests/conformance.py --live

# 3) 手验网关
python -m dsh_agent_kit.gateway --port 8777
# 打开 http://127.0.0.1:8777/
```

## 代码约定

| 项 | 约定 |
|---|---|
| Python | 3.9+，**只用标准库**（不许加依赖） |
| Node | 18+，**只用标准库** |
| 任务传参 | 永远走 stdin，argv 只留 `-` |
| 事件处理 | 未知 `type` 必须忽略（前向兼容） |
| 错误 | 携带 `stderr` 尾部 + 已收到的事件 |
| 文案 | 中文，写「症状 → 原因 → 修法」，不写空泛原则 |

## 加一项新能力时

1. 先判断属于哪一层（L1 进程 / L2 HTTP / L3 终端），写进 `docs/01-architecture.md` 的选型表；
2. 如果改协议消费方式，同步更新 `spec/events.schema.json` 与 `docs/02-protocol.md`；
3. 在 `README.md` 的踩坑清单里补一行（症状 / 原因 / 修法）；
4. 加测试。

## Commit 信息

```
feat(gateway): 支持 Last-Event-ID 断线重连
fix(client): 超时必须在 readline 阻塞时也生效
docs(protocol): 补 turn_end.reason 的未知值处理
test(node): 覆盖非 strict 模式的 error 事件
```

## 报告问题

请附：

- `dsh --version` 与操作系统；
- 最小复现（`printf '%s' "task" | dsh --profile headless --json -` 的前 5 行输出）；
- `python tests/conformance.py --live` 的结果；
- 你期望的行为 vs 实际行为。
