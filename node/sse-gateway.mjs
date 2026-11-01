#!/usr/bin/env node
// sse-gateway.mjs — Node 版零依赖 SSE 网关（与 python/dsh_agent_kit/gateway.py 对齐）
//
//   node node/sse-gateway.mjs --port 8778 --cwd /path/to/project
//   GET  /         内置单页 demo
//   POST /ask      {task, session_id?, cwd?} -> text/event-stream
//   GET  /health   {ok, dsh, profile, cwd}

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { DshClient, DEFAULT_PROFILE } from "./dsh-client.mjs";

const DEMO_HTML = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>dsh-agent-kit · node SSE demo</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:14px/1.6 ui-monospace,Menlo,monospace;background:#0d1117;color:#c9d1d9}
 header{padding:10px 16px;border-bottom:1px solid #21262d}
 header b{color:#58a6ff} #meta{color:#8b949e;font-size:12px;margin-left:12px}
 main{padding:16px;display:grid;gap:12px;max-width:960px}
 textarea{width:100%;height:88px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;
          border-radius:6px;padding:10px;font:inherit}
 button{background:#238636;color:#fff;border:0;border-radius:6px;padding:8px 16px;font:inherit;cursor:pointer}
 #out{white-space:pre-wrap;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;min-height:180px}
 #cost{color:#8b949e;font-size:12px}
</style></head><body>
<header><b>dsh-agent-kit</b><span id="meta"></span></header>
<main>
  <textarea id="task" placeholder="输入任务"></textarea>
  <div><button id="go">运行</button></div>
  <div id="out"></div><div id="cost"></div>
</main>
<script>
const out=document.querySelector("#out"), cost=document.querySelector("#cost"), meta=document.querySelector("#meta");
let sid=null;
fetch("/health").then(r=>r.json()).then(h=>meta.textContent=\`profile=\${h.profile} cwd=\${h.cwd}\`);
document.querySelector("#go").onclick=async()=>{
  const task=document.querySelector("#task").value.trim(); if(!task) return;
  out.textContent=""; cost.textContent="";
  const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({task,session_id:sid})});
  const rd=r.body.getReader(), dec=new TextDecoder(); let buf="";
  for(;;){const {value,done}=await rd.read(); if(done) break;
    buf+=dec.decode(value,{stream:true}); let i;
    while((i=buf.indexOf("\\n\\n"))>=0){const f=buf.slice(0,i); buf=buf.slice(i+2);
      if(!f.startsWith("data: ")) continue; const ev=JSON.parse(f.slice(6));
      if(ev.type==="text") out.textContent+=ev.text;
      else if(ev.type==="final") out.textContent=ev.text;
      else if(ev.type==="session"){sid=ev.sessionId; meta.textContent="sid="+sid;}
      else if(ev.type==="status"&&ev.usage) cost.textContent="usage: "+JSON.stringify(ev.usage);
      else if(ev.type==="error") out.textContent+="\\n[error] "+ev.message;
    }}
};
</script></body></html>`;

export function createGateway({
  cwd = process.cwd(),
  dsh = "dsh",
  profile = DEFAULT_PROFILE,
  patches = [],
  token = null,
  timeoutMs = 600_000,
  maxConcurrency = 4,
  quiet = false,
  launcher = null,
} = {}) {
  const root = path.resolve(cwd);
  let active = 0;

  const log = (...args) => {
    if (!quiet) console.error("[gateway]", ...args);
  };

  const cors = (res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  };

  const json = (res, code, obj) => {
    const body = JSON.stringify(obj);
    cors(res);
    res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
    res.end(body);
  };

  const authorized = (req) => {
    if (!token) return true;
    if (req.headers.authorization === `Bearer ${token}`) return true;
    const url = new URL(req.url, "http://127.0.0.1");
    return url.searchParams.get("token") === token;
  };

  const readBody = (req) =>
    new Promise((resolve) => {
      const chunks = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
        } catch {
          resolve({});
        }
      });
      req.on("error", () => resolve({}));
    });

  const safeCwd = (requested) => {
    if (!requested) return root;
    const target = path.resolve(root, requested);
    return target === root || target.startsWith(root + path.sep) ? target : null;
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");

    if (req.method === "OPTIONS") {
      cors(res);
      res.writeHead(204);
      return res.end();
    }
    if (!authorized(req)) return json(res, 401, { error: "unauthorized" });

    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      cors(res);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      return res.end(DEMO_HTML);
    }

    if (req.method === "GET" && url.pathname === "/health") {
      return json(res, 200, { ok: true, dsh, profile, patches, cwd: root });
    }

    if (req.method === "POST" && url.pathname === "/shutdown") {
      const remote = req.socket.remoteAddress || "";
      if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(remote)) {
        return json(res, 403, { error: "loopback only" });
      }
      json(res, 200, { ok: true });
      return server.close();
    }

    if (req.method === "POST" && url.pathname === "/ask") {
      const payload = await readBody(req);
      const task = String(payload.task ?? "").trim();
      if (!task) return json(res, 400, { error: "task required" });

      const cwdForTask = safeCwd(payload.cwd);
      if (!cwdForTask) return json(res, 403, { error: "cwd outside allowed root" });

      if (active >= maxConcurrency) {
        return json(res, 429, { error: "busy", maxConcurrency });
      }
      active += 1;

      // SSE 头：不设 Content-Length，Node 自动用 chunked
      cors(res);
      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "close",
        "X-Accel-Buffering": "no",
      });
      res.flushHeaders?.();

      const started = Date.now();
      const client = new DshClient({
        dsh, profile, cwd: cwdForTask, patches, timeoutMs, strict: false, launcher,
      });

      const emit = (ev) => res.write(`data: ${JSON.stringify(ev)}\n\n`);

      try {
        for await (const ev of client.stream(task, { sessionId: payload.session_id || null })) {
          emit(ev);
        }
      } catch (error) {
        emit({ type: "error", message: `${error.name}: ${error.message}` });
      } finally {
        active -= 1;
        res.end();
        log(`ask done in ${((Date.now() - started) / 1000).toFixed(1)}s`);
      }
      return;
    }

    return json(res, 404, { error: "not found", path: url.pathname });
  });

  server.on("clientError", (_err, socket) => socket.destroy());
  return server;
}

