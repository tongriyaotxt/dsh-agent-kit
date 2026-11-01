#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回归：SSE 网关（chunked 帧、CORS、白名单、token、错误帧）。

    python tests/test_gateway.py
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from dsh_agent_kit.gateway import create_server  # noqa: E402

LAUNCHER = [sys.executable, os.path.join(HERE, "fake_dsh.py")]
STATE_DIR = os.path.join(tempfile.gettempdir(), "dsh-agent-kit-gw-tests")


class Gateway:
    def __init__(self, **kw):
        kw.setdefault("cwd", ROOT)
        kw.setdefault("launcher", LAUNCHER)
        kw.setdefault("quiet", True)
        kw.setdefault("port", 0)
        self.server, self.cfg = create_server(**kw)
        self.host, self.port = self.server.server_address[:2]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, headers=None, timeout=60):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        head = {"Content-Type": "application/json"}
        head.update(headers or {})
        conn.request(method, path, body=payload, headers=head)
        return conn.getresponse()

    def json(self, method, path, body=None, headers=None):
        resp = self.request(method, path, body, headers)
        raw = resp.read().decode("utf-8")
        return resp.status, (json.loads(raw) if raw else None)

    def sse(self, body, headers=None):
        """读一整个 SSE 响应，返回事件列表。"""
        resp = self.request("POST", "/ask", body, headers)
        assert resp.status == 200, (resp.status, resp.read()[:400])
        events, buf = [], ""
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace")
            if line in ("\r\n", "\n"):
                if buf:
                    events.append(json.loads(buf))
                    buf = ""
                continue
            if line.startswith("data: "):
                buf += line[6:].rstrip("\r\n")
        return resp, events

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def fresh_state():
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    os.environ["FAKE_DSH_STATE"] = STATE_DIR


def test_health_and_demo_page():
    gw = Gateway()
    try:
        status, body = gw.json("GET", "/health")
        assert status == 200
        assert body["ok"] is True and body["profile"] == "headless"
        resp = gw.request("GET", "/")
        html = resp.read().decode("utf-8")
        assert resp.status == 200 and "SSE gateway demo" in html
        assert resp.getheader("Access-Control-Allow-Origin") == "*"
        status, body = gw.json("GET", "/nope")
        assert status == 404
    finally:
        gw.close()


def test_ask_streams_chunked_events():
    fresh_state()
    gw = Gateway()
    try:
        resp, events = gw.sse({"task": "gateway check"})
        assert resp.getheader("Transfer-Encoding") == "chunked", "必须手写 chunked，否则前端卡住"
        assert resp.getheader("Content-Type").startswith("text/event-stream")
        assert events[0]["type"] == "session", events[:2]
        assert events[-1]["type"] == "final", events[-2:]
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        assert text == "ECHO gateway check", text
        assert any(e["type"] == "status" and e.get("usage") for e in events)
    finally:
        gw.close()


def test_session_continuity_over_http():
    fresh_state()
    gw = Gateway()
    try:
        _, first = gw.sse({"task": "记住 ORION-7"})
        sid = next(e["sessionId"] for e in first if e["type"] == "session")
        _, second = gw.sse({"task": "代号是什么？", "session_id": sid})
        final = next(e for e in second if e["type"] == "final")
        assert final["text"] == "ORION-7", final
        assert next(e["sessionId"] for e in second if e["type"] == "session") == sid
    finally:
        gw.close()


def test_bad_requests():
    gw = Gateway()
    try:
        status, body = gw.json("POST", "/ask", {"task": "  "})
        assert status == 400 and "task" in body["error"]

        status, body = gw.json("POST", "/ask", {"task": "x", "cwd": "../../../etc"})
        assert status == 403, "cwd 必须限制在 --cwd 之内"

        status, _ = gw.json("GET", "/does-not-exist")
        assert status == 404
    finally:
        gw.close()


def test_token_gate():
    gw = Gateway(token="s3cret")
    try:
        status, _ = gw.json("GET", "/health")
        assert status == 401, "配了 token 就必须校验"
        status, body = gw.json("GET", "/health", headers={"Authorization": "Bearer s3cret"})
        assert status == 200 and body["ok"] is True
        status, _ = gw.json("GET", "/health?token=s3cret")
        assert status == 200, "浏览器打开 demo 页需要 ?token= 通道"
    finally:
        gw.close()


def test_failure_becomes_error_frame():
    fresh_state()
    os.environ["FAKE_DSH_EXIT"] = "5"
    gw = Gateway()
    try:
        _, events = gw.sse({"task": "boom"})
        errors = [e for e in events if e["type"] == "error"]
        assert errors, events[-3:]
        assert "exit 5" in errors[0]["message"], errors[0]
    finally:
        gw.close()
        os.environ.pop("FAKE_DSH_EXIT", None)


def test_cwd_subdir_allowed():
    fresh_state()
    sub = os.path.join(ROOT, "tests")
    gw = Gateway()
    try:
        resp, events = gw.sse({"task": "subdir", "cwd": "tests"})
        sid_events = [e for e in events if e["type"] == "session"]
        assert sid_events and os.path.realpath(sid_events[0]["cwd"]) == os.path.realpath(sub)
    finally:
        gw.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    fresh_state()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print("FAIL %-42s %s: %s" % (name, type(exc).__name__, exc))
        else:
            passed += 1
            print("PASS %-42s" % name)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
