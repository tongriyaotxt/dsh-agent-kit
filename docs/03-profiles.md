# 03 · Profile：给 agent 配一个专属能力集

`profile` 是 DSH 的启动单位：一组 bundle + 你的覆盖层。同一台机器可以有多个 profile，各自装不同的插件、不同的 MCP、不同的默认模型。

接入别的应用时**永远指定专用 profile**，不要依赖开发机的默认配置 —— 否则换台机器就跑不起来。

## 一、CLI 形状（先记这个，能省一半踩坑）

```
dsh [--profile] <name> [launcher-options] [app-args...]
dsh plugin --profile <name> <pnpm-args...>
```

| 位置 | 参数 | 归属 |
|---|---|---|
| profile 名 | `dsh headless` 或 `dsh --profile headless` | 启动器（两种写法等价） |
| 启动器选项 | `--patch <path>`（可重复）、`--from-default-profile <name>`、`--dump-config`、`--dump-config-schema`、`--dump-default-config` | **必须在 app 参数之前** |
| app 参数 | `--json`、`--session-id <id>`、`--help`、任务文本、`-`（stdin） | 之后 |

顺序错了的真实报错：

```bash
$ dsh headless --patch x.yml
unknown option '--patch'          # 被当成 headless 的 app 参数

$ dsh --patch x.yml --profile headless --json -   # ✓
```

## 二、造一个专用 profile

```bash
dsh --from-default-profile headless --profile myagent
```

> 实测：这个命令会**创建并从模板启动**该 profile，所以紧接着会要求一个任务
> （`dsh: a task is required`）。加个无害任务即可，例如
> `dsh myagent --from-default-profile headless "reply with OK"`；或者忽略这条报错，
> 因为 profile 目录已经建好了。新建的 `cordis.patch.yml` 只有一个 `[]`，是干净的用户层
> —— 与升级后「旧 profile 里残留不兼容插件」相比，这正是它更可靠的用法。

生成：

```
~/.dsh/profiles/myagent/
├── package.json        # dsh.profile.bundles: 用哪些 bundle
├── cordis.yml          # 空 []（树由 patch 组成）
└── cordis.patch.yml    # 你的覆盖层
```

`package.json` 片段：

```json
{
  "name": "dsh-profile-myagent",
  "private": true,
  "dsh": {
    "profile": {
      "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"]
    }
  }
}
```

`cordis.patch.yml` 是**有序的覆盖层**：`insert` 加插件、`- id: x` + `disabled: true` 关插件。模板见 [`../configs/profile-template/`](../configs/profile-template/)。

启动：

```bash
dsh --profile myagent --json -
```

## 三、看懂组合结果：`--dump-config`

不要靠读 YAML 猜最终生效了什么，直接让 dsh 打印组合后的树：

```bash
dsh --profile headless --dump-config              # 含用户层与 --patch
dsh --profile headless --dump-default-config      # 不含用户层/补丁
dsh --profile headless --dump-config-schema       # 条目与 patch 的 JSON Schema
```

输出形如（节选）：

```yaml
# == @deepseek-ai/dsh-base
- id: tool-plugin-manager
  name: '@deepseek-ai/dsh-plugin-manager/tools'
  disabled: true
- id: plugin-manager
  name: '@deepseek-ai/dsh-plugin-manager'
  disabled: !!js '!ctx.get(''profileContext'')'
# == @deepseek-ai/dsh-base, patched by @deepseek-ai/dsh-headless
- id: hmr
  name: '@deepseek-ai/dsh-hmr'
  disabled: true
```

两个关键读法：

1. **`# == bundle` 注释**告诉你这一条来自哪一层、被谁改过。追「为什么这个插件没生效」只看这里。
2. **`disabled: !!js '...'`** 是**运行期表达式**，不是布尔。所以「数一下有多少插件被 disabled」这种静态比对必然误判 —— 想知道某能力在不在，**只能行为验证**（让它做一次，看结果）。

## 四、headless vs tui：能干什么

装包数不同（**headless 97 / tui 124**），但差异不在数量，在几个关键能力。实测结论（`0.2.1-rc.2`）：

| 能力 | headless | tui | 备注 |
|---|---|---|---|
| bash / pwsh | ✅ | ✅ | 命令执行 |
| fs / fs-search | ✅ | ✅ | 读写、搜文件 |
| skill | ✅ | ✅ | 加载 skills |
| web_search / web_fetch | ✅ | ✅ | 实测可用 |
| subagent | ✅ | ✅ | 派子任务成功 |
| workflow | ✅ | ✅ | 多代理编排 |
| **MCP 工具** | ❌ | ✅ | headless 缺配置，用 `--patch` 补 |
| compaction | ❌ | ✅ | 上下文压缩 |
| plan mode / goal / todo | ❌ | ✅ | 长任务编排 |
| TUI 面板 / workspace / storage | — | ✅ | UI 基础设施，headless 不需要 |

**结论**：要做「无人值守的接入」，headless 够用；唯一必须补的是 MCP，见 [04-mcp.md](04-mcp.md)。要 plan/goal/长任务编排，那就得考虑用 tui profile，但那意味着你要驱动界面 —— 通常不值得，改用「应用侧自己编排 + 多次 headless 调用」更稳。

## 五、常驻化：把覆盖层写进 profile

`--patch` 是「临时叠加」。想常驻就把同样的 `insert` 段复制进 `~/.dsh/profiles/<name>/cordis.patch.yml`，之后不用每次传：

```bash
# 临时
dsh --profile headless --patch configs/mcp.patch.yml --json -
# 常驻：把 insert 段粘进 cordis.patch.yml 后
dsh --profile headless --json -
```

判断补丁是否真的生效：

```bash
dsh --profile headless --patch configs/mcp.patch.yml --dump-config | grep -A3 mcp-
```

## 六、profile 相关的错误谱

| 现象 | 原因 | 排查 |
|---|---|---|
| `unknown option '--patch'` | flag 写在了 app 参数之后 | 移到 `--json` 之前 |
| `profile not found` | 名字拼错 / `DSH_HOME` 不同 | `ls $DSH_HOME/profiles` |
| 插件装了但没生效 | `disabled` 是 JS 表达式 | `--dump-config` 看 `# ==` 注释链 |
| 换机器就坏 | 用了开发机默认 profile | 专用 profile + 配置进仓库 |
| MCP 工具不存在 | headless 无 MCP 配置 | 用 `--patch` 或常驻覆盖层 |
