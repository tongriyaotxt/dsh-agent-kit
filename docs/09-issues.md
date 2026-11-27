# 09 · 问题清单（到商业标准为止）

> 归档日期：2026-11-25 · 基线：dsh `0.2.1-rc.2` · 本文件是迭代的唯一权威 backlog。
>
> **优先级定义**
> - **P0** 阻断商用：真实用户第一次用就会撞，且没有兜底
> - **P1** 首批用户会撞：影响体验或信任，有绕路但用户不知道绕
> - **P2** 规模化后暴露：多 app / 多用户 / 长时间运行才出现
> - **P3** 可选优化
>
> **判据必须可执行**：每条都要能回答"改完后怎么验它好了"。答不出来的条目不算条目。

---

## A. 协议与运行时（12）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| A1 | `headless` bundle 把 `credentials` / `authorization` 打成 `disabled: true`，直接跑 `MISSING_CREDENTIAL`，`final.text` 为空串 | 接入方第一小时就卡死，且报错藏在 `turn_end.reason` 里，像"模型没反应" | `configs/cred.patch.yml` 存在；`docs/03` 有专节；`conformance --live` 默认带它 | **P0** |
| A2 | `dsh-tui` profile 跑 `--json -` 挂起（实测 180 s 无输出） | 照文档"要 plan/goal 用 tui"的人会挂死 | `docs/03` 显式警告；客户端对"无输出超时"单独报错（见 A8） | **P0** |
| A3 | 事件流没有版本号，只有 `dsh --version` | dsh 升级改了字段，消费方只能靠"跑起来怪怪的"发现 | schema 增加 `schemaVersion` 约束或 `docs/02` 增加"版本↔协议"对照表 | P1 |
| A4 | `tool` 事件"字段按原样透传"，无稳定结构 | UI 无法稳定渲染工具调用，只能 print | 抓 20 条真实 `tool` 事件，归纳出最小公共字段，写进 schema（`additionalProperties: true`） | P1 |
| A5 | Windows 上 spawn 子进程未显式 `encoding='utf-8'`，中文路径/内容可能按 GBK 解 | 中文用户第一条路径就炸 | `client.py` 显式 `encoding="utf-8", errors="replace"`；`test_client.py` 加中文 cwd 用例 | P1 |
| A6 | 逐行读取的缓冲上限 4 MB，agent 输出大 JSON 会被截断 | 大数据任务静默丢内容 | 上限提到 32 MB + 超限时产出 `_raw` 而非截断；加测试 | P1 |
| A7 | 无背压：事件涌入时消费者跟不上，内存堆积 | 长任务 UI 卡死 | 客户端提供 `max_buffer` 与丢弃策略；压测 10 万事件不 OOM | P2 |
| A8 | 只有"总超时"，没有"无输出超时" | 挂起（A2）无法与"慢任务"区分 | 增加 `idle_timeout`：连续 N 秒无任何 stdout 即判挂起 | P1 |
| A9 | 未知 `--session-id` 退出码非 0，但客户端没有"自动降级开新会话" | 用户重开 app 就失败一次 | 客户端遇 `SESSION_NOT_FOUND` 类错误自动重试一次新会话，并记录 `_retry` 事件 | P1 |
| A10 | `thinking` 可能是空串，且关闭思考时整段缺失 | UI 需要判空，易出空白块 | 客户端把空 `thinking` 折叠为一个 `thinking_done` 标记；文档说明 | P2 |
| A11 | `_raw` 降级后事件语义丢失，无法参与统计 | 成本/进度面板缺数 | `_raw` 事件带 `rawIndex` 与 `offset`，可在审计里回放 | P2 |
| A12 | stderr 截尾 500–800 字符，插件加载日志常超 | 排查时看不到真正的错误 | 全文落盘 `~/.dsh/logs/<session>.err`，只在 UI 显示截尾 | P2 |

---

