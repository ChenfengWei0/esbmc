#!/usr/bin/env python3
"""Summarize RQ1 patch review verdicts from subagent ledgers.

This is intentionally read-only.  It gives the mandatory status path a
deterministic way to report how much provisional theory is blocked by review,
and which patches are explicitly needs-work/rejected after active review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_SUBAGENTS = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENTS = Path("/tmp/veriput_rq1_extra_subagents.json")
MANDATORY_REVIEW_FIELDS = (
    "changed_code",
    "prior_failure",
    "correctness_argument",
    "verdict",
    "theory_delta",
    "next_action",
)


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"agents": []}
    return value if isinstance(value, dict) else {"agents": []}


def _iter_agents(paths: list[Path]):
    for path in paths:
        doc = _json(path)
        for agent in doc.get("agents") or []:
            if isinstance(agent, dict):
                yield path, agent


def build_summary(paths: list[Path]) -> dict:
    buckets = {
        "accepted": [],
        "pending": [],
        "needs-work": [],
        "rejected": [],
        "readonly": [],
    }
    for path, agent in _iter_agents(paths):
        if agent.get("status") != "completed":
            continue
        patch_id = str(agent.get("patch_id") or "").strip()
        if not patch_id:
            continue
        mode = str(agent.get("mode") or "write")
        missing_fields: list[str] = []
        if mode == "readonly":
            verdict = "readonly"
        else:
            verdict = str(agent.get("review_status") or "pending")
            if verdict not in {"accepted", "pending", "needs-work", "rejected"}:
                verdict = "pending"
            commit_sha = str(
                agent.get("review_commit") or agent.get("commit_sha")
                or agent.get("commit") or agent.get("patch_commit") or ""
            ).strip()
            if verdict == "accepted" and not commit_sha:
                verdict = "pending"
            note = str(agent.get("review_note") or agent.get("note", ""))
            missing_fields = [
                field for field in MANDATORY_REVIEW_FIELDS
                if f"{field}=" not in note and f"{field}:" not in note
            ]
            if verdict in {"accepted", "needs-work", "rejected"} and \
                    missing_fields:
                verdict = "pending"
        buckets[verdict].append({
            "slot": agent.get("slot"),
            "task": agent.get("task"),
            "patch_id": patch_id,
            "agent_id": agent.get("agent_id"),
            "source": str(path),
            "write_scope": agent.get("write_scope") or [],
            "commit_sha": str(
                agent.get("review_commit") or agent.get("commit_sha")
                or agent.get("commit") or agent.get("patch_commit") or ""
            ).strip(),
            "note": agent.get("review_note") or agent.get("note", ""),
            "missing_review_fields": missing_fields if mode != "readonly" else [],
        })
    return {
        "schema": "veriput-rq1-patch-review-summary/v1",
        "reviewed_ledgers": [str(path) for path in paths],
        "counts": {key: len(value) for key, value in buckets.items()},
        "net_theory_rule": (
            "Only accepted write-mode patches may raise net theoretical "
            "coverage, and accepted reviews must record a commit sha. Pending, "
            "needs-work, rejected, and accepted-without-commit patches must "
            "remain visible and cannot be reported as theory progress."),
        "buckets": buckets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subagents", type=Path, default=DEFAULT_SUBAGENTS)
    parser.add_argument("--extra-subagents",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENTS)
    args = parser.parse_args()
    print(json.dumps(build_summary([args.subagents, args.extra_subagents]),
                     indent=2,
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
