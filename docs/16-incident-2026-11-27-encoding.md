# 16 · 事故档：`live` 首次运行就红（2026-11-27）

> 状态：**已结** · 影响面：`live` 工作流 1 次失败、0 个用户、0 行业务代码改动
> 关联：`docs/09-issues.md` [**E11**](09-issues.md) · 修复 [`e48e8af`](https://github.com/tongriyaotxt/qed/commit/e48e8afec05b4a387c67e3d3b7d8ee833fbc0635) · 发布说明 [`CHANGELOG.md`](../CHANGELOG.md) 1.10.1
> 性质：**闸门自身的故障**，不是被闸门守的东西坏了

## 一、一句话

闸门把数字数对了（`headless 98 / tui 126`），崩的是**把它写出来**：Windows 上 stdout 是管道时
按 ANSI 代码页编码，`≈` 不在 `cp1252` 里，那次 `print` 抛 `UnicodeEncodeError`，退出码 1。

**红的不是被测对象，是测完之后那行 `print`。** 这一条区别决定了排障往哪儿看 ——
先看 traceback 的最后一帧，再看断言。

## 二、时间线（UTC，括号内为北京时间）

| 时间 | 事件 |
|---|---|
| 11-26 14:37 | `bad25bc` 推入（复盘文档），`ci` 绿 |
| 11-27 10:41:08（18:41） | `live` **run #1** 由 `schedule` 触发（cron 写的是 02:00 UTC，实际被队列拖到 10:41）；`windows-latest` · Python 3.13.17 |
| 10:41:11 | job 开始。`npm install -g @deepseek-ai/dsh@0.2.1-rc.2` 花了 **9 分 56 秒**（整个 job 20 分钟预算的一半） |
| 10:51:07 | 「版本与凭证补丁结构自检」通过 —— `credentials` / `authorization` 两条都还在，补丁有效 |
| 10:51:08 → 10:51:10 | 「文档漂移（包里条目数 vs docs/03）」开始，**2 秒后红** |
| 10:51:14 | run #1 `conclusion=failure`；唯一 annotation：`Process completed with exit code 1` |
| 11:02:51 | 修复 `e48e8af` 推送成功（23 个文件） |
| 11:02:54 → 11:03:58 | `ci` **8/8 job 绿**，含新增的「控制台编码闸门」步骤 |
| 11:03:01 → 11:12:39 | `live` **run #2**（手动 `workflow_dispatch`）`conclusion=success`，漂移步 `PASS` |

从红到绿：**1 小时 21 分**，其中 9 分 56 秒是 `npm install`，真正的定位时间约 10 分钟。

## 三、症状（原始日志，未删改）

```
真 dsh 协议冒烟  文档漂移（包里条目数 vs docs/03）  ##[group]Run python tests/check_docs_drift.py
shell: C:\Program Files\PowerShell\7\pwsh.EXE -command ". '{0}'"
Traceback (most recent call last):
  File "D:\a\qed\qed\tests\check_docs_drift.py", line 120, in <module>
    sys.exit(main())
  File "D:\a\qed\qed\tests\check_docs_drift.py", line 107, in main
    print("  %s  %-9s 文档 ≈%d  实测 %d  差 %d（容差 %d）"
          % ("PASS" if ok else "FAIL", profile, claimed[profile], actual, delta, tolerance))
  File "C:\hostedtoolcache\windows\Python\3.13.17\x64\Lib\encodings\cp1252.py", line 19, in encode
    return codecs.charmap_encode(input,self.errors,encoding_table)[0]
UnicodeEncodeError: 'charmap' codec can't encode characters in position 18-19: character maps to <undefined>
##[error]Process completed with exit code 1.
```

读法：`main()` 走到了第 107 行的 `print`，说明 **`count_entries()` 已经算完且没抛错** ——
数字是对的，崩在输出。位置 18-19 正是 `≈` 与紧随其后的空格落点。

## 四、根因（三层，少一层就修不对）

**1. 直接原因：`cp1252` 编不出 `≈`。**
`文档`、`实测`、`（容差）` 这些汉字同样编不出，只是 `≈` 排在最前，traceback 报的它。

**2. 为什么会用 `cp1252`：stdout 不是终端，是管道。**
Python 3 在 Windows 上取 `sys.stdout` 的编码时看 `_WindowsConsoleIO`/locale：交互式终端走
UTF-8 或代码页，而**重定向/管道**没有控制台可问，回落到 ANSI 代码页 —— 英文机 `cp1252`、
中文机 `cp936`。GitHub 的 runner 是英文机，`live.yml` 又把输出交给 pwsh 收集，于是必然踩中。
**同一条命令在开发机终端里永远是绿的**，这就是它躲过了本地测试的原因。

**3. 为什么偏偏是这个文件：守卫靠「记得写」。**
`tests/conformance.py`、`tests/test_client.py`、`tests/test_gateway.py` 早就有

```python
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
```

`tests/check_docs_drift.py` 是第 4 个，漏了。这与 `docs/09` 的 **A5 / B12**（"中文用户第一条
路径就炸"）是同一类问题，只是这次炸在 CI 上、炸在了一个「永远没人看」的 workflow 里。

## 五、处置

| 面 | 做了什么 |
|---|---|
| **修复** | 18 个会 `print` 非 ASCII 的入口补上同款守卫：`tests/*` 9 个、`python/qed/*` 5 个、`examples/*` 4 个。后两者是用户在 cmd / PowerShell 里直接敲的东西 —— `qed --help`、`qed demo`、`python examples/hello.py` 在中文 Windows 控制台上本来同样会崩（`✓` 不在 GBK 里） |
| **防复发** | 新增 [`tests/test_console_encoding.py`](../tests/test_console_encoding.py)，两把锁 + 两条元检查，并入 `ci.yml`：静态锁查「守卫在不在 `main()` 里、早不早于首行输出」；行为锁用 `PYTHONIOENCODING=cp1252` / `cp936` 真跑 [`check_docs_drift.py`](../tests/check_docs_drift.py) 与 `qed --help` |
| **文档** | `docs/06` 输出类加「症状 → 修法」；`docs/09` 记 E11（P1，计数 36→37）并把 E2 的「待首次线上运行确认」补成确认；`CHANGELOG` 1.10.1 |

补的不是第 19 个守卫，而是「第 19 个也不会被忘掉」——因为「记得写」已经失败过一次。

## 六、验证

| 项 | 修前 | 修后 |
|---|---|---|
| `tests/test_console_encoding.py` | **4/7**（复现同一条 `UnicodeEncodeError`，行号 107、position 18-19 全一致） | **7/7** |
| `PYTHONIOENCODING=cp1252 python tests/check_docs_drift.py` | exit 1 | **exit 0**，`PASS 98/98`、`PASS 126/126` |
| `PYTHONIOENCODING=gbk python tests/check_docs_drift.py` | exit 1 | **exit 0** |
| `qed demo` / `qed --help`（gbk、cp1252） | exit 1（`'gbk' codec can't encode '\u2713'`） | **exit 0** |
| `ci`（push `e48e8af`） | — | **8/8 job 绿**，含新闸门 |
| `live` run #2 | run #1 failure | **success**：`PASS headless 文档 ≈98 实测 98 差 0（容差 3）` |

线上那一行原文（run #2，11:12:35）：

```
  PASS  headless  文档 ≈98  实测 98  差 0（容差 3）
  SKIP  tui       dsh 退出码 1：... profile "dsh-tui" does not exist ...
```

`tui` 是 **SKIP 不是 PASS**：干净 runner 上没有 `dsh-tui` profile，脚本按设计「取不到就跳过、
退出码 0」。见下一节。

## 七、三条教训

1. **「红的」不等于「被测的东西坏了」。** 这次 98/98、126/126 全对。判据：先读 traceback 最后
   一帧，再看断言 —— 如果最后一帧在 `encodings/*.py` 里，问题在 I/O，不在被测逻辑。
2. **本地跑不出来，不算没问题。** 凡是「只在 CI 红」的，优先怀疑**平台默认值**：编码、路径分隔符、
   大小写、默认 shell。这次是编码，也是这个仓库第 3 次栽在「平台默认值」上（前两次见 A5 / A12）。
3. **同一个坑修过三次还会出现第四次。** 三个文件都写了守卫仍漏第四个 —— 说明当时修的是**症状**，
   没有修**机制**。没有闸门的约定等于没有约定。

## 八、留档边界（这次明确没做的事）

| 项 | 现状 | 为什么留着 |
|---|---|---|
| `tui` 的门禁 | 干净 runner 上 SKIP，每日 CI 实际只守 `headless` | 要它真守 tui，得先让 runner 有 `dsh-tui` profile；硬失败会让每日跑永远红 |
| 4 套测试未进 `ci.yml` | `test_broker` / `test_guard` / `test_verify` / `test_protocol_live` 只在本地跑（约 31 条断言），与 `docs/15` 里「12 项 Python 闸门」的说法不一致 | 需要先在 ubuntu 上确认它们零依赖可跑，属于下一轮的事 |
| 版本号 | `python/pyproject.toml` 还是 `1.9.0`，`CHANGELOG` 已到 `1.10.1` | 发布动作，不能顺手改 |
| 推送通道 | 本机 `github.com:443` 被重置，`git clone/ls-remote` 不通，只能走 `gh` 的 Git Data API（blob→tree→commit→ref） | 与 `docs/15` R9 同一条限制；它不影响仓库内容，只影响下次推送的手法 |
