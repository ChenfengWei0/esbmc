#!/usr/bin/env python3
"""Deterministic subagent lease manager for RQ1.

This file is the hard rule for avoiding multi-agent write conflicts.  A
subagent may edit only after its slot has an active lease whose write_scope does
not overlap any other active lease.  Completed agents can record a patch_id;
the progress ledger counts only completed patch_ids.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

from rq1_no_valid_progress import SUBAGENT_PLAN


DEFAULT_STATE = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_LOCK = Path("/tmp/veriput_rq1_subagents.lock")
DEFAULT_REVIEW_EVENTS = Path("/tmp/veriput_rq1_review_events.jsonl")
DEFAULT_MAX_AGENTS = 24
DEFAULT_STALE_MINUTES = 20.0
REQUIRED_REASONING_EFFORT = "medium"
AUTOCLOSE = Path(__file__).resolve().parent / "rq1_subagent_autoclose.py"


def now() -> float:
    return time.time()


def split_scope(scope: str) -> list[str]:
    if scope == "all touched files, no independent writes unless resolving conflicts":
        return ["<integration-review-only>"]
    return [item.strip() for item in scope.split(",") if item.strip()]


def plan_by_slot() -> dict[str, dict]:
    out = {}
    for slot, task, write_scope, coverage in SUBAGENT_PLAN:
        out[slot] = {
            "slot": slot,
            "task": task,
            "write_scope": split_scope(write_scope),
            "expected_coverage": coverage,
        }
    return out


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {
        "schema": "veriput-rq1-subagents/v1",
        "status": "active",
        "max_concurrent_threads_per_session": DEFAULT_MAX_AGENTS,
        "agents": [],
    }


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def locked_state(path: Path, lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = load_state(path)
        yield state
        save_state(path, state)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def pending_close_count() -> int:
    proc = subprocess.run(
        [sys.executable, str(AUTOCLOSE), "plan"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "subagent autoclose plan failed; cannot lease more agents")
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"subagent autoclose plan emitted invalid JSON: {exc}") from exc
    return int(doc.get("pending_close_count") or 0)


def active_agents(state: dict) -> list[dict]:
    return [
        agent for agent in state.get("agents") or []
        if agent.get("status") in ("leased", "running")
    ]


def scope_conflicts(scope_a: list[str], scope_b: list[str]) -> list[str]:
    a = set(scope_a)
    b = set(scope_b)
    if "<integration-review-only>" in a or "<integration-review-only>" in b:
        return []
    return sorted(a & b)


def lease_slot(state: dict, slot: str, agent_id: str, mode: str,
               allow_pending_close: bool = False) -> dict:
    pending = pending_close_count()
    if pending and not allow_pending_close:
        raise SystemExit(
            "COMPLETED_SUBAGENTS_NOT_CLOSED: "
            f"pending_close_count={pending}; close or ack before leasing")
    plan = plan_by_slot()
    if slot not in plan:
        raise SystemExit(f"unknown slot {slot}")
    if mode not in ("write", "readonly"):
        raise SystemExit("--mode must be write or readonly")
    for existing in state.get("agents") or []:
        if existing.get("agent_id") != agent_id:
            continue
        status = existing.get("status")
        if status in ("leased", "running", "completed"):
            raise SystemExit(
                "duplicate agent_id lease refused: "
                f"agent_id={agent_id} existing_status={status} "
                f"slot={existing.get('slot')}")
    lease = dict(plan[slot])
    lease.update({
        "agent_id": agent_id,
        "mode": mode,
        "status": "leased",
        "leased_ts": now(),
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "required_reasoning_effort": REQUIRED_REASONING_EFFORT,
    })
    if mode == "write":
        for active in active_agents(state):
            if active.get("mode") != "write":
                continue
            overlap = scope_conflicts(lease["write_scope"],
                                      active.get("write_scope") or [])
            if overlap:
                raise SystemExit(
                    f"write_scope conflict: {slot} overlaps "
                    f"{active.get('slot')} on {overlap}")
    state.setdefault("agents", []).append(lease)
    return lease


def complete_agent(state: dict, agent_id: str, patch_id: str) -> dict:
    for agent in state.get("agents") or []:
        if agent.get("agent_id") == agent_id:
            agent["status"] = "completed"
            agent["completed_ts"] = now()
            if patch_id:
                agent["patch_id"] = patch_id
            if agent.get("mode") == "write":
                agent.setdefault("review_status", "pending")
            return agent
    raise SystemExit(f"agent_id not found: {agent_id}")


def review_agent(
    state: dict,
    agent_id: str,
    reviewer_id: str,
    verdict: str,
    note: str,
    commit_sha: str,
) -> dict:
    if verdict not in ("accepted", "rejected", "needs-work"):
        raise SystemExit("--verdict must be accepted, rejected, or needs-work")
    if verdict == "accepted" and not commit_sha.strip():
        raise SystemExit(
            "--commit-sha is required when --verdict accepted; review without "
            "a commit cannot count toward net theory")
    for agent in state.get("agents") or []:
        if agent.get("agent_id") == agent_id:
            if agent.get("mode") != "write":
                raise SystemExit("only write-mode agents require review")
            agent["review_status"] = verdict
            agent["reviewed_ts"] = now()
            agent["reviewer_id"] = reviewer_id
            if commit_sha.strip():
                agent["commit_sha"] = commit_sha.strip()
                agent["review_commit"] = commit_sha.strip()
            if note:
                agent["review_note"] = note
            event = {
                "event": "review",
                "ts": now(),
                "slot": agent.get("slot"),
                "task": agent.get("task"),
                "agent_id": agent_id,
                "reviewer_id": reviewer_id,
                "patch_id": agent.get("patch_id"),
                "verdict": verdict,
                "commit_sha": commit_sha.strip(),
                "note": note,
                "write_scope": agent.get("write_scope") or [],
            }
            with DEFAULT_REVIEW_EVENTS.open("a") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
            return agent
    raise SystemExit(f"agent_id not found: {agent_id}")


def mark_running(state: dict, agent_id: str) -> dict:
    for agent in state.get("agents") or []:
        if agent.get("agent_id") == agent_id:
            agent["status"] = "running"
            agent["running_ts"] = now()
            return agent
    raise SystemExit(f"agent_id not found: {agent_id}")


def print_prompts(state: dict) -> None:
    active_slots = {agent.get("slot") for agent in active_agents(state)}
    completed_slots = {
        agent.get("slot") for agent in state.get("agents") or []
        if agent.get("status") == "completed"
    }
    for slot, item in plan_by_slot().items():
        if slot in active_slots or slot in completed_slots:
            continue
        print(f"{slot}\t{item['task']}\t{','.join(item['write_scope'])}")


def watchdog_report(state: dict, stale_minutes: float) -> dict:
    current = now()
    active = active_agents(state)
    completed = [
        agent for agent in state.get("agents") or []
        if agent.get("status") == "completed"
    ]
    plan = plan_by_slot()
    leased_or_done = {agent.get("slot") for agent in state.get("agents") or []}
    stale_threshold = max(0.0, stale_minutes) * 60.0
    stale = []
    for agent in active:
        started = float(agent.get("running_ts") or agent.get("leased_ts") or current)
        age_s = max(0.0, current - started)
        if age_s >= stale_threshold:
            stale.append({
                "slot": agent.get("slot"),
                "agent_id": agent.get("agent_id"),
                "age_s": round(age_s, 3),
                "task": agent.get("task"),
                "write_scope": agent.get("write_scope") or [],
            })

    conflicts = []
    for index, left in enumerate(active):
        if left.get("mode") != "write":
            continue
        for right in active[index + 1:]:
            if right.get("mode") != "write":
                continue
            overlap = scope_conflicts(left.get("write_scope") or [],
                                      right.get("write_scope") or [])
            if overlap:
                conflicts.append({
                    "left_slot": left.get("slot"),
                    "right_slot": right.get("slot"),
                    "overlap": overlap,
                })

    completed_without_patch = [
        {
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "task": agent.get("task"),
        }
        for agent in completed
        if not agent.get("patch_id")
    ]
    completed_write_without_review = [
        {
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "task": agent.get("task"),
            "patch_id": agent.get("patch_id"),
            "review_status": agent.get("review_status") or "pending",
            "write_scope": agent.get("write_scope") or [],
        }
        for agent in completed
        if agent.get("mode") == "write"
        and (agent.get("review_status") or "pending") != "accepted"
    ]
    accepted_without_commit = [
        {
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "patch_id": agent.get("patch_id"),
            "reviewer_id": agent.get("reviewer_id"),
        }
        for agent in completed
        if agent.get("mode") == "write"
        and (agent.get("review_status") or "pending") == "accepted"
        and not str(agent.get("review_commit") or agent.get("commit_sha")
                    or agent.get("commit") or agent.get("patch_commit")
                    or "").strip()
    ]
    non_medium_active = [
        {
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "reasoning_effort": agent.get("reasoning_effort"),
            "task": agent.get("task"),
        }
        for agent in active
        if str(agent.get("reasoning_effort") or "") != REQUIRED_REASONING_EFFORT
    ]
    queued_slots = [
        {
            "slot": slot,
            "task": item["task"],
            "write_scope": item["write_scope"],
        }
        for slot, item in plan.items()
        if slot not in leased_or_done
    ]
    return {
        "schema": "veriput-rq1-subagent-watchdog/v1",
        "active_count": len(active),
        "completed_count": len(completed),
        "queued_count": len(queued_slots),
        "capacity_configured": int(state.get("max_concurrent_threads_per_session")
                                   or DEFAULT_MAX_AGENTS),
        "stale_threshold_s": round(stale_threshold, 3),
        "stale_running_agents": stale,
        "write_conflicts": conflicts,
        "completed_without_patch_id": completed_without_patch,
        "completed_write_agents_without_accepted_review":
            completed_write_without_review,
        "accepted_reviews_without_commit": accepted_without_commit,
        "non_medium_active_agents": non_medium_active,
        "required_reasoning_effort": REQUIRED_REASONING_EFFORT,
        "queued_slots": queued_slots,
        "supervision_rule": (
            "Poll this command before progress reports; stale agents must be "
            "queried or replaced, completed agents must have patch_id before "
            "counting toward N/204, write_conflicts must be resolved before "
            "accepting any patch, and every write-mode completed patch must "
            "receive an independent accepted review before it is treated as "
            "fully integrated. Subagents must inspect prior failure records "
            "and owning source code before editing; fresh ESBMC/RQ1 runs are "
            "not accepted as a substitute for code-level root-cause analysis. "
            "Completion must state inspected failure artifacts, inspected code, "
            "root cause, fix target, and theoretical coverage. Accepted "
            "reviews must record a commit sha, and every spawned subagent must "
            "be explicitly requested with reasoning_effort=medium."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("available")
    watchdog = sub.add_parser("watchdog")
    watchdog.add_argument("--stale-minutes",
                          type=float,
                          default=DEFAULT_STALE_MINUTES)
    lease = sub.add_parser("lease")
    lease.add_argument("--slot", required=True)
    lease.add_argument("--agent-id", required=True)
    lease.add_argument("--mode", choices=("write", "readonly"), default="write")
    lease.add_argument("--allow-pending-close", action="store_true")
    running = sub.add_parser("running")
    running.add_argument("--agent-id", required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("--agent-id", required=True)
    complete.add_argument("--patch-id", default="")
    review = sub.add_parser("review")
    review.add_argument("--agent-id", required=True)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument("--verdict",
                        choices=("accepted", "rejected", "needs-work"),
                        required=True)
    review.add_argument("--note", default="")
    review.add_argument("--commit-sha", default="")
    args = parser.parse_args()

    if args.cmd in {"lease", "running", "complete", "review"}:
        with locked_state(args.state, args.lock) as state:
            if args.cmd == "lease":
                result = lease_slot(state, args.slot, args.agent_id, args.mode,
                                    args.allow_pending_close)
            elif args.cmd == "running":
                result = mark_running(state, args.agent_id)
            elif args.cmd == "complete":
                result = complete_agent(state, args.agent_id, args.patch_id)
            else:
                result = review_agent(state, args.agent_id, args.reviewer_id,
                                      args.verdict, args.note, args.commit_sha)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    state = load_state(args.state)
    if args.cmd == "plan":
        print(json.dumps({
            "state": state,
            "plan": plan_by_slot(),
            "active_agents": active_agents(state),
        }, indent=2, sort_keys=True))
    elif args.cmd == "available":
        print_prompts(state)
    elif args.cmd == "watchdog":
        print(json.dumps(watchdog_report(state, args.stale_minutes),
                         indent=2,
                         sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
