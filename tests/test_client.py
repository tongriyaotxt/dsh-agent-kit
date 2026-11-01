#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回归：不装 dsh、不烧 token，验证接入层的每一处坑。

    python tests/test_client.py
    # 或 pytest tests/test_client.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from dsh_agent_kit import (  # noqa: E402
    DshClient,
    DshError,
    build_argv,
    build_env,
    resolve_launcher,
)

FAKE = os.path.join(HERE, "fake_dsh.py")
LAUNCHER = [sys.executable, FAKE]
STATE_DIR = os.path.join(tempfile.gettempdir(), "dsh-agent-kit-tests")


def client(**kw) -> DshClient:
    kw.setdefault("cwd", ROOT)
    return DshClient(launcher=LAUNCHER, **kw)


def fresh_state() -> None:
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    os.environ["FAKE_DSH_STATE"] = STATE_DIR


# --------------------------------------------------------------------------- #
# 纯函数：argv / env
# --------------------------------------------------------------------------- #
def test_argv_order() -> None:
    """启动器 flag 必须在 app 参数之前，否则真 CLI 报 unknown option。"""
    argv = build_argv(
        profile="headless",
        launcher=["dsh"],
        patches=["a.yml", "b.yml"],
        session_id="session-1",
    )
    assert argv[:3] == ["dsh", "--patch", "a.yml"], argv
    i_patch = argv.index("--patch")
    i_profile = argv.index("--profile")
    i_json = argv.index("--json")
    i_sid = argv.index("--session-id")
    i_task = len(argv) - 1
    assert i_patch < i_profile < i_json < i_sid < i_task, argv
    assert argv[-1] == "-", "任务必须走 stdin"


def test_env_scrubs_session_vars() -> None:
    base = {
        "DSH_SESSION_ID": "parent-session",
        "DSH_PROFILE": "dsh-tui",
        "DSH_PROFILE_DIR": "/x",
        "DSH_HOME": "/home/x/.dsh",
        "PATH": "/usr/bin",
    }
    env = build_env(base=base)
    for key in ("DSH_SESSION_ID", "DSH_PROFILE", "DSH_PROFILE_DIR"):
        assert key not in env, "嵌套调用会串进父会话: %s" % key
    assert env["DSH_HOME"] == "/home/x/.dsh", "DSH_HOME 必须保留，否则找不到 profile"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert build_env(base=base, drop_session_env=False)["DSH_SESSION_ID"] == "parent-session"


def test_resolve_launcher() -> None:
    got = resolve_launcher(sys.executable)
    assert got[-1] == sys.executable
    if os.name == "nt":
        shim = os.path.join(tempfile.mkdtemp(), "dsh.cmd")
        with open(shim, "w", encoding="utf-8") as fh:
            fh.write("@echo off\n")
        assert resolve_launcher(shim)[:2] == ["cmd", "/c"], "Windows 上 .cmd 必须 cmd /c 包装"


# --------------------------------------------------------------------------- #
# 真跑：事件流、会话、失败、超时
# --------------------------------------------------------------------------- #
def test_stream_event_order() -> None:
    fresh_state()
    types = [ev["type"] for ev in client().stream("hello world")]
    assert types[0] == "session", types
    assert types[-1] == "final", types
    assert types.count("text") >= 2, "正文应分片下发，便于流式渲染"
    assert types.count("final") == 1


def test_ask_result_shape() -> None:
    fresh_state()
    turn = client().ask("hello world")
    assert turn.text == "ECHO hello world", turn.text
    assert turn.usage and turn.usage["inputTokens"] == 169
    assert turn.session_id and turn.session_id.startswith("session-fake-")
    assert turn.exit_code == 0
    assert turn.duration > 0
    assert turn["text"] == turn.text, "Turn 应支持字典式访问"
    statuses = turn.events_of("status")
    assert any(s.get("usage") for s in statuses)


def test_multiturn_session_continuity() -> None:
    fresh_state()
    c = client()
    first = c.ask("记住 ORION-7")
    second = c.ask("代号是什么？")
    assert first.session_id == second.session_id, "会话必须续接（--session-id）"
    assert "ORION-7" in second.text, second.text
    assert second.events_of("session")[0]["sessionId"] == first.session_id


def test_explicit_new_session() -> None:
    fresh_state()
    c = client()
    a = c.ask("记住 ORION-7")
    b = c.ask("代号是什么？", session_id=None)   # 显式要求新会话
    assert a.session_id != b.session_id
    assert "ORION-7" not in b.text, "新会话不应记得上一轮"


