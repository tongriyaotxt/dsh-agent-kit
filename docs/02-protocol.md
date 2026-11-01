# 02 · 事件协议

`--json` 会把一次运行序列化成 **NDJSON**（newline-delimited JSON）：stdout 每行一个完整 JSON 对象，不合并、不缩进、不包裹数组。

机器可读定义：[`../spec/events.schema.json`](../spec/events.schema.json)。

## 一、完整事件类型

| `type` | 必含字段 | 出现次数 | 说明 |
|---|---|---|---|
| `session` | `sessionId`, `cwd` | 1（首行） | 会话建立。**第一个要处理的事件** |
| `status` | `phase` | 多次 | 生命周期与用量 |
| `thinking` | `text` | 0..n | 思考增量 |
| `text` | `text` | 0..n | 正文增量 |
| `tool` | 版本相关 | 0..n | 工具调用，字段按原样透传 |
| `final` | `text` | 1（正常结束） | 最终答案 |

客户端额外可能产出两种**本地合成**事件（不来自 dsh，用于错误处理）：

| `type` | 字段 | 何时 |
|---|---|---|
| `_raw` | `text` | stdout 出现了非 JSON 行（极少；降级保留原文） |
| `error` | `message` | 超时 / 非 0 退出（`strict=False` 时以事件形式给出） |

## 二、`status.phase` 状态机

```
turn_start ──► step_start ──► step_end ──► … ──► turn_end
                   ▲            │
                   └────────────┘   (一个 turn 内多个 step)
```

| `phase` | 附带字段 | 含义 |
|---|---|---|
| `turn_start` | `turn` | 一轮对话开始 |
| `step_start` | `turn`, `step` | 一次模型调用开始 |
| `step_end` | `turn`, `step`, `usage` | 一次模型调用结束，**用量在这里** |
| `turn_end` | `turn`, `reason` | 一轮结束，`reason.kind` 给终止原因 |

### usage 字段

```json
{"inputTokens":15968,"outputTokens":4,"cacheReadTokens":384,"totalTokens":16356}
```

`step_end.usage` 是**单次 step** 的用量；多 step 的 turn 要自己累加。接到成本面板时以 `inputTokens + outputTokens` 计，`cacheReadTokens` 单独统计（单价不同）。

### reason.kind

常见值 `completed`。消费方必须容忍未知值：当成「结束了但没有正常完成」处理，并以退出码为准。

## 三、实测事件序列

一次最简单的运行（`printf 'reply with OK' | dsh --profile headless --json -`）：

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

带工具调用的运行会在 `step_start` 与 `step_end` 之间插入 `tool` 与更多 `text` 增量；`thinking` 可能为空串（关闭思考时），也会出现多次。

## 四、三条不变量（回归测试就在验这三条）

1. **首行是 `session`**，`sessionId` 非空字符串；
2. **正常结束有 `final`**，且 `status(turn_end).reason` 存在；
3. **stdout 只有 JSONL**，诊断只在 stderr；退出码 ≠ 0 即失败。

任何一条被破坏，都是协议级破坏（而非应用 bug），应当被视为 dsh 版本不兼容处理。

## 五、消费模式

### 逐行解析（不要等 EOF）

```python
for line in proc.stdout:            # 阻塞逐行，天然流式
    line = line.decode("utf-8", "replace").strip()
    if not line: continue
    try: ev = json.loads(line)
    except json.JSONDecodeError: ev = {"type": "_raw", "text": line}
```

### 增量 vs 最终

- `text` 事件是**增量片段**，拼接即可得到正文；
- `final.text` 是**最终答案**，可能包含工具执行后的改写；
- **展示用 `text`，落库用 `final`**。两者不一致时以 `final` 为准。

### 并发

不同进程之间完全独立。要并行就起多个进程，各自的 `sessionId` 互不相干。**不要**用同一个 `sessionId` 并发两轮 —— 会话是串行状态。

## 六、版本演进

dsh 目前是 `0.2.1-rc.2`。协议策略按「向后兼容优先」对待：

| 变化 | 消费方应如何应对 |
|---|---|
| 新增 `type` | **忽略未知类型**（默认分支不做事） |
| 新增字段 | 忽略未知字段（schema 里 `additionalProperties: true`） |
| 字段改名/删除 | 视为主版本变更，跟进 schema 更新 |
| `tool` 事件字段变动 | 按原样透传，不做结构假设 |

自查命令：

```bash
dsh --version
python tests/conformance.py --live    # 用真实 dsh 校验不变量
```

## 七、退出码与错误

| 情况 | 表现 | 处理 |
|---|---|---|
| 正常 | exit 0 + `final` | 取 `final.text` |
| 未知 `--session-id` | exit ≠ 0，stderr 报错 | 丢弃旧 id 开新会话 |
| 模型/网络错误 | exit ≠ 0，stderr 有诊断 | 透传 stderr 尾部给用户 |
| 你的超时 | 客户端 `kill()` | 记 `error: timeout`，不要当业务失败重试无限次 |
| profile 不存在 | exit ≠ 0，启动即失败 | 检查 `dsh --profile <name> --dump-config` |

stderr 可能很长（插件加载日志）。截尾 500–800 字符足够定位。
