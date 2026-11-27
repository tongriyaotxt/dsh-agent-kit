#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""控制台编码闸门 —— 让「print 一行中文就崩」这类问题不能再回来。

背景（2026-11-27 · docs/09-issues.md E11）：
    live.yml 在 windows-latest 上红了，红的不是断言，是最后一行 `print`：

        UnicodeEncodeError: 'charmap' codec can't encode characters in
        position 18-19: character maps to <undefined>

    `check_docs_drift.py` 数出来的数字全对（headless 98 / tui 126），崩掉的只是
    「把 `文档 ≈98 实测 98` 这行字写出去」—— Windows 上 Python 把**重定向/管道**的
    stdout 默认按 ANSI 代码页编码（英文机 cp1252、中文机 cp936），`≈` 和汉字都不在里面。

    本地看不见：开发机的终端早被设成 UTF-8；只有 CI、`| more`、`> out.txt` 才暴露。
    这与 A5 / B12「中文用户第一条路径就炸」是同一类问题，只是这次炸在 CI 上。

两把锁：
    1. **静态锁**：凡是会 print 非 ASCII 的脚本，必须在第一次输出之前把 stdout/stderr
       reconfigure 成 utf-8（仓库既有写法，见 tests/test_client.py）；
    2. **行为锁**：用 `PYTHONIOENCODING=cp1252` / `cp936` 真跑一遍，退出码必须 0、
       输出里不能有 UnicodeEncodeError。

为什么两把都要：静态锁只能防「忘了写」，行为锁才能证明「写了有用」。
`python/dsh_agent_kit` 那套旧包不在扫描范围 —— pyproject 只打包 `qed`，它是历史遗留。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 会被 CI / 用户直接执行的目录；只有这些文件值得要求守卫
SCOPE = ["tests", os.path.join("python", "qed"), "examples"]

# 仓库既有写法（tests/test_client.py:297-299）：reconfigure 到 utf-8
GUARD = re.compile(r"reconfigure\(\s*encoding=[\"']utf-8[\"']")

NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _prints_non_ascii(source: str) -> list:
    """返回「print 的字面量里有非 ASCII」的行号。docstring 不计。"""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "print":
            continue
        for arg in node.args:
            for sub in ast.walk(arg):
                chunks = []
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    chunks.append(sub.value)
                elif isinstance(sub, ast.JoinedStr):        # f-string
                    for part in sub.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            chunks.append(part.value)
                if any(NON_ASCII.search(c) for c in chunks):
                    hits.append(node.lineno)
                    break
    return sorted(set(hits))


def _main_range(source: str):
    """模块级 def main() 的行范围；没有就是 None。"""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node.lineno, node.end_lineno
    return None


def analyse(source: str):
    """返回 (非 ASCII print 行号, 是否缺守卫)。

    判据不是「文件里出现过 reconfigure」，而是「入口 main() 在它自己输出之前就切好了」：
      · 守卫必须落在 main() 的函数体里；
      · main() 里第一条非 ASCII 的 print 必须在守卫之后；
    非 ASCII print 写在 helper 里（如 conformance.py 的 `_ok`）时只管它在不在 main() 里，
    因为那条 helper 只可能被 main() 调起来。
    """
    hits = _prints_non_ascii(source)
    if not hits:
        return hits, False

    match = GUARD.search(source)
    guard_line = source[: match.start()].count("\n") + 1 if match else None
    span = _main_range(source)

    if span is None:
        return hits, guard_line is None or guard_line > hits[0]

    start, end = span
    if guard_line is None or not (start < guard_line <= end):
        return hits, True
    inside = [line for line in hits if start < line <= end]
    return hits, bool(inside) and guard_line > inside[0]


def analyse_file(path: str):
    with open(path, encoding="utf-8") as fh:
        return analyse(fh.read())


