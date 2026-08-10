#!/usr/bin/env python3
"""Unified watchdog status for RQ1 agents and ESBMC workers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


DEFAULT_SUBAGENT_STATE = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENT_STATE = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_LOCAL_STATE = Path("/tmp/veriput_rq1_local_state.json")
DEFAULT_EXTRA_LOCAL_STATE_GLOB = "/tmp/veriput_rq1_local_extra_state*.json"
DEFAULT_REMOTE_STATE = Path("/tmp/veriput_rq1_remote_state.json")
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_ESBMC_RSS_LIMIT_GIB = 12
DEFAULT_PROGRESS_GLOBS = (
    "/tmp/veriput_local_progress.jsonl",
    "/tmp/veriput_local_extra_progress.jsonl",
    "/tmp/veriput_local_extra*_progress.jsonl",
    "/tmp/veriput_local_oom_progress.jsonl",
    "/tmp/veriput_remote_progress.jsonl",
)
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_DISPATCH_QUEUE = Path("/tmp/veriput_rq1_dispatch_queue.json")
DEFAULT_REVIEW_QUEUE = Path("/tmp/veriput_rq1_review_queue.json")
DEFAULT_SUBAGENT_CLOSE_STATE = Path("/tmp/veriput_rq1_subagent_close_state.json")
DEFAULT_OOM_HIGHMEM_QUEUE = Path("/tmp/veriput_rq1_oom_highmem.tsv")
LOCAL_WORKER_SCRIPT_RE = (
    r"/(rq1_veriput_run|rq1_local_pump|rq1_local_supervisor|certify_all|"
    r"put_all|solidity_path_put|solidity_path_generalise)\.py(\s|$)")


def _is_rq1_worker_process(comm: str, args: str) -> bool:
    if comm in {"esbmc", "forge", "anvil"}:
        return True
    if "/build/src/esbmc/esbmc" in args or "/release/bin/esbmc" in args:
        return True
    return re.search(LOCAL_WORKER_SCRIPT_RE, args) is not None


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _run(cmd: list[str], timeout: int = 8) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "timeout",
        }


def _pid_alive(pid: object) -> bool:
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    return Path(f"/proc/{pid_i}").exists()


def _meminfo_mib() -> dict:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                values[parts[0][:-1]] = int(int(parts[1]) / 1024)
            except ValueError:
                pass
    total = values.get("MemTotal", 0)
    free = values.get("MemFree", 0)
    available = values.get("MemAvailable", 0)
    cached = values.get("Cached", 0) + values.get("SReclaimable", 0)
    buffers = values.get("Buffers", 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "mem_total_mib": total,
        "mem_free_mib": free,
        "mem_available_mib": available,
        "buff_cache_mib": cached + buffers,
        "process_used_estimate_mib": max(0, total - free - cached - buffers),
        "swap_total_mib": swap_total,
        "swap_used_mib": max(0, swap_total - swap_free),
        "cache_explanation": (
            "Linux buff/cache is reclaimable and must not be counted as live "
            "RQ1/ESBMC worker RSS. Use local_processes/local_esbmc_memory to "
            "decide whether workers are still running."),
    }


def _mem_available_mib() -> int:
    return int(_meminfo_mib().get("mem_available_mib") or 0)


def _local_process_rows() -> list[dict]:
    proc = _run([
        "ps",
        "-eo",
        "pid=,ppid=,etimes=,rss=,comm=,args=",
    ])
    rows = []
    for line in proc["stdout"].splitlines():
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        pid, ppid, etimes, rss, comm, args = parts
        if not _is_rq1_worker_process(comm, args):
            continue
        rows.append({
            "pid": int(pid),
            "ppid": int(ppid),
            "runtime_s": int(etimes),
            "rss_mib": round(int(rss) / 1024, 3),
            "comm": comm,
            "args": args[:600],
        })
    return rows


def _esbmc_rss_summary(rows: list[dict], rss_limit_gib: int) -> dict:
    limit_mib = int(rss_limit_gib) * 1024
    esbmc_rows = [
        row for row in rows
        if "esbmc" in str(row.get("comm")) or "esbmc" in str(row.get("args"))
    ]
    max_rss_mib = max((float(row.get("rss_mib") or 0) for row in esbmc_rows),
                      default=0.0)
    return {
        "esbmc_processes": len(esbmc_rows),
        "esbmc_max_rss_mib": round(max_rss_mib, 3),
        "esbmc_rss_limit_mib": limit_mib,
        "esbmc_over_rss_limit": max_rss_mib > limit_mib,
    }


def _remote_probe(host: str, progress_tail: int) -> dict:
    script = r'''
set -u
echo '{"kind":"host","hostname":"'"$(hostname)"'","mem_available_mib":'$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)',"nproc":'$(nproc)'}'
if [ -s /tmp/veriput_remote_worker.pid ]; then
  worker_pid="$(cat /tmp/veriput_remote_worker.pid 2>/dev/null || true)"
  if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
    echo '{"kind":"remote_worker_pid","pid":"'"$worker_pid"'","alive":true}'
  else
    echo '{"kind":"remote_worker_pid","pid":"'"$worker_pid"'","alive":false}'
  fi
else
  echo '{"kind":"remote_worker_pid","pid":null,"alive":false}'
fi
ps -eo pid=,ppid=,etimes=,rss=,comm=,args= | awk '
  $5 !~ /^(bash|sh|awk|pgrep|tail|ps)$/ &&
  ($5 ~ /^esbmc$/ ||
   $0 ~ /python[0-9.]* .*\/(rq1_veriput_run|certify_all|put_all|solidity_path_put|solidity_path_generalise)\.py/ ||
   $0 ~ /python[0-9.]* .*\/rq1_remote_pump\.py/) {
    gsub(/\\/,"\\\\",$0);
    gsub(/"/,"\\\"",$0);
    printf("{\"kind\":\"process\",\"line\":\"%s\"}\n", $0);
  }'
if [ -f /tmp/veriput_remote_build.pid ]; then
  build_pid="$(cat /tmp/veriput_remote_build.pid 2>/dev/null || true)"
  if [ -n "$build_pid" ] && kill -0 "$build_pid" 2>/dev/null; then
    echo '{"kind":"remote_build","pid":"'"$build_pid"'","alive":true}'
  else
    echo '{"kind":"remote_build","pid":"'"$build_pid"'","alive":false}'
  fi
fi
python3 - <<'PY'
import json
import time
from pathlib import Path

root = Path("/tmp/veriput_rq1_case_leases.d")
counts = {}
stale_running = []
now = time.time()
for lease in root.iterdir() if root.exists() else []:
    if not lease.is_dir():
        continue
    state = {}
    try:
        state = json.loads((lease / "state.json").read_text())
    except Exception:
        state = {}
    status = str(state.get("status") or "unknown")
    counts[status] = counts.get(status, 0) + 1
    try:
        updated = float((lease / "updated_ts").read_text().strip())
    except Exception:
        updated = float(state.get("updated_ts") or lease.stat().st_mtime)
    age = max(0.0, now - updated)
    if status == "running" and age >= 1200:
        stale_running.append({
            "lease": lease.name,
            "age_s": round(age, 3),
            "bench": state.get("bench"),
            "subject": state.get("subject"),
        })
print(json.dumps({
    "kind": "remote_leases",
    "lease_dir": str(root),
    "total": sum(counts.values()),
    "counts": counts,
    "stale_running_count": len(stale_running),
    "stale_running_tail": stale_running[-20:],
}, sort_keys=True))
PY
tail -__REMOTE_PROGRESS_TAIL__ /tmp/veriput_remote_progress.jsonl 2>/dev/null | sed 's/^/{"kind":"progress","raw":/' | sed 's/$/}/'
'''.replace("__REMOTE_PROGRESS_TAIL__", str(max(1, int(progress_tail))))
    return _run([
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        host,
        script,
    ], timeout=12)


def _jsonl_tail(path: Path, limit: int) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"raw": line[-1000:]})
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _case_progress_key(row: dict) -> str:
    return f"{row.get('bench', '')}/{row.get('subject', '')}"


def _progress_item(path: str, row: dict) -> dict:
    return {
        "progress_file": path,
        "bench": row.get("bench"),
        "subject": row.get("subject"),
        "category": row.get("category"),
        "status": row.get("status"),
        "rc": row.get("rc"),
        "ts": row.get("ts"),
        "bucket": row.get("bucket"),
        "valid": row.get("valid"),
        "put_valid": row.get("put_valid"),
        "r1r2": row.get("r1r2"),
        "note": row.get("note"),
    }


def _summarize_progress_rows(rows_by_path: list[tuple[str, list[dict]]],
                             stuck_after_s: float) -> dict:
    latest_by_case: dict[str, dict] = {}
    recent_done: list[dict] = []
    recent_failed: list[dict] = []
    recent_oom: list[dict] = []
    recent_weak: list[dict] = []
    recent_unknown_done: list[dict] = []
    now = time.time()
    newest_ts = 0.0
    status_counts: dict[str, int] = {}
    for path, rows in rows_by_path:
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            item = _progress_item(path, row)
            key = _case_progress_key(row)
            if key != "/":
                latest_by_case[key] = item
            ts_raw = row.get("ts")
            ts_epoch = _parse_ts_epoch(ts_raw)
            if ts_epoch:
                newest_ts = max(newest_ts, ts_epoch)
            if status == "done":
                recent_done.append(item)
                has_quality_fields = any(
                    key in row for key in ("valid", "put_valid", "r1r2",
                                           "bucket"))
                if not has_quality_fields:
                    recent_unknown_done.append(item)
                    continue
                valid = int(row.get("valid") or 0)
                put_valid = int(row.get("put_valid") or 0)
                r1r2 = int(row.get("r1r2") or 0)
                if not (valid and put_valid and r1r2):
                    recent_weak.append(item)
            elif status in {"killed-over-rss", "OOM_OR_MEMORY_PRESSURE"}:
                recent_oom.append(item)
                recent_failed.append(item)
            elif status and status not in {"running", "done"}:
                recent_failed.append(item)
    running = [
        item for item in latest_by_case.values()
        if str(item.get("status") or "") == "running"
    ]
    seconds_since_progress = None
    if newest_ts:
        seconds_since_progress = max(0.0, now - newest_ts)
    idle_or_stuck = bool(
        not running and seconds_since_progress is not None
        and seconds_since_progress >= stuck_after_s)
    return {
        "currently_running_count": len(running),
        "currently_running_cases": sorted(
            running,
            key=lambda item: str(item.get("progress_file") or ""))[:40],
        "recent_done_count_in_tail": len(recent_done),
        "recent_unknown_done_count_in_tail": len(recent_unknown_done),
        "recent_failed_count_in_tail": len(recent_failed),
        "recent_oom_count_in_tail": len(recent_oom),
        "recent_weak_or_failed_count_in_tail": len(recent_failed) +
        len(recent_weak),
        "recent_done_tail": recent_done[-20:],
        "recent_unknown_done_tail": recent_unknown_done[-20:],
        "recent_failed_tail": recent_failed[-20:],
        "recent_oom_tail": recent_oom[-20:],
        "recent_weak_tail": recent_weak[-20:],
        "status_counts_in_tail": status_counts,
        "latest_result_by_case_tail": sorted(
            latest_by_case.values(),
            key=lambda item: f"{item.get('progress_file')}:{item.get('ts')}"
        )[-40:],
        "seconds_since_last_progress": (
            round(seconds_since_progress, 3)
            if seconds_since_progress is not None else None),
        "idle_or_stuck": idle_or_stuck,
        "stuck_after_s": stuck_after_s,
    }


def _parse_ts_epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _progress_report(globs: tuple[str, ...], tail_limit: int,
                     stuck_after_s: float) -> dict:
    paths: list[Path] = []
    seen = set()
    for pattern in globs:
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    workers = []
    rows_by_path: list[tuple[str, list[dict]]] = []
    for path in paths:
        rows = _jsonl_tail(path, tail_limit)
        last = rows[-1] if rows else {}
        worker = {
            "progress_file": str(path),
            "rows_seen_tail": len(rows),
            "last": last,
        }
        workers.append(worker)
        rows_by_path.append((str(path), rows))
    summary = _summarize_progress_rows(rows_by_path, stuck_after_s)
    return {
        "worker_progress_files": [str(path) for path in paths],
        "workers": workers,
        **summary,
    }


def _tsv_count(path: Path) -> int:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return 0
    return max(0, len([line for line in lines if line.strip()]) - 1)


def _local_lease_report(path: Path, stale_s: float) -> dict:
    doc = _json(path)
    leases = doc.get("leases") if isinstance(doc.get("leases"), dict) else {}
    counts: dict[str, int] = {}
    stale_running = []
    now = time.time()
    for key, lease in leases.items():
        if not isinstance(lease, dict):
            continue
        status = str(lease.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        updated = float(lease.get("updated_ts") or lease.get("ts") or 0)
        age = max(0.0, now - updated) if updated else 0.0
        if status == "running" and updated and age >= stale_s:
            stale_running.append({
                "key": key,
                "age_s": round(age, 3),
                "bench": lease.get("bench"),
                "subject": lease.get("subject"),
                "worker_id": lease.get("worker_id"),
            })
    return {
        "lease_file": str(path),
        "total": sum(counts.values()),
        "counts": counts,
        "stale_s": stale_s,
        "stale_running_count": len(stale_running),
        "stale_running_tail": stale_running[-20:],
    }


def _subagent_report(state: dict, extra_state: dict, stale_s: float,
                     min_active: int) -> dict:
    now = time.time()
    agents = list(state.get("agents") or [])
    agents.extend(extra_state.get("agents") or [])
    active = [
        agent for agent in agents
        if agent.get("status") in ("leased", "running")
    ]
    stale = []
    for agent in active:
        started = float(agent.get("running_ts") or agent.get("leased_ts") or now)
        age = max(0.0, now - started)
        if age >= stale_s:
            stale.append({
                "slot": agent.get("slot"),
                "agent_id": agent.get("agent_id"),
                "age_s": round(age, 3),
                "task": agent.get("task"),
            })
    active_details = []
    for agent in active:
        started = float(agent.get("running_ts") or agent.get("leased_ts") or now)
        active_details.append({
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "nickname": agent.get("nickname"),
            "task": agent.get("task"),
            "mode": agent.get("mode") or "unknown",
            "write_scope": agent.get("write_scope") or [],
            "runtime_s": round(max(0.0, now - started), 3),
            "expected_coverage": agent.get("expected_coverage"),
        })
    def is_write_patch(agent: dict) -> bool:
        if agent.get("status") != "completed":
            return False
        mode = str(agent.get("mode") or "").strip().lower()
        if mode == "readonly":
            return False
        if mode == "write":
            return True
        return bool(str(agent.get("patch_id") or "").strip()
                    and (agent.get("write_scope") or []))

    pending_review = []
    rejected_or_needs_work = []
    for agent in agents:
        if not is_write_patch(agent):
            continue
        review = str(agent.get("review_status") or "pending")
        row = {
            "slot": agent.get("slot"),
            "agent_id": agent.get("agent_id"),
            "patch_id": agent.get("patch_id"),
            "task": agent.get("task"),
            "write_scope": agent.get("write_scope") or [],
            "review_status": review,
        }
        if review == "pending":
            pending_review.append(row)
        elif review in {"rejected", "needs-work"}:
            rejected_or_needs_work.append(row)
    return {
        "active": len(active),
        "active_details": active_details,
        "min_active_required": min_active,
        "below_min_active": len(active) < min_active,
        "below_min_active_warning": (
            f"ACTIVE_SUBAGENTS_BELOW_{min_active}: dispatch or reuse repair "
            "subagents immediately after closing completed agents."
            if len(active) < min_active else ""),
        "dispatch_more_subagents_required": len(active) < min_active,
        "completed": sum(1 for a in agents if a.get("status") == "completed"),
        "queued": sum(1 for a in agents if a.get("status") == "queued"),
        "stale": stale,
        "pending_review_count": len(pending_review),
        "pending_review_tail": pending_review[-12:],
        "rejected_or_needs_work_count": len(rejected_or_needs_work),
        "rejected_or_needs_work_tail": rejected_or_needs_work[-12:],
        "review_rule": (
            "Completed write-mode patches, including legacy records with "
            "patch_id+write_scope but missing mode, remain provisional until an "
            "independent review marks review_status=accepted; rejected or "
            "needs-work patches must not justify net theoretical coverage."),
        "active_report_rule": (
            "Every progress report must include active subagent count and "
            "active_details; running AUTO repair agents count as active even "
            "before they have a patch_id."),
    }


def _remote_probe_summary(remote_stdout: str, stuck_after_s: float) -> dict:
    process_rows = []
    progress_rows = []
    host = {}
    leases = {}
    worker_pid = {}
    for line in remote_stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "host":
            host = row
        elif row.get("kind") == "remote_worker_pid":
            worker_pid = row
        elif row.get("kind") == "process":
            process_rows.append(row)
        elif row.get("kind") == "remote_leases":
            leases = row
        elif row.get("kind") == "progress":
            raw = row.get("raw")
            if isinstance(raw, dict):
                progress_rows.append(raw)
            elif isinstance(raw, str):
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = {}
                if isinstance(decoded, dict):
                    progress_rows.append(decoded)
    progress_summary = _summarize_progress_rows(
        [("/tmp/veriput_remote_progress.jsonl", progress_rows)], stuck_after_s)
    return {
        "host": host,
        "worker_pid": worker_pid,
        "process_count": len(process_rows),
        "process_tail": process_rows[-30:],
        "leases": leases,
        "progress": progress_summary,
    }


def _autoclose_report(state: dict, extra_state: dict, close_state: dict) -> dict:
    closed = set(close_state.get("closed_agent_ids") or [])
    pending = []
    for agent in list(state.get("agents") or []) + list(
            extra_state.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id") or "")
        if not agent_id or agent_id in closed:
            continue
        if agent.get("status") != "completed":
            continue
        pending.append({
            "agent_id": agent_id,
            "slot": agent.get("slot"),
            "patch_id": agent.get("patch_id"),
            "task": agent.get("task"),
        })
    return {
        "pending_close_count": len(pending),
        "pending_close": pending[:40],
        "closed_count": len(closed),
        "rule": (
            "Completed subagents must be closed and acked in "
            "rq1_subagent_autoclose.py before spawning more work."),
    }


def build_report(args: argparse.Namespace) -> dict:
    local_state = _json(args.local_state)
    extra_local_states = [
        _json(path) for path in sorted(Path("/").glob(
            args.extra_local_state_glob.lstrip("/")))
    ]
    remote_state = _json(args.remote_state)
    subagent_state = _json(args.subagent_state)
    extra_subagent_state = _json(args.extra_subagent_state)
    dispatch_queue = _json(args.dispatch_queue)
    review_queue = _json(args.review_queue)
    close_state = _json(args.subagent_close_state)
    local_rows = _local_process_rows()
    local_rss_limit = int(local_state.get("esbmc_rss_limit_gib")
                          or DEFAULT_ESBMC_RSS_LIMIT_GIB)
    local_lease_file = Path(
        str(local_state.get("lease_file") or "/tmp/veriput_rq1_case_leases.json"))
    local_lease_stale_s = float(local_state.get("lease_stale_s") or 1200)
    if args.no_remote_probe:
        remote = {
            "returncode": 0,
            "stdout": "",
            "stderr": "remote probe skipped by --no-remote-probe",
        }
    else:
        remote = _remote_probe(args.remote_host, args.remote_progress_tail)
    remote_summary = _remote_probe_summary(remote["stdout"],
                                           args.worker_stuck_after_s)
    progress_report = _progress_report(tuple(args.progress_glob),
                                       args.progress_tail,
                                       args.worker_stuck_after_s)
    local_running_count = (
        progress_report["currently_running_count"] if local_rows else 0
    )
    remote_worker_pid = remote_summary.get("worker_pid") or {}
    remote_has_process = bool(remote_summary.get("process_count"))
    remote_pid_alive = bool(remote_worker_pid.get("alive"))
    remote_running_count = (
        remote_summary["progress"]["currently_running_count"]
        if (remote_has_process or remote_pid_alive) else 0
    )
    assignments = dispatch_queue.get("assignments") or []
    assignment_count = int(dispatch_queue.get("assignment_count") or 0)
    subagent_dispatch_status = {
        "assignment_count": assignment_count,
        "min_assignment_target": int(
            dispatch_queue.get("min_assignment_target") or 12),
        "write_owner_count": int(dispatch_queue.get("write_owner_count") or 0),
        "readonly_root_cause_count": int(
            dispatch_queue.get("readonly_root_cause_count") or 0),
        "below_min_assignment_target":
            assignment_count < int(dispatch_queue.get("min_assignment_target")
                                   or 12),
        "pending_bucket_keys": [
            item.get("bucket_key") for item in assignments[:8]
        ],
        "main_agent_action_required": assignment_count > 0,
        "spawn_tool_visible_to_shell": False,
        "spawn_blocker": (
            "Codex subagent spawning is not callable from this Python/shell "
            "watchdog. The main agent must dispatch these prompts through the "
            "conversation tool layer when such a tool is available; otherwise "
            "it must report that this is not fully autonomous."
        ),
        "review_required_after_patch": True,
        "quality_review_pending_count": int(
            (review_queue.get("assignment_count") or 0)
            if isinstance(review_queue, dict) else 0),
        "non_monotonic_progress_rule": (
            "If a dispatched patch's claimed coverage is contradicted by a "
            "later worker result, rq1_no_valid_progress.py must subtract the "
            "subject or quality debt and a new assignment must stay queued."),
    }
    return {
        "schema": "veriput-rq1-watchdog-status/v1",
        "ts": time.time(),
        "subagents": _subagent_report(subagent_state, extra_subagent_state,
                                      args.stale_subagent_s,
                                      args.min_active_subagents),
        "subagent_autoclose": _autoclose_report(subagent_state,
                                                extra_subagent_state,
                                                close_state),
        "local_host": {
            **_meminfo_mib(),
            "mem_available_gib": round(_mem_available_mib() / 1024, 3),
        },
        "local_worker": {
            "state_file": str(args.local_state),
            "pid": local_state.get("pid"),
            "alive": _pid_alive(local_state.get("pid")),
            "memory_watchdog": bool(local_state.get("memory_watchdog")),
            "process_watchdog": bool(local_state.get("process_watchdog")),
            "progress_watchdog": bool(local_state.get("progress_watchdog")),
            "lease_watchdog": bool(local_state.get("lease_file")),
            "case_parallel": local_state.get("case_parallel"),
            "running_case_count": local_running_count,
            "stale_progress_running_count": progress_report[
                "currently_running_count"],
            "running_count_rule": (
                "current running cases require a live local ESBMC/RQ1 process; "
                "stale progress-tail rows are not counted as active workers."),
        },
        "local_leases": _local_lease_report(local_lease_file,
                                            local_lease_stale_s),
        "extra_local_workers": [
            {
                "state_file": str(path),
                "pid": state.get("pid"),
                "alive": _pid_alive(state.get("pid")),
                "memory_watchdog": bool(state.get("memory_watchdog")),
                "process_watchdog": bool(state.get("process_watchdog")),
                "progress_watchdog": bool(state.get("progress_watchdog")),
                "case_parallel": state.get("case_parallel"),
            }
            for path, state in zip(
                sorted(Path("/").glob(args.extra_local_state_glob.lstrip("/"))),
                extra_local_states)
        ],
        "local_esbmc_memory": _esbmc_rss_summary(local_rows, local_rss_limit),
        "worker_progress": progress_report,
        "repair_tickets_tail": _jsonl_tail(args.repair_tickets,
                                           args.repair_ticket_tail),
        "repair_dispatch": {
            "queue_file": str(args.dispatch_queue),
            "assignment_count": assignment_count,
            "min_assignment_target":
                dispatch_queue.get("min_assignment_target"),
            "write_owner_count": dispatch_queue.get("write_owner_count"),
            "readonly_root_cause_count":
                dispatch_queue.get("readonly_root_cause_count"),
            "assignments": assignments[:8],
            "rule": dispatch_queue.get("rule"),
            "dispatch_status": subagent_dispatch_status,
        },
        "review_dispatch": {
            "queue_file": str(args.review_queue),
            "assignment_count": int(review_queue.get("assignment_count") or 0),
            "max_patches_per_assignment":
                review_queue.get("max_patches_per_assignment"),
            "assignments": (review_queue.get("assignments") or [])[:8],
            "rule": review_queue.get("rule"),
        },
        "oom_highmem_queue": {
            "queue_file": str(args.oom_highmem_queue),
            "case_count": _tsv_count(args.oom_highmem_queue),
            "rule": (
                "Only explicit OOM/memory-pressure cases should be rerun with "
                "higher memory; all other cases stay on the normal budget."),
        },
        "local_processes": local_rows,
        "remote_worker": {
            "state_file": str(args.remote_state),
            "host": args.remote_host,
            "worker_pid": (remote_state.get("worker") or {}).get("pid"),
            "worker_pid_probe": remote_summary.get("worker_pid") or {},
            "worker_alive_by_pid_file": bool(
                (remote_summary.get("worker_pid") or {}).get("alive")),
            "memory_watchdog": bool((remote_state.get("worker") or {}).get(
                "memory_watchdog")),
            "process_watchdog": bool((remote_state.get("worker") or {}).get(
                "remote_watchdog")),
            "progress_watchdog": bool((remote_state.get("worker") or {}).get(
                "progress_watchdog")),
            "lease_watchdog": bool((remote_state.get("worker") or {}).get(
                "lease_watchdog")),
            "case_count": (remote_state.get("worker") or {}).get("case_count"),
            "case_parallel": (remote_state.get("worker") or {}).get(
                "case_parallel"),
            "remote_lease_dir": (remote_state.get("worker") or {}).get(
                "remote_lease_dir"),
            "remote_lease_stale_s": (remote_state.get("worker") or {}).get(
                "remote_lease_stale_s"),
            "remote_lease_refresh_s": (remote_state.get("worker") or {}).get(
                "remote_lease_refresh_s"),
            "leases": remote_summary.get("leases") or {},
            "terminal_case_count": sum(
                int((remote_summary.get("leases") or {}).get("counts", {}).get(
                    status, 0) or 0)
                for status in ("done", "failed", "killed-over-rss")),
            "running_case_count": remote_running_count,
            "stale_progress_running_count": remote_summary["progress"][
                "currently_running_count"],
            "running_count_rule": (
                "current running cases require a live remote worker pid or "
                "remote ESBMC/RQ1 process; stale progress-tail rows are not "
                "counted as active workers."),
            "running_cases": remote_summary["progress"][
                "currently_running_cases"],
            "recent_done_count_in_tail": remote_summary["progress"][
                "recent_done_count_in_tail"],
            "recent_unknown_done_count_in_tail": remote_summary["progress"][
                "recent_unknown_done_count_in_tail"],
            "recent_failed_count_in_tail": remote_summary["progress"][
                "recent_failed_count_in_tail"],
            "recent_oom_count_in_tail": remote_summary["progress"][
                "recent_oom_count_in_tail"],
            "seconds_since_last_progress": remote_summary["progress"][
                "seconds_since_last_progress"],
            "idle_or_stuck": remote_summary["progress"]["idle_or_stuck"],
        },
        "remote_probe": {
            "returncode": remote["returncode"],
            "stdout_tail": remote["stdout"].splitlines()[-80:],
            "stderr": remote["stderr"][-1000:],
            "parsed": remote_summary,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subagent-state",
                        type=Path,
                        default=DEFAULT_SUBAGENT_STATE)
    parser.add_argument("--extra-subagent-state",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENT_STATE)
    parser.add_argument("--local-state", type=Path, default=DEFAULT_LOCAL_STATE)
    parser.add_argument("--extra-local-state-glob",
                        default=DEFAULT_EXTRA_LOCAL_STATE_GLOB)
    parser.add_argument("--remote-state", type=Path, default=DEFAULT_REMOTE_STATE)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--no-remote-probe", action="store_true")
    parser.add_argument("--stale-subagent-s", type=float, default=1200.0)
    parser.add_argument("--min-active-subagents", type=int, default=10)
    parser.add_argument("--worker-stuck-after-s", type=float, default=900.0)
    parser.add_argument("--progress-glob",
                        action="append",
                        default=list(DEFAULT_PROGRESS_GLOBS))
    parser.add_argument("--progress-tail", type=int, default=12)
    parser.add_argument("--remote-progress-tail", type=int, default=80)
    parser.add_argument("--repair-tickets",
                        type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--repair-ticket-tail", type=int, default=12)
    parser.add_argument("--dispatch-queue",
                        type=Path,
                        default=DEFAULT_DISPATCH_QUEUE)
    parser.add_argument("--review-queue",
                        type=Path,
                        default=DEFAULT_REVIEW_QUEUE)
    parser.add_argument("--subagent-close-state",
                        type=Path,
                        default=DEFAULT_SUBAGENT_CLOSE_STATE)
    parser.add_argument("--oom-highmem-queue",
                        type=Path,
                        default=DEFAULT_OOM_HIGHMEM_QUEUE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    doc = build_report(args)
    payload = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
