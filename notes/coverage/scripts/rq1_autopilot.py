#!/usr/bin/env python3
"""Deterministic RQ1 automation entry point.

The repository process cannot call Codex host lifecycle tools directly.  This
script owns everything that *can* be automated from the repository side:

1. collect the current controller decision;
2. write host-level spawn/close/start-worker actions to a durable queue;
3. enforce the three-active-subagent threshold as a hard gate;
4. print the Chinese status format only when tracked values change.

The main agent must execute host actions from the queue in order and record the
result through rq1_subagent_orchestrator.py / rq1_subagent_autoclose.py.  If it
does anything else, it is outside the scripted workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
CONTROL = HERE / "rq1_agent_control.py"
AUTOCLOSE = HERE / "rq1_subagent_autoclose.py"
ORCHESTRATOR = HERE / "rq1_subagent_orchestrator.py"
REVIEW_INGEST = HERE / "rq1_review_ingest.py"
COMPLETION_INGEST = HERE / "rq1_completion_ingest.py"
MIN_ACTIVE = 3
MAX_SPAWN = 3

DEFAULT_HOST_ACTIONS = Path("/tmp/veriput_rq1_host_actions.jsonl")
DEFAULT_AUTOPILOT_STATE = Path("/tmp/veriput_rq1_autopilot_state.json")
DEFAULT_REPORT_CACHE = Path("/tmp/veriput_rq1_autopilot_report_cache.json")

HOST_ONLY_ACTIONS = {
    "spawn_agent",
    "close_agent_or_ack_not_found",
    "start_workers_on_theory_manifest",
}


def _run(cmd: list[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _fingerprint(action: dict) -> str:
    message = str(action.get("message") or "")
    stable = {
        key: action.get(key)
        for key in (
            "action",
            "agent_id",
            "slot",
            "patch_id",
            "bucket_key",
            "mode",
            "write_scope",
            "tsv",
            "case_count",
        )
    }
    if message:
        stable["message_sha256"] = hashlib.sha256(message.encode()).hexdigest()
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _append_new_actions(path: Path, actions: list[dict]) -> list[dict]:
    seen: set[str] = set()
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id"):
                seen.add(str(row["id"]))
    except OSError:
        pass

    added: list[dict] = []
    with path.open("a") as stream:
        for action in actions:
            if action.get("action") not in HOST_ONLY_ACTIONS:
                continue
            item = dict(action)
            item["id"] = _fingerprint(item)
            item["created_ts"] = time.time()
            item["status"] = "pending-host-execution"
            item["rule"] = (
                "Host action must be executed in order, then acknowledged in "
                "the RQ1 ledger. Repo scripts cannot perform this Codex host "
                "operation directly."
            )
            if item["id"] in seen:
                continue
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(item["id"])
            added.append(item)
    return added


def _rewrite_actions(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows))


def _load_actions(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _sync_completed_close_actions(path: Path) -> int:
    close_state = _json(Path("/tmp/veriput_rq1_subagent_close_state.json"))
    closed = set(close_state.get("closed_agent_ids") or [])
    if not closed:
        return 0
    rows = _load_actions(path)
    changed = 0
    for row in rows:
        if row.get("action") != "close_agent_or_ack_not_found":
            continue
        if row.get("status") != "pending-host-execution":
            continue
        if str(row.get("agent_id") or "") in closed:
            row["status"] = "done"
            row["done_ts"] = time.time()
            row["done_reason"] = "autoclose ack state contains agent_id"
            changed += 1
    if changed:
        _rewrite_actions(path, rows)
    return changed


def _control_json(args: argparse.Namespace) -> dict:
    cmd = [
        sys.executable,
        str(CONTROL),
        "--format",
        "json",
        "--min-active",
        str(args.min_active),
        "--max-spawn",
        str(args.max_spawn),
        "--remote-host",
        args.remote_host,
    ]
    if args.apply_safe_actions:
        cmd.append("--apply-safe-actions")
    proc = _run(cmd)
    if not proc.stdout.strip():
        raise SystemExit(proc.stderr.strip() or "rq1_agent_control.py produced no output")
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"控制器输出不是 JSON: {exc}\n{proc.stdout[-2000:]}") from exc
    if not isinstance(doc, dict):
        raise SystemExit("控制器输出 JSON 必须是对象")
    doc["_control_returncode"] = proc.returncode
    doc["_control_stderr_tail"] = proc.stderr[-1000:]
    return doc


def _tracked(doc: dict, added_actions: list[dict]) -> dict:
    progress = doc.get("actual_progress") if isinstance(doc.get("actual_progress"), dict) else {}
    review_summary = doc.get("patch_review_summary")
    if not isinstance(review_summary, dict):
        review_summary = {}
    review_counts = review_summary.get("counts") if isinstance(review_summary.get("counts"), dict) else {}
    host_rows = _load_actions(DEFAULT_HOST_ACTIONS)
    pending_host = [
        row for row in host_rows
        if row.get("status") == "pending-host-execution"
    ]
    return {
        "actual_valid": progress.get("actual_valid_cases"),
        "actual_subjects": progress.get("actual_subjects"),
        "actual_put": progress.get("actual_put_cases"),
        "actual_r1r2": progress.get("actual_r1r2_cases"),
        "actual_no_valid": progress.get("actual_no_valid_cases"),
        "theory_net": progress.get("theoretical_progress"),
        "theory_manifest_case_count": doc.get("theory_manifest_case_count"),
        "active_subagents": doc.get("active_subagents"),
        "min_active_subagents": doc.get("min_active_subagents"),
        "pending_close_count": doc.get("pending_close_count"),
        "repair_assignment_count": doc.get("repair_assignment_count"),
        "review_assignment_count": doc.get("review_assignment_count"),
        "review_counts": review_counts,
        "worker_running": doc.get("total_worker_running_case_count"),
        "local_worker_process_count": doc.get("local_worker_process_count"),
        "remote_worker_process_count": doc.get("remote_worker_process_count"),
        "pending_host_actions": len(pending_host),
        "pending_host_spawn": sum(
            1 for row in pending_host if row.get("action") == "spawn_agent"),
        "pending_host_close": sum(
            1 for row in pending_host
            if row.get("action") == "close_agent_or_ack_not_found"),
        "new_host_action_ids": [item.get("id") for item in added_actions],
        "gate": doc.get("gate"),
        "hard_fail": doc.get("hard_fail"),
    }


def _print_changed_report(doc: dict, added_actions: list[dict], cache: Path) -> None:
    current = _tracked(doc, added_actions)
    previous = _json(cache)
    changed = {
        key: {"old": previous.get(key), "new": value}
        for key, value in current.items()
        if previous.get(key) != value
    }
    _write_json(cache, current)
    if not changed:
        return

    progress = doc.get("actual_progress") if isinstance(doc.get("actual_progress"), dict) else {}
    print("RQ1自动流程变化报告:")
    print(f"  变化项={len(changed)}")
    for key in sorted(changed):
        print(f"  变化={key} 旧值={changed[key]['old']} 新值={changed[key]['new']}")
    print(
        "  实际RQ1="
        f"valid={progress.get('actual_valid_cases')}/{progress.get('actual_subjects')}"
        f" no_valid={progress.get('actual_no_valid_cases')}"
        f" PUT={progress.get('actual_put_cases')}"
        f" R1R2={progress.get('actual_r1r2_cases')}"
    )
    print(
        "  理论覆盖="
        f"net={progress.get('theoretical_progress')}"
        f" manifest={doc.get('theory_manifest_case_count')}"
        f" provisional={progress.get('implemented_progress_provisional')}"
    )
    print(
        "  subagent="
        f"active={doc.get('active_subagents')}/{doc.get('min_active_subagents')}"
        f" pending_close={doc.get('pending_close_count')}"
        f" repair_queue={doc.get('repair_assignment_count')}"
        f" review_queue={doc.get('review_assignment_count')}"
    )
    print(
        "  worker="
        f"running={doc.get('total_worker_running_case_count')}"
        f" local_proc={doc.get('local_worker_process_count')}"
        f" remote_proc={doc.get('remote_worker_process_count')}"
    )
    host_rows = _load_actions(DEFAULT_HOST_ACTIONS)
    pending_host = [
        row for row in host_rows
        if row.get("status") == "pending-host-execution"
    ]
    print(
        "  host动作队列="
        f"pending={len(pending_host)}"
        f" spawn={sum(1 for row in pending_host if row.get('action') == 'spawn_agent')}"
        f" close={sum(1 for row in pending_host if row.get('action') == 'close_agent_or_ack_not_found')}"
    )
    local_memory = doc.get("local_memory") if isinstance(doc.get("local_memory"), dict) else {}
    remote_memory = doc.get("remote_memory") if isinstance(doc.get("remote_memory"), dict) else {}
    print(
        "  内存="
        f"本机available={local_memory.get('available_gib')}GiB"
        f" 本机buff_cache={local_memory.get('buffer_cache_gib')}GiB"
        f" 远程available={remote_memory.get('available_gib')}GiB"
        f" 远程buff_cache={remote_memory.get('buffer_cache_gib')}GiB"
    )
    print(f"  门禁={doc.get('gate')} hard_fail={str(doc.get('hard_fail')).lower()}")
    for action in added_actions[:20]:
        print(
            f"  新host动作={action.get('id')}"
            f" type={action.get('action')}"
            f" bucket={action.get('bucket_key')}"
            f" agent={action.get('agent_id')}"
            f" case_count={action.get('case_count')}"
        )


def tick(args: argparse.Namespace) -> int:
    _sync_completed_close_actions(args.host_actions)
    doc = _control_json(args)
    added = _append_new_actions(args.host_actions, doc.get("actions") or [])
    closed_marked = _sync_completed_close_actions(args.host_actions)
    state = {
        "schema": "veriput-rq1-autopilot/v1",
        "updated_ts": time.time(),
        "control_returncode": doc.get("_control_returncode"),
        "host_actions": str(args.host_actions),
        "last_gate": doc.get("gate"),
        "last_hard_fail": doc.get("hard_fail"),
        "last_added_host_actions": added,
        "close_actions_marked_done": closed_marked,
        "hard_boundary": (
            "Codex host spawn/close are not callable from repo Python. This "
            "script is the authoritative queue/gate; the host executor must "
            "consume /tmp/veriput_rq1_host_actions.jsonl and then ack ledger."
        ),
    }
    _write_json(args.state, state)
    _print_changed_report(doc, added, args.report_cache)
    return 2 if doc.get("hard_fail") else 0


def ingest_review(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    cmd = [
        sys.executable,
        str(REVIEW_INGEST),
        "--reviewed-agent-id",
        args.reviewed_agent_id,
        "--patch-id",
        args.patch_id,
    ]
    if args.reviewer_id:
        cmd += ["--reviewer-id", args.reviewer_id]
    if args.auto_commit:
        cmd.append("--auto-commit")
    cmd.append("--record-invalid")
    if args.commit_message:
        cmd += ["--commit-message", args.commit_message]
    proc = _run(cmd, raw)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def ingest_completion(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    cmd = [
        sys.executable,
        str(COMPLETION_INGEST),
        "--patch-id",
        args.patch_id,
    ]
    if args.agent_id:
        cmd += ["--agent-id", args.agent_id]
    if args.slot:
        cmd += ["--slot", args.slot]
    proc = _run(cmd, raw)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-actions", type=Path, default=DEFAULT_HOST_ACTIONS)
    parser.add_argument("--state", type=Path, default=DEFAULT_AUTOPILOT_STATE)
    parser.add_argument("--report-cache", type=Path, default=DEFAULT_REPORT_CACHE)
    sub = parser.add_subparsers(dest="cmd", required=True)

    tick_cmd = sub.add_parser("tick")
    tick_cmd.add_argument("--min-active", type=int, default=MIN_ACTIVE)
    tick_cmd.add_argument("--max-spawn", type=int, default=MAX_SPAWN)
    tick_cmd.add_argument("--remote-host", default="invmut-w2")
    tick_cmd.add_argument("--apply-safe-actions", action="store_true")

    review = sub.add_parser("ingest-review")
    review.add_argument("--reviewed-agent-id", required=True)
    review.add_argument("--reviewer-id", default="")
    review.add_argument("--patch-id", required=True)
    review.add_argument("--auto-commit", action="store_true")
    review.add_argument("--commit-message", default="")

    completion = sub.add_parser("ingest-completion")
    completion.add_argument("--agent-id", default="")
    completion.add_argument("--slot", default="")
    completion.add_argument("--patch-id", required=True)

    args = parser.parse_args()
    if args.cmd == "tick":
        return tick(args)
    if args.cmd == "ingest-review":
        return ingest_review(args)
    if args.cmd == "ingest-completion":
        return ingest_completion(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
