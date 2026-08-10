#!/usr/bin/env python3
"""Supervise host subagent resources through an explicit probe command."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ORCHESTRATOR = HERE / "rq1_subagent_orchestrator.py"
DEFAULT_STATE = Path("/tmp/veriput_rq1_subagent_resource_state.json")
DEFAULT_ACTIONS = Path("/tmp/veriput_rq1_host_actions.jsonl")
PROBE_ENV = "VERIPUT_HOST_RESOURCE_COMMAND"


def _run_json(cmd: list[str], payload: dict, timeout_s: float) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload, ensure_ascii=False) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, timeout_s),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "resource-probe-timeout"}
    try:
        value = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        value = {"raw_stdout": proc.stdout[-2000:]}
    if not isinstance(value, dict):
        value = {"agents": value}
    value.update({"ok": proc.returncode == 0 and value.get("ok", True),
                  "returncode": proc.returncode,
                  "stderr_tail": proc.stderr[-2000:]})
    return value


def _active() -> list[dict]:
    proc = subprocess.run(
        ["python3", str(ORCHESTRATOR), "watchdog"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return doc.get("active_details") or []


def _append_action(path: Path, action: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(action, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--max-rss-gib", type=float, default=8.0)
    parser.add_argument("--max-runtime-s", type=float, default=1800.0)
    parser.add_argument("--heartbeat-timeout-s", type=float, default=180.0)
    parser.add_argument("--probe-timeout-s", type=float, default=15.0)
    args = parser.parse_args()
    active = _active()
    raw_command = os.environ.get(PROBE_ENV, "")
    state = {
        "schema": "veriput-rq1-subagent-resource-watchdog/v1",
        "ts": time.time(),
        "active_count": len(active),
        "probe_configured": bool(raw_command),
        "max_rss_gib": args.max_rss_gib,
        "max_runtime_s": args.max_runtime_s,
        "heartbeat_timeout_s": args.heartbeat_timeout_s,
        "violations": [],
        "actions": [],
    }
    if not raw_command:
        state["status"] = "resource-supervision-unavailable"
        state["reason"] = f"{PROBE_ENV} is not configured"
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 2
    probe = _run_json(shlex.split(raw_command), {"agents": active},
                      args.probe_timeout_s)
    state["probe"] = probe
    measurements = {
        str(row.get("agent_id")): row
        for row in probe.get("agents") or [] if isinstance(row, dict)
    }
    now = time.time()
    for agent in active:
        agent_id = str(agent.get("agent_id") or "")
        measurement = measurements.get(agent_id)
        if not measurement:
            state["violations"].append({"agent_id": agent_id,
                                         "reason": "agent-missing-from-probe"})
            continue
        rss = float(measurement.get("rss_gib") or 0)
        started = float(measurement.get("started_ts") or now)
        heartbeat = float(measurement.get("heartbeat_ts") or now)
        reasons = []
        if rss > args.max_rss_gib:
            reasons.append(f"rss>{args.max_rss_gib}GiB")
        if now - started > args.max_runtime_s:
            reasons.append(f"runtime>{args.max_runtime_s}s")
        if now - heartbeat > args.heartbeat_timeout_s:
            reasons.append(f"heartbeat>{args.heartbeat_timeout_s}s")
        if reasons:
            violation = {"agent_id": agent_id, "slot": agent.get("slot"),
                         "reasons": reasons, "measurement": measurement}
            state["violations"].append(violation)
            action = {
                "action": "interrupt_agent",
                "agent_id": agent_id,
                "slot": agent.get("slot"),
                "reason": ";".join(reasons),
                "created_ts": now,
                "status": "pending-host-execution",
            }
            _append_action(args.actions, action)
            state["actions"].append(action)
    state["status"] = "ok" if probe.get("ok") else "probe-failed"
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return 0 if state["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