## B. 真实使用场景（16）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| B1 | 第一次打开是空白，用户不知道能干什么 | 首屏流失 | 空状态给 3 个可点的真实任务（不是文案，点了就出结果） | **P0** |
| B2 | 用户说"做个发票统计"，AI 立刻写 HTML/界面 | 界面带着逻辑跑，逻辑永远不稳；返工 | `AGENT_RULES.md` 强制"先 `cases.json` 后 `view`"；宿主在 `cases.json` 不存在时**拒绝**渲染窗口 | **P0** |
| B3 | spawn 后 6.6 s 内界面无任何反馈 | 用户以为卡死，重复点击 | 收到 `session` 事件即显示"已连接 · 第 1 步"；首字到达 < 8 s | P1 |
| B4 | 用户中途改主意，半成品目录留着 | 磁盘脏，下次扫到坏 app | `~/apps/.trash/<ts>/` 归档 + 一键清理；启动时跳过 `_` 前缀目录 | P1 |
| B5 | 第二天找不到昨天做的 app | 复购/留存全靠记性 | 宿主首页列出所有 app（图标+最近使用+一句话说明），支持搜索 | **P0** |
| B6 | 想给同事用，对方没装 dsh | 交付断在最后一米 | 导出为单文件（3.12 MB Go exe + app 目录），双击即用；或导出为"文本配方" | P1 |
| B7 | 改一个小地方要不要重新生成整个 app | 用户不敢改 | 改一句话 → 只跑 `cases.json` 回归 → 通过就热重载窗口 | P1 |
| B8 | 数据被覆盖/删除后没有恢复入口 | 数据丢失 = 信任归零 | `data/` 每次会话前 git 快照；UI 有"回退到上一版" | P1 |
| B9 | 断网时 app 直接报错 | 离线场景全废 | 离线时显示"当前需要联网"，缓存上一次结果可看 | P1 |
| B10 | 用户输入是 PDF / 图片 / Excel | 主流真实输入跑不通 | 宿主侧统一转换层：PDF→文本、图→OCR（可选件）；`state/inputs/` 约定 | **P0** |
| B11 | 三个问题连问，用户不耐烦 | 倒问变成劝退 | 一问一答 + 每题给默认答案（可直接回车）；最多 3 问 | P1 |
| B12 | 中文文件名、中文内容、中文路径 | 编码坑连爆 | `test_client.py` 加中文 cwd + 中文内容用例，必须绿 | P1 |
| B13 | 10 分钟长任务时用户关窗口 | 任务孤儿进程 | 关窗 = 后台继续 + 托盘提示；或在 `AGENT_RULES.md` 声明"关窗即取消" | P1 |
| B14 | 同一个 app 同时点两次"跑" | 两个进程写同一目录，数据打架 | 目录级文件锁；第二次点击排队而非并发 | P2 |
| B15 | 用户看不懂 agent 在干什么 | 黑箱，不敢信 | `tool` 事件渲染成"正在读 invoice.xlsx"这类人话 | P1 |
| B16 | 用户说"不对"但说不清哪里不对 | 无法收敛 | 宿主给出"上一版 vs 这版"的 diff，让用户点选哪几处不对 | P1 |

---

## C. 产品与交互（10）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| C1 | 没有进度表达，只有一个转圈 | 长任务焦虑 | 步骤条（读→算→写→自证），每步有真实事件驱动 | P1 |
| C2 | 没有成本表达 | 用户不知道一次多少钱 | 状态栏显示本 app 累计 tokens / 估算金额（用 `step_end.usage` 累加） | P1 |
| C3 | 失败后没有"重试"按钮 | 只能重开 | 失败卡片带 `重试` / `看日志` / `换个说法` 三个动作 | P1 |
| C4 | 没有"这个 app 是什么、能碰什么"的说明 | 信任缺失 | 每个 app 首屏有"能做什么 / 能碰哪些目录 / 上次改动时间" | P1 |
| C5 | 图标从哪来 | 一堆一样的默认图标 | AI 生成 32×32 PNG 存 `app.json` 的 `icon`；缺失时用首字 | P2 |
| C6 | 开 5 个 app 怎么切换 | 窗口地狱 | 宿主统一一个"应用坞"（侧栏/托盘菜单），每 app 独立窗口 | P1 |
| C7 | 托盘还是窗口，谁决定 | 每个 app 形态不一致 | `app.json` 的 `surface` 字段三选一：`window` / `tray` / `panel`，默认 `window` | P2 |
| C8 | 全局快捷键冲突 | 抢别人的键 | 注册前检测占用，冲突时让用户改 | P2 |
| C9 | 高 DPI / 深色模式 | 糊、刺眼 | WebView2 跟随系统主题；`prefers-color-scheme` 必须生效 | P2 |
| C10 | 无网络时的降级 UI | 白屏 | 有明确的离线态页面，不是报错栈 | P2 |

