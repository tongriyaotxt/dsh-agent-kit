#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量任务：一次跑 N 个，控并发、每任务一个进程、结果落 JSONL。

    python examples/batch.py --workers 3 "总结这个文件的用途：{path}" README.md docs/*.md

要点：

* 每任务一个**独立进程**（隔离崩溃与超时），不要复用进程；
* 会话是新开的（批量任务之间不该共享上下文）；
* 结果写 runs/batch-<ts>.jsonl，便于回放与计费。
"""

import argparse
import concurrent.futures as futures
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from dsh_agent_kit import DshClient, DshError  # noqa: E402


def run_one(template: str, target: str, profile: str, cwd: str) -> dict:
    task = template.format(path=target, name=os.path.basename(target))
    client = DshClient(profile=profile, cwd=cwd, timeout=900, track_session=False)
    started = time.monotonic()
    try:
        turn = client.ask(task)
        return {
            "target": target,
            "ok": True,
            "text": turn.text,
            "usage": turn.usage,
            "session_id": turn.session_id,
            "seconds": round(time.monotonic() - started, 2),
        }
    except DshError as exc:
        return {
            "target": target,
            "ok": False,
            "error": "%s: %s" % (exc.kind, str(exc)[:500]),
            "seconds": round(time.monotonic() - started, 2),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="批量跑 DSH 任务")
    parser.add_argument("template", help='任务模板，用 {path} / {name} 占位')
    parser.add_argument("targets", nargs="+")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--profile", default="headless")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    out_dir = os.path.join(os.getcwd(), "runs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, "batch-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S"))

    results = []
    with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = [pool.submit(run_one, args.template, t, args.profile, args.cwd) for t in args.targets]
        for job in futures.as_completed(jobs):
            res = job.result()
            results.append(res)
            flag = "ok " if res["ok"] else "ERR"
            preview = (res.get("text") or res.get("error") or "").replace("\n", " ")[:80]
            print("[%s] %-28s %5.1fs  %s" % (flag, res["target"], res["seconds"], preview))

    with open(out_path, "w", encoding="utf-8") as fh:
        for res in results:
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["ok"])
    tokens = sum((r.get("usage") or {}).get("totalTokens", 0) for r in results)
    print("\n%d/%d 成功，合计 %d tokens，明细：%s" % (ok, len(results), tokens, out_path))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
