#!/usr/bin/env python3
"""Summarize RQ1 patch review verdicts from subagent ledgers.

This is intentionally read-only.  It gives the mandatory status path a
deterministic way to report how much provisional theory is blocked by review,
and which patches are explicitly needs-work/rejected after active review.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_SUBAGENTS = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENTS = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_REVIEW_EVENTS = Path("/tmp/veriput_rq1_review_events.jsonl")
MANDATORY_REVIEW_FIELDS = (
    "changed_code",
    "prior_failure",
    "correctness_argument",
    "verdict",
    "theory_delta",
    "commit decision",
    "next_action",
)
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
VERDICT_RANK = {
    "readonly": 0,
    "pending": 1,
    "accepted": 2,
    "needs-work": 3,
    "rejected": 4,
}


def _extract_review_fields(note: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    markers = list(MANDATORY_REVIEW_FIELDS)
    for index, field in enumerate(markers):
        starts = []
        for sep in (":", "="):
            pos = note.find(field + sep)
            if pos >= 0:
                starts.append((pos, len(field + sep)))
        if not starts:
            continue
        start, prefix_len = min(starts)
        value_start = start + prefix_len
        value_end = len(note)
        for later in markers[index + 1:]:
            candidates = [
                note.find(later + ":", value_start),
                note.find(later + "=", value_start),
            ]
            candidates = [pos for pos in candidates if pos >= 0]
            if candidates:
                value_end = min(value_end, min(candidates))
        fields[field] = note[value_start:value_end].strip(" ;\n\t")
    return fields


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


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _valid_commit_sha(value: str) -> bool:
    return COMMIT_RE.match(str(value or "").strip()) is not None


def _bucket_row(source: str, agent: dict, verdict: str,
                missing_fields: list[str]) -> dict:
    note = str(agent.get("review_note") or agent.get("note", ""))
    return {
        "slot": agent.get("slot"),
        "task": agent.get("task"),
        "patch_id": str(agent.get("patch_id") or "").strip(),
        "agent_id": agent.get("agent_id"),
        "source": source,
        "write_scope": agent.get("write_scope") or [],
        "commit_sha": str(
            agent.get("review_commit") or agent.get("commit_sha")
            or agent.get("commit") or agent.get("patch_commit") or ""
        ).strip(),
        "note": note,
        "review_fields": _extract_review_fields(note),
        "missing_review_fields": missing_fields,
        "verdict": verdict,
        "review_round": int(agent.get("review_round") or 0),
    }


def _reconcile_buckets(buckets: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Keep one effective verdict per write patch.

    Review events can arrive more than once.  A later needs-work/rejected review
    must not coexist with an older accepted event for the same patch, otherwise
    status reports tell the runner both to commit and not to commit.  For write
    patches the stricter verdict wins; readonly rows are informational unless no
    write verdict exists for that key.
    """

    by_key: dict[tuple[str, str], dict] = {}
    readonly_rows = []
    for verdict, rows in buckets.items():
        for row in rows:
            if verdict == "readonly":
                readonly_rows.append(row)
                continue
            key = (str(row.get("agent_id") or ""),
                   str(row.get("patch_id") or ""))
            if not key[0] or not key[1]:
                continue
            current = by_key.get(key)
            if current is None:
                by_key[key] = row
                continue
            cur_rank = VERDICT_RANK.get(str(current.get("verdict")), 0)
            new_rank = VERDICT_RANK.get(str(row.get("verdict")), 0)
            if new_rank >= cur_rank:
                by_key[key] = row
    out = {key: [] for key in buckets}
    for row in by_key.values():
        verdict = str(row.get("verdict") or "pending")
        out.setdefault(verdict, []).append(row)
    write_keys = {
        (str(row.get("agent_id") or ""), str(row.get("patch_id") or ""))
        for row in by_key.values()
    }
    for row in readonly_rows:
        key = (str(row.get("agent_id") or ""), str(row.get("patch_id") or ""))
        if key not in write_keys:
            out["readonly"].append(row)
    return out


def build_summary(paths: list[Path], review_events: Path) -> dict:
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
            if verdict == "accepted" and not _valid_commit_sha(commit_sha):
                verdict = "pending"
            note = str(agent.get("review_note") or agent.get("note", ""))
            review_fields = _extract_review_fields(note)
            missing_fields = [
                field for field in MANDATORY_REVIEW_FIELDS
                if not review_fields.get(field)
            ]
            if verdict in {"accepted", "needs-work", "rejected"} and \
                    missing_fields:
                verdict = "pending"
        buckets[verdict].append(
            _bucket_row(str(path), agent, verdict,
                        missing_fields if mode != "readonly" else []))
    seen_events: set[tuple[str, str, str]] = set()
    for event in _jsonl(review_events):
        if event.get("event") != "review":
            continue
        verdict = str(event.get("verdict") or "pending")
        if verdict not in {"accepted", "needs-work", "rejected"}:
            continue
        patch_id = str(event.get("patch_id") or "").strip()
        agent_id = str(event.get("agent_id") or "").strip()
        key = (agent_id, patch_id, verdict)
        if not patch_id or key in seen_events:
            continue
        seen_events.add(key)
        if any(row.get("agent_id") == agent_id and
               row.get("patch_id") == patch_id
               for row in buckets.get(verdict, [])):
            continue
        note = str(event.get("note") or "")
        fields = _extract_review_fields(note)
        missing_fields = [
            field for field in MANDATORY_REVIEW_FIELDS
            if not fields.get(field)
        ]
        if missing_fields:
            verdict = "pending"
        if verdict == "accepted" and not _valid_commit_sha(
                str(event.get("commit_sha") or "")):
            verdict = "pending"
        buckets[verdict].append(
            _bucket_row(str(review_events), {
                "slot": event.get("slot"),
                "task": event.get("task"),
                "patch_id": patch_id,
                "agent_id": agent_id,
                "write_scope": event.get("write_scope") or [],
                "commit_sha": event.get("commit_sha") or "",
                "note": note,
            }, verdict, missing_fields))
    buckets = _reconcile_buckets(buckets)
    round_violations = [
        row for rows in buckets.values() for row in rows
        if int(row.get("review_round") or 0) > 1
    ]
    return {
        "schema": "veriput-rq1-patch-review-summary/v1",
        "reviewed_ledgers": [str(path) for path in paths] + [str(review_events)],
        "counts": {key: len(value) for key, value in buckets.items()},
        "review_round_limit": 1,
        "review_round_violations": round_violations,
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
    parser.add_argument("--review-events",
                        type=Path,
                        default=DEFAULT_REVIEW_EVENTS)
    args = parser.parse_args()
    print(
        json.dumps(
            build_summary([args.subagents, args.extra_subagents],
                          args.review_events),
            indent=2,
            sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
