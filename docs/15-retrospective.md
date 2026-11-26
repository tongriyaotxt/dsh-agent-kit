# 15 · 结项记录

> 2026-11-26 · 从一条视频号链接开始，到 `github.com/tongriyaotxt/qed` 上线为止。
>
> 这份文档不是使用说明（那是 `README.md` 和 `docs/01–11`），是**复盘**：
> 做了什么、凭什么这么判断、哪里被真实世界打回过。

---

## 一、一句话

**给 AI 写的代码加第二把锁：用例全绿 + 变异全抓，少一个都不算做完。**

```bash
pip install qed-kit
qed demo        # 60 秒 · 零依赖 · 不用 API key · 不联网 · 全程真实执行
```

| 指标 | 数字 |
|---|---|
| 公开仓库 | `tongriyaotxt/qed` · 89 KB · 86 文件 |
| 质量闸门 | 12 项 Python + 8 条 Go + CI 8 个 job（全绿） |
| 测试 | pytest 101 · 负样本 18 · 变异 6/6 · 协议快照 6 |
| 宿主体积 | 7.6 MB（Go + 系统 WebView2，不打包 Chromium） |

---

## 二、时间线

起点是一条微信视频号链接（`weixin.qq.com/sph/A7Nh7QQSjP`），内容是
「Claude 昨夜杀入桌面应用，小白一句话造 Mac/Windows 软件」。当时的判断是：
**功能是真的，但它解决的是交付，不是正确性。**

| 轮次 | 做了什么 | 关键判断 |
|---|---|---|
| R1 | 76 条问题清单 + 三道闸门 | **闸门要能被违反**，否则是装饰 |
| R2 | 自证 runner + 脚手架 | 脚手架**故意生成红的**，拒绝"看起来做完了" |
| R3 | 协议对齐 | 工具事件是 `tool_call`+`tool_result`，三方文档一起错 |
| R4 | Profile 守卫 | 只读清单被实测收窄；同用户能 `chmod +w`，所以是检测不是阻止 |
| R5 | 宿主落地 | GUI 子系统无控制台 → 工具挂死；stdout 先 EOF → 永远转圈 |
| R6 | Broker 接进宿主 | **实测否掉自己一半假设**：只挡网络出口，不挡文件读取 |
| R7 | 密钥库 + 一键直达 | 迁移被 dsh 内存快照覆盖 → 复读自检 + 自动回滚 |
| R8 | demo + 英文门面 + SVG | 路人 99% 没装 dsh，不能要求他先配环境 |
| R9 | 脱敏 + 上线 | `github.com:443` 不通（api 通）→ 用 Git Data API 推送 |
| R10 | 改名 `qed` | 旧名像内部代号，`-agent-kit` 有几千个同名 |

---

## 三、21 个坑

这一节是整份文档里最值钱的部分。**每一条都是"我以为"被"实际"打回。**

### A. 协议与契约（4 条）

| # | 我以为 | 实际 | 修法 |
|---|---|---|---|
| 1 | conformance 说 `ALL PASS` 就可信 | 把 `schema_check` 改成 `[]`，23 个测试仍全绿、它自己还报 PASS | 18 条负样本：**闸门必须能被违反** |
| 2 | 工具事件是单一 `type: "tool"` | 是 `tool_call` + `tool_result`，按 `callId` 配对 | schema/docs/fake 三方同步；加真实抓包快照回归 |
| 3 | schema 和文档一致就够了 | **schema、文档、fake 运行时三方一起错**，测试绿着但真实世界是盲的 | 用真实抓包当 fixture，钉死协议 |
| 4 | `_raw` 降级够用 | `schema_check` 遇到非 dict 会抛 `AttributeError` —— 闸门在坏输入上崩溃 | 加类型守卫；负样本首次运行就抓到了 |

### B. 安全与权限（5 条）

| # | 我以为 | 实际 | 修法 |
|---|---|---|---|
| 5 | 沙箱能挡住读凭证 | `workspace-write` 下 agent 照样算出凭证文件的完整 SHA256 —— **沙箱是写边界，不是读边界** | 改成 broker：key 不进子进程环境 |
| 6 | broker 就闭合了 D1 | 让 agent 读**真实路径**，它照样拿到真 key（`credentials.path` 只改服务从哪读，不改文件系统） | ADR 里如实写「只挡网络出口」，不假装解决 |
| 7 | profile 全设只读更安全 | `cordis.yml` 被锁 → dsh 启动即 `EPERM`（它启动时要重写那棵树） | 只读清单收窄到「用户层」 |
| 8 | 把 key 从文件迁走就行 | 12 小时后**真 key 又回来了** —— 另一个 dsh 进程持有内存快照，把整个文件重写了 | 写完立刻复读自检，被覆盖就自动回滚并报错 |
| 9 | `.rollback-` 目录不算配置 | 它里面的 `cordis.patch.yml` 被当成能力声明算进指纹，一有回滚就误报 | 目录名前缀也跳过 |

### C. 平台行为（6 条）