---

## D. 安全与权限（10）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| D1 | app 以同一 OS 用户身份运行，读得到 `~/.dsh/.credentials.yaml`（`dsh-credentials-local` README 自己写明"无法与 agent 隔离机密"） | **API key 可被 app 读走并外发** | **v1.7.0 实测校准**：宿主 broker **只挡网络出口**（dsh 不再自己拿 key、不经过子进程环境），**不挡文件读取** —— agent 用 `read` 直接读真实路径仍拿到真 key。剩下的洞要 v0.2「宿主密钥库」把 key 从文件移走（需用户确认，会影响其他 dsh 会话）。见 `docs/10` 第六节 | **P0** |
| D2 | app 能 spawn 任意进程 | 等于把机器交出去 | v0.1 只做检测 + 告警；强制白名单需 L2 受限令牌 | **P0** |
| D3 | app 能无限制外联 | 数据外泄 | **v1.2.0 已处理（关插件）**：`configs/lockdown.patch.yml` 关掉 web 工具，行为验证 `TOOL_MISSING`；域名白名单 v0.2 | **P0** |
| D4 | AI 能改自己的 profile（`cordis.patch.yml`）→ 自我提权 | 权限模型形同虚设 | **v1.5.0 部分缓解（检测层）**：`ProfileGuard` 指纹 + 收窄只读 + spawn 前后比对；实测只读下 dsh 正常跑完一轮（exit 0）。同用户仍能 `chmod +w`，**阻止**需 L2 | **P0** |
| D5 | `outbox/` 是明文，可能含身份证/发票号 | 隐私泄露 | outbox 目录 ACL 收紧；导出时提示；`app.json` 可声明 `sensitive: true` 加密 | P1 |
| D6 | 多 app 共享 `~/.dsh/sessions`，会话可能串台 | 数据串 | 每 app 独立 `DSH_HOME` 或用独立 profile + 独立 sessions 根 | P1 |
| D7 | 别人给的 app 文件夹 = 直接任意代码执行 | 供应链攻击 | 导入流程：只读预览 → 展示"它将碰什么" → 二次确认 → 沙箱首次运行 | **P0** |
| D8 | 没有审计：谁在什么时候碰了什么 | 出事查不清 | `state/audit.log` 已有约定（docs/08 第六节）；宿主统一汇总到 `~/.apps/audit.jsonl` | P1 |
| D9 | 没有配额：一个 app 能烧掉全部额度 | 账单事故 | 每 app 限额（tokens / 时长 / 次数），超限暂停并提示 | P2 |
| D10 | Windows 沙箱用 ACL，失败模式未知 | 权限报错看不懂 | 抓 10 个真实 ACL 拒绝样本，写进 `docs/06` 排障矩阵 | P1 |

---

## E. 工程与维护（11）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| E1 | `conformance.py` 自己**没有任何测试**；变异 C（禁用 schema 检查）后 23 个测试仍全绿、它自己还报 `ALL PASS` | 测谎仪没校准，整个信任链悬空 | `tests/test_conformance_negative.py` 12 条负样本；变异 M1/M2 必须让它红 | **P0** |
| E2 | CI 从不跑 `--live`，真 dsh 一次没在 CI 里验过 | dsh 升级改协议无人知 | `.github/workflows/live.yml` 手动 + 每日跑；失败即 issue | **P0** |
| E3 | 文档写 headless 97 包 / tui 124 包，实测 98 / 126，版本号相同 | 文档可信度崩 | `tests/check_docs_drift.py`：数 `--dump-config` 条目并与文档比对；文档改为"约数 + 版本标注" | P1 |
| E4 | 无 `.git`，文档里的"实测"无 commit 可追溯 | 无法回归、无法定责 | `git init` + 每个实测数字附 commit（**需用户许可**） | P1 |
| E5 | 7 个 example 一个都不在 CI | 示例腐坏无人知 | CI 用 `fake_dsh` 跑 `examples/hello.py`、`stream.py`、`multiturn.py`；`batch.py` 用假数据 | P1 |
| E6 | 没有性能基准 | 优化无据 | `tests/bench.py`：首字延迟、单轮总时长、事件吞吐，落 `bench.json` 并进 CI artifact | P2 |
| E7 | 没有错误码表 | 用户拿到的是英文栈 | `docs/11-error-codes.md`：`MISSING_CREDENTIAL` / `SESSION_NOT_FOUND` / `TIMEOUT` / `SPAWN_FAIL` 各配一句人话 + 处置 | P2 |
| E8 | 没有回滚策略 | 改坏了只能手工回退 | 每次改前自动快照到 `_bak/`；`CHANGELOG` 记录可回滚点 | P1 |
| E9 | kit 版本 ↔ dsh 版本无对应表 | 兼容性靠猜 | `docs/02` 加"kit 1.x ↔ dsh 0.2.x"矩阵，CI 校验 | P1 |
| E10 | 没有发布流程 | 交付随意 | `CHANGELOG` + tag + 产物清单；一条 `make release` | P2 |
| E11 | `live.yml` 首次线上运行就红：崩在 `print` 上（`UnicodeEncodeError: 'charmap' codec ...`），不是断言 | 每日真实 dsh 验证拿不到结果，E2 等于没闭 | 入口 `main()` 统一 `reconfigure(encoding="utf-8")`；`tests/test_console_encoding.py` 双闸门（静态 + cp1252/cp936 实跑），CI 纳入 | **P1** |

