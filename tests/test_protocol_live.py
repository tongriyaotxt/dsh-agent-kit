#!/usr/bin/env python3
"""协议回归 —— 拿**真实抓包**盯住 schema 与文档，防止再次与 dsh 实际输出漂移。

为什么需要这个（2026-11-26 实测发现）：
    docs/02 与 spec 早期都写「工具事件是单一 type: "tool"」，
    但 dsh 0.2.1-rc.2 实际发的是 tool_call + tool_result 两条。
    按旧写法写的 UI 永远不会命中 type === "tool"，工具调用会整段静默消失。
    fake_dsh 当时也在发假形状 —— 测试全绿，真实场景是盲的。
    这就是「用自己造的假数据自洽」的典型失败。

做法：
    tests/fixtures/live_tool.jsonl 是 2026-11-26 从真 dsh 抓的完整事件流（13 条），
    提交进仓库当作协议快照。本文件对它断言：
      1. 通过全部不变量 + schema 校验
      2. 必须含 tool_call / tool_result，且 callId 能配对
      3. **不许出现 type: "tool"**（旧名已废弃，出现即为漂移）
      4. schema 里这两个定义仍在，且 required 覆盖约定字段

零依赖，可被 pytest 收集，也可直接跑。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from conformance import load_schema, stream_invariants  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "live_tool.jsonl")
SCHEMA = load_schema()


def load_fixture():
    with open(FIXTURE, encoding="utf-8-sig") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ 用例
def test_fixture_is_real_capture():
    events = load_fixture()
    assert len(events) >= 10, "抓包样本太少（%d 条），不足以覆盖一轮完整对话" % len(events)
    assert events[0]["type"] == "session"
    assert events[-1]["type"] == "final"


def test_fixture_passes_all_invariants():
    found = stream_invariants(load_fixture(), SCHEMA)
    bad = {k: v for k, v in found.items() if v}
    assert not bad, "真实抓包不满足不变量：%r" % (bad,)


def test_fixture_contains_tool_pair():
    events = load_fixture()
    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    assert calls, "抓包里没有 tool_call —— 采集任务或 dsh 行为变了"
    assert results, "抓包里没有 tool_result"
    assert calls[0]["callId"] == results[0]["callId"], "callId 必须配对"
    assert calls[0]["tool"], "tool_call 必须有 tool 名"


def test_no_legacy_tool_type():
    """旧名 `tool` 是协议漂移的指纹：一旦出现，说明有人按过期文档实现。"""
    legacy = [e for e in load_fixture() if e.get("type") == "tool"]
    assert not legacy, "出现已废弃的 type: 'tool'，schema/文档再次漂移"


def test_schema_defines_both_tool_types():
    defs = SCHEMA["$defs"]
    for name in ("tool_call", "tool_result"):
        assert name in defs, "schema 缺 %s 定义" % name
    assert set(defs["tool_call"]["required"]) >= {"type", "callId", "tool"}
    assert set(defs["tool_result"]["required"]) >= {"type", "callId", "status"}
    assert defs["tool"]["deprecated"] is True, "旧 tool 定义应标 deprecated"
    refs = json.dumps(SCHEMA["oneOf"])
    for name in ("tool_call", "tool_result"):
        assert "#/$defs/" + name in refs, "oneOf 里缺 %s" % name


def test_docs_no_longer_claim_single_tool():
    with open(os.path.join(ROOT, "docs", "02-protocol.md"), encoding="utf-8-sig") as fh:
        doc = fh.read()
    assert "`tool_call`" in doc and "`tool_result`" in doc, "docs/02 没写新的两种类型"
    assert "| `tool` | 版本相关" not in doc, "docs/02 仍在声称单一 tool 类型"


CASES = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except AssertionError as exc:
            print("  FAIL  %s  <- %s" % (name, exc))
            failed += 1
        except Exception as exc:                  # noqa: BLE001
            print("  FAIL  %s  <- %s: %s" % (name, type(exc).__name__, exc))
            failed += 1
    print("\n%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


try:
    import pytest  # noqa: F401

    @pytest.mark.parametrize("label,fn", CASES, ids=[c[0] for c in CASES])
    def test_protocol_live(label, fn):
        fn()
except ImportError:
    pass


if __name__ == "__main__":
    sys.exit(main())