| # | 我以为 | 实际 | 修法 |
|---|---|---|---|
| 10 | `-H windowsgui` 只是不弹黑窗 | GUI 子系统**没有控制台**，子进程里的 pwsh 工具挂死（同任务在有控制台的 Python 里 11.7 s 跑完） | `CREATE_NO_WINDOW` |
| 11 | 读完 stdout 就等进程退出 | dsh 输出完 `final` 就关 stdout，node 还活一会儿 → `cmd.Wait()` 永不返回，UI 永远转圈 | **EOF 即收尾**，最多等 1.5 s 拿退出码 |
| 12 | 收尾时顺便清理残留进程 | `taskkill` 慢一点，`_end` 就发不出去 | 先 emit，再异步清理 |
| 13 | `ReverseProxy` 的 Director 只负责 header | 它**已经**按 `singleJoiningSlash` 拼过路径，再拼一次得到 `/anthropic/anthropic/v1/messages` → 404 | 只注入 header |
| 14 | `ConvertTo-Json` 能序列化数组 | 它把 `ArrayList` 变成带 `Count` 的对象 → GitHub 报 `Invalid tree info` | tree 手写 JSON |
| 15 | `github.com` 和 `api.github.com` 一样 | 这台机器上 `github.com:443` 超时 20 s，`api.github.com` 200/0.4 s | 用 Git Data API 推送（blob → tree → commit → ref） |

### D. 工程流程（4 条）

| # | 我以为 | 实际 | 修法 |
|---|---|---|---|
| 16 | 加个 job 到 ci.yml 很简单 | `runs-on: ubuntu-latest` 和 `strategy:` 挤到同一行 → YAML 解析失败，**0 秒红** | 本地先用 `yaml.safe_load` 校验 |
| 17 | 支持 Python 3.9 没问题 | `setup-python` 在 `ubuntu-latest` 上已经没有 3.9 → job 直接失败 | 改 3.10–3.13，同时把 `requires-python` 同步 |
| 18 | `pip install dsh-agent-kit` 能用 | **包还没上 PyPI**，第一条命令就是假的 | 全改成 `git clone` + `pip install -e`，并注明"上 PyPI 后可用" |
| 19 | `utf-8` 读取够了 | Windows 记事本/PowerShell 写的文件带 BOM → `compile()` 全失败 → **变异候选被静默跳过** | 全链路 `utf-8-sig` |

### E. 交互（2 条）

| # | 我以为 | 实际 | 修法 |
|---|---|---|---|
| 20 | 空状态点一次就够了 | 点完 `stream` 被清空、按钮消失，我后面几轮一直对着**空白区域**点，误以为是代码 bug | 空状态做成常驻的"下一步"区，随选择重建 |
| 21 | app 名字能直接当 profile 名 | 中文名 slug 化成 `app-<hash>`，profile 又加一次 `app-` → `app-app-b99cffcf` | slug 不带前缀，由 profile 加 |

---

## 四、诚实的边界

三件事**没做成**，写在这里而不是藏在 backlog 里：

**① D1 只关了一半。**
broker 切断了网络出口（日志可见 `POST /anthropic/v1/messages` 走宿主），但 agent 用 `read`
读真实路径的文件仍能拿到真 key。技术上，在「多个 dsh 进程共存」的机器上无法可靠地把 key
移出文件。折中方案是**告警 + 一键迁移 + 迁移自检回滚** —— 把不可见风险变成可见选择。

**② "商业可用"只到技术判据。**
五条技术判据（30 分钟上手 / 自证 6/6 / 双击 500 ms / 新 app ≤ 30 KB / 只学一个概念）全达标，
但真实用户、支持、分发一个都没有。`docs/09-issues.md` 里 F 类是商业化，8 条一条没动。

**③ 星标不在工程范围内。**
能做的都做了：60 秒零依赖 demo、英文门面、首屏 SVG、大厂功能做锚点、CI 全绿、issue 模板。
**能不能被转发不取决于代码。** 任何说"做完这些必火"的人都在骗你。

---

## 五、资产清单

| 类别 | 位置 |
|---|---|
| 公开仓库 | `github.com/tongriyaotxt/qed`（`v1.9.0` / `v1.10.0` release） |
| 使用文档 | `README.md`（英）· `README.zh.md`（中）· `docs/01–11` |
| 决策记录 | `docs/10-permission-adr.md`（权限三层，含实测）· `docs/13-launch.md`（发布包） |
| 质量闸门 | `tests/`（12 个脚本）· `host/keystore_test.go` · `.github/workflows/`（3 个） |
| 问题清单 | `docs/09-issues.md`（76 条，带实测状态） |
| 首屏视觉 | `assets/demo.svg`（手写，无字体依赖） |

---

## 六、如果要继续

按 ROI 排序（`docs/09-issues.md` 是权威列表）：

1. **B10 PDF/Excel 输入** —— demo 现在是"发票金额"这种干净数据。能处理真 PDF，才配得上"不是玩具"。
2. **多窗口 + 应用坞** —— 宿主现在一次只跑一个任务。
3. **P1 的 30 条** —— 决定用久了会不会被骂。
4. **L2 隔离**（受限令牌 / AppContainer）—— 才能真关掉 D1 的文件出口和 D2 的进程白名单。

---

## 七、一句话总结

这个项目从头到尾只做了一件事：**把"我觉得对"变成"证毕"。**

过程中最值钱的不是代码，是那 21 个坑 —— 每一条都是"设计被真实世界打回"的记录。
如果只能带走一句：

> **能被你违反的闸门才是闸门；不能被违反的，是装饰。**