---

## F. 商业化（8）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| F1 | 谁付钱不清楚：用户、app 作者、还是企业 | 定价无从谈起 | 写清三种计费面（按 app、按用量、按席位）里选一种主打 | P1 |
| F2 | 成本不可见 | 不敢用 | 见 C2；再加"本月累计" | P1 |
| F3 | 多用户 / 审计 / 配额缺失 | 进不了企业 | 见 D8/D9；企业版至少要有审计导出 | P1 |
| F4 | 数据合规（本地 vs 云） | 过不了采购 | 明确"数据不出本机"的白皮书级说明（当前架构天然满足，要写成卖点） | P2 |
| F5 | 与 Claude / ChatGPT 的差异没讲清 | 被认为是模仿 | 三条差异写成一句话：不绑账号、可离线、产物是文件夹 | P1 |
| F6 | 分发方式未定 | 用户拿不到 | 决定：单文件 exe / 文件夹 / 一条命令；先做单文件 exe | P1 |
| F7 | 出问题找谁 | 无支持闭环 | 内置"导出诊断包"（日志+配置+版本，脱敏） | P2 |
| F8 | 价格锚点 | 定价拍脑袋 | 对标"一个外包小工具的人力成本"，不是对标 ChatGPT 订阅 | P2 |

---

## G. 极端与边界（10）

| ID | 现象 | 影响 | 判据 | 级别 |
|---|---|---|---|---|
| G1 | 磁盘满时写入半截 | 数据损坏 | 原子写（临时文件 + rename）；ENOSPC 明确报错 | P2 |
| G2 | ACL 沙箱拒绝时的报错不可读 | 用户以为坏了 | 见 D10 | P1 |
| G3 | 杀软拦截未签名的 Go exe | 装不上（**真实高发**） | 提供"如何加白"说明 + 可选签名流程；首启自检并提示 | P1 |
| G4 | WebView2 缺失或损坏 | 窗口起不来 | 启动自检 runtime 版本；缺失时给一键安装链接 + 降级到托盘模式 | P1 |
| G5 | 区域设置导致小数点/日期格式不同 | 数字算错 | `cases.json` 强制 ISO 日期与 `1.5` 小数；CLI 层 `LC_ALL=C` 兜底 | P2 |
| G6 | 同时开 20 个 app | 内存 224 MB × N | 见 C6 应用坞；空闲 app 自动卸载 webview（保留进程） | P2 |
| G7 | 一个 app 死循环 CPU 100% | 整机卡 | 子进程 CPU 看门狗，超阈值暂停并提示 | P1 |
| G8 | 超长输出把 UI 卡死 | 假死 | 前端虚拟列表 + 只渲染最后 N 行；事件丢弃时显示"已折叠 N 条" | P1 |
| G9 | 系统睡眠 / 唤醒 | 定时任务错乱 | 唤醒后重算下次执行时间，不补跑堆积的任务 | P2 |
| G10 | 多显示器 / 缩放变化 | 窗口跑到屏幕外 | 记住窗口位置并做屏幕边界校正 | P3 |

---

## 统计

| 级别 | 条数 | 处理策略 |
|---|---|---|
| **P0** | 12 | v0.1 之前必须清 |
| **P1** | 37 | v0.1 之后第一批迭代 |
| **P2** | 24 | 规模化前 |
| **P3** | 4 | 有空再说 |

