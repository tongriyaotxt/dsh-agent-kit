# 08 · 应用侧契约：给 agent 三件东西

传输层（前七篇）解决「怎么把话送到」。这一篇解决「送到之后它凭什么能把事办对」。

结论先说：**不要为每个 app 写一套专用工具协议。** 只要满足下面三条，任何应用都能被 agent 驱动 —— 因为 agent 自带 `fs` 和 `bash`，文件就是最通用的 API。

## 一、三件套

| 你要提供的 | 形态 | 作用 |
|---|---|---|
| **状态可序列化** | `state/<app>.json` | agent 读得懂当前世界 |
| **文档出口** | `docs/*.md` | agent 的产出有地方落 |
| **任务入口** | 一个 textarea + 一次 POST | 人能把意图说清楚 |

外加一份**规则手册** `AGENT_RULES.md`（放在项目根或 `bridge/` 下），告诉 agent 在这个项目里怎么干活。

```
your-app/
├── AGENT_RULES.md        ← 规则手册（给 agent 读）
├── state/app.json        ← 状态（agent 读写）
├── docs/                 ← 产出（agent 写，界面列出来）
└── bridge/               ← 网关（spawn dsh，喂任务）
```

## 二、为什么文件通道比 RPC 好

| | 文件通道 | 专用 RPC/工具协议 |
|---|---|---|
| 实现成本 | 一个 `toJSON()` + 一个目录 | 每个能力一个接口 + schema + 版本 |
| 可调试 | `cat` 就能看 | 要抓包 |
| 可回滚 | 有历史文件 | 要自己实现 |
| agent 兼容性 | 任何模型都会读写文件 | 要教它新协议 |
| 人机协同 | 人改文件 = agent 看见 | 要双向同步 |

代价：**没有事务**。所以规则要写死「读—改—写回」，并且写回前先备份（见第六节）。

## 三、双向感知：轮询状态文件

不需要 WebSocket。前端每 2 秒看一次 `state/app.json` 的 `updated` 字段：

```js
let seen = 0;
setInterval(async () => {
  const s = await (await fetch("/state?name=app")).json();
  if (s.updated !== seen) { seen = s.updated; render(s); }
}, 2000);
```

于是形成闭环：

- **人**在界面上改 → 写回文件 → agent 下次读得到；
- **agent** 改文件 → `updated` 变化 → 界面自动刷新 → 人立刻看见。

这就是全部协议。不需要 SDK、不需要双向 RPC、不需要为每个 app 定制工具。

## 四、规则手册模板

`AGENT_RULES.md` 必须回答五个问题，缺一个 agent 就会跑偏：

```markdown
# 给 agent 的操作手册

你在这个项目里的唯一职责：<一句话>。不要 <越界事项>。

## 一、工作流（三条命令）
1. `python tools/ctl.py status`      有没有新请求
2. `python tools/ctl.py context`     读上下文（含硬约束）
3. `python tools/ctl.py write ...`   写回结果

## 二、上下文在哪
| 文件 | 内容 |
|---|---|
| state/app.json | 完整状态（优先读 .md 摘要） |
| docs/ | 已有产出，别重复写 |

## 三、输出格式（严格 JSON）
```json
{"ops":[...], "gaps":{"certain":[], "uncertain":[], "need_from_you":[]}}
```

## 四、硬规则（违反会被拒）
1. ...
2. ...

## 五、禁止事项
- ❌ 不要改 state/（那是人的输入）
- ❌ 不要输出完整方案，只出可裁决的最小改动
```

写规则手册的三条经验：

1. **职责要窄**。「你只负责 X」比「你是一个全能助手」有效十倍。
2. **校验要能被违反**。规则 4「`rel=replaces` 必须带 `note`」这种可机械校验的写法，比「请注意说明原因」有用。
3. **给出反例**。附一个故意不合规的示例文件，规则才有边界感。

## 五、结构化输出：标记块

agent 的输出混着解释文字，应用侧要能可靠地抽出结构化部分。用**成对标记**最简单：

```
@@PATCH
{ "ops": [...], "gaps": {...} }
@@END
```

应用侧正则抽取：

```python
import json, re
m = re.search(r"@@PATCH\s*(\{.*?\})\s*@@END", text, re.S)
payload = json.loads(m.group(1)) if m else None
```

规则手册里明确要求「最后打印 `@@PATCH <json> @@END`」，比让 agent「只输出 JSON」可靠得多 —— 前者允许它先思考再给结论，后者经常被它自己破坏。

## 六、写回的四个工程约束

1. **先备份**：写 `state/app.json` 前 `cp` 到 `state/.bak/<ts>.json`；
2. **校验后写**：先跑 schema/枚举校验，不合格整体拒绝，不要「部分写入」除非显式 `--partial`；
3. **带基线**：payload 里带 `base`（读到的版本号），写回时对不上就拒绝 —— 防「人改了、agent 用旧状态覆盖」；
4. **可重放**：每次写回追加一行到 `state/audit.log`（时间、task、session_id、diff 摘要）。

## 七、任务模板（应用侧拼 prompt）

任务文本建议三段式，别只说一句自然语言：

```
读取 state/app.json 与 AGENT_RULES.md。
按规则为 <目标> 生成 ops，写入 state/inbox.json。
最后打印 @@PATCH <json> @@END。
```

- **读什么**（明确文件路径，别让它猜）
- **按什么规则**（指到规则手册）
- **写哪里 + 怎么回**（输出契约）

单行即可，`shell=True` 会把换行当命令分隔符（我们用 stdin 传参规避了这点，但任务文本本身保持单行更省心）。

## 八、验收清单

- [ ] `state/*.json` 有 `updated` 字段，界面靠它轮询
- [ ] `AGENT_RULES.md` 有「职责 / 工作流 / 格式 / 硬规则 / 禁止」五节
- [ ] 输出用成对标记包裹，应用侧有容错抽取（抽不到不等于任务失败）
- [ ] 写回前备份 + 校验 + 基线比对
- [ ] 每条改动都能被一句话裁决（「这假设成立吗？成立我损失多少？」）
- [ ] agent 说「做不到」时，界面上有明确提示，而不是静默失败
