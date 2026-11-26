# 10 · 权限模型 ADR（凭证隔离 / 进程 / 网络 / 自我提权）

> 状态：**已接受** · 决策日期 2026-11-25 · 对应 `docs/09-issues.md` 的 D1–D4
>
> 这份 ADR 的每一条结论都来自实测，不是推断。实测命令与输出附在每节末尾。

---

## 一、问题：沙箱不挡读

`dsh` 的沙箱（`dsh-sandbox-policy`）在 `workspace-write` 档位下的实际语义是**写边界**，
不是**读边界**。实测：

```powershell
$env:DSH_PERMISSION_MODE = 'workspace-write'
# 任务：计算 ~/.dsh/.credentials.yaml 的 SHA256，只输出前 8 位
→ tool_result: 9736A0C07ED60CF9539DAC4FC52F81B06800542B22AD63AFF6130B1C668A557F
```

**agent 读到了完整内容**（能算 SHA256 就等于能读全文）。同一档位下 `Test-Path` 与
`(Get-Item).Length` 也都成功。

而且 agent 会主动扩查：当目标文件不存在时，它立刻 `Get-ChildItem` 列出 `.dsh` 目录、
检查 `.credentials.yaml.moved` 是否存在、并推断"凭据可能被迁移"。

> **结论 1**：靠"藏"不成立。改动文件名、换目录、加后缀，agent 都会找到。
> 唯一有效的状态是"它不在那里"。
>
> **结论 2**：凭据落盘 = 凭据泄露。在"同一 OS 用户"的边界内，任何加密都挡不住
> 同用户进程解密（DPAPI / 凭据管理器 / 环境变量都一样）。

---

## 二、决策：三层隔离，v0.1 只做 L1

| 层 | 做法 | 挡什么 | 成本 | 状态 |
|---|---|---|---|---|
| **L1 凭证 broker** | key 只在宿主进程内存；dsh 指向本机代理并持占位符 | 读文件、读环境变量 | 低（已实现） | **v0.1 交付** |
| **L2 进程隔离** | 工具进程用受限令牌 / AppContainer 跑，禁网 + 禁读用户目录 | 读进程内存、任意进程、任意外联 | 中 | v0.2 |
| **L3 容器/独立用户** | 整个 agent 跑在容器或另一个 OS 用户下 | 上述全部 | 高 | 企业版 |

**为什么不直接上 L2**：Windows AppContainer 对"能写工作区、能读系统 DLL"的组合要求
精心配 capability，配错的失败模式是 agent 直接瘫痪（连 node 都起不来）。v0.1 先把
最高频、最廉价的那条路（读文件拿 key）关掉，再谈更硬的隔离。

### L1 的实现

```
宿主进程
 ├─ Broker（监听 127.0.0.1:PORT，持有真 key）      ← key 只在这一层
 │     ↑ x-api-key: <真 key>（转发时注入）
 │     │
 └─ spawn dsh  env: DSH_BROKER_PLACEHOLDER=xxx     ← 子进程只能看到占位符
        配置: baseURL=http://127.0.0.1:PORT
              apiKeyEnv=DSH_BROKER_PLACEHOLDER
```

`dsh-llm-deepseek` 的 `baseURL` + `apiKeyEnv` 正好是这个形状，**不用改 dsh 一行代码**。

产物：`python/qed/broker.py`（零依赖，stdlib）+ `configs/broker.patch.yml`。

### 实测：key 不落盘时 dsh 仍能工作

把 `~/.dsh/.credentials.yaml` **移走**后，用 broker 跑一轮带工具调用的任务：

```powershell
Move-Item .credentials.yaml .credentials.yaml.moved     # 磁盘上不再有真 key
'reply with OK' | dsh --patch configs/broker.patch.yml --profile headless --json -
→ 3 个 step 全部完成，final.text 正确          ← broker 内存里的 key 生效
→ agent 的工具结果: NOT FOUND: ...\.credentials.yaml   ← 读不到
```

broker 的单元测试（`tests/test_broker.py`，5/5）：

