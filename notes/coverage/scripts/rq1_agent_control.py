#!/usr/bin/env python3
"""Single RQ1 control loop decision point.

This script cannot call Codex host tools such as spawn_agent/close_agent from
inside the repository.  It does make the control policy deterministic: every
turn must consume the emitted actions in order.  Workers are allowed only after
subagent capacity, review, theory manifest, and close-state gates are clean.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
AUTOCLOSE = HERE / "rq1_subagent_autoclose.py"
WATCHDOG = HERE / "rq1_subagent_orchestrator.py"
REPAIR_DISPATCHER = HERE / "rq1_repair_dispatcher.py"
REVIEW_DISPATCHER = HERE / "rq1_review_dispatcher.py"
THEORY_CASES = HERE / "rq1_theory_covered_cases.py"
DEFAULT_THEORY_TSV = Path("/tmp/veriput_rq1_theory_covered_cases.tsv")
DEFAULT_DELTA_CACHE = Path("/tmp/veriput_rq1_agent_control_snapshot.json")
MIN_ACTIVE = 5


def _run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    try:
        doc = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        doc = {}
    doc["_returncode"] = proc.returncode
    doc["_stderr_tail"] = proc.stderr[-1000:]
    return doc


def _count_tsv_rows(path: Path) -> int:
    try:
        with path.open() as stream:
            return max(0, sum(1 for _line in stream) - 1)
    except OSError:
        return 0


def _spawn_action(assignment: dict) -> dict:
    prompt = assignment.get("prompt")
    if not prompt:
        subjects = assignment.get("subjects") or []
        subject_lines = "\n".join(
            f"- {row.get('bench')}/{row.get('subject')} "
            f"category={row.get('category') or row.get('result_bucket')}"
            for row in subjects[:8])
        prompt = f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC/RQ1/ctest/pytest/certify_all/put_all/solidity_path_put.
Do NOT touch /home/samson/workspace/VeriPUT/Datasets.

Repair assignment: {assignment.get('bucket_key')}.
Mode: {assignment.get('mode')}.
Exclusive write scope: {', '.join(assignment.get('write_scope') or [])}.

Inspect listed failed/weak cases and owning source code. Patch only if you find
a code-level root cause in the assigned scope. Completion must include inspected
artifacts, changed paths, correctness argument, and exact theory_delta.

Subjects:
{subject_lines}
"""
    return {
        "action": "spawn_agent",
        "reasoning_effort": "medium",
        "bucket_key": assignment.get("bucket_key"),
        "mode": assignment.get("mode"),
        "write_scope": assignment.get("write_scope") or [],
        "message": prompt,
    }


def build_actions(theory_tsv: Path, min_active: int, max_spawn: int) -> dict:
    autoclose = _run_json([sys.executable, str(AUTOCLOSE), "plan"])
    watchdog = _run_json([sys.executable, str(WATCHDOG), "watchdog"])
    repair = _run_json([sys.executable, str(REPAIR_DISPATCHER)])
    review = _run_json([sys.executable, str(REVIEW_DISPATCHER)])
    theory = _run_json(
        [sys.executable, str(THEORY_CASES), "--out", str(theory_tsv)])

    actions = []
    pending_close = autoclose.get("pending_close") or []
    for item in pending_close:
        actions.append({
            "action": "close_agent_or_ack_not_found",
            "agent_id": item.get("agent_id"),
            "slot": item.get("slot"),
            "patch_id": item.get("patch_id"),
        })

    active = int(watchdog.get("active_count") or 0)
    non_medium = watchdog.get("non_medium_active_agents") or []
    conflicts = watchdog.get("write_conflicts") or []
    stale = watchdog.get("stale_running_agents") or []
    if pending_close:
        gate = "blocked_pending_close"
    elif non_medium:
        gate = "blocked_non_medium_active"
    elif conflicts:
        gate = "blocked_write_conflicts"
    elif stale:
        gate = "blocked_stale_agents"
    else:
        gate = "open"

    if gate == "open" and active < min_active:
        needed = min(max_spawn, min_active - active)
        review_assignments = review.get("assignments") or []
        repair_assignments = repair.get("assignments") or []
        spawn_from = review_assignments + repair_assignments
        for assignment in spawn_from[:needed]:
            actions.append(_spawn_action(assignment))

    theory_case_count = _count_tsv_rows(theory_tsv)
    if theory_case_count > 0 and active >= min_active and gate == "open":
        actions.append({
            "action": "start_workers_on_theory_manifest",
            "tsv": str(theory_tsv),
            "case_count": theory_case_count,
            "rule": "workers must run only theory-covered cases",
        })
    elif theory_case_count <= 0:
        actions.append({
            "action": "do_not_start_workers",
            "reason": "theory_manifest_empty",
            "case_count": theory_case_count,
        })

    hard_fail = bool(pending_close or non_medium or conflicts or stale
                     or (active < min_active and not actions))
    return {
        "schema": "veriput-rq1-agent-control/v1",
        "active_subagents": active,
        "min_active_subagents": min_active,
        "pending_close_count": len(pending_close),
        "non_medium_active_count": len(non_medium),
        "write_conflict_count": len(conflicts),
        "stale_agent_count": len(stale),
        "repair_assignment_count": repair.get("assignment_count"),
        "review_assignment_count": review.get("assignment_count"),
        "theory_manifest": str(theory_tsv),
        "theory_manifest_case_count": theory_case_count,
        "gate": gate,
        "hard_fail": hard_fail,
        "actions": actions,
        "rule": (
            "Consume actions in order. Do not start workers while the manifest "
            "is empty. Spawn actions must use reasoning_effort=medium and must "
            "be recorded via rq1_subagent_orchestrator.py lease/running."),
    }


