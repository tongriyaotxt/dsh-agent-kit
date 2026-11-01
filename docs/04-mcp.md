# 04 · MCP：给 headless 补工具

`headless` profile 不加载 MCP 客户端，所以 `mcp__<server>__<tool>` 系列工具在里面**不存在**。这是 headless 与 tui 之间唯一必须处理的差距。

## 一、补法：`--patch` 叠加一层

```bash
dsh --profile headless --patch configs/mcp.patch.yml --json -
```

补丁文件（`configs/mcp.patch.yml` 是可用的起点）：

```yaml
- insert:
    - id: mcp-screen-use
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: screen-use
        transport: stdio
        command: /path/to/python
        args: ['-m', 'screen_use.mcp_server']
```

字段含义：

| 字段 | 说明 |
|---|---|
| `id` | 覆盖层条目 id，同一 id 会覆盖前层的同名条目 |
| `name` | 固定 `@deepseek-ai/dsh-mcp-client` |
| `config.serverName` | 工具前缀：`mcp__<serverName>__<tool>` |
| `config.transport` | 目前 `stdio` |
| `config.command` / `args` | 启动 MCP server 的真实命令；**用绝对路径** |
| `config.env` | 可选，注入 server 的环境变量（如 API key） |

客户端侧：

```python
c = DshClient(cwd="/proj", patches=["configs/mcp.patch.yml"])
c.ask("截个图并描述界面")     # 现在能调到 mcp__screen-use__screenshot
```

`patches` 是**列表**，因为 `--patch` 可重复：可以拆成「基础补丁 + 项目补丁」分层叠加。

## 二、验证补丁生效

不看配置，看行为：

```bash
# 解析层：条目是否进了组合树
dsh --profile headless --patch configs/mcp.patch.yml --dump-config | grep -c 'dsh-mcp-client'

# 行为层：让 agent 数一下自己能用的 mcp 工具
printf '%s' '列出你可用的 mcp__ 前缀工具，只回数量' | dsh --profile headless --patch configs/mcp.patch.yml --json -
```

行为层判据：没补丁时 agent 会回答「没有这类工具」（实测 `NO_MCP`）；补丁生效后能列出具体工具名（实测 `HAS_MCP`，`screen-use` 一个 server 就带来 18 个 `mcp__screen-use__*`）。

## 三、成本：这是最容易忽略的一笔账

**MCP 的工具定义会全部进上下文，按 token 计费。**

实测：只开 `screen-use` 一个 MCP，单轮 `inputTokens` 从 **169 → 1.8 万**（×100）。

所以：

- 默认只开**当前任务真正需要**的那一个 server；
- 工具多的 server（CAD / CATIA 之类，几十个工具）不要放进常驻 profile；
- 需要多个能力时，宁可**按需切换补丁**，也不要一次全开；
- 每次接入都看一眼 `status.step_end.usage.inputTokens`，异常暴涨先怀疑 MCP。

```python
t = c.ask("...")
print(t.usage)   # {"inputTokens": 18000, ...}  ← 数一下就知道谁在吃 token
```

## 四、常驻化

把 `insert` 段复制进 `~/.dsh/profiles/headless/cordis.patch.yml`，之后不需要每次传 `--patch`：

```bash
# ~/.dsh/profiles/headless/cordis.patch.yml
- insert:
    - id: mcp-screen-use
      ...
```

确认：

```bash
dsh --profile headless --dump-config | grep -c dsh-mcp-client
```

更干净的做法是**新建专用 profile**（见 [03-profiles.md](03-profiles.md)），把 MCP 装在那里，headless 保持轻量。

## 五、自己写一个 MCP server

任何说 stdio + JSON-RPC 的 server 都能挂。最小骨架（Python，零依赖，stdio）：

```python
import json, sys

def reply(id_, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:                      # 一行一个 JSON-RPC
    msg = json.loads(line)
    method, id_ = msg.get("method"), msg.get("id")
    if method == "initialize":
        reply(id_, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "demo", "version": "0.1.0"}})
    elif method == "tools/list":
        reply(id_, {"tools": [{"name": "ping", "description": "回 pong",
                               "inputSchema": {"type": "object", "properties": {}}}]})
    elif method == "tools/call":
        reply(id_, {"content": [{"type": "text", "text": "pong"}]})
    elif id_ is not None:
        reply(id_, {})
```

挂上：

```yaml
- insert:
    - id: mcp-demo
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: demo
        transport: stdio
        command: python
        args: ['/abs/path/demo_server.py']
```

要点：

- **stdout 只能是 JSON-RPC**，日志写 stderr（否则协议被打断）；
- `serverName` 决定工具前缀，起名要短（会进 token）；
- 每个工具的 `description` + `inputSchema` 都进上下文 —— 描述写得越长，成本越高；
- server 崩了不会拖垮 dsh，但该批工具会消失，调用会失败。

## 六、MCP 错误谱

| 现象 | 原因 | 修法 |
|---|---|---|
| agent 说没有 mcp 工具 | 没加 `--patch` / 补丁没进层 | `--dump-config \| grep dsh-mcp-client` |
| 工具存在但调用报错 | `command` 路径错 / server 起不来 | 手动执行 `command args` 看 stderr |
| 启动变慢、token 暴涨 | 工具定义太多 | 只留必需 server |
| 换机器失效 | 补丁里写死了本机路径 | 用占位符 + 部署时替换 |
| 补丁加了但顺序错 | `--patch` 在 app 参数后 | 移到 `--json` 前 |
