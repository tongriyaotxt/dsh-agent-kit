# 11 · 五分钟：从零到一个「自证成立」的 app

> 这是 v0.1 的主路径。全程不需要 IDE、不需要 npm、不需要界面。
>
> 前置：`dsh` 已装（`dsh --version` 有输出）、Python ≥ 3.9。

---

## 第 0 步 · 一条命令生成骨架

```bash
export PYTHONPATH=/path/to/dsh-agent-kit/python      # Windows: $env:PYTHONPATH = "..."
python -m dsh_agent_kit.scaffold 发票统计 --desc "按月统计发票总额"
```

生成的是**四件套 + 数据目录**：

```
~/apps/发票统计/
  app.json          能力声明（档位 / 网络 / 表面）—— 也是权限指纹的锚点
  AGENT_RULES.md    给 agent 的规则手册（六节：职责/工作流/上下文/格式/硬规则/反例）
  logic.py          纯函数 run(input, ctx) ← 只有这里该变
  cases.json        断言：每条 input → expect ← 这是验收标准，不是建议
  verify.py         自证入口（用例 + 变异）
  说明.md            给用户看的唯一一份文件
  state/ data/ out/ outbox/
```

## 第 1 步 · 它现在必然是红的

```bash
cd ~/apps/发票统计
python verify.py
```

```
发票统计 · 0/2 用例通过
  FAIL c1  期望 {"count": 2, "total": 123.5}，实际 {"todo": true}
  FAIL c2  期望 {"count": 0, "total": 0}，实际 {"todo": true}
```

**这是故意的。** 生成即绿会让人以为做完了；红着才是诚实的状态。

## 第 2 步 · 让 agent 自己做（推荐路径）

```bash
# 一个终端起 broker（凭证隔离，见 docs/10）
python -m dsh_agent_kit.broker --port 8787

# 另一个终端，在 app 目录里
dsh --patch <kit>/configs/broker.patch.yml --profile headless \
  "读 AGENT_RULES.md 和 cases.json，实现 logic.py 的 run()，\
   用 python verify.py --mutate 自证：用例全绿且变异全抓。不要改 cases.json 的 expect。"
```

**实测（2026-11-25，`0.2.1-rc.2`）**：35 秒完成，agent 自己读规则 → 写实现 → 跑变异 →
自检"没有残留 `_mutant_*`、`cases.json` 未被改动" → 交付。

独立复核（不信 agent 的自述）：

```
发票统计 · 2/2 用例通过 · 变异 5/5 被抓
  自证成立 ✓
```

## 第 3 步 · 不想用 agent，自己写也一样

```bash
# 改 logic.py，然后：
python verify.py            # 只看用例
python verify.py --mutate   # 用例绿了，再验断言不是摆设
```

## 第 4 步 · 什么才算「做完了」

只有一条判据：

```
用例全绿 + 变异全抓 = 自证成立 ✓
```

| 状态 | 含义 | 下一步 |
|---|---|---|
| 用例红 | 逻辑没写对 | 改 `logic.py` |
| 用例绿但变异逃逸 | **断言太弱**，测试是摆设 | 往 `cases.json` 补用例 |
| 用例绿 + 变异全抓 | 自证成立 | 可以交付了 |
| 报 `缺 cases.json` | 没有验收标准 | 先写断言，再写代码 |

**「用例绿但变异逃逸」是最容易被忽略的一种失败**：代码看起来对了，其实只是没被测到。
`verify.py` 会明确列出逃逸的变异，告诉你该补哪类用例。

## 第 5 步 · 权限是怎么收紧的

| 手段 | 文件 | 效果 |
|---|---|---|
| 凭证不进磁盘 | `configs/broker.patch.yml` + `dsh_agent_kit.broker` | agent 读遍磁盘只有占位符 |
| 只写本目录 | `DSH_PERMISSION_MODE=workspace-write` | 写边界收在 app 目录 |
| 关外联 | `configs/lockdown.patch.yml` | `web_search` / `web_fetch` 不存在 |
| 逻辑沙箱 | `verify.LocalCtx` | 自证时代码只能碰 `data/ out/ state/` |

细节与边界（包括挡不住什么）见 [`10-permission-adr.md`](10-permission-adr.md)。

---

## 常见问题

**Q：为什么 `verify.py` 里有一行硬编码的绝对路径？**
脚手架生成时写入了 kit 的位置，换机器改那一行即可。这样 `cd` 到任何地方都能跑。

**Q：`cases.json` 可以先不写吗？**
不行。没有断言就没有自证，`verify` 会直接报错。这正是「先逻辑后界面」的闸门（D2/B2）。

**Q：Windows 上用记事本改文件会不会坏？**
不会。读取一律走 `utf-8-sig`，BOM 会被吞掉（`tests/test_verify.py::test_bom_in_logic_and_cases` 覆盖）。

**Q：agent 改了 `cases.json` 的 expect 来迁就代码怎么办？**
`AGENT_RULES.md` 的硬规则第 2 条禁止这么做；真发生了，`git diff cases.json` 一眼能看出来——
所以 app 目录建议进版本库（`docs/09-issues.md` E4 提到 kit 自己还没有 git，是同一个问题）。
