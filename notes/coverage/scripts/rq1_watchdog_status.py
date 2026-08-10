#!/usr/bin/env python3
"""Unified watchdog status for RQ1 agents and ESBMC workers."""

from __future__ import annotations

import argparse
import json
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
)
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_DISPATCH_QUEUE = Path("/tmp/veriput_rq1_dispatch_queue.json")
DEFAULT_SUBAGENT_CLOSE_STATE = Path("/tmp/veriput_rq1_subagent_close_state.json")


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


def _mem_available_mib() -> int:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(int(line.split()[1]) / 1024)
    return 0


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
        haystack = f"{comm} {args}"
        if not any(token in haystack for token in (
                "esbmc",
                "rq1_veriput_run.py",
                "certify_all.py",
                "put_all.py",
                "solidity_path_put.py",
                "rq1_local_pump.py",
        )):
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


def _remote_probe(host: str) -> dict:
    script = r'''
set -u
echo '{"kind":"host","hostname":"'"$(hostname)"'","mem_available_mib":'$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)',"nproc":'$(nproc)'}'
ps -eo pid=,ppid=,etimes=,rss=,comm=,args= | awk '
  $5 ~ /esbmc/ || $0 ~ /rq1_veriput_run.py|certify_all.py|put_all.py|solidity_path_put.py|rq1_remote_worker/ {
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
tail -5 /tmp/veriput_remote_progress.jsonl 2>/dev/null | sed 's/^/{"kind":"progress","raw":/' | sed 's/$/}/'
'''
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


def _progress_report(globs: tuple[str, ...], tail_limit: int) -> dict:
    paths: list[Path] = []
    seen = set()
    for pattern in globs:
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    workers = []
    running = []
    recent_done = []
    recent_failed = []
    for path in paths:
        rows = _jsonl_tail(path, tail_limit)
        last = rows[-1] if rows else {}
        worker = {
            "progress_file": str(path),
            "rows_seen_tail": len(rows),
            "last": last,
        }
        workers.append(worker)
        for row in rows:
            status = str(row.get("status") or "")
            item = {
                "progress_file": str(path),
                "bench": row.get("bench"),
                "subject": row.get("subject"),
                "category": row.get("category"),
                "status": status,
                "rc": row.get("rc"),
                "ts": row.get("ts"),
            }
            if status == "running":
                running.append(item)
            elif status == "done":
                recent_done.append(item)
            elif status and status not in {"running", "done"}:
                recent_failed.append(item)
    return {
        "worker_progress_files": [str(path) for path in paths],
        "workers": workers,
        "running_tail": running[-20:],
        "recent_done_tail": recent_done[-20:],
        "recent_failed_or_skipped_tail": recent_failed[-20:],
    }


def _subagent_report(state: dict, extra_state: dict, stale_s: float) -> dict:
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
    return {
        "active": len(active),
        "completed": sum(1 for a in agents if a.get("status") == "completed"),
        "queued": sum(1 for a in agents if a.get("status") == "queued"),
        "stale": stale,
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
    close_state = _json(args.subagent_close_state)
    local_rows = _local_process_rows()
    local_rss_limit = int(local_state.get("esbmc_rss_limit_gib")
                          or DEFAULT_ESBMC_RSS_LIMIT_GIB)
    remote = _remote_probe(args.remote_host)
    assignments = dispatch_queue.get("assignments") or []
    assignment_count = int(dispatch_queue.get("assignment_count") or 0)
    subagent_dispatch_status = {
        "assignment_count": assignment_count,
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
        "non_monotonic_progress_rule": (
            "If a dispatched patch's claimed coverage is contradicted by a "
            "later worker result, rq1_no_valid_progress.py must subtract the "
            "subject or quality debt and a new assignment must stay queued."),
    }
    return {
        "schema": "veriput-rq1-watchdog-status/v1",
        "ts": time.time(),
        "subagents": _subagent_report(subagent_state, extra_subagent_state,
                                      args.stale_subagent_s),
        "subagent_autoclose": _autoclose_report(subagent_state,
                                                extra_subagent_state,
                                                close_state),
        "local_host": {
            "mem_available_mib": _mem_available_mib(),
            "mem_available_gib": round(_mem_available_mib() / 1024, 3),
        },
        "local_worker": {
            "state_file": str(args.local_state),
            "pid": local_state.get("pid"),
            "alive": _pid_alive(local_state.get("pid")),
            "memory_watchdog": bool(local_state.get("memory_watchdog")),
            "process_watchdog": bool(local_state.get("process_watchdog")),
            "progress_watchdog": bool(local_state.get("progress_watchdog")),
            "case_parallel": local_state.get("case_parallel"),
        },
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
        "worker_progress": _progress_report(tuple(args.progress_glob),
                                            args.progress_tail),
        "repair_tickets_tail": _jsonl_tail(args.repair_tickets,
                                           args.repair_ticket_tail),
        "repair_dispatch": {
            "queue_file": str(args.dispatch_queue),
            "assignment_count": assignment_count,
            "assignments": assignments[:8],
            "rule": dispatch_queue.get("rule"),
            "dispatch_status": subagent_dispatch_status,
        },
        "local_processes": local_rows,
        "remote_worker": {
            "state_file": str(args.remote_state),
            "host": args.remote_host,
            "worker_pid": (remote_state.get("worker") or {}).get("pid"),
            "memory_watchdog": bool((remote_state.get("worker") or {}).get(
                "memory_watchdog")),
            "process_watchdog": bool((remote_state.get("worker") or {}).get(
                "remote_watchdog")),
            "progress_watchdog": bool((remote_state.get("worker") or {}).get(
                "progress_watchdog")),
            "case_parallel": (remote_state.get("worker") or {}).get(
                "case_parallel"),
        },
        "remote_probe": {
            "returncode": remote["returncode"],
            "stdout_tail": remote["stdout"].splitlines()[-80:],
            "stderr": remote["stderr"][-1000:],
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
    parser.add_argument("--stale-subagent-s", type=float, default=1200.0)
    parser.add_argument("--progress-glob",
                        action="append",
                        default=list(DEFAULT_PROGRESS_GLOBS))
    parser.add_argument("--progress-tail", type=int, default=12)
    parser.add_argument("--repair-tickets",
                        type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--repair-ticket-tail", type=int, default=12)
    parser.add_argument("--dispatch-queue",
                        type=Path,
                        default=DEFAULT_DISPATCH_QUEUE)
    parser.add_argument("--subagent-close-state",
                        type=Path,
                        default=DEFAULT_SUBAGENT_CLOSE_STATE)
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
