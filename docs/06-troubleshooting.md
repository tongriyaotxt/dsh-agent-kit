# 06 · 排障：症状 → 诊断 → 修法

按「你看到的症状」查，不要按猜测查。

## 一、启动类

| 症状 | 诊断 | 修法 |
|---|---|---|
| `FileNotFoundError: 'dsh'` | Windows 上 npm 生成的是 `dsh.cmd`，`Popen` 不走 PATHEXT | `shutil.which("dsh")` 拿真实路径，再 `cmd /c` 包装（客户端已内置） |
| `unknown option '--patch'` | `--patch` 写在 app 参数之后 | 移到 `--json` 之前：`dsh --patch x.yml --profile headless --json -` |
| `profile not found` | 名字错 / `DSH_HOME` 不是你以为的那个 | `echo $DSH_HOME`；`ls $DSH_HOME/profiles` |
| 退出码 1，stderr 有 `disabling profile plugin row "xxx": Plugin xxx@1.0.0 is incompatible with dsh <ver>` | profile 的用户层里有一个**版本不兼容的插件**（常见于升级 dsh 之后），dsh 拒绝启动该 profile | 从模板新建干净 profile：`dsh <new> --from-default-profile headless`，用它跑；或升级/移除那个插件 |
| 启动 3 秒以上 | 冷启动要加载插件树 | 正常的量级；要把延迟压下去就精简 profile |

> 上面那条「插件不兼容」是**实测**到的失败模式：升级 dsh 后旧 profile 里的自定义插件可能卡住整个 profile，
> 而报错藏在 stderr 里。`python tests/conformance.py --live` 会把 stderr 尾部原样打出来，一眼可见。

```bash
# 三连诊断
dsh --version
echo $DSH_HOME && ls $DSH_HOME/profiles
dsh --profile headless --dump-config | head -30
```

## 二、输出类

| 症状 | 诊断 | 修法 |
|---|---|---|
| 任务里中文变 `???` / 引号丢失 | 任务拼进了命令行，被 shell 解析 | 任务走 **stdin**，argv 只留 `-` |
| `JSON.parse` 偶发失败 | stderr 混进了 stdout | 分开收；解析失败降级为 `_raw` 事件 |
| 拿到 `text` 但内容不全 | 把增量当成了完整答案 | 以 `final.text` 为准 |
| 事件一条都不来（挂住） | 没读到 EOF / 任务没写完 stdin | 写完任务**必须 close stdin** |
| 输出乱码（前端） | UTF-8 多字节被 chunk 切开 | `TextDecoder.decode(v, {stream:true})` |

```bash
# 最小可复现：确认是不是你的代码问题
printf '%s' "reply with OK" | dsh --profile headless --json - | head -3
```

## 三、流式类（前端一直转圈）

| 症状 | 原因 | 修法 |
|---|---|---|
| 首帧迟迟不到 | 没发 `Transfer-Encoding: chunked` | 手写 chunk 帧（见 [05](05-server-side.md)） |
| 一次性全到（不是流式） | 帧后没 `flush()` | 每帧 flush |
| 最后一帧等不到、连接不关 | 没写 `0\r\n\r\n` | 补结束帧 |
| 前面有 nginx 时卡住 | 代理缓冲 | `X-Accel-Buffering: no` |
| 跨端口 403/预检失败 | 缺 CORS/OPTIONS | 加 `Access-Control-Allow-*` + `OPTIONS` 分支 |

浏览器侧诊断：

```js
// 1) 看是不是真的流式
const t0 = performance.now();
const rd = r.body.getReader();
(await rd.read()); console.log("first chunk", performance.now() - t0, "ms");
// 若 ~0ms 且总量等于全部，说明后端没 flush
```

## 四、会话类

| 症状 | 原因 | 修法 |
|---|---|---|
| 多轮丢上下文 | 没保存 `sessionId` | 存 `session.sessionId`，下轮传 `--session-id` |
| `unknown id` 报错 | 会话被清理 / id 写错 | 丢弃旧 id 开新会话，不要重试同一个 id |
| 偶发串进别人的会话 | 子进程继承了 `DSH_SESSION_ID` | 启动前摘除 `DSH_SESSION_ID` / `DSH_PROFILE` / `DSH_PROFILE_DIR` |
| 并发两轮互相污染 | 同一个 `sessionId` 并发使用 | 会话是串行状态：一个会话同时只跑一轮 |

```python
# 会话健康的自检
t1 = c.ask("记住代号 ORION-7，只回 OK")
t2 = c.ask("代号是什么？")
assert "ORION-7" in t2.text, "会话没续上"
assert t1.session_id == t2.session_id
```

## 五、能力类

| 症状 | 原因 | 修法 |
|---|---|---|
| agent 说「没有 mcp 工具」 | headless 默认不带 MCP | `--patch configs/mcp.patch.yml`，见 [04](04-mcp.md) |
| 工具在但调用失败 | MCP server 起不来 | 手动执行 `command args`，看它 stderr |
| token 用量暴涨 | MCP 工具定义进上下文 | 只开必需 server；看 `step_end.usage.inputTokens` |
| 没有上下文压缩，长任务被截断 | headless 无 compaction | 应用侧分段：一个任务一件事，结果落文件 |
| 没有 plan/goal/todo | headless 无这些插件 | 应用侧自己编排多轮；不要指望 headless 有长任务状态机 |

## 六、超时与僵尸

| 症状 | 原因 | 修法 |
|---|---|---|
| 进程不退出 | 依赖 dsh 内部超时 | 客户端 `deadline` + `kill()` |
| kill 后残留子进程（Windows） | `kill()` 只杀直接子进程 | 用 job object 或 `taskkill /T /F /PID` |
| 队列越堆越多 | 没有并发上限 | 信号量限流；排队超时直接拒 |

```python
# Windows 上彻底清理进程树
subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
               capture_output=True)
```

## 七、成本

| 症状 | 原因 | 修法 |
|---|---|---|
| 每轮 `inputTokens` 上万 | 系统提示 + 工具定义 + profile 插件 | 精简 profile，关掉不用的 MCP |
| 同一任务反复贵 | 每次都开新会话，丢了 cache | 复用 `sessionId`（`cacheReadTokens` 会上升，单价更低） |
| 看不出钱花在哪 | 没接 usage | 把 `step_end.usage` 落库，按任务聚合 |

## 八、五步归零法

真卡住时按这个顺序，五分钟内定位：

```bash
# 1) dsh 本身能跑吗
printf '%s' "reply with OK" | dsh --profile headless --json - | tail -2

# 2) profile 对吗
dsh --profile headless --dump-config | head -5

# 3) 你的客户端能跑吗（不烧 token 的离线回归）
python tests/test_client.py

# 4) 协议还对吗（真跑一轮，验三条不变量）
python tests/conformance.py --live

# 5) 只有前端有问题？
python -m dsh_agent_kit.gateway --port 8777   # 打开 http://127.0.0.1:8777/
```

第 1、3 步之间就能把「dsh 坏了」和「你的接入层坏了」分开 —— 这是排查的全部关键。