// ---------------------------- CLI ----------------------------
function parseArgs(argv) {
  const out = { patches: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => argv[++i];
    if (arg === "--port") out.port = Number(next());
    else if (arg === "--host") out.host = next();
    else if (arg === "--cwd") out.cwd = next();
    else if (arg === "--dsh") out.dsh = next();
    else if (arg === "--profile") out.profile = next();
    else if (arg === "--patch") out.patches.push(next());
    else if (arg === "--token") out.token = next();
    else if (arg === "--timeout") out.timeoutMs = Number(next()) * 1000;
    else if (arg === "--max-concurrency") out.maxConcurrency = Number(next());
    else if (arg === "--quiet") out.quiet = true;
    else if (arg === "--help" || arg === "-h") out.help = true;
  }
  return out;
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isMain) {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(`用法: node node/sse-gateway.mjs [--port 8778] [--host 127.0.0.1] [--cwd DIR]
       [--profile headless] [--patch FILE]... [--token SECRET]
       [--timeout 600] [--max-concurrency 4] [--dsh dsh] [--quiet]`);
    process.exit(0);
  }
  const server = createGateway({
    cwd: args.cwd ?? process.cwd(),
    host: args.host,
    dsh: args.dsh ?? "dsh",
    profile: args.profile ?? DEFAULT_PROFILE,
    patches: args.patches,
    token: args.token ?? null,
    timeoutMs: args.timeoutMs ?? 600_000,
    maxConcurrency: args.maxConcurrency ?? 4,
    quiet: args.quiet ?? false,
  });
  const host = args.host ?? "127.0.0.1";
  const port = args.port ?? 8778;
  server.listen(port, host, () => {
    const actual = server.address().port;
    console.error(`dsh-agent-kit node gateway -> http://${host}:${actual}/  (profile=${args.profile ?? DEFAULT_PROFILE}, cwd=${path.resolve(args.cwd ?? process.cwd())}${args.token ? ", token required" : ""})`);
  });
  const stop = () => server.close(() => process.exit(0));
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

export { DEMO_HTML };
