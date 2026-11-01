// test_node.mjs — Node 客户端与网关的离线回归（零 token）
//
//   node --test tests/test_node.mjs
//   # 或： cd node && npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

import { DshClient, DshError, buildArgv, buildEnv } from "../node/dsh-client.mjs";
import { createGateway } from "../node/sse-gateway.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);
const FAKE = path.join(HERE, "fake_dsh.py");
const PY = process.env.DSH_KIT_PYTHON || (process.platform === "win32" ? "python" : "python3");
const LAUNCHER = [PY, FAKE];
const STATE = path.join(os.tmpdir(), "dsh-agent-kit-node-tests");

const freshState = () => {
  fs.rmSync(STATE, { recursive: true, force: true });
  process.env.FAKE_DSH_STATE = STATE;
};

const client = (opts = {}) => new DshClient({ launcher: LAUNCHER, cwd: ROOT, ...opts });

test("buildArgv 把启动器 flag 放在 app 参数之前", () => {
  const argv = buildArgv({ launcher: ["dsh"], patches: ["a.yml", "b.yml"], sessionId: "s1" });
  const iPatch = argv.indexOf("--patch");
  const iProfile = argv.indexOf("--profile");
  const iJson = argv.indexOf("--json");
  const iSid = argv.indexOf("--session-id");
  assert.ok(iPatch < iProfile && iProfile < iJson && iJson < iSid, argv.join(" "));
  assert.equal(argv.at(-1), "-", "任务必须走 stdin");
});

test("buildEnv 摘除会话级 DSH 变量", () => {
  const env = buildEnv(
    {},
    { base: { DSH_SESSION_ID: "parent", DSH_PROFILE: "tui", DSH_PROFILE_DIR: "/x", DSH_HOME: "/h" } },
  );
  assert.equal(env.DSH_SESSION_ID, undefined);
  assert.equal(env.DSH_PROFILE, undefined);
  assert.equal(env.DSH_HOME, "/h", "DSH_HOME 必须保留");
  assert.equal(env.PYTHONIOENCODING, "utf-8");
});

test("stream 事件顺序", async () => {
  freshState();
  const types = [];
  for await (const ev of client().stream("hello world")) types.push(ev.type);
  assert.equal(types[0], "session");
  assert.equal(types.at(-1), "final");
  assert.ok(types.filter((t) => t === "text").length >= 2);
});

test("ask 返回 text/usage/sessionId", async () => {
  freshState();
  const turn = await client().ask("hello world");
  assert.equal(turn.text, "ECHO hello world");
  assert.equal(turn.usage.inputTokens, 169);
  assert.match(turn.sessionId, /^session-fake-/);
  assert.equal(turn.exitCode, 0);
});

test("多轮续接同一会话", async () => {
  freshState();
  const c = client();
  const first = await c.ask("记住 ORION-7");
  const second = await c.ask("代号是什么？");
  assert.equal(first.sessionId, second.sessionId);
  assert.equal(second.text, "ORION-7");
});

test("reset 后开新会话", async () => {
  freshState();
  const c = client();
  const a = await c.ask("记住 ORION-7");
  c.reset();
  const b = await c.ask("代号是什么？");
  assert.notEqual(a.sessionId, b.sessionId);
  assert.equal(b.text, "NONE");
});

test("超时抛 DshError(kind=timeout) 且不挂死", async () => {
  freshState();
  process.env.FAKE_DSH_STALL = "1";
  const started = Date.now();
  try {
    await assert.rejects(
      () => client({ timeoutMs: 1000 }).ask("stall"),
      (err) => err instanceof DshError && err.kind === "timeout",
    );
  } finally {
    delete process.env.FAKE_DSH_STALL;
  }
  assert.ok(Date.now() - started < 15000, "超时未生效");
});

test("非 0 退出：strict 抛错 / 非 strict 给 error 事件", async () => {
  freshState();
  process.env.FAKE_DSH_EXIT = "3";
  try {
    await assert.rejects(
      () => client({ strict: true }).ask("boom"),
      (err) => err instanceof DshError && err.kind === "exit" && err.exitCode === 3,
    );
    const soft = await client({ strict: false }).ask("boom");
    const errors = soft.events.filter((e) => e.type === "error");
    assert.ok(errors.length === 1 && errors[0].message.includes("exit 3"));
  } finally {
    delete process.env.FAKE_DSH_EXIT;
  }
});

test("非 JSON 行降级为 _raw", async () => {
  freshState();
  process.env.FAKE_DSH_BADLINE = "1";
  try {
    const turn = await client().ask("badline");
    const raws = turn.events.filter((e) => e.type === "_raw");
    assert.equal(raws.length, 1);
    assert.ok(turn.text.startsWith("ECHO"));
  } finally {
    delete process.env.FAKE_DSH_BADLINE;
  }
});

test("网关：SSE 事件流可被 fetch 消费", async () => {
  freshState();
  const server = createGateway({ cwd: ROOT, launcher: LAUNCHER, quiet: true });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  try {
    const health = await (await fetch(`http://127.0.0.1:${port}/health`)).json();
    assert.equal(health.ok, true);

    const resp = await fetch(`http://127.0.0.1:${port}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: "gateway check" }),
    });
    assert.equal(resp.status, 200);
    assert.match(resp.headers.get("content-type"), /text\/event-stream/);

    const raw = await resp.text();
    const events = raw
      .split("\n\n")
      .filter((f) => f.startsWith("data: "))
      .map((f) => JSON.parse(f.slice(6)));
    assert.equal(events[0].type, "session");
    assert.equal(events.at(-1).type, "final");
    assert.equal(
      events.filter((e) => e.type === "text").map((e) => e.text).join(""),
      "ECHO gateway check",
    );

    const bad = await fetch(`http://127.0.0.1:${port}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: "  " }),
    });
    assert.equal(bad.status, 400);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