## 本轮（闭环轮）范围

只做 **P0 里的工程三项** + **E5**，因为它们决定后面所有迭代是否可信：

- **E1** → `tests/test_conformance_negative.py`（12 条负样本，闸门的闸门）
- **E1'** → `tests/mutate.py`（变异测试，CI 里跑，3 处必须红）
- **E2** → `.github/workflows/live.yml`（真 dsh 冒烟）
- **A1** → `configs/cred.patch.yml` + `docs/03` 专节（顺手，10 分钟）
- **A2** → `docs/03` 警告（顺手）
- **E3** → `tests/check_docs_drift.py` + 文档改约数
- **E5** → CI 跑 examples（用 fake_dsh，零 token）

其余 P0 留给 v0.1 及之后，逐轮推进。

---

## 进度（2026-11-25 · v1.1.0）

### 已关闭

| ID | 结论 | 证据 |
|---|---|---|
| **E1** | 已闭 ｜ `tests/test_conformance_negative.py` 16 条负样本，含 4 条元检查 | 16/16 · 变异 M1–M4 全被抓 |
| **E1'** | 已闭 ｜ `tests/mutate.py` 6 个变异点 | `6/6 个变异被抓到` |
| **E2** | 已闭 ｜ `.github/workflows/live.yml` 每日 + 手动，含凭证条目自检 | 待首次线上运行确认 |
| **E3** | 已闭 ｜ `tests/check_docs_drift.py` + 文档改 ≈ 约数 | `headless 98/98 · tui 126/126` |
| **E5** | 已闭 ｜ `tests/run_examples.py` 纳入 CI | `6/6 个示例跑通` |
| **A1** | 已闭 ｜ `configs/cred.patch.yml` + `docs/03` 第七节 | 单轮 6.6 s 正常返回 |
| **A2** | 已闭 ｜ `docs/03` 第四节 + 第七节警告 | — |

### 附带修掉的 bug（不在原清单里）

| 现象 | 发现方式 | 修法 |
|---|---|---|
| `schema_check` 遇到非 dict 事件抛 `AttributeError`，闸门在坏输入上崩溃 | 负样本「混入非对象行」首次运行 | `tests/conformance.py` 加类型守卫 |
| Node 客户端无法被注入启动器，示例在 CI 里跑不了（`spawn dsh ENOENT`） | `run_examples.py` 首跑 | 新增 `envLauncherOverride()`，与 Python 侧 `QED_LAUNCHER` 对齐 |
| 变异点 `if not isinstance(ev, dict):` 在文件里出现两次，变异脚本 SKIP | `mutate.py` 首跑 | 变异锚点加长到跨两行 |

### 下一轮（v0.1）

按 P0 剩余 5 条推进，顺序即执行顺序：

1. **D1** 凭证隔离（broker 模式）— 决定整个安全模型，必须先定
2. **D7** 导入第三方 app 的信任流程
3. **B2** 「先 cases.json 后界面」的强制闸门
4. **B10** PDF / 图片 / Excel 输入的统一转换层
5. **B1 / B5** 空状态与 app 列表（首次使用与回访）

D2 / D3 / D4（进程、网络、自我提权）与 D1 同属一个权限模型，一起设计。

---

## 进度（2026-11-25 · v1.3.0）

### 已关闭 / 部分关闭

