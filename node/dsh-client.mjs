// dsh-client.mjs — Node ≥ 18 零依赖 DSH 客户端
//
//   import { DshClient } from "./dsh-client.mjs";
//   const c = new DshClient({ cwd: "/path/to/project" });
//   for await (const ev of c.stream("解释这个仓库")) {
//     if (ev.type === "text") process.stdout.write(ev.text);
//   }
//
// 与 python/dsh_agent_kit/client.py 行为一一对应：任务走 stdin、stderr 分流、
// 自己实现超时（含进程树清理）、会话自动续接、环境变量摘除。

import { spawn } from "node:child_process";
import readline from "node:readline";

export const DEFAULT_PROFILE = "headless";
export const SCRUB_ENV = ["DSH_SESSION_ID", "DSH_PROFILE", "DSH_PROFILE_DIR"];
export const KIND_SPAWN = "spawn";
export const KIND_TIMEOUT = "timeout";
export const KIND_EXIT = "exit";

const UNSET = Symbol("unset");

export class DshError extends Error {
  constructor(message, { kind = KIND_EXIT, exitCode = null, stderr = "", events = [] } = {}) {
    super(message);
    this.name = "DshError";
    this.kind = kind;
    this.exitCode = exitCode;
    this.stderr = stderr;
    this.events = events;
  }
}

/** 子进程环境：启动那一刻构造，摘除会话级 DSH 变量。 */
export function buildEnv(extra = {}, { dropSessionEnv = true, base = process.env } = {}) {
  const env = { ...base };
  if (dropSessionEnv) for (const key of SCRUB_ENV) delete env[key];
  env.PYTHONIOENCODING = "utf-8";
  return { ...env, ...extra };
}

/**
 * 拼 argv。顺序规则：`dsh [launcher-flags] <name> [app-args...]`
 * —— `--patch` 写在 `--json` 之后会得到 unknown option。
 */
export function buildArgv({
  profile = DEFAULT_PROFILE,
  launcher = ["dsh"],
  patches = [],
  sessionId = null,
  extraLauncher = [],
  extraApp = [],
  taskViaStdin = true,
  task = null,
} = {}) {
  const argv = [...launcher];
  for (const p of patches) argv.push("--patch", String(p));
  argv.push("--profile", profile, ...extraLauncher, "--json");
  if (sessionId) argv.push("--session-id", sessionId);
  argv.push(...extraApp);
  argv.push(taskViaStdin ? "-" : String(task));
  return argv;
}

/** Windows 上 npm 装的是 dsh.cmd，spawn 它必须走 cmd /c。 */
function spawnFor(argv, opts) {
  const [cmd, ...args] = argv;
  if (process.platform === "win32" && /\.(cmd|bat)$/i.test(cmd)) {
    return spawn("cmd", ["/c", cmd, ...args], opts);
  }
  return spawn(cmd, args, opts);
}

function killTree(child) {
  if (!child || child.killed) return;
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/T", "/F", "/PID", String(child.pid)], { stdio: "ignore" });
    } else {
      child.kill("SIGKILL");
    }
  } catch {
    /* 进程可能已经退出 */
  }
}

/** 把回调式行流桥成异步迭代器（超时才不会被阻塞的 read 卡住）。 */
class LineQueue {
  constructor() {
    this.items = [];
    this.waiters = [];
    this.finished = false;
    this.failure = null;
  }

  push(item) {
    if (this.finished) return;
    const waiter = this.waiters.shift();
    if (waiter) waiter.resolve({ value: item, done: false });
    else this.items.push(item);
  }

  end() {
    this.finished = true;
    for (const waiter of this.waiters.splice(0)) waiter.resolve({ value: undefined, done: true });
  }

  fail(error) {
    this.failure = error;
    this.finished = true;
    for (const waiter of this.waiters.splice(0)) waiter.reject(error);
  }

  next() {
    if (this.items.length) return Promise.resolve({ value: this.items.shift(), done: false });
    if (this.failure) return Promise.reject(this.failure);
    if (this.finished) return Promise.resolve({ value: undefined, done: true });
    return new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
  }

  [Symbol.asyncIterator]() {
    return this;
  }
}

export class DshClient {
  constructor({
    dsh = "dsh",
    profile = DEFAULT_PROFILE,
    cwd = process.cwd(),
    patches = [],
    env = {},
    timeoutMs = 600_000,
    strict = true,
    trackSession = true,
    dropSessionEnv = true,
    extraLauncher = [],
    extraApp = [],
    launcher = null,
  } = {}) {
    this.launcher = launcher ? [...launcher] : [dsh];
    this.dsh = dsh;
    this.profile = profile;
    this.cwd = cwd;
    this.patches = [...patches];
    this.timeoutMs = Number(timeoutMs);
    this.strict = strict;
    this.trackSession = trackSession;
    this.extraLauncher = [...extraLauncher];
    this.extraApp = [...extraApp];
    this._envExtra = { ...env };
    this._dropSessionEnv = dropSessionEnv;
    this._sessionId = null;
  }

