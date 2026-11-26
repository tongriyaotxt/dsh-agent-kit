# host —— 极简宿主（v0.1）

一个窗口，列出 `~/apps/*`，点开就能跑。**一个进程管所有 app**，不搞「每个工具一个 exe」。

```
qed.exe                     # 默认扫 ~/apps
qed.exe -root D:\apps       # 指定根目录
qed.exe -cred configs/cred.patch.yml   # 需要时补凭证 patch
```

## 实测数据（2026-11-26 · Win11 26300）

| 指标 | 值 |
|---|---|
| 磁盘 | **3.4 MB**（Go + 系统 WebView2，不打包 Chromium） |
| 冷启动到窗口 | **500–705 ms** |
| 主进程内存 | 26.8 MB |
| 附带的 WebView2 进程 | +4 个 / +200 MB（Chromium 的物理账单，换壳改不掉） |
| 一次完整对话（6 个工具调用） | 11.7 s |

## 架构

```
qed.exe（.NET 风格的 WebView2 窗口 + Go）
  ├─ 扫描 ~/apps/*/app.json        → 左侧列表
  ├─ spawn: cmd /c dsh.cmd --profile <app> --json -
  │     cwd = app 目录            ← 写边界收在这里
  │     DSH_PERMISSION_MODE = app.json 的 sandbox
  │     CREATE_NO_WINDOW         ← 关键，见下
  └─ NDJSON 逐行 → window.__onEvent(JSON) → 渲染
```

## 凭证：broker 做了什么，没做什么

宿主启动时会：

1. 从环境变量或 `~/.dsh/.credentials.yaml` 读真 key（**只放内存**）
2. 起本地反代 `127.0.0.1:<随机端口>`，转发时注入 `x-api-key`
3. 写一份**只有占位符**的凭据文件 + patch，让 dsh 走 broker

日志会显示 `broker: POST /anthropic/v1/messages`。

> ⚠️ **它只挡网络出口，不挡文件读取。**
> 实测：让 agent 去读 `~/.dsh/.credentials.yaml` 的真实路径，它照样拿到了真 key
> （`credentials.path` 只改 dsh 服务从哪里取凭证，不改文件系统）。
> 详见 `docs/10-permission-adr.md` 第六节。
>
> 要真正关掉，得把 key 从文件里移走 —— 那是 v0.2 的「宿主密钥库」，见同节。

## 三个踩过的坑（都已修，都有注释）

**① GUI 子系统没有控制台 → 工具进程挂死**
`-H windowsgui` 编译出的宿主没有 console，`cmd /c dsh` 里再 spawn 的 pwsh 工具会挂住。
同一任务在「有控制台」的 Python 里 11.7 s 跑完，在 GUI 宿主里 60 s 还卡在 thinking。
修法：`CREATE_NO_WINDOW`（有 console 但不弹窗），见 `proc_windows.go`。

**② stdout 先 EOF、进程后退出 → UI 永远收不到结束**
`cmd.Wait()` 会等所有写端关闭；而 dsh 输出完 `final` 就关 stdout，node 进程还活一会儿。
修法：**EOF 即收尾**——读到 EOF 后最多等 1.5 s 拿退出码，然后立刻发 `_end`，收尸放到异步。

**③ taskkill 阻塞收尾**
把 `taskkill /T /F` 放在发 `_end` 之前，它慢一点 UI 就转圈。
修法：先 `emit(_end)`，再异步清理。

## 诊断

GUI 宿主没有控制台，出问题只能靠文件：

```powershell
$env:QED_DEBUG = '1'
.\qed.exe
Get-Content $env:TEMP\qed.log -Tail 20
```

## 还没做的

- 多窗口 / 应用坞（现在一次只跑一个任务，第二个会提示「已有任务在跑」）
- 把 `ProfileGuard` 接进 spawn 前后（API 已就绪，见 `docs/10`）
- 把 broker 接进 UI（现在靠 `QED_BROKER_URL` 环境变量）
- 空状态三个示例任务现在只是填进输入框，没有一键直达
