#!/usr/bin/env node
// 最小 Node 集成：spawn dsh，逐行消费事件，流式打印正文 + 用量。
//
//   node examples/node_stream.mjs "解释这个仓库的构建流程"

import { DshClient } from "../node/dsh-client.mjs";

const task = process.argv.slice(2).join(" ") || "reply with OK";
const client = new DshClient({ cwd: process.cwd() });

let usage = null;
try {
  for await (const ev of client.stream(task)) {
    switch (ev.type) {
      case "session":
        console.error("[session]", ev.sessionId);
        break;
      case "text":
        process.stdout.write(ev.text ?? "");
        break;
      case "thinking":
        // 想显示思考就打开：process.stderr.write(ev.text ?? "");
        break;
      case "tool":
        console.error("\n[tool]", ev.name ?? "?");
        break;
      case "status":
        if (ev.usage) usage = ev.usage;
        break;
      case "error":
        console.error("\n[error]", ev.message);
        break;
      default:
        break; // 未知类型必须忽略（前向兼容）
    }
  }
} catch (error) {
  console.error(`\n失败(${error.kind ?? "unknown"}): ${error.message}`);
  process.exit(1);
}

process.stdout.write("\n");
if (usage) console.error("[usage]", JSON.stringify(usage), "session:", client.sessionId);
