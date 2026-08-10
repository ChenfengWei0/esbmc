#!/usr/bin/env python3
"""Build deterministic cross-review assignments for RQ1 subagent patches."""

from __future__ import annotations

import argparse
import json
import time
from collections import OrderedDict
from pathlib import Path


DEFAULT_SUBAGENTS = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENTS = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_OUT = Path("/tmp/veriput_rq1_review_queue.json")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"agents": []}


def _agents(*docs: dict) -> list[dict]:
    out: list[dict] = []
    for doc in docs:
        for agent in doc.get("agents") or []:
            if isinstance(agent, dict):
                out.append(agent)
    return out


def _scope_key(agent: dict) -> str:
    scope = " ".join(str(item) for item in agent.get("write_scope") or [])
    if "src/solidity-frontend" in scope or "src/goto-programs" in scope:
        return "ESBMC_FRONTEND_COVERAGE"
    if "rq1_veriput_run.py" in scope:
        return "RQ1_RUNNER"
    if "certify_all.py" in scope or "solidity_path_generalise.py" in scope:
        return "CERTIFIER"
    if "solidity_path_put.py" in scope or "put_all.py" in scope:
        return "PUT_QUALITY"
    if "veriput_subjects.py" in scope or "unit_schedule.py" in scope:
        return "SCHEDULER_SUBJECTS"
    return "MISC"


def _prompt(group: dict) -> str:
    lines = []
    for item in group["patches"][:12]:
        lines.append(
            f"- {item.get('slot')} patch={item.get('patch_id')} "
            f"agent={item.get('agent_id')} scope={','.join(item.get('write_scope') or [])}"
        )
    return f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC, ctest, pytest, RQ1, certify_all, put_all,
solidity_path_put, or any benchmark case. Do NOT modify
/home/samson/workspace/VeriPUT/Datasets.

Cross-review bucket {group['bucket_key']}. Inspect the listed diffs plus
adjacent shared call paths and the progress-ledger coverage claims. Do not make
unrelated edits. If a patch conflicts with another patch or its theoretical
coverage is contradicted by canonical RQ1 results, report the patch_id and the
coverage delta that must be removed.

Patches requiring independent review:
{chr(10).join(lines)}

Completion must include reviewed patch_ids, files inspected, conflicts found,
soundness risks, accepted/rejected/needs-work verdict, and any theoretical
coverage delta."""


def build_review_queue(subagents: Path, extra_subagents: Path) -> dict:
    agents = _agents(_json(subagents), _json(extra_subagents))
    grouped: OrderedDict[str, dict] = OrderedDict()
    for agent in agents:
        if agent.get("status") != "completed" or agent.get("mode") != "write":
            continue
        review_status = str(agent.get("review_status") or "pending")
        if review_status == "accepted":
            continue
        key = _scope_key(agent)
        group = grouped.setdefault(key, {
            "bucket_key": key,
            "patches": [],
        })
        group["patches"].append(agent)
    assignments = []
    for group in grouped.values():
        group["patch_count"] = len(group["patches"])
        group["prompt"] = _prompt(group)
        assignments.append(group)
    return {
        "schema": "veriput-rq1-review-dispatch/v1",
        "generated_ts": time.time(),
        "assignment_count": len(assignments),
        "assignments": assignments,
        "rule": (
            "Every completed write-mode patch with review_status other than "
            "accepted must be cross-reviewed. Net theoretical coverage must not "
            "count pending/rejected/needs-work patch_ids."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subagents", type=Path, default=DEFAULT_SUBAGENTS)
    parser.add_argument("--extra-subagents",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    doc = build_review_queue(args.subagents, args.extra_subagents)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