def _iter_scripts():
    for target in SCOPE:
        base = os.path.join(ROOT, target)
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _run(code_or_path, encoding: str, is_code: bool = False):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = encoding
    env.pop("PYTHONUTF8", None)            # 别让 UTF-8 模式把要测的东西遮掉
    args = [sys.executable]
    args += ["-c", code_or_path] if is_code else [code_or_path]
    return subprocess.run(
        args, cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )


# ---------------------------------------------------------------- 静态锁
def test_every_printer_guards_stdout():
    bad = []
    for path in _iter_scripts():
        hits, needs = analyse_file(path)
        if needs:
            bad.append("%s  (print 非 ASCII 于行 %s)" % (os.path.relpath(path, ROOT), hits))
    assert not bad, (
        "这些脚本会在非 UTF-8 控制台上抛 UnicodeEncodeError，"
        "请在 main() 开头 reconfigure 到 utf-8：\n  " + "\n  ".join(bad))


def test_scope_is_not_empty():
    """闸门自己也要有底数：扫到 0 个文件说明路径写错了，那是假绿。"""
    scripts = list(_iter_scripts())
    assert len(scripts) >= 15, "只扫到 %d 个脚本，SCOPE 可能失效" % len(scripts)


# ---------------------------------------------------------------- 行为锁
def test_drift_checker_survives_cp1252():
    """2026-11-27 线上就是这条挂的：数字全对，写出去那一步抛 UnicodeEncodeError。"""
    proc = _run(os.path.join("tests", "check_docs_drift.py"), "cp1252")
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "UnicodeEncodeError" not in combined, "cp1252 下仍然崩：\n" + combined[-800:]
    assert proc.returncode == 0, "退出码 %d：\n%s" % (proc.returncode, combined[-800:])
    assert ("PASS" in combined) or ("SKIP" in combined), "没跑到判定行：\n" + combined[-800:]


def test_cli_usage_survives_ansi_codepages():
    """`qed --help` 全是中文：英文机（cp1252）与中文机（cp936）都得能打出来。"""
    code = ("import sys;sys.path.insert(0, 'python');"
            "from qed.cli import main;sys.exit(main(['--help']))")
    for encoding in ("cp1252", "cp936"):
        proc = _run(code, encoding, is_code=True)
        combined = (proc.stdout or "") + (proc.stderr or "")
        assert "UnicodeEncodeError" not in combined, "%s 下崩：\n%s" % (encoding, combined[-600:])
        assert proc.returncode == 0, "%s 下退出码 %d：\n%s" % (
            encoding, proc.returncode, combined[-600:])
        assert "用法:" in combined and "qed scaffold" in combined, \
            "%s 下没打出用法：\n%s" % (encoding, combined[-600:])


# ---------------------------------------------------------------- 元检查
def test_detector_catches_missing_guard():
    """测谎仪要能被测谎：不加守卫的样例必须被判为「缺守卫」。"""
    hits, needs = analyse('def main():\n    print("中文 ok")\n')
    assert hits == [2] and needs, "检测器漏了无守卫样例"


def test_detector_accepts_guarded_sample():
    guarded = (
        "def main():\n"
        "    for stream in (sys.stdout, sys.stderr):\n"
        "        if hasattr(stream, \"reconfigure\"):\n"
        "            stream.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n"
        "    print(\"中文 ok\")\n"
    )
    hits, needs = analyse(guarded)
    assert hits == [5] and not needs, "检测器误报已加守卫的样例"


def test_detector_flags_guard_placed_too_late():
    """守卫写在 print 之后等于没写 —— 这种「看起来加了」也要判红。"""
    late = (
        "def main():\n"
        "    print(\"中文 ok\")\n"
        "    for stream in (sys.stdout,):\n"
        "        stream.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n"
    )
    _, needs = analyse(late)
    assert needs, "检测器没有检查守卫的顺序"


# ------------------------------------------------------------------ 收尾
CASES = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except AssertionError as exc:
            print("  FAIL  %s  <- %s" % (name, exc))
            failed += 1
        except Exception as exc:                    # noqa: BLE001
            print("  FAIL  %s  <- %s: %s" % (name, type(exc).__name__, exc))
            failed += 1
    print("\n%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
