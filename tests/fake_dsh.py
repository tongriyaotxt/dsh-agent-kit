#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fake_dsh.py — 行为级 dsh 替身，让接入层回归测试**零 token、零副作用**。

它只实现接入层依赖的那部分 CLI 行为：

* ``dsh [launcher-flags] <name> [app-args...]`` 的解析顺序 —— 启动器 flag 写在
  app 参数之后会像真 CLI 一样报 ``unknown option '--patch'``；
* ``--json`` 事件序列：session → status(turn_start/step_start) → thinking →
  text×n → status(step_end, usage) → status(turn_end, reason) → final；
* ``-`` 从 stdin 读任务，else 用 argv 拼；
* 会话记忆：``--session-id`` 复用同一份记忆（用于验证多轮续接）。

故障注入（环境变量）：

    FAKE_DSH_EXIT=3        发若干事件后以 3 退出，stderr 打诊断
    FAKE_DSH_STALL=1       发出首帧后长时间挂起（测客户端超时 kill）
    FAKE_DSH_BADLINE=1     插入一行非 JSON（测降级成 _raw）
    FAKE_DSH_DELAY_MS=40   每个事件之间 sleep
    FAKE_DSH_STATE=<dir>   会话记忆目录（默认系统临时目录）
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import uuid

VALUE_FLAGS = {"--profile": "profile", "--patch": "patch", "--from-default-profile": "from_default"}
BOOL_FLAGS = {"--dump-config", "--dump-config-schema", "--dump-default-config"}


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def parse_argv(argv):
    launcher = {"profile": None, "patches": [], "dump_config": False}
    app = {"json": False, "session_id": None, "task": None}

    i = 0
    # 阶段 1：启动器 flag，直到遇到第一个不是启动器 flag 的参数
    while i < len(argv):
        arg = argv[i]
        if arg in VALUE_FLAGS:
            if i + 1 >= len(argv):
                die("option '%s' argument missing" % arg)
            val = argv[i + 1]
            if arg == "--profile":
                launcher["profile"] = val
            elif arg == "--patch":
                if not os.path.exists(val):
                    die("patch file not found: %s" % val)
                launcher["patches"].append(val)
            elif arg == "--from-default-profile":
                launcher["from_default"] = val
            i += 2
            continue
        if arg in BOOL_FLAGS:
            if arg == "--dump-config":
                launcher["dump_config"] = True
            i += 1
            continue
        break

    # 阶段 2：app 参数（启动器 flag 出现在这里 = 真 CLI 的 unknown option）
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            app["json"] = True
        elif arg == "--session-id":
            if i + 1 >= len(argv):
                die("option '--session-id' argument missing")
            app["session_id"] = argv[i + 1]
            i += 1
        elif arg in ("-h", "--help"):
            sys.stdout.write("fake dsh: --json, --session-id, task or '-'\n")
            sys.exit(0)
        elif arg == "-":
            app["task"] = sys.stdin.read()
        elif arg in VALUE_FLAGS or arg in BOOL_FLAGS:
            die("unknown option '%s'" % arg.split("=")[0])
        elif arg.startswith("-") and arg != "-":
            die("unknown option '%s'" % arg)
        else:
            app["task"] = arg if app["task"] is None else app["task"] + " " + arg
        i += 1

    return launcher, app


def state_path() -> str:
    root = os.environ.get("FAKE_DSH_STATE") or os.path.join(tempfile.gettempdir(), "fake-dsh-state")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "sessions.json")


def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    try:
        with open(state_path(), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except OSError:
        pass


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    delay = int(os.environ.get("FAKE_DSH_DELAY_MS", "0") or 0)
    if delay:
        time.sleep(delay / 1000.0)


def answer_for(task: str, memory: dict) -> str:
    code = re.search(r"记住\s*([A-Za-z0-9_.\-]+)", task)
    if code:
        memory["code"] = code.group(1)
    if "代号" in task:
        return memory.get("code") or "NONE"
    flat = " ".join(task.split())
    return "ECHO " + flat[:60] if flat else "ECHO"


def main(argv) -> int:
    launcher, app = parse_argv(argv)

    if launcher["dump_config"]:
        # 只打印解析结果，绝不真正跑模型 —— 用来验证 flag 顺序与补丁生效
        sys.stdout.write("# fake composed config\n")
        sys.stdout.write("# profile: %s\n" % launcher["profile"])
        for p in launcher["patches"]:
            sys.stdout.write("- id: mcp-from-patch\n  patch: %s\n" % p)
        sys.stdout.write("- id: tool-plugin-manager\n  disabled: true\n")
        return 0

    profile = launcher["profile"]
    if not profile:
        die("missing profile: use --profile <name>")

    task = app["task"]
    if task is None:
        die("no task: pass text or '-' to read stdin")
    task = task.strip()

    sid = app["session_id"] or "session-fake-" + uuid.uuid4().hex[:12]
    state = load_state()
    memory = state.setdefault(sid, {"turn": 0, "code": None})
    memory["turn"] = int(memory.get("turn", 0)) + 1
    state[sid] = memory

    answer = answer_for(task, memory)   # 先算答案（可能写入记忆），再落盘
    save_state(state)

    emit({"type": "session", "sessionId": sid, "cwd": os.getcwd()})
    emit({"type": "status", "phase": "turn_start", "turn": memory["turn"]})
    emit({"type": "status", "phase": "step_start", "turn": memory["turn"], "step": 1})
    emit({"type": "thinking", "text": ""})

    if os.environ.get("FAKE_DSH_BADLINE"):
        sys.stdout.write("this line is not json\n")
        sys.stdout.flush()

    if os.environ.get("FAKE_DSH_TOOL"):
        emit({"type": "tool", "name": "fake_tool", "input": {"path": "a.txt"}})

    for i in range(0, len(answer), 4):
        emit({"type": "text", "text": answer[i:i + 4]})

    if os.environ.get("FAKE_DSH_STALL"):
        sys.stdout.flush()
        time.sleep(3600)

    usage = {
        "inputTokens": 169,
        "outputTokens": len(answer),
        "cacheReadTokens": 0,
        "totalTokens": 169 + len(answer),
    }
    emit({"type": "status", "phase": "step_end", "turn": memory["turn"], "step": 1, "usage": usage})

    code = os.environ.get("FAKE_DSH_EXIT")
    if code:
        sys.stderr.write("fake dsh: simulated failure (exit %s)\n" % code)
        sys.stderr.flush()
        return int(code)

    emit({"type": "status", "phase": "turn_end", "turn": memory["turn"], "reason": {"kind": "completed"}})
    emit({"type": "final", "text": answer})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