def _tracked_snapshot(doc: dict) -> dict:
    return {
        "active_subagents": doc.get("active_subagents"),
        "min_active_subagents": doc.get("min_active_subagents"),
        "pending_close_count": doc.get("pending_close_count"),
        "non_medium_active_count": doc.get("non_medium_active_count"),
        "write_conflict_count": doc.get("write_conflict_count"),
        "stale_agent_count": doc.get("stale_agent_count"),
        "repair_assignment_count": doc.get("repair_assignment_count"),
        "review_assignment_count": doc.get("review_assignment_count"),
        "theory_manifest_case_count": doc.get("theory_manifest_case_count"),
        "gate": doc.get("gate"),
        "hard_fail": doc.get("hard_fail"),
        "action_keys": [
            [
                action.get("action"),
                action.get("bucket_key"),
                action.get("reasoning_effort"),
                action.get("reason"),
            ]
            for action in (doc.get("actions") or [])
        ],
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _delta(doc: dict, cache: Path) -> dict:
    current = _tracked_snapshot(doc)
    previous = _read_json(cache)
    changed = {
        key: {
            "old": previous.get(key),
            "new": value,
        }
        for key, value in current.items()
        if previous.get(key) != value
    }
    cache.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    return changed


def _print_text(doc: dict, changed: dict | None) -> None:
    print("rq1_agent_control_report:")
    if changed is not None:
        print(f"  changed_count={len(changed)}")
        for key in sorted(changed):
            print(f"  changed={key} old={changed[key]['old']} new={changed[key]['new']}")
        if not changed:
            return
    print(f"  active_subagents={doc['active_subagents']}/{doc['min_active_subagents']}")
    print(f"  pending_close_count={doc['pending_close_count']}")
    print(f"  non_medium_active_count={doc['non_medium_active_count']}")
    print(f"  write_conflict_count={doc['write_conflict_count']}")
    print(f"  stale_agent_count={doc['stale_agent_count']}")
    print(f"  repair_assignment_count={doc['repair_assignment_count']}")
    print(f"  review_assignment_count={doc['review_assignment_count']}")
    print(f"  theory_manifest_case_count={doc['theory_manifest_case_count']}")
    print(f"  gate={doc['gate']}")
    print(f"  hard_fail={str(doc['hard_fail']).lower()}")
    print("  actions:")
    for index, action in enumerate(doc.get("actions") or [], 1):
        print(
            f"    {index}. action={action.get('action')}"
            f" bucket={action.get('bucket_key')}"
            f" effort={action.get('reasoning_effort')}"
            f" reason={action.get('reason')}")
    print("  rule=execute_actions_in_order_and_do_not_hand_format_status")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theory-tsv", type=Path, default=DEFAULT_THEORY_TSV)
    parser.add_argument("--min-active", type=int, default=MIN_ACTIVE)
    parser.add_argument("--max-spawn", type=int, default=5)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--only-changes", action="store_true")
    parser.add_argument("--delta-cache", type=Path, default=DEFAULT_DELTA_CACHE)
    args = parser.parse_args()
    doc = build_actions(args.theory_tsv, args.min_active, args.max_spawn)
    if args.format == "json":
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        changed = _delta(doc, args.delta_cache) if args.only_changes else None
        if args.only_changes and not changed:
            return 0
        _print_text(doc, changed)
    return 2 if doc.get("hard_fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
