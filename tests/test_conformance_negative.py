#!/usr/bin/env python3
"""闸门的闸门 —— 验证 conformance.py 的每条不变量**都能被违反**。

背景（docs/09-issues.md E1）：
    在 conformance.py 里把 `found["schema"] = schema_check(...)` 改成 `[]`，
    23 个测试仍全绿、conformance 自己还打印 `ALL PASS`。
    测谎仪没被校准，整条信任链就悬空了。

本文件的两条规则：
    1. 每条负样本不只断言「红了」，还要断言「红在对的类别」——
       防止一个把所有事件都判红的糟糕实现蒙混过关；
    2. 必须有一条正向样本（好流必须通过）和一条「未知 type 必须被容忍」，
       防止把闸门焊死成永远报错。

零依赖：可被 pytest 收集，也可 `python tests/test_conformance_negative.py` 直接跑。
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conformance import load_schema, schema_check, stream_invariants  # noqa: E402

SCHEMA = load_schema()

# 一条完全合规的最小事件流（与 docs/02 的实测序列一致）
GOOD = [
    {"type": "session", "sessionId": "session-abc123", "cwd": "/tmp/proj"},
    {"type": "status", "phase": "turn_start", "turn": 1},
    {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
    {"type": "thinking", "text": ""},
    {"type": "text", "text": "OK"},
    {"type": "status", "phase": "step_end", "turn": 1, "step": 1,
     "usage": {"inputTokens": 169, "outputTokens": 2,
               "cacheReadTokens": 0, "totalTokens": 171}},
    {"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}},
    {"type": "final", "text": "OK"},
]


def verdict(events):
    """True = 通过。任何类别有违规即为 False。"""
    found = stream_invariants(events, SCHEMA)
    return all(not v for v in found.values())


def violations(events, category):
    return stream_invariants(events, SCHEMA)[category]


def expect_violation(events, category, label):
    """坏的流必须被判红，且红在指定类别上。"""
    found = stream_invariants(events, SCHEMA)
    if all(not v for v in found.values()):
        return False, "%s：整条流被判为通过（闸门哑了）" % label
    if not found[category]:
        hit = {k: v for k, v in found.items() if v}
        return False, "%s：红了但不在 %r 类别，实际红在 %r" % (label, category, hit)
    return True, ""


def expect_pass(events, label):
    found = stream_invariants(events, SCHEMA)
    bad = {k: v for k, v in found.items() if v}
    if bad:
        return False, "%s：合规流被判红 %r" % (label, bad)
    return True, ""


def mutate(fn):
    """基于 GOOD 造一个坏流，不污染原数据。"""
    events = copy.deepcopy(GOOD)
    fn(events)
    return events


# ---------------------------------------------------------------- 负样本

def case_empty_stream():
    return expect_violation([], "type", "空事件流")


def case_first_not_session():
    def f(ev):
        ev[0] = {"type": "boot", "sessionId": "x", "cwd": "/tmp"}
    return expect_violation(mutate(f), "session", "首事件不是 session")


def case_session_id_blank():
    def f(ev):
        ev[0]["sessionId"] = "   "
    return expect_violation(mutate(f), "session", "sessionId 全是空白")


def case_missing_final():
    def f(ev):
        ev.pop()                                   # 删掉 final
    return expect_violation(mutate(f), "final", "缺 final")


def case_double_final():
    def f(ev):
        ev.append({"type": "final", "text": "OK"})
    return expect_violation(mutate(f), "final", "两条 final")


def case_missing_turn_end():
    def f(ev):
        ev[:] = [e for e in ev if not (e.get("type") == "status"
                                       and e.get("phase") == "turn_end")]
    return expect_violation(mutate(f), "turn_end", "缺 status(turn_end)")


def case_turn_end_no_reason():
    def f(ev):
        for e in ev:
            if e.get("type") == "status" and e.get("phase") == "turn_end":
                e.pop("reason", None)
    return expect_violation(mutate(f), "turn_end", "turn_end 缺 reason")


def case_no_usage():
    def f(ev):
        for e in ev:
            e.pop("usage", None)
    return expect_violation(mutate(f), "usage", "全流没有任何 usage")


def case_negative_usage():
    def f(ev):
        for e in ev:
            if "usage" in e:
                e["usage"]["inputTokens"] = -1
    return expect_violation(mutate(f), "schema", "usage 出现负数")


def case_text_event_missing_field():
    def f(ev):
        for e in ev:
            if e.get("type") == "text":
                e.pop("text", None)
    return expect_violation(mutate(f), "schema", "text 事件缺 text 字段")


def case_non_object_line():
    def f(ev):
        ev.insert(2, "这不是 JSON 对象")
    return expect_violation(mutate(f), "non_object", "混入非对象行")


def case_missing_type():
    def f(ev):
        ev.insert(3, {"text": "没有 type 字段"})
    return expect_violation(mutate(f), "type", "事件缺 type")


def case_tool_call_missing_callid():
    """工具事件的两条是 UI 渲染工具调用的唯一依据，缺字段必须红。"""
    def f(ev):
        ev.insert(4, {"type": "tool_call", "tool": "pwsh", "input": {}})
    return expect_violation(mutate(f), "schema", "tool_call 缺 callId")


def case_tool_result_missing_status():
    def f(ev):
        ev.insert(4, {"type": "tool_result", "callId": "call_1"})
    return expect_violation(mutate(f), "schema", "tool_result 缺 status")


# ---------------------------------------------------------------- 正向样本

def case_good_stream_must_pass():
    return expect_pass(GOOD, "合规最小流")


def case_unknown_type_tolerated():
    """协议前向兼容：未知 type 必须被忽略，不能判红。"""
    def f(ev):
        ev.insert(4, {"type": "future_event_v9", "whatever": [1, 2, 3]})
    return expect_pass(mutate(f), "未知 type 必须被容忍")


def case_local_synth_events_tolerated():
    """客户端本地合成的 _raw / error 不属于 dsh 协议，必须被容忍。"""
    def f(ev):
        ev.insert(1, {"type": "_raw", "text": "一行不是 JSON 的 stdout"})
        ev.insert(2, {"type": "error", "message": "timeout 900s"})
    return expect_pass(mutate(f), "_raw / error 必须被容忍")


# ---------------------------------------------------------------- 元检查

def case_schema_check_is_live():
    """直接打 schema_check：它必须真的能报出问题。

    这是对变异 C 的定点防护：任何让 schema_check 恒返回 [] 的改动都会在这里红。
    """
    bad = [{"type": "session", "sessionId": "s"},
           {"type": "final", "text": 123}]          # final.text 应为 string
    problems = schema_check(bad, SCHEMA)
    if not problems:
        return False, "schema_check 对明显错误（final.text 为整数）返回空问题列表"
    return True, ""


CASES = [
    # 负样本
    ("空事件流", case_empty_stream),
    ("首事件非 session", case_first_not_session),
    ("sessionId 空白", case_session_id_blank),
    ("缺 final", case_missing_final),
    ("两条 final", case_double_final),
    ("缺 turn_end", case_missing_turn_end),
    ("turn_end 缺 reason", case_turn_end_no_reason),
    ("全流无 usage", case_no_usage),
    ("usage 负数", case_negative_usage),
    ("text 缺字段", case_text_event_missing_field),
    ("非对象行", case_non_object_line),
    ("事件缺 type", case_missing_type),
    ("tool_call 缺 callId", case_tool_call_missing_callid),
    ("tool_result 缺 status", case_tool_result_missing_status),
    # 正向
    ("合规流必须通过", case_good_stream_must_pass),
    ("未知 type 容忍", case_unknown_type_tolerated),
    ("_raw/error 容忍", case_local_synth_events_tolerated),
    # 元
    ("schema_check 是活的", case_schema_check_is_live),
]


def main():
    failed = 0
    for label, fn in CASES:
        ok, why = fn()
        print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else "  <- " + why))
        if not ok:
            failed += 1
    total = len(CASES)
    print("\n%d/%d 通过" % (total - failed, total))
    if failed:
        print("闸门不可信：conformance 的不变量存在无法被违反的条目。")
    return 1 if failed else 0


# pytest 入口：参数化，让 `pytest tests/` 也能覆盖
try:
    import pytest  # noqa: F401

    @pytest.mark.parametrize("label,fn", CASES, ids=[c[0] for c in CASES])
    def test_conformance_gate(label, fn):
        ok, why = fn()
        assert ok, "%s: %s" % (label, why)
except ImportError:      # 没有 pytest 时仍可独立运行
    pass


if __name__ == "__main__":
    sys.exit(main())