def test_reset_forgets_session() -> None:
    fresh_state()
    c = client()
    a = c.ask("记住 ORION-7")
    c.reset()
    b = c.ask("代号是什么？")
    assert a.session_id != b.session_id
    assert b.session_id == c.session_id


def test_strict_exit_error() -> None:
    fresh_state()
    os.environ["FAKE_DSH_EXIT"] = "3"
    try:
        c = client(strict=True)
        try:
            c.ask("boom")
        except DshError as exc:
            assert exc.kind == "exit", exc.kind
            assert exc.exit_code == 3
            assert "simulated failure" in exc.stderr
            assert exc.events, "异常应带上已收到的事件，便于排障"
        else:
            raise AssertionError("strict 模式必须抛 DshError")
    finally:
        os.environ.pop("FAKE_DSH_EXIT", None)


def test_nonstrict_exit_yields_error_event() -> None:
    fresh_state()
    os.environ["FAKE_DSH_EXIT"] = "4"
    try:
        turn = client(strict=False).ask("boom")
    finally:
        os.environ.pop("FAKE_DSH_EXIT", None)
    assert turn.events_of("error"), "非 strict 模式应给出 error 事件"
    assert "exit 4" in turn.events_of("error")[0]["message"]


def test_timeout_kills_process() -> None:
    fresh_state()
    os.environ["FAKE_DSH_STALL"] = "1"
    started = time.monotonic()
    try:
        try:
            client(timeout=1.0).ask("stall")
        except DshError as exc:
            assert exc.kind == "timeout", exc.kind
        else:
            raise AssertionError("超时必须抛 DshError(kind=timeout)")
    finally:
        os.environ.pop("FAKE_DSH_STALL", None)
    assert time.monotonic() - started < 20, "超时不生效，客户端卡住了"


def test_bad_line_degrades_to_raw() -> None:
    fresh_state()
    os.environ["FAKE_DSH_BADLINE"] = "1"
    try:
        turn = client().ask("badline")
    finally:
        os.environ.pop("FAKE_DSH_BADLINE", None)
    raws = turn.events_of("_raw")
    assert raws and "not json" in raws[0]["text"]
    assert turn.text.startswith("ECHO"), "非 JSON 行不应中断流"


def test_tool_event_passthrough() -> None:
    fresh_state()
    os.environ["FAKE_DSH_TOOL"] = "1"
    try:
        turn = client().ask("use a tool")
    finally:
        os.environ.pop("FAKE_DSH_TOOL", None)
    tools = turn.events_of("tool")
    assert tools and tools[0]["name"] == "fake_tool", "tool 事件应按原样透传"


# --------------------------------------------------------------------------- #
# argv 顺序在真实解析器上的行为（用 strict 的假 CLI 验证坑位）
# --------------------------------------------------------------------------- #
def test_patch_position_accepted() -> None:
    fresh_state()
    patch = os.path.join(STATE_DIR, "patch.yml")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(patch, "w", encoding="utf-8") as fh:
        fh.write("- insert: []\n")

    c = client(patches=[patch], extra_launcher=["--dump-config"])
    argv = c.argv()
    proc = subprocess.run(argv, input="", capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert "patch:" in proc.stdout, proc.stdout


def test_patch_position_rejected_when_after_app_args() -> None:
    """证据：--patch 写在 app 参数之后 = unknown option（这就是坑 5）。"""
    patch = os.path.join(STATE_DIR, "patch.yml")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(patch, "w", encoding="utf-8") as fh:
        fh.write("- insert: []\n")
    argv = LAUNCHER + ["--profile", "headless", "--json", "--patch", patch, "-"]
    proc = subprocess.run(argv, input="x", capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode != 0
    assert "unknown option '--patch'" in proc.stderr, proc.stderr


def test_stdin_carries_quotes_and_cjk() -> None:
    """证明任务走 stdin 时，引号/中文/反斜杠不会被 shell 吃掉。"""
    fresh_state()
    task = '把 "a b" 里的 C:\\tmp\\x 解释一下\n并给我三点'
    turn = client().ask(task)
    assert '"a b"' in turn.text
    assert "C:\\tmp\\x" in turn.text
    assert "三点" in turn.text


# --------------------------------------------------------------------------- #
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    fresh_state()
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 测试入口需要汇总
            failed.append((name, exc))
            print("FAIL %-42s %s: %s" % (name, type(exc).__name__, exc))
        else:
            passed += 1
            print("PASS %-42s" % name)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
