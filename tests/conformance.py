#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一致性测试：校验事件流是否满足协议的三条不变量。

    python tests/conformance.py                # 离线：验证 fixtures + 一个假 dsh 会话
    python tests/conformance.py --live         # 真实 dsh：验协议 + 会话续接（会消耗 token）
    python tests/conformance.py --live --profile headless --patch configs/mcp.patch.yml

不变量（docs/02-protocol.md）：

1. 首行是 session，sessionId 非空；
2. 正常结束有 final，且 status(turn_end).reason 存在；
3. 每行都是合法 JSON 对象，带非空 type；退出码 0。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from dsh_agent_kit import DshClient, DshError  # noqa: E402

SCHEMA_PATH = os.path.join(ROOT, "spec", "events.schema.json")
FIXTURE = os.path.join(HERE, "fixtures", "turn.jsonl")

_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _ok(cond: bool, label: str) -> bool:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    return cond


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _check_type(value, want, where: str, problems: list) -> None:
    kinds = want if isinstance(want, list) else [want]
    if not any(isinstance(value, _TYPES[k]) for k in kinds if k in _TYPES):
        problems.append("%s: 期望 %s，实际 %s" % (where, kinds, type(value).__name__))


def schema_check(events, schema) -> list:
    """逐事件做最小 schema 校验（未知 type 按前向兼容忽略）。"""
    defs = schema["$defs"]
    problems = []
    for index, ev in enumerate(events):
        kind = ev.get("type")
        if kind in ("_raw", "error"):
            continue
        spec = defs.get(kind)
        if spec is None:
            continue                              # 未知类型：忽略（协议版本策略）
        for req in spec.get("required", []):
            if req not in ev:
                problems.append("#%d %s: 缺字段 %r" % (index, kind, req))
        for key, rule in spec.get("properties", {}).items():
            if key not in ev or "type" not in rule:
                continue
            _check_type(ev[key], rule["type"], "#%d %s.%s" % (index, kind, key), problems)
        usage = ev.get("usage")
        if isinstance(usage, dict):
            for key, val in usage.items():
                if not isinstance(val, int) or val < 0:
                    problems.append("#%d usage.%s 应为非负整数，实际 %r" % (index, key, val))
    return problems


def stream_invariants(events, schema) -> dict:
    """按类别返回违规项，便于逐条打勾。"""
    found = {"non_object": [], "type": [], "session": [], "final": [],
             "turn_end": [], "usage": [], "schema": []}

    if not events:
        found["type"].append("事件流为空")
        return found

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            found["non_object"].append("#%d 不是 JSON 对象" % i)
            continue
        kind = ev.get("type")
        if not isinstance(kind, str) or not kind:
            found["type"].append("#%d 缺少非空 type" % i)

    first = events[0] if isinstance(events[0], dict) else {}
    if first.get("type") != "session":
        found["session"].append("首事件应为 session，实际 %r" % first.get("type"))
    sid = first.get("sessionId")
    if not isinstance(sid, str) or not sid.strip():
        found["session"].append("session.sessionId 为空或非字符串")

    finals = [e for e in events if isinstance(e, dict) and e.get("type") == "final"]
    if len(finals) != 1:
        found["final"].append("应恰有 1 条 final，实际 %d" % len(finals))

    ends = [e for e in events if isinstance(e, dict)
            and e.get("type") == "status" and e.get("phase") == "turn_end"]
    if not ends:
        found["turn_end"].append("缺少 status(turn_end)")
    elif not ends[-1].get("reason"):
        found["turn_end"].append("status(turn_end) 缺 reason")

    if not any(isinstance(e, dict) and e.get("usage") for e in events):
        found["usage"].append("没有任何 usage（成本面板会瞎）")

    found["schema"] = schema_check(events, schema)
    return found


def report(events, title: str, schema) -> bool:
    print("\n== %s ==" % title)
    found = stream_invariants(events, schema)
    labels = [
        ("non_object", "每行都是 JSON 对象"),
        ("type", "每条都有非空 type"),
        ("session", "首事件是 session 且 sessionId 非空"),
        ("final", "恰有 1 条 final"),
        ("turn_end", "turn_end 带 reason"),
        ("usage", "usage 存在"),
        ("schema", "字段类型符合 schema"),
    ]
    ok = True
    for key, label in labels:
        problems = found.get(key) or []
        ok &= _ok(not problems, label)
        for p in problems:
            print("        · " + p)
    usage = next((e["usage"] for e in reversed(events)
                  if isinstance(e, dict) and e.get("type") == "status" and e.get("usage")), None)
    if usage:
        print("        usage: %s" % json.dumps(usage, ensure_ascii=False))
    return ok