  get sessionId() {
    return this._sessionId;
  }

  reset() {
    this._sessionId = null;
  }

  get env() {
    return buildEnv(this._envExtra, { dropSessionEnv: this._dropSessionEnv });
  }

  argv({ sessionId = null, extraApp = [] } = {}) {
    return buildArgv({
      profile: this.profile,
      launcher: this.launcher,
      patches: this.patches,
      sessionId,
      extraLauncher: this.extraLauncher,
      extraApp: [...this.extraApp, ...extraApp],
    });
  }

  _resolveSession(sessionId) {
    if (sessionId === UNSET) return this.trackSession ? this._sessionId : null;
    return sessionId; // 可能是 null：强制新会话
  }

  /** 逐条产出事件对象。 */
  async *stream(task, { sessionId = UNSET, timeoutMs, extraApp = [] } = {}) {
    const sid = this._resolveSession(sessionId);
    const limit = timeoutMs ?? this.timeoutMs;
    const argv = this.argv({ sessionId: sid, extraApp });

    let child;
    try {
      child = spawnFor(argv, {
        cwd: this.cwd,
        env: this.env,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (error) {
      throw new DshError(`无法启动 dsh: ${error.message}`, { kind: KIND_SPAWN });
    }

    const queue = new LineQueue();
    const events = [];
    let stderrText = "";
    let timedOut = false;
    // stdout EOF 往往早于进程真正退出（exitCode 还是 null），必须等 'close' 才能判退出码
    const closed = new Promise((resolve) => child.once("close", (code) => resolve(code)));

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderrText += chunk;
    });
    child.stdout.setEncoding("utf8");

    const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    rl.on("line", (line) => queue.push(line));
    rl.on("close", () => queue.end());

    child.on("error", (error) => queue.fail(new DshError(`spawn 失败: ${error.message}`, { kind: KIND_SPAWN })));
    child.stdin.on("error", () => { /* 子进程提前退出时会 EPIPE，忽略 */ });

    // 任务走 stdin：写完立刻 end（=EOF），否则 dsh 会一直等输入
    child.stdin.end(task ?? "", "utf8");

    const timer = setTimeout(() => {
      timedOut = true;
      killTree(child);
      queue.end();
    }, limit);

    try {
      for await (const line of queue) {
        const trimmed = String(line).trim();
        if (!trimmed) continue;
        let ev;
        try {
          ev = JSON.parse(trimmed);
        } catch {
          ev = { type: "_raw", text: trimmed };
        }
        if (ev && typeof ev !== "object") ev = { type: "_raw", text: trimmed };
        events.push(ev);
        if (ev.type === "session" && ev.sessionId && this.trackSession) {
          this._sessionId = ev.sessionId;
        }
        yield ev;
      }
    } finally {
      clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) killTree(child);
    }

    const code = await closed;

    if (timedOut) {
      if (this.strict) {
        throw new DshError(`超时 ${limit}ms`, { kind: KIND_TIMEOUT, stderr: stderrText, events });
      }
      yield { type: "error", message: `timeout ${limit}ms`, kind: KIND_TIMEOUT };
      return;
    }

    if (code !== 0 && code !== null) {
      if (this.strict) {
        throw new DshError(`dsh 退出码 ${code}: ${stderrText.trim().slice(-800)}`, {
          kind: KIND_EXIT,
          exitCode: code,
          stderr: stderrText,
          events,
        });
      }
      yield {
        type: "error",
        message: `exit ${code}: ${stderrText.trim().slice(-800)}`,
        kind: KIND_EXIT,
        exitCode: code,
      };
    }
  }

  /** 跑完一轮，返回 { text, sessionId, usage, events, exitCode, stderr, durationMs }。 */
  async ask(task, { sessionId = UNSET, timeoutMs, extraApp = [] } = {}) {
    const started = Date.now();
    const events = [];
    const parts = [];
    let finalText = null;
    let sid = null;
    let usage = null;
    let error = null;

    for await (const ev of this.stream(task, { sessionId, timeoutMs, extraApp })) {
      events.push(ev);
      if (ev.type === "session") sid = ev.sessionId ?? sid;
      else if (ev.type === "text") parts.push(ev.text ?? "");
      else if (ev.type === "final") finalText = ev.text ?? "";
      else if (ev.type === "status" && ev.usage) usage = ev.usage;
      else if (ev.type === "error") error = ev;
    }

    return {
      text: finalText !== null ? finalText : parts.join(""),
      sessionId: sid ?? this._sessionId,
      usage,
      events,
      exitCode: error ? null : 0,
      stderr: error ? error.message : "",
      durationMs: Date.now() - started,
    };
  }
}
