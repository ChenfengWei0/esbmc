#!/usr/bin/env python3
"""Ingest a completed write-mode RQ1 subagent notification.

This records completion in the lease ledger and prints the mandatory Chinese
summary.  It does not count theory as net progress; net progress requires a
separate accepted review with a commit sha.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ORCHESTRATOR = HERE / "rq1_subagent_orchestrator.py"


def _read_json_stdin() -> dict:
    raw = sys.stdin.read()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"输入不是合法 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit("输入 JSON 必须是对象")
    return doc


def _status_text(doc: dict) -> str:
    status = doc.get("status")
    if isinstance(status, dict):
        text = status.get("completed") or status.get("error") or ""
    else:
        text = str(status or "")
    if not text:
        raise SystemExit("通知里没有 status.completed 文本")
    return str(text)


def _lines_with(text: str, needles: tuple[str, ...]) -> list[str]:
    out = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(needle.lower() in lowered for needle in needles):
            out.append(line.strip())
    return out


def _verdict_hint(text: str) -> str:
    lowered = text.lower()
    if "review_status: pending" in lowered or "review pending" in lowered:
        return "pending-review"
    if "needs-work" in lowered or "返工" in lowered:
        return "needs-work"
    if "accepted" in lowered or "通过" in lowered:
        return "accepted"
    return "completion-recorded"


def _theory_delta(text: str) -> str:
    match = re.search(r"(?im)^\s*(?:理论\s*delta|theory_delta)\s*[：:]\s*(.*)$",
                      text)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?i)([+-]\s*\d+[^.\n]*(?:valid|PUT|R1|R2)[^.\n]*)",
                      text)
    return match.group(1).strip() if match else "未声明；按 0 处理，等待 review"


def _dataset_check(text: str) -> str:
    lowered = text.lower()
    if "datasets" in lowered and ("未修改" in text or "untouched" in lowered
                                  or "no output" in lowered):
        return "确认未修改 Datasets"
    return "未找到明确 Datasets 确认；必须保持 pending review"


def _record_complete(agent_id: str, patch_id: str) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ORCHESTRATOR),
            "complete",
            "--agent-id",
            agent_id,
            "--patch-id",
            patch_id,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and "not found" not in (proc.stderr + proc.stdout):
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id")
    parser.add_argument("--patch-id", required=True)
    parser.add_argument("--slot", default="")
    args = parser.parse_args()
    doc = _read_json_stdin()
    agent_id = args.agent_id or str(doc.get("agent_path") or "")
    if not agent_id:
        raise SystemExit("--agent-id 必填，或通知 JSON 必须含 agent_path")
    text = _status_text(doc)
    _record_complete(agent_id, args.patch_id)
    inspected = _lines_with(text, ("artifact", "记录", "root_cause", "root cause",
                                   "失败", "prior"))
    changed = _lines_with(text, ("已修改", "changed", "修改路径", "changed_code"))
    why = _lines_with(text, ("根因", "root cause", "为什么", "correctness"))
    checks = _lines_with(text, ("验证", "syntax", "diff --check", "未运行",
                                "do not run"))

    print("RQ1 subagent完成入账报告:")
    print(f"  agent={agent_id}")
    print(f"  slot={args.slot}")
    print(f"  patch_id={args.patch_id}")
    print(f"  状态={_verdict_hint(text)}")
    print(f"  失败记录={'; '.join(inspected[:8]) or '未自动识别，review 必须补充'}")
    print(f"  改了什么={'; '.join(changed[:8]) or '未自动识别，review 必须补充'}")
    print(f"  为什么改={'; '.join(why[:8]) or '未自动识别，review 必须补充'}")
    print(f"  静态检查={'; '.join(checks[:8]) or '未自动识别'}")
    print(f"  Datasets={_dataset_check(text)}")
    print(f"  理论delta={_theory_delta(text)}")
    print("  净理论覆盖=0，必须等独立 review accepted 且有 commit 才能入账")
    print("  下一步=自动派 review；review 不通过则自动保持返工队列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