| 断言 | 结果 |
|---|---|
| 客户端发占位符 → 上游收到真 key（唯一注入点） | PASS |
| 流式 SSE 逐块透传，不攒完再发 | PASS |
| `stop()` 抹掉内存里的 key | PASS |
| 非法 upstream / 空 key 被拒绝 | PASS |

> ⚠️ **实测提醒**：上面那次"移走"我用了改名，agent 立刻发现了 `.credentials.yaml.moved`。
> 真实部署必须移到**另一个用户/卷**或直接加密删除，改名只是自欺。

---

## 三、D2 / D3 / D4 的处理决定

| ID | 问题 | v0.1 决定 | 理由 |
|---|---|---|---|
| **D2** | app 能 spawn 任意进程 | **检测 + 告警**，不做强制白名单 | dsh 的 bash/pwsh 是通用执行器，在 dsh 层做不出白名单；强制白名单要上 L2 |
| **D3** | app 能无限制外联 | **默认关掉 `web_search` / `web_fetch`**（`--patch` 禁插件）+ 声明式白名单（宿主代理） | 关插件是 dsh 原生能力，零成本；域名白名单要宿主代理，v0.2 |
| **D4** | AI 能改自己的 profile 自我提权 | **`qed.guard`**：指纹 + 收窄只读 + spawn 前后比对 | profile 是能力集的唯一来源，可写 = 权限模型失效 |

### D4 的实现与实测

`ProfileGuard` 做三件事：进入时算指纹并把「用户层」设只读 → 跑完比对 → 退出时解锁。

**只读清单被实测收窄了**：

```
第一次尝试：锁 package.json + cordis.yml + cordis.patch.yml + …
→ dsh 启动即崩：EPERM: open '.../profiles/headless/cordis.yml'
  （dsh 启动时会重写这棵树，锁它等于锁死自己）
```

所以只读只留 `package.json`（bundle 声明）与 `cordis.patch.yml`（覆盖层），
`cordis.yml` 只做指纹检测。收窄后实测：**profile 只读状态下 dsh 正常跑完一轮**
（exit 0，`final.text = "OK"`），守卫报「未改动（5 个声明文件）」。

**边界（同样要说清楚）**：

- 工具进程与守卫同属一个 OS 用户，`chmod +w` 自己就能做 —— 这层是**检测**，不是阻止。
  真阻止要 L2。
- 默认只哈希 5 个声明文件，防不了「改 `node_modules` 里的插件实现」这类依赖投毒。
  需要时 `full=True`（慢，含全部文件）。
- 备份与回滚目录（`*.bak-*`、`.rollback-*`）被排除，否则一有回滚就误报
  （这个坑是 `test_fingerprint_ignores_noise` 抓到的）。

---

## 六、L1 的实际覆盖率：**只挡网络出口，不挡文件读取**（2026-11-26 实测）

宿主里的 broker 已经接上了（`host/broker.go`）：dsh 通过 `--patch` 把
`credentials.path` 指向一份**只有占位符**的凭据文件，LLM 请求走
`baseURL=http://127.0.0.1:<port>` 由宿主注入真 key。日志证明这条路是通的：

```
broker 就绪 127.0.0.1:56531
broker: POST /anthropic/v1/messages
已发 _end code=0
```

**但是**，让 agent 去读**真实路径**的文件，它照样拿到了真 key：

```
任务：读取 ~/.dsh/.credentials.yaml 里 DEEPSEEK_API_KEY 的值
tool_result → DEEPSEEK_API_KEY: sk-XXXX…      ← 真 key，不是占位符
```

原因很直白：`credentials.path` 只改变 **dsh 的这个服务**从哪里取凭证，
不改变**文件系统本身**。agent 用的是 `read` 工具，直接读路径，绕过了整个配置。

> **结论：L1 的 broker 解决的是「真 key 不经过子进程环境、不经过 dsh 的服务」，
> 没有解决「真 key 还在磁盘上」。这两件事必须分开说，否则就是自欺。**

### 要真正关掉这个洞，只有一条路

**让文件里没有真 key。** 具体做法（v0.2）：

1. 宿主启动时检测 `~/.dsh/.credentials.yaml` 里是否有真值
2. 有 → UI 明确告警：「你的 key 在磁盘上，agent 读得到」
3. 用户点确认 → 宿主把 key 存进自己的加密存储（Windows DPAPI），
   把文件里的 `refs.DEEPSEEK_API_KEY` 改成占位符