| ID | 结论 | 证据 |
|---|---|---|
| **E1** | 已闭 ｜ 16 条负样本 + 4 条元检查 | 16/16 · 变异 M1–M4 全抓 |
| **E1'** | 已闭 ｜ `tests/mutate.py` 6 个变异点 | 6/6 |
| **E2** | 已闭 ｜ `live.yml` 每日 + 手动 | 待首次线上运行 |
| **E3** | 已闭 ｜ `check_docs_drift.py` + 文档改约数 | 98/98 · 126/126 |
| **E5** | 已闭 ｜ `run_examples.py` 纳入 CI | 6/6 |
| **A1** | 已闭 ｜ `cred.patch.yml` + `docs/03` 第七节 | 单轮 6.6 s |
| **A2** | 已闭 ｜ `docs/03` 警告 | — |
| **D1** | **部分缓解（L1）** ｜ broker 让 key 不落盘、不进子进程环境 | 文件移走后 dsh 仍工作；读进程内存需 L2 |
| **D3** | 已闭（v0.1） ｜ 关 web 插件 | 行为验证 `TOOL_MISSING` |
| **B2** | **已闭（v0.1）** ｜ 脚手架 + `verify` 的强制闸门：没有 `cases.json` 直接报错；`AGENT_RULES.md` 硬规则第 1 条禁止先做界面 | `test_missing_cases_reports_clearly` |
| **A3** | 降级处理 ｜ 不加显式版本号，改由 `test_protocol_live.py` + `live.yml` 用**真实抓包**发现漂移 | 见 1.4.0 |
| **A4** | **已闭** ｜ 实测确认工具事件是 `tool_call` + `tool_result`（不是单一 `tool`），schema/docs/fake 三方同步修正 | `test_protocol_live.py` 6/6 |
| **A5 / B12** | 部分关闭 ｜ `verify` 全链路 `utf-8-sig`（BOM 容错） | `test_bom_in_logic_and_cases` |
| **A8** | **已闭** ｜ `idle_timeout` 区分「挂起」与「慢任务」 | 真实 dsh-tui 挂起 5.3 s 被抓住 |

### 本阶段新增的能力

- **自证 runner**（`qed.verify`）：用例 + 变异双闸门，`用例绿 + 变异全抓` 才算成立。
- **脚手架**（`qed.scaffold`）：生成即**红**，杜绝"看起来做完了"。
- **凭证 broker**（`qed.broker`）：见 `docs/10-permission-adr.md`。

### 垂直切片状态

```
scaffold → 红 → dsh 自己实现（35 s）→ verify --mutate → 2/2 + 变异 5/5 → 自证成立 ✓
```

**已跑通一次**，但只覆盖「纯逻辑 + 断言」这一条路径。以下仍未验证：
界面载体（Go + WebView2）、多轮交互、真实 PDF 输入、断网降级。

### 下一轮（v0.2 候选，按 ROI）

1. **A8 idle_timeout**：区分"挂起"与"慢任务"，客户端层可独立完成
2. **A3/A4**：协议版本号 + `tool` 事件的最小公共字段（UI 要渲染工具调用）
3. **D4**：profile 只读 + spawn 前后 hash 比对
4. **B10**：PDF / Excel 输入转换层（先做 PDF→文本，用本机已有工具）
5. **宿主最小壳**：扫 `~/apps/*`、一个 app 一个窗口、事件流上屏

---

## 进度（2026-11-27 · v1.10.1）

### 已关闭

| ID | 结论 | 证据 |
|---|---|---|
| **E11** | 已闭 ｜ 18 个入口补 UTF-8 stdout 守卫；新增 `tests/test_console_encoding.py` 双闸门并纳入 CI | 未修树 `4/7`（同一条 `UnicodeEncodeError`）→ 修后 `7/7`；`PYTHONIOENCODING=cp1252 python tests/check_docs_drift.py` 由 exit 1 变 exit 0 |
| **E2** | 首次线上运行确认（补上当初那句「待首次线上运行确认」） | `live` run #1：安装 dsh、结构自检、跳过 live 调用都正常，**唯一**红点是 E11 |

### 这次为什么值得记一笔

`live` 第一次跑就红了，而日志第一眼像**断言失败**（`Process completed with exit code 1`），
实际是：

```
文档漂移（包里条目数 vs docs/03）  print("  %s  %-9s 文档 ≈%d ...")
UnicodeEncodeError: 'charmap' codec can't encode characters in position 18-19
```

三个教训：

1. **「红的」不等于「被测的东西坏了」。** 数字全对（98/98、126/126），崩的是把它写出来。
   排障顺序要反过来：先看 traceback 的最后一帧，再看断言。
2. **本地跑不出来，不是"没问题"。** 开发机终端是 UTF-8，CI 管道是 cp1252，
   同一份代码两种行为。凡是"只在 CI 红"的，先怀疑**平台默认值**（编码、路径分隔符、大小写）。
3. **同一个坑已经在三个文件里被踩过并修好了**（`conformance.py` / `test_client.py` /
   `test_gateway.py` 都有 `reconfigure`），第四个文件还是漏了 —— 因为当时只靠"记得写"。
   所以这次补的不是第 19 个守卫，而是**第 19 个也不会被忘掉**：静态锁查"有没有"，
   行为锁查"有没有用"。

