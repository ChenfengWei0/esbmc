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
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
AUTOCLOSE = HERE / "rq1_subagent_autoclose.py"
WATCHDOG = HERE / "rq1_subagent_orchestrator.py"
REPAIR_DISPATCHER = HERE / "rq1_repair_dispatcher.py"
REVIEW_DISPATCHER = HERE / "rq1_review_dispatcher.py"
PATCH_REVIEW_SUMMARY = HERE / "rq1_patch_review_summary.py"
THEORY_CASES = HERE / "rq1_theory_covered_cases.py"
LEDGER = HERE / "rq1_no_valid_progress.py"
WATCHDOG_STATUS = HERE / "rq1_watchdog_status.py"
REAP_STALE_LEASES = HERE / "rq1_reap_stale_worker_leases.py"
DEFAULT_THEORY_TSV = Path("/tmp/veriput_rq1_theory_covered_cases.tsv")
DEFAULT_DELTA_CACHE = Path("/tmp/veriput_rq1_agent_control_snapshot.json")
DEFAULT_LOCAL_LEASES = Path("/tmp/veriput_rq1_case_leases.json")
DEFAULT_REMOTE_LEASE_DIR = "/tmp/veriput_rq1_case_leases.d"
FLOW_DOC = HERE / "rq1_automation_flow.md"
MIN_ACTIVE = 10
MAX_SPAWN = 10
WORKER_PATTERN = (
    "esbmc|rq1_veriput_run|certify_all|put_all|solidity_path_put|"
    "rq1_local_pump|rq1_remote_pump|forge|anvil")
LOCAL_WORKER_SCRIPT_RE = (
    r"/(rq1_veriput_run|rq1_local_pump|rq1_local_supervisor|certify_all|"
    r"put_all|solidity_path_put|solidity_path_generalise)\.py(\s|$)")


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