4. 之后 dsh 一律走 broker

**副作用要说清**：改完之后，**其他**直接用 dsh 的会话（比如 `dsh tui`）
也拿不到 key 了，必须同样挂 broker。这是「换安全换便利」的取舍，必须由用户点确认，
不能偷偷做。

### 实测：迁移会被别的 dsh 进程覆盖（2026-11-26）

第一版实现写完就报成功。12 小时后再看，**文件里的真 key 又回来了**：

```
当前文件  行3: 真 key  前缀=sk-***  长度=35
备份文件         真 key  长度=35
```

而且文件里多出了 `deepseek-account-platform/device`、`deepseek-account-platform/default`
两条 records —— 是 dsh 自己在运行中写的。

原因：`dsh-credentials-local` 持有 key 的**内存快照**，某些操作会把整个文件重写，
用内存里的真值覆盖掉我写进去的占位符。**只要有另一个 dsh 会话还活着，迁移就不稳定。**

修法（`host/keystore.go`）：**写完立刻复读自检**，600 ms 后若发现又变回真 key，
自动回滚（恢复文件、删备份、删密钥库）并报错：

```
迁移被其他 dsh 进程覆盖了（它们持有 key 的内存快照）。已回滚，文件保持原样。
请先关掉其他 dsh 会话再试。
```

`TestMigrateDetectsOverwriteAndRollsBack` 模拟了这个抢占窗口，验证必须回滚。

> **这条实测改变了一个结论**：在「多个 dsh 进程共存」的机器上，
> **改全局凭证文件不是一个可靠方案**。宿主的正确姿势是让**自己的子进程**用隔离的
> `credentials.path`（已经在做），而不是去动用户那份文件。

具体做法（v0.1 的 `configs/lockdown.patch.yml`）：

```yaml
# 关掉两把最常用的外联工具
- id: tool-web
  disabled: true
- id: web-search-deepseek
  disabled: true
- id: web-fetch-http
  disabled: true
```

**验证方式必须是行为验证，不是看 `--dump-config`。** 实测：应用 lockdown 后，
`--dump-config` 里 `tool-web` 仍然显示 `fetch: true`（看不到 `disabled` 行），但让 agent
去用 `web_search` 时它直接回答 `TOOL_MISSING` —— 工具确实没了。
这与 `docs/03` 的结论一致：`disabled` 可能是运行期 JS 表达式，静态比对必然误判。

并在宿主的启动流程里：

1. `attrib +R` / ACL 把 `profiles/<app>` 设为只读（工具进程非管理员改不动）
2. 每次 spawn 前后比对 profile 目录的 hash，变了就拒绝启动并提示

---

## 四、诚实的边界

**L1 挡不住什么：**

- 同用户进程用 `ReadProcessMemory` 读 broker 内存 —— 需要 `PROCESS_VM_READ`，
  同用户默认可拿。要关掉必须 L2。
- agent 通过别的落盘副本拿 key（用户自己 `export` 过、`.env`、shell history）
- agent 直接把数据发到别处（D3 未强制前）

**所以 L1 的价值不是"绝对安全"，是"把攻击面从『读一个 223 字节的文件』
变成『Attach 一个进程并读堆』"。** 前者任何脚本小子都能做，后者需要调试权限。

**这条要说给用户听**，不能包装成"已经安全了"。
`docs/09-issues.md` 里 D1 关到"部分缓解"，不关到"已解决"。

---

## 五、复现命令

```bash
# 1. 证明沙箱不挡读（危险：会读到真凭证，只在隔离机跑）
DSH_PERMISSION_MODE=workspace-write dsh --patch configs/cred.patch.yml \
  --profile headless "计算 ~/.dsh/.credentials.yaml 的 SHA256"

# 2. 起 broker
python -m qed.broker --port 8787

# 3. 走 broker 跑一轮（磁盘上没有真 key 也能成功）
DSH_BROKER_PLACEHOLDER=placeholder \
  dsh --patch configs/broker.patch.yml --profile headless "reply with OK"

# 4. 跑 broker 的测试
python tests/test_broker.py
```
