#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自证 runner —— 把「AI 说它做对了」变成一句可复现的 `6/6 passed`。

三件事，缺一不算自证：

    1. 用例跑绿       cases.json 里每条 input→expect 都要成立
    2. 变异被抓       往 logic 里注入错误，用例必须变红（证明断言不是摆设）
    3. ctx 受限       执行 AI 写的代码时，只给 app 目录内的读写，不给网络/子进程

第 2 条是这套东西和「普通单元测试」的全部区别：没有它，AI 写一个永远绿的断言
就自证成功了。详见 docs/09-issues.md E1 与 docs/10-permission-adr.md。

用法：
    python -m qed.verify --app ~/apps/发票统计
    python -m qed.verify --app ~/apps/发票统计 --mutate-only
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

CASES_FILE = "cases.json"
LOGIC_FILE = "logic.py"
FLOAT_TOL = 1e-6


# ---------------------------------------------------------------- 归一化比对
def normalize(value: Any) -> Any:
    """递归归一：dict 键排序、float 收敛到 1e-6、tuple→list。"""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def same(a: Any, b: Any) -> bool:
    return normalize(a) == normalize(b)


# ---------------------------------------------------------------- 受限 ctx
class CtxError(RuntimeError):
    pass


class LocalCtx:
    """自证时给 logic 的最小上下文：只有 app 目录内的读写。

    刻意不提供网络、子进程、环境变量读取 —— 自证要能在无人看守时跑
    AI 生成的代码，能力必须收到最小。
    """

    def __init__(self, app_dir: str, allowed_subdirs=("data", "out", "state")):
        self.app_dir = os.path.realpath(app_dir)
        self.allowed = tuple(os.path.realpath(os.path.join(self.app_dir, d))
                             for d in allowed_subdirs)
        self.log_lines: List[str] = []

    def _resolve(self, rel: str, must_exist: bool = False) -> str:
        path = os.path.realpath(os.path.join(self.app_dir, rel))
        if not any(path == a or path.startswith(a + os.sep) for a in self.allowed):
            raise CtxError("越界访问: %r（只允许 data/ out/ state/）" % rel)
        if must_exist and not os.path.exists(path):
            raise CtxError("文件不存在: %r" % rel)
        return path

    def read(self, rel: str) -> str:
        with open(self._resolve(rel, must_exist=True), encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()

    def write(self, rel: str, content: str) -> None:
        path = self._resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

    def exists(self, rel: str) -> bool:
        try:
            return os.path.exists(self._resolve(rel))
        except CtxError:
            return False

    def log(self, message: str) -> None:
        self.log_lines.append(str(message))


# ---------------------------------------------------------------- 加载
@dataclass
class Report:
    app: str = ""
    passed: int = 0
    total: int = 0
    failures: List[Tuple[str, str]] = field(default_factory=list)   # (case_id, 说明)
    duration: float = 0.0
    mutation_caught: int = 0
    mutation_total: int = 0
    mutation_survivors: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def green(self) -> bool:
        return self.error is None and self.passed == self.total and self.total > 0

    @property
    def verified(self) -> bool:
        """全绿 + 变异全抓 = 自证成立。"""
        return (self.green and self.mutation_total > 0
                and self.mutation_caught == self.mutation_total)

    def summary(self) -> str:
        if self.error:
            return "自证失败：%s" % self.error
        head = "%d/%d 用例通过" % (self.passed, self.total)
        if self.mutation_total:
            head += " · 变异 %d/%d 被抓" % (self.mutation_caught, self.mutation_total)
        return head


def load_cases(app_dir: str, name: str = CASES_FILE) -> List[Dict[str, Any]]:
    path = os.path.join(app_dir, name)
    if not os.path.exists(path):
        raise CtxError("缺 %s：没有断言就不叫自证" % name)
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise CtxError("%s 里没有 cases[]" % name)
    for i, case in enumerate(cases):
        if "input" not in case:
            raise CtxError("第 %d 条用例缺 input" % (i + 1))
        if "expect" not in case:
            raise CtxError("第 %d 条用例缺 expect" % (i + 1))
    return cases


def load_logic(app_dir: str, filename: str = LOGIC_FILE, module_tag: str = "logic"):
    path = os.path.join(app_dir, filename)
    if not os.path.exists(path):
        raise CtxError("缺 %s" % filename)
    spec = importlib.util.spec_from_file_location("qed_%s_%d" % (module_tag, time.time_ns()), path)
    if spec is None or spec.loader is None:
        raise CtxError("无法加载 %s" % filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise CtxError("%s 里没有 run(input, ctx)" % filename)
    return module


# ---------------------------------------------------------------- 跑用例
def run_cases(app_dir: str, cases_file: str = CASES_FILE,
              logic_file: str = LOGIC_FILE) -> Report:
    report = Report(app=os.path.basename(os.path.realpath(app_dir)))
    started = time.time()
    try:
        cases = load_cases(app_dir, cases_file)
        module = load_logic(app_dir, logic_file)
    except Exception as exc:                       # noqa: BLE001 自证要把原因说清楚
        report.error = str(exc)
        return report

    ctx = LocalCtx(app_dir)
    report.total = len(cases)
    for case in cases:
        cid = str(case.get("id") or ("case-%d" % (len(report.failures) + report.passed + 1)))
        try:
            got = module.run(case["input"], ctx)
        except Exception as exc:                   # noqa: BLE001
            detail = "%s: %s" % (type(exc).__name__, exc)
            report.failures.append((cid, "抛异常 " + detail))
            continue
        if same(got, case["expect"]):
            report.passed += 1
        else:
            report.failures.append((
                cid,
                "期望 %s，实际 %s" % (
                    json.dumps(normalize(case["expect"]), ensure_ascii=False, sort_keys=True)[:200],
                    json.dumps(normalize(got), ensure_ascii=False, sort_keys=True)[:200],
                ),
            ))
    report.duration = time.time() - started
    return report


# ---------------------------------------------------------------- 变异测试
#: (标签, 变换函数)。变换只改一处，且必须仍是合法 Python。
#: 前几条是语法模式匹配，后几条是「几乎必然改变行为」的通用变异 ——
#: 简单实现（比如只有一行 sum）往往没有比较符/布尔表达式，靠通用变异兜底。
def _drop_last_return(src: str) -> str:
    idx = src.rfind("return ")
    if idx < 0:
        return src
    end = src.find("\n", idx)
    if end < 0:
        end = len(src)
    return src[:idx] + "return None" + src[end:]


def _bump_first_number(src: str) -> str:
    import re as _re
    m = _re.search(r"(?<![\w.])(\d+)(?![\w.])", src)
    if not m:
        return src
    value = str(int(m.group(1)) + 1)
    return src[:m.start()] + value + src[m.end():]


MUTATORS: List[Tuple[str, Callable[[str], str]]] = [
    ("最后一个 return → None", _drop_last_return),
    ("第一个数字字面量 +1", _bump_first_number),
    ("算术 + → -", lambda s: s.replace(" + ", " - ", 1)),
    ("算术 - → +", lambda s: s.replace(" - ", " + ", 1)),
    ("比较 == → !=", lambda s: s.replace(" == ", " != ", 1)),
    ("比较 > → >=", lambda s: s.replace(" > ", " >= ", 1)),
    ("布尔 and → or", lambda s: s.replace(" and ", " or ", 1)),
    ("True → False", lambda s: s.replace("True", "False", 1)),
    ("min → max", lambda s: s.replace("min(", "max(", 1)),
    ("round → int", lambda s: s.replace("round(", "int(", 1)),
    ("len → 恒等 0", lambda s: s.replace("len(", "(lambda _x: 0)(", 1)),
]


def _mutate_source(src: str) -> List[Tuple[str, str]]:
    """返回 [(标签, 变异后源码)]，只保留真正改变且语法合法的。"""
    out: List[Tuple[str, str]] = []
    for label, fn in MUTATORS:
        try:
            mutated = fn(src)
        except Exception:                          # noqa: BLE001
            continue
        if mutated == src:
            continue
        try:
            compile(mutated, "<mutant>", "exec")
        except SyntaxError:
            continue
        out.append((label, mutated))
    return out


def run_mutations(app_dir: str, cases_file: str = CASES_FILE,
                  logic_file: str = LOGIC_FILE) -> Report:
    """对 logic 注入变异，要求每条用例集都能变红。"""
    report = run_cases(app_dir, cases_file, logic_file)
    if not report.green:
        # 用例红是正常结果（逻辑还没写/写错了），不是环境错误 —— 不要设 error，
        # 否则 CLI 会以 exit 2 上报「自证失败」，把「红」和「跑不起来」混为一谈。
        return report

    src_path = os.path.join(app_dir, logic_file)
    with open(src_path, encoding="utf-8-sig") as fh:
        original = fh.read()

    candidates = _mutate_source(original)
    if not candidates:
        report.error = "找不到可用的变异点：logic 里没有可替换的算术/比较/布尔表达式"
        return report

    report.mutation_total = len(candidates)
    for label, mutated in candidates:
        tmp_name = "_mutant_%d.py" % (abs(hash(label)) % 100000)
        tmp_path = os.path.join(app_dir, tmp_name)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(mutated)
            sub = run_cases(app_dir, cases_file, tmp_name)
            if sub.passed == sub.total and sub.total > 0:
                report.mutation_survivors.append(label + "（用例没抓到，断言太弱）")
            else:
                report.mutation_caught += 1
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    return report


# ---------------------------------------------------------------- CLI
def _fmt(report: Report) -> str:
    rows = ["%s · %s" % (report.app or "app", report.summary())]
    for cid, detail in report.failures:
        rows.append("  FAIL %-10s %s" % (cid, detail))
    if report.mutation_survivors:
        rows.append("  逃逸的变异：")
        for label in report.mutation_survivors:
            rows.append("    - " + label)
    if report.verified:
        rows.append("  自证成立 ✓")
    elif report.mutation_total:
        survivors = report.mutation_total - report.mutation_caught
        rows.append("  %s：%d/%d 变异逃逸 —— 用例绿了，但它挡不住做错"
                    % ("自证不成立", survivors, report.mutation_total))
    elif report.green:
        rows.append("  用例全绿但未做变异检查（加 --mutate）")
    return "\n".join(rows)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    parser = argparse.ArgumentParser(description="app 自证 runner")
    parser.add_argument("--app", required=True, help="app 目录（含 cases.json / logic.py）")
    parser.add_argument("--cases", default=CASES_FILE)
    parser.add_argument("--logic", default=LOGIC_FILE)
    parser.add_argument("--mutate", action="store_true", help="用例全绿后跑变异检查")
    parser.add_argument("--mutate-only", action="store_true", help="等同 --mutate")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    app_dir = os.path.abspath(os.path.expanduser(args.app))
    if not os.path.isdir(app_dir):
        print("目录不存在: %s" % app_dir, file=sys.stderr)
        return 2

    do_mutate = args.mutate or args.mutate_only
    report = (run_mutations(app_dir, args.cases, args.logic)
              if do_mutate else run_cases(app_dir, args.cases, args.logic))

    if args.json:
        print(json.dumps({
            "app": report.app,
            "passed": report.passed, "total": report.total,
            "failures": report.failures,
            "mutation": {"caught": report.mutation_caught,
                         "total": report.mutation_total,
                         "survivors": report.mutation_survivors},
            "verified": report.verified, "error": report.error,
        }, ensure_ascii=False, indent=2))
    else:
        print(_fmt(report))

    if report.error:
        return 2
    if not report.green:
        return 1
    if do_mutate and not report.verified:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