def _run_text(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _extract_key_values(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        if not line or "=" not in line or line.startswith(" "):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _extract_json_section(text: str, heading: str) -> dict:
    marker = f"{heading}:"
    start = text.find(marker)
    if start < 0:
        return {}
    brace = text.find("{", start + len(marker))
    if brace < 0:
        return {}
    depth = 0
    end = brace
    for pos in range(brace, len(text)):
        char = text[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    try:
        value = json.loads(text[brace:end])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _meminfo() -> dict:
    values = {}
    try:
        text = Path("/proc/meminfo").read_text(errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemAvailable|MemFree|Buffers|Cached):\s+(\d+)", line)
        if match:
            values[match.group(1)] = int(match.group(2)) / 1024 / 1024
    cached = values.get("Cached", 0.0) + values.get("Buffers", 0.0)
    return {
        "total_gib": round(values.get("MemTotal", 0.0), 3),
        "available_gib": round(values.get("MemAvailable", 0.0), 3),
        "free_gib": round(values.get("MemFree", 0.0), 3),
        "buffer_cache_gib": round(cached, 3),
    }


def _worker_process_count() -> int:
    proc = subprocess.run(
        ["ps", "-eo", "comm=,args="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if not parts:
            continue
        comm = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        if comm in {"esbmc", "forge", "anvil"}:
            rows.append(line)
        elif "/build/src/esbmc/esbmc" in args or "/release/bin/esbmc" in args:
            rows.append(line)
        elif re.search(LOCAL_WORKER_SCRIPT_RE, args):
            rows.append(line)
    return len(rows)


def _remote_resource_snapshot(host: str) -> dict:
    script = (
        "python3 - <<'PY'\n"
        "import json, re, subprocess\n"
        "mem = {}\n"
        "for line in open('/proc/meminfo', errors='replace'):\n"
        "    m = re.match(r'^(MemTotal|MemAvailable|MemFree|Buffers|Cached):\\s+(\\d+)', line)\n"
        "    if m:\n"
        "        mem[m.group(1)] = round(int(m.group(2)) / 1024 / 1024, 3)\n"
        "workers = subprocess.run(['pgrep','-af',"
        f"'{WORKER_PATTERN}'"
        "], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.splitlines()\n"
        "workers = [w for w in workers if 'pgrep -af' not in w]\n"
        "print(json.dumps({'memory': {'total_gib': mem.get('MemTotal', 0),"
        "'available_gib': mem.get('MemAvailable', 0),"
        "'free_gib': mem.get('MemFree', 0),"
        "'buffer_cache_gib': round(mem.get('Cached', 0) + mem.get('Buffers', 0), 3)},"
        "'worker_process_count': len(workers), 'workers': workers[:20]}, sort_keys=True))\n"
        "PY")
    doc = _run_json([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, script
    ])
    if not doc:
        return {
            "memory": {},
            "worker_process_count": 0,
            "workers": [],
            "probe_error": "remote resource probe failed",
        }
    return doc


def _progress_summary() -> dict:
    _rc, stdout, stderr = _run_text(
        [sys.executable, str(LEDGER), "--no-remote-probe"])
    keys = _extract_key_values(stdout)
    actual = _extract_json_section(stdout, "actual_rq1_progress")
    resources = _extract_json_section(stdout, "resource_maximization")
    return {
        "ledger_stderr_tail": stderr[-1000:],
        "actual_subjects": actual.get("subjects"),
        "actual_valid_cases": actual.get("valid_cases"),
        "actual_no_valid_cases": actual.get("no_valid_cases"),
        "actual_put_cases": actual.get("put_cases"),
        "actual_no_put_cases": actual.get("no_put_cases"),
        "actual_r1r2_cases": actual.get("r1r2_cases"),
        "actual_no_r1r2_cases": actual.get("no_r1r2_cases"),
        "theoretical_progress": keys.get("theoretical_progress"),
        "theoretical_progress_gross": keys.get("theoretical_progress_gross"),
        "implemented_progress_provisional": keys.get(
            "implemented_progress_provisional"),
        "implemented_progress_provisional_gross": keys.get(
            "implemented_progress_provisional_gross"),
        "put_theoretical_progress": keys.get("put_theoretical_progress"),
        "r1r2_theoretical_progress": keys.get("r1r2_theoretical_progress"),
        "resource_maximized": resources.get("maximized"),
        "resource_reasons": resources.get("reasons") or [],
    }


def _review_field(note: str, field: str) -> str:
    match = re.search(
        rf"(?:^|[;\n])\s*{re.escape(field)}\s*[:=]\s*(.*?)(?=(?:[;\n]\s*"
        rf"(?:changed_code|prior_failure|correctness_argument|verdict|"
        rf"theory_delta|next_action)\s*[:=])|$)",
        note,
        flags=re.DOTALL,
    )
    return (match.group(1).strip() if match else "")[:800]


def _watchdog_status(min_active: int) -> dict:
    doc = _run_json([
        sys.executable,
        str(WATCHDOG_STATUS),
        "--min-active-subagents",
        str(min_active),
        "--progress-tail",
        "3",
        "--remote-progress-tail",
        "3",
    ])
    return doc


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


def build_actions(theory_tsv: Path, min_active: int, max_spawn: int,
                  remote_host_name: str) -> dict:
    autoclose = _run_json([sys.executable, str(AUTOCLOSE), "plan"])
    watchdog = _run_json([sys.executable, str(WATCHDOG), "watchdog"])
    host_watchdog = _watchdog_status(min_active)
    repair = _run_json([sys.executable, str(REPAIR_DISPATCHER)])
    review = _run_json([sys.executable, str(REVIEW_DISPATCHER)])
    review_summary = _run_json([sys.executable, str(PATCH_REVIEW_SUMMARY)])
    theory = _run_json(
        [sys.executable, str(THEORY_CASES), "--out", str(theory_tsv)])
    progress = _progress_summary()
    mem = _meminfo()
    remote_resource = _remote_resource_snapshot(remote_host_name)

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
                     or active < min_active)
    local_worker = host_watchdog.get("local_worker")
    if not isinstance(local_worker, dict):
        local_worker = {}
    remote_worker = host_watchdog.get("remote_worker")
    if not isinstance(remote_worker, dict):
        remote_worker = {}
    remote_probe = host_watchdog.get("remote_probe")
    if not isinstance(remote_probe, dict):
        remote_probe = {}
    remote_parsed = remote_probe.get("parsed")
    if not isinstance(remote_parsed, dict):
        remote_parsed = {}
    remote_host = remote_parsed.get("host")
    if not isinstance(remote_host, dict):
        remote_host = {}
    worker_progress = host_watchdog.get("worker_progress")
    if not isinstance(worker_progress, dict):
        worker_progress = {}
    subagents = host_watchdog.get("subagents")
    if not isinstance(subagents, dict):
        subagents = {}
    local_process_count = _worker_process_count()
    remote_process_count = int(remote_parsed.get("process_count") or 0)
    local_progress_running = int(local_worker.get("running_case_count") or 0)
    remote_progress_running = int(remote_worker.get("running_case_count") or 0)
    local_running = local_progress_running if local_process_count else 0
    remote_running = remote_progress_running if remote_process_count else 0
    local_leases = host_watchdog.get("local_leases")
    if not isinstance(local_leases, dict):
        local_leases = {}
    remote_leases = remote_worker.get("leases")
    if not isinstance(remote_leases, dict):
        remote_leases = {}
    local_stale_leases = int(local_leases.get("stale_running_count") or 0)
    remote_stale_leases = int(remote_leases.get("stale_running_count") or 0)
    if local_stale_leases or remote_stale_leases:
        actions.insert(0, {
            "action": "reap_stale_worker_leases",
            "command": [
                sys.executable,
                str(REAP_STALE_LEASES),
            ],
            "local_stale": local_stale_leases,
            "remote_stale": remote_stale_leases,
            "reason": "lease says running but no live worker process owns it",
        })
    weak_or_failed_count = int(
        worker_progress.get("recent_failed_count_in_tail") or 0) + int(
            worker_progress.get("recent_oom_count_in_tail") or 0) + len(
                worker_progress.get("recent_weak_tail") or [])
    if weak_or_failed_count:
        actions.append({
            "action": "refresh_dispatch_and_spawn_repair_for_worker_feedback",
            "weak_or_failed_tail_count": weak_or_failed_count,
            "reason": (
                "worker output below valid PUT R1/R2 must subtract theory or "
                "spawn a repair/review assignment"),
        })
    return {
        "schema": "veriput-rq1-agent-control/v1",
        "actual_progress": progress,
        "active_subagents": active,
        "min_active_subagents": min_active,
        "active_subagent_details": subagents.get("active_details") or [],
        "pending_close_count": len(pending_close),
        "pending_close": pending_close,
        "non_medium_active_count": len(non_medium),
        "write_conflict_count": len(conflicts),
        "stale_agent_count": len(stale),
        "repair_assignment_count": repair.get("assignment_count"),
        "review_assignment_count": review.get("assignment_count"),
        "patch_review_summary": review_summary,
        "theory_manifest": str(theory_tsv),
        "theory_manifest_case_count": theory_case_count,
        "local_memory": mem,
        "local_worker_running_case_count": local_running,
        "remote_worker_running_case_count": remote_running,
        "total_worker_running_case_count": local_running + remote_running,
        "local_worker_process_count": local_process_count,
        "remote_worker_process_count": remote_process_count,
        "stale_worker_progress": {
            "local_progress_running_without_process":
                local_progress_running if not local_process_count else 0,
            "remote_progress_running_without_process":
                remote_progress_running if not remote_process_count else 0,
            "local_stale_lease_count": local_stale_leases,
            "remote_stale_lease_count": remote_stale_leases,
            "local_stale_leases": local_leases.get("stale_running_tail") or [],
            "remote_stale_leases":
                remote_leases.get("stale_running_tail") or [],
            "rule": (
                "Progress-file running rows are stale unless a matching "
                "local/remote worker process is alive."),
        },
        "remote_memory": {
            "available_gib":
                (remote_resource.get("memory") or {}).get("available_gib"),
            "nproc": remote_host.get("nproc"),
            "total_gib": (remote_resource.get("memory") or {}).get("total_gib"),
            "free_gib": (remote_resource.get("memory") or {}).get("free_gib"),
            "buffer_cache_gib":
                (remote_resource.get("memory") or {}).get("buffer_cache_gib"),
        },
        "remote_worker_process_details": remote_resource.get("workers") or [],
        "worker_progress": {
            "recent_done_count_in_tail":
                worker_progress.get("recent_done_count_in_tail"),
            "recent_failed_count_in_tail":
                worker_progress.get("recent_failed_count_in_tail"),
            "recent_oom_count_in_tail":
                worker_progress.get("recent_oom_count_in_tail"),
            "recent_weak_or_failed_count_in_tail":
                worker_progress.get("recent_weak_or_failed_count_in_tail"),
            "currently_running_cases":
                worker_progress.get("currently_running_cases") or [],
            "recent_done_tail": worker_progress.get("recent_done_tail") or [],
            "recent_failed_tail": worker_progress.get("recent_failed_tail") or [],
            "recent_weak_tail": worker_progress.get("recent_weak_tail") or [],
        },
        "local_worker": local_worker,
        "remote_worker": remote_worker,
        "host_tool_boundary": {
            "repo_script_can_spawn_codex_subagent": False,
            "repo_script_can_close_codex_subagent": False,
            "hard_rule": (
                "Repo scripts can only emit mandatory actions. The main "
                "agent must execute Codex host-layer spawn/close actions and "
                "then record lease/running/review/ack state. Follow "
                f"{FLOW_DOC}."),
        },
        "gate": gate,
        "hard_fail": hard_fail,
        "actions": actions,
        "rule": (
            "Consume actions in order. Do not start workers while the manifest "
            "is empty. Spawn actions must use reasoning_effort=medium and must "
            "be recorded via rq1_subagent_orchestrator.py lease/running. Keep "
            "at least 10 active subagents whenever repair/review assignments "
            f"exist. Follow {FLOW_DOC}."),
    }


def _tracked_snapshot(doc: dict) -> dict:
    review_summary = doc.get("patch_review_summary")
    if not isinstance(review_summary, dict):
        review_summary = {}
    review_buckets = review_summary.get("buckets")
    if not isinstance(review_buckets, dict):
        review_buckets = {}
    review_counts = review_summary.get("counts")
    if not isinstance(review_counts, dict):
        review_counts = {}
    return {
        "active_subagents": doc.get("active_subagents"),
        "min_active_subagents": doc.get("min_active_subagents"),
        "active_subagent_keys": [
            [
                item.get("agent_id"),
                item.get("slot"),
                item.get("mode"),
                item.get("task"),
            ]
            for item in (doc.get("active_subagent_details") or [])
        ],
        "pending_close_count": doc.get("pending_close_count"),
        "pending_close_keys": [
            [
                item.get("agent_id"),
                item.get("slot"),
                item.get("patch_id"),
            ]
            for item in (doc.get("pending_close") or [])
        ],
        "non_medium_active_count": doc.get("non_medium_active_count"),
        "write_conflict_count": doc.get("write_conflict_count"),
        "stale_agent_count": doc.get("stale_agent_count"),
        "repair_assignment_count": doc.get("repair_assignment_count"),
        "review_assignment_count": doc.get("review_assignment_count"),
        "theory_manifest_case_count": doc.get("theory_manifest_case_count"),
        "actual_progress": doc.get("actual_progress"),
        "local_worker_running_case_count": doc.get(
            "local_worker_running_case_count"),
        "remote_worker_running_case_count": doc.get(
            "remote_worker_running_case_count"),
        "total_worker_running_case_count": doc.get(
            "total_worker_running_case_count"),
        "local_worker_process_count": doc.get("local_worker_process_count"),
        "remote_worker_process_count": doc.get("remote_worker_process_count"),
        "stale_worker_progress": {
            "local_progress_running_without_process":
                (doc.get("stale_worker_progress") or {}).get(
                    "local_progress_running_without_process"),
            "remote_progress_running_without_process":
                (doc.get("stale_worker_progress") or {}).get(
                    "remote_progress_running_without_process"),
            "local_stale_lease_count":
                (doc.get("stale_worker_progress") or {}).get(
                    "local_stale_lease_count"),
            "remote_stale_lease_count":
                (doc.get("stale_worker_progress") or {}).get(
                    "remote_stale_lease_count"),
            "local_stale_lease_keys": [
                [
                    item.get("bench"),
                    item.get("subject"),
                    item.get("key"),
                ]
                for item in (
                    (doc.get("stale_worker_progress") or {}).get(
                        "local_stale_leases") or [])
            ],
            "remote_stale_lease_keys": [
                [
                    item.get("bench"),
                    item.get("subject"),
                    item.get("lease"),
                ]
                for item in (
                    (doc.get("stale_worker_progress") or {}).get(
                        "remote_stale_leases") or [])
            ],
        },
        "worker_progress": {
            "recent_done_count_in_tail":
                (doc.get("worker_progress") or {}).get(
                    "recent_done_count_in_tail"),
            "recent_failed_count_in_tail":
                (doc.get("worker_progress") or {}).get(
                    "recent_failed_count_in_tail"),
            "recent_oom_count_in_tail":
                (doc.get("worker_progress") or {}).get(
                    "recent_oom_count_in_tail"),
            "recent_weak_or_failed_count_in_tail":
                (doc.get("worker_progress") or {}).get(
                    "recent_weak_or_failed_count_in_tail"),
            "currently_running_keys": [
                [
                    item.get("bench"),
                    item.get("subject"),
                    item.get("status"),
                    item.get("bucket"),
                ]
                for item in (
                    (doc.get("worker_progress") or {}).get(
                        "currently_running_cases") or [])
            ],
            "recent_failed_keys": [
                [
                    item.get("bench"),
                    item.get("subject"),
                    item.get("status"),
                    item.get("bucket"),
                ]
                for item in (
                    (doc.get("worker_progress") or {}).get(
                        "recent_failed_tail") or [])
            ],
            "recent_weak_keys": [
                [
                    item.get("bench"),
                    item.get("subject"),
                    item.get("status"),
                    item.get("bucket"),
                ]
                for item in (
                    (doc.get("worker_progress") or {}).get(
                        "recent_weak_tail") or [])
            ],
        },
        "host_tool_boundary": doc.get("host_tool_boundary"),
        "gate": doc.get("gate"),
        "hard_fail": doc.get("hard_fail"),
        "review_counts": review_counts,
        "review_keys": {
            key: [
                [
                    item.get("slot"),
                    item.get("task"),
                    item.get("patch_id"),
                    item.get("agent_id"),
                    item.get("commit_sha"),
                    item.get("write_scope"),
                    item.get("note"),
                ]
                for item in (review_buckets.get(key) or [])
            ]
            for key in ("accepted", "pending", "needs-work", "rejected")
        },
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


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def _reap_stale_worker_leases(doc: dict, remote_host: str) -> dict:
    stale = doc.get("stale_worker_progress")
    if not isinstance(stale, dict):
        return {"local_removed": 0, "remote_removed": 0}
    if doc.get("local_worker_process_count") or doc.get(
            "remote_worker_process_count"):
        return {
            "local_removed": 0,
            "remote_removed": 0,
            "skipped": "live worker process exists",
        }

    removed_local = 0
    lease_doc = _read_json(DEFAULT_LOCAL_LEASES)
    leases = lease_doc.get("leases")
    if isinstance(leases, dict):
        for item in stale.get("local_stale_leases") or []:
            key = item.get("key")
            if key in leases:
                leases[key]["status"] = "stale-reaped"
                leases[key]["reaped_ts"] = time.time()
                removed_local += 1
        _write_json(DEFAULT_LOCAL_LEASES, lease_doc)

    remote_names = [
        str(item.get("lease") or "")
        for item in (stale.get("remote_stale_leases") or [])
        if item.get("lease")
    ]
    removed_remote = 0
    if remote_names:
        quoted = " ".join(
            shlex.quote(f"{DEFAULT_REMOTE_LEASE_DIR}/{name}")
            for name in remote_names)
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                remote_host,
                f"rm -rf {quoted}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            removed_remote = len(remote_names)
    return {
        "local_removed": removed_local,
        "remote_removed": removed_remote,
    }


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
    if changed is not None:
        if not changed:
            return
    print("RQ1自动控制报告:")
    if changed is not None:
        print(f"  变化项数量={len(changed)}")
        for key in sorted(changed):
            print(
                f"  变化={key} 旧值={changed[key]['old']} 新值={changed[key]['new']}")
    progress = doc.get("actual_progress")
    if not isinstance(progress, dict):
        progress = {}
    memory = doc.get("local_memory")
    if not isinstance(memory, dict):
        memory = {}
    remote_memory = doc.get("remote_memory")
    if not isinstance(remote_memory, dict):
        remote_memory = {}
    worker_progress = doc.get("worker_progress")
    if not isinstance(worker_progress, dict):
        worker_progress = {}
    print(
        "  实际RQ1="
        f"valid={progress.get('actual_valid_cases')}/{progress.get('actual_subjects')}"
        f" no_valid={progress.get('actual_no_valid_cases')}"
        f" PUT={progress.get('actual_put_cases')}"
        f" no_PUT={progress.get('actual_no_put_cases')}"
        f" R1R2={progress.get('actual_r1r2_cases')}"
        f" no_R1R2={progress.get('actual_no_r1r2_cases')}")
    print(
        "  理论覆盖="
        f"net={progress.get('theoretical_progress')}"
        f" gross={progress.get('theoretical_progress_gross')}"
        f" provisional={progress.get('implemented_progress_provisional')}"
        f" provisional_gross={progress.get('implemented_progress_provisional_gross')}"
        f" PUT={progress.get('put_theoretical_progress')}"
        f" R1R2={progress.get('r1r2_theoretical_progress')}")
    print(f"  活跃subagent={doc['active_subagents']}/{doc['min_active_subagents']}")
    for agent in (doc.get("active_subagent_details") or [])[:12]:
        print(
            f"    活跃subagent={agent.get('agent_id')}"
            f" slot={agent.get('slot')}"
            f" mode={agent.get('mode')}"
            f" 任务={agent.get('task')}"
            f" 运行秒={agent.get('runtime_s')}")
    print(f"  待关闭subagent={doc['pending_close_count']}")
    for item in (doc.get("pending_close") or [])[:8]:
        print(
            f"    待关闭={item.get('agent_id')}"
            f" slot={item.get('slot')}"
            f" patch={item.get('patch_id')}")
    print(f"  非medium活跃subagent={doc['non_medium_active_count']}")
    print(f"  写冲突数量={doc['write_conflict_count']}")
    print(f"  超时未响应subagent={doc['stale_agent_count']}")
    print(f"  待派修复任务={doc['repair_assignment_count']}")
    print(f"  待派review任务={doc['review_assignment_count']}")
    print(f"  理论覆盖worker清单case数={doc['theory_manifest_case_count']}")
    print(
        "  worker="
        f"本机运行case={doc.get('local_worker_running_case_count')}"
        f" 远程运行case={doc.get('remote_worker_running_case_count')}"
        f" 总运行case={doc.get('total_worker_running_case_count')}"
        f" 本机worker进程={doc.get('local_worker_process_count')}"
        f" 远程worker进程={doc.get('remote_worker_process_count')}")
    stale = doc.get("stale_worker_progress")
    if isinstance(stale, dict):
        print(
            "  陈旧worker进度="
            "本机无进程running="
            f"{stale.get('local_progress_running_without_process')}"
            " 远程无进程running="
            f"{stale.get('remote_progress_running_without_process')}")
        print(
            "  陈旧worker租约="
            f"本机={stale.get('local_stale_lease_count')}"
            f" 远程={stale.get('remote_stale_lease_count')}")
        for item in (stale.get("local_stale_leases") or [])[:5]:
            print(
                f"    本机陈旧租约={item.get('bench')}/{item.get('subject')}"
                f" age_s={item.get('age_s')}"
                f" worker={item.get('worker_id')}")
        for item in (stale.get("remote_stale_leases") or [])[:5]:
            print(
                f"    远程陈旧租约={item.get('bench')}/{item.get('subject')}"
                f" age_s={item.get('age_s')}"
                f" lease={item.get('lease')}")
    print(
        "  本机内存="
        f"total={memory.get('total_gib')}GiB"
        f" available={memory.get('available_gib')}GiB"
        f" free={memory.get('free_gib')}GiB"
        f" buff_cache={memory.get('buffer_cache_gib')}GiB")
    remote_memory = doc.get("remote_memory")
    if not isinstance(remote_memory, dict):
        remote_memory = {}
    print(
        "  远程内存="
        f"total={remote_memory.get('total_gib')}GiB"
        f" available={remote_memory.get('available_gib')}GiB"
        f" free={remote_memory.get('free_gib')}GiB"
        f" buff_cache={remote_memory.get('buffer_cache_gib')}GiB"
        f" nproc={remote_memory.get('nproc')}")
    for worker in (doc.get("remote_worker_process_details") or [])[:6]:
        print(f"    远程worker明细={worker[:220]}")
    print(
        "  worker最近反馈="
        f"done={worker_progress.get('recent_done_count_in_tail')}"
        f" failed={worker_progress.get('recent_failed_count_in_tail')}"
        f" oom={worker_progress.get('recent_oom_count_in_tail')}"
        f" weak_or_failed={worker_progress.get('recent_weak_or_failed_count_in_tail')}")
    if int(doc.get("total_worker_running_case_count") or 0) > 0:
        for item in (worker_progress.get("currently_running_cases") or [])[:8]:
            print(
                f"    正在跑case={item.get('bench')}/{item.get('subject')}"
                f" status={item.get('status')} bucket={item.get('bucket')}")
    for item in (worker_progress.get("recent_failed_tail") or [])[-5:]:
        print(
            f"    最近失败case={item.get('bench')}/{item.get('subject')}"
            f" status={item.get('status')} bucket={item.get('bucket')}"
            f" valid={item.get('valid')} PUT={item.get('put_valid')}"
            f" R1R2={item.get('r1r2')}")
    for item in (worker_progress.get("recent_weak_tail") or [])[-5:]:
        print(
            f"    最近弱case={item.get('bench')}/{item.get('subject')}"
            f" status={item.get('status')} bucket={item.get('bucket')}"
            f" valid={item.get('valid')} PUT={item.get('put_valid')}"
            f" R1R2={item.get('r1r2')}")
    reasons = progress.get("resource_reasons") or []
    print(f"  资源最大化={progress.get('resource_maximized')}")
    if reasons:
        print("  未最大化原因=" + "；".join(map(str, reasons)))
    print(f"  worker门禁={doc['gate']}")
    print(f"  硬失败={str(doc['hard_fail']).lower()}")
    review_summary = doc.get("patch_review_summary")
    if not isinstance(review_summary, dict):
        review_summary = {}
    counts = review_summary.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    print(
        "  review汇总="
        f"accepted={counts.get('accepted')}"
        f" pending={counts.get('pending')}"
        f" needs_work={counts.get('needs-work')}"
        f" rejected={counts.get('rejected')}")
    buckets = review_summary.get("buckets")
    if not isinstance(buckets, dict):
        buckets = {}
    for verdict, label in (
            ("accepted", "review通过"),
            ("needs-work", "review不通过需返工"),
            ("rejected", "review拒绝"),
            ("pending", "等待review"),
    ):
        for item in (buckets.get(verdict) or [])[:8]:
            note = str(item.get("note") or "")
            print(
                f"    {label}={item.get('slot')}/{item.get('patch_id')}"
                f" agent={item.get('agent_id')}"
                f" commit={item.get('commit_sha')}"
                f" 任务={item.get('task')}"
                f" 修改范围={','.join(item.get('write_scope') or [])}")
            for field, field_label in (
                    ("changed_code", "改了什么"),
                    ("prior_failure", "为什么改"),
                    ("correctness_argument", "是否正确"),
                    ("verdict", "review结论"),
                    ("theory_delta", "理论变化"),
                    ("next_action", "下一步"),
            ):
                value = _review_field(note, field) or "<缺失>"
                print(f"      {field_label}={value[:260]}")
            missing = item.get("missing_review_fields") or []
            if missing:
                print(f"      review缺字段={','.join(missing)}")
    print("  自动动作:")
    for index, action in enumerate(doc.get("actions") or [], 1):
        print(
            f"    {index}. 动作={action.get('action')}"
            f" bucket={action.get('bucket_key')}"
            f" effort={action.get('reasoning_effort')}"
            f" 原因={action.get('reason')}"
            f" 本机陈旧={action.get('local_stale')}"
            f" 远程陈旧={action.get('remote_stale')}"
            f" 弱/失败tail={action.get('weak_or_failed_tail_count')}")
    for item in doc.get("safe_actions_applied") or []:
        print(
            f"    已执行安全动作={item.get('action')}"
            f" 结果={item.get('result')}")
    boundary = doc.get("host_tool_boundary")
    if isinstance(boundary, dict):
        print(
            "  host工具边界="
            f"脚本可spawn={boundary.get('repo_script_can_spawn_codex_subagent')}"
            f" 脚本可close={boundary.get('repo_script_can_close_codex_subagent')}"
            " 必须由主agent执行host动作并回填ledger=true")
    print(f"  规则=必须按自动动作顺序执行；禁止手写漂移状态；流程文件={FLOW_DOC}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theory-tsv", type=Path, default=DEFAULT_THEORY_TSV)
    parser.add_argument("--min-active", type=int, default=MIN_ACTIVE)
    parser.add_argument("--max-spawn", type=int, default=10)
    parser.add_argument("--remote-host", default="invmut-w2")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--only-changes", action="store_true")
    parser.add_argument("--apply-safe-actions", action="store_true")
    parser.add_argument("--delta-cache", type=Path, default=DEFAULT_DELTA_CACHE)
    args = parser.parse_args()
    doc = build_actions(args.theory_tsv, args.min_active, args.max_spawn,
                        args.remote_host)
    if args.apply_safe_actions:
        applied = []
        if any(action.get("action") == "reap_stale_worker_leases"
               for action in doc.get("actions") or []):
            applied.append({
                "action": "reap_stale_worker_leases",
                "result": _reap_stale_worker_leases(doc, args.remote_host),
            })
        doc["safe_actions_applied"] = applied
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