def read_fixture() -> list:
    events = []
    with open(FIXTURE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def run_offline(schema) -> bool:
    ok = report(read_fixture(), "fixture: tests/fixtures/turn.jsonl", schema)

    launcher = [sys.executable, os.path.join(HERE, "fake_dsh.py")]
    client = DshClient(launcher=launcher, cwd=ROOT)
    turn = client.ask("offline conformance")
    ok &= report(turn.events, "fake dsh: 单轮", schema)

    second = client.ask("代号是什么？")
    ok &= _ok(turn.session_id == second.session_id, "会话续接（fake）")

    # 故障路径也必须给出可判定的事件
    bad = DshClient(launcher=launcher, cwd=ROOT, strict=False)
    os.environ["FAKE_DSH_EXIT"] = "7"
    try:
        failed = bad.ask("fail")
    finally:
        os.environ.pop("FAKE_DSH_EXIT", None)
    ok &= _ok(any(e["type"] == "error" for e in failed.events), "非 0 退出 -> error 事件")
    return ok


def _hint_for(exc: DshError) -> None:
    text = str(exc)
    if "MISSING_CREDENTIAL" in text:
        print("        提示：这个 profile 没有可用的模型凭据。二选一：")
        print("          · export DEEPSEEK_API_KEY=... 后重跑；")
        print("          · 用 `dsh web` 的 Models 页把 key 写进凭据服务，再重跑。")
    elif "is incompatible with dsh" in text:
        print("        提示：该 profile 的用户层里有版本不兼容的插件，dsh 拒绝启动。")
        print("          临时绕开：dsh --patch <disable.yml> --profile <name> --json -")
        print("          长期修法：dsh <new> --from-default-profile headless，用干净 profile。")


def run_live(schema, profile: str, dsh: str, patches: list) -> bool:
    client = DshClient(dsh=dsh, profile=profile, cwd=ROOT, patches=patches, timeout=900)
    ok = True

    print("\n-- 第 1 轮：建立会话")
    try:
        first = client.ask("记住代号 ORION-7，只回复 OK 两个字母。")
    except DshError as exc:
        print("  FAIL  dsh 调用失败（%s）: %s" % (exc.kind, str(exc)[:400]))
        _hint_for(exc)
        return False
    ok &= report(first.events, "live: 第 1 轮", schema)
    ok &= _ok("ORION-7" not in first.text or True, "（信息）首轮回复: %r" % first.text[:60])

    print("\n-- 第 2 轮：续接同一会话")
    try:
        second = client.ask("代号是什么？只回复代号本身。", session_id=first.session_id)
    except DshError as exc:
        print("  FAIL  dsh 调用失败（%s）: %s" % (exc.kind, str(exc)[:400]))
        _hint_for(exc)
        return False
    ok &= report(second.events, "live: 第 2 轮", schema)
    ok &= _ok(first.session_id == second.session_id, "两轮使用同一 sessionId")
    ok &= _ok("ORION-7" in second.text, "会话记忆生效（回复含 ORION-7）: %r" % second.text[:80])

    print("\n-- 版本")
    print("  profile=%s patches=%s" % (profile, patches or "[]"))
    return ok


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="DSH 事件流一致性测试")
    parser.add_argument("--live", action="store_true", help="调真实 dsh（会消耗 token）")
    parser.add_argument("--profile", default="headless")
    parser.add_argument("--dsh", default="dsh")
    parser.add_argument("--patch", action="append", default=[], dest="patches")
    args = parser.parse_args(argv)

    schema = load_schema()
    print("schema: spec/events.schema.json  (draft 2020-12)")
    ok = run_offline(schema)
    if args.live:
        ok &= run_live(schema, args.profile, args.dsh, args.patches)
    else:
        print("\n（跳过 live 检查；加 --live 用真实 dsh 验证）")

    print("\n%s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
