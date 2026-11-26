# 13 · 发布包

> 给"点 Publish 那一刻"用的。所有文案都控制在一屏内，删掉形容词，只留可验证的事实。

---

## 仓库设置

**Description**（GitHub 顶部那句话，160 字符内）

```
Two locks for AI-written code: cases pass, and mutations get caught. A test that always
passes proves nothing — this finds out whether your assertions can be fooled.
```

更短的备选：

```
Make AI-written code prove itself: green cases AND caught mutations, or it isn't done.
```

**Topics**

```
ai  ai-agents  mutation-testing  self-verification  llm  code-generation
testing  developer-tools  agent  prompt-engineering  python  golang
```

**其它**

- 关掉 Wiki、Projects、Discussions（除非有人来问）
- 开 Issues，模板已就位
- Release tag：`v0.2.0`，正文直接贴 `CHANGELOG.md` 的 1.9.0 段

---

## 首屏三句话（README 已经这么写了，这里单独抽出来备用）

1. **The hard part of AI coding isn't generation. It's knowing whether it's right.**
2. A test that always passes proves nothing — and most AI-written tests always pass.
3. This project adds the second lock: mutation testing.

---

## 发帖文案

### A. Hacker News（`Show HN`）

标题（≤80 字符，不要用感叹号）：

```
Show HN: Make AI-written code prove itself (cases + mutation testing)
```

正文：

```
Every AI coding tool can produce a green test suite. Almost none of them can tell you
whether that green means anything — the agent wrote the tests too, and a test that always
passes is indistinguishable from a test that works.

This adds a second lock: mutate the source, watch the tests. If they stay green, your
assertions were decoration.

    git clone https://github.com/tongriyaotxt/qed
    pip install -e qed/python
    qed demo        # 60s, no deps, no API key, no network

The demo really runs — it scaffolds an app that is deliberately red, implements it, shows
2/2 cases pass with 4/4 mutations caught, then weakens one assertion to show what
"self-proof fails" looks like (1/2 mutations survived).

A few things I got wrong on the way, which are now tests:
  · the conformance checker could be silently disabled and everything stayed green
    -> 18 negative samples that must be violable
  · dsh emits tool_call + tool_result, not a single `tool` — every doc and the fake
    runtime agreed on the wrong shape, so the whole suite was green against a fiction
    -> a protocol test pinned to a real 13-event capture
  · a "migrate your API key off disk" feature that silently got overwritten by another
    daemon holding the key in memory -> self-verify after writing, roll back if it changed

Not a coding agent. Not a test framework. It decides whether "done" is allowed to be said.
```

### B. Reddit（r/LocalLLaMA 或 r/MachineLearning）

标题：

```
A test that always passes proves nothing — so I built the second lock for AI-written code
```

正文：用 A 的前三段 + demo 命令，删掉"Not a coding agent"那三行，改成一句
（Reddit 读者更在意"能不能用"，不在意"边界声明"）。

### C. X / Twitter（两段式 thread）

```
1/ Every AI coding tool can hand you a green test suite.
   None of them can tell you whether that green means anything.
   The agent wrote the tests too. A test that always passes is indistinguishable
   from a test that works.

2/ So: mutate the source, watch the tests.
   Cases green + mutations caught = done.
   Cases green + a mutation survives = your assertions were decoration.

   git clone https://github.com/tongriyaotxt/qed
   pip install -e qed/python
   qed demo   # 60s, no deps, no API key

3/ The demo shows both outcomes:
   2/2 cases · 4/4 mutations caught  → SELF-PROOF HOLDS
   1/1 cases · 1/2 mutations caught  → SELF-PROOF FAILS: 1/2 survived
```

### D. 中文（掘金 / 少数派 / V2EX）

```
AI 写代码最大的问题不是「写不出来」，是「你不知道它写对了没有」。

每个 AI 编程工具都能给你一个全绿的测试。几乎没有哪个能告诉你这个绿有没有意义
——测试也是它写的，而一个永远绿的测试和一个真正有效的测试，从结果上完全一样。

我加的第二把锁是变异测试：把源码改坏，看测试还绿不绿。
绿了，说明你的断言是装饰品。

  git clone https://github.com/tongriyaotxt/qed
  pip install -e qed/python
  qed demo      # 60 秒，零依赖，不用 API key，不联网

demo 是真的跑：先给你一个故意红的 app，实现它，2/2 用例 + 4/4 变异被抓，
然后故意把断言放松，让你看到「自证不成立」长什么样。
```

---

## 发布顺序（重要）

1. **先 push 仓库，等 10 分钟** —— 让 CI 跑完，README 的 SVG 能正常渲染
2. **自己按 README 从头走一遍**（新机器或干净虚拟环境），确认 `pip install` 那条命令真的能用
3. **截图存档**（README 截图、demo 输出、CI 全绿）—— 发帖时用
4. 先发中文（受众近、反馈快），根据反馈改 README
5. 24 小时后再发 HN（HN 更挑，README 改过一轮再上）
6. 有人在 issue 里问，24 小时内回 —— 前 10 个 issue 决定别人怎么看你

---

## 不要做的事

- ❌ 不要写"revolutionary"、"game-changing"、"10x" —— 会立刻被 HN 打回
- ❌ 不要假装支持所有语言（现在只有 Python + Node）
- ❌ 不要在标题里放 emoji
- ❌ 不要承诺 roadmap —— 只写已经跑通的东西
- ❌ 不要为了凑星标去互 star

---

## 我能保证的 vs 不能保证的

**能保证**（都已实测）：

- `pip install` 后 60 秒内有可见结果，且不需要 dsh / API key / 网络
- 12 项质量闸门全绿，任何人可以复现
- README 里的每个数字都能在仓库里找到出处

**不能保证**：

- 星标数量。它取决于时机、有没有人转发、以及这个话题当时有多热。
  工程能做的只是"把可被转发的条件准备好"——剩下的不由代码决定。
