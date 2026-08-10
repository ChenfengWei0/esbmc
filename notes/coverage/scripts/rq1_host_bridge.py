#!/usr/bin/env python3
"""Execute durable RQ1 host actions through an explicit external bridge.

Codex host lifecycle APIs are outside the repository Python process.  This
module makes that boundary deterministic: a configured command receives one
JSON action on stdin and must return JSON.  Missing commands leave the action
blocked and visible instead of pretending that an agent or worker exists.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ORCHESTRATOR = HERE / "rq1_subagent_orchestrator.py"
AUTOCLOSE = HERE / "rq1_subagent_autoclose.py"
WORKER_SUPERVISOR = HERE / "rq1_worker_supervisor.py"
DEFAULT_ACTIONS = Path("/tmp/veriput_rq1_host_actions.jsonl")
DEFAULT_STATE = Path("/tmp/veriput_rq1_host_bridge_state.json")
DEFAULT_EVENTS = Path("/tmp/veriput_rq1_host_bridge_events.jsonl")
COMMAND_ENV = {
    "spawn_agent": "VERIPUT_HOST_SPAWN_COMMAND",
    "close_agent_or_ack_not_found": "VERIPUT_HOST_CLOSE_COMMAND",
    "interrupt_agent": "VERIPUT_HOST_INTERRUPT_COMMAND",
    "start_workers_on_theory_manifest": "VERIPUT_HOST_WORKER_COMMAND",
}


def _json(path: Path, default):
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _load_rows(path: Path) -> list[dict]:
    rows = []
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


def _save_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                  for row in rows))


def _emit(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _command(action: dict, args: argparse.Namespace) -> list[str] | None:
    override = args.spawn_command if action.get("action") == "spawn_agent" else None
    override = override or args.close_command if action.get(
        "action") == "close_agent_or_ack_not_found" else override
    override = override or args.worker_command if action.get(
        "action") == "start_workers_on_theory_manifest" else override
    env_name = COMMAND_ENV.get(str(action.get("action") or ""))
    raw = override or (os.environ.get(env_name, "") if env_name else "")
    if not raw:
        if action.get("action") == "start_workers_on_theory_manifest":
            return [sys.executable, str(WORKER_SUPERVISOR), "start",
                    "--action-stdin"]
        return None
    return shlex.split(raw)


def _run_bridge(action: dict, command: list[str], timeout_s: float) -> dict:
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(action, ensure_ascii=False) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, timeout_s),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "bridge-timeout", "returncode": 124}
    output = proc.stdout.strip()
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError:
        payload = {"raw_stdout": output[-4000:]}
    if not isinstance(payload, dict):
        payload = {"bridge_output": payload}
    payload.update({
        "ok": proc.returncode == 0 and bool(payload.get("ok", True)),
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-4000:],
    })
    return payload


def _ledger_lease(action: dict, result: dict) -> tuple[bool, str]:
    if action.get("action") != "spawn_agent":
        return True, "not-a-spawn"
    agent_id = str(result.get("agent_id") or "")
    if not agent_id:
        return False, "bridge did not return agent_id"
    slot = str(result.get("slot") or action.get("slot") or "")
    if not slot:
        stamp = int(float(action.get("created_ts") or time.time())) % 100000000
        shard = int(str(action.get("id") or "0")[:2], 16) % 100
        slot = f"R{stamp:08d}-{shard:02d}"
    cmd = [
        sys.executable, str(ORCHESTRATOR), "lease", "--slot", slot,
        "--agent-id", agent_id, "--mode", str(action.get("mode") or "write"),
        "--allow-pending-close"
    ]
    for scope in action.get("write_scope") or []:
        cmd.extend(["--write-scope", str(scope)])
    cmd.extend(["--task", str(action.get("bucket_key") or "dynamic repair shard")])
    cmd.extend(["--expected-coverage", str(action.get("expected_coverage") or "dynamic")])
    lease = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, check=False)
    if lease.returncode != 0:
        return False, lease.stderr.strip() or lease.stdout.strip()
    running = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "running", "--agent-id", agent_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if running.returncode != 0:
        return False, running.stderr.strip() or running.stdout.strip()
    return True, "leased-and-running"


def _ack_close(action: dict) -> tuple[bool, str]:
    agent_id = str(action.get("agent_id") or "")
    if not agent_id:
        return True, "no-agent-id"
    proc = subprocess.run(
        [sys.executable, str(AUTOCLOSE), "ack", "--agent-id", agent_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        return False, proc.stderr.strip() or proc.stdout.strip()
    return True, "autoclose-acked"


def process_once(args: argparse.Namespace) -> dict:
    rows = _load_rows(args.actions)
    selected = [row for row in rows if row.get("status") in {
        "pending-host-execution", "blocked-host-bridge", "bridge-failed"
    }]
    if not args.retry_blocked:
        selected = [row for row in selected
                    if row.get("status") == "pending-host-execution"]
    changed = 0
    events = []
    for action in selected[:max(1, args.max_actions)]:
        action["attempts"] = int(action.get("attempts") or 0) + 1
        action["last_attempt_ts"] = time.time()
        command = _command(action, args)
        if command is None:
            action.update({
                "status": "blocked-host-bridge",
                "last_error": (
                    f"missing bridge command; configure "
                    f"{COMMAND_ENV.get(action.get('action'))}"),
            })
            result = {"ok": False, "reason": action["last_error"]}
        else:
            result = _run_bridge(action, command, args.timeout_s)
            if result.get("ok"):
                if action.get("action") == "spawn_agent":
                    ok, reason = _ledger_lease(action, result)
                elif action.get("action") == "close_agent_or_ack_not_found":
                    ok, reason = _ack_close(action)
                else:
                    ok, reason = True, "worker-command-accepted"
                result["ledger_ok"] = ok
                result["ledger_reason"] = reason
            else:
                result.setdefault("reason", "bridge-command-failed")
        if result.get("ok") and result.get("ledger_ok", True):
            action.update({"status": "done", "done_ts": time.time(),
                           "bridge_result": result})
        else:
            action.update({"status": "bridge-failed" if command else "blocked-host-bridge",
                           "last_error": result.get("reason") or result.get("ledger_reason"),
                           "bridge_result": result})
        event = {"ts": time.time(), "action_id": action.get("id"),
                 "action": action.get("action"), "status": action.get("status"),
                 "bucket_key": action.get("bucket_key"), "result": result}
        events.append(event)
        changed += 1
    if changed:
        _save_rows(args.actions, rows)
        for event in events:
            _emit(args.events, event)
    state = {
        "schema": "veriput-rq1-host-bridge/v1",
        "updated_ts": time.time(),
        "pending": sum(row.get("status") == "pending-host-execution" for row in rows),
        "blocked": sum(row.get("status") == "blocked-host-bridge" for row in rows),
        "failed": sum(row.get("status") == "bridge-failed" for row in rows),
        "done": sum(row.get("status") == "done" for row in rows),
        "processed_now": changed,
        "events": events,
    }
    _write(args.state, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-s", type=float, default=5.0)
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--retry-blocked", action="store_true")
    parser.add_argument("--spawn-command")
    parser.add_argument("--close-command")
    parser.add_argument("--worker-command")
    args = parser.parse_args()
    while True:
        state = process_once(args)
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
