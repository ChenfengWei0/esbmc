#!/usr/bin/env python3
"""Generate required close actions for completed RQ1 subagents.

The actual Codex close operation is a host tool call, not a shell command.  This
script is the hard state machine that records which completed agents must be
closed and which ones were already acknowledged as closed by the main agent.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


DEFAULT_PRIMARY = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_CLOSE_STATE = Path("/tmp/veriput_rq1_subagent_close_state.json")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def _agents(primary: Path, extra: Path) -> list[dict]:
    out = []
    for source, path in (("primary", primary), ("extra", extra)):
        doc = _json(path)
        for row in doc.get("agents") or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["_source"] = source
            out.append(item)
    return out


def close_plan(primary: Path, extra: Path, state_path: Path) -> dict:
    state = _json(state_path)
    closed = set(state.get("closed_agent_ids") or [])
    pending = []
    for agent in _agents(primary, extra):
        agent_id = str(agent.get("agent_id") or "")
        if not agent_id or agent_id in closed:
            continue
        if agent.get("status") != "completed":
            continue
        pending.append({
            "agent_id": agent_id,
            "slot": agent.get("slot"),
            "task": agent.get("task"),
            "patch_id": agent.get("patch_id"),
            "source": agent.get("_source"),
        })
    return {
        "schema": "veriput-rq1-subagent-autoclose/v1",
        "state_file": str(state_path),
        "pending_close_count": len(pending),
        "pending_close": pending,
        "closed_count": len(closed),
        "rule": (
            "Before spawning new subagents, close every pending completed "
            "agent with the Codex close_agent tool, then run this script with "
            "`ack --agent-id ...` for each successful close. Do not leave "
            "completed agents open until thread limit is hit."),
    }


def ack(state_path: Path, agent_id: str) -> dict:
    state = _json(state_path)
    closed = list(state.get("closed_agent_ids") or [])
    if agent_id not in closed:
        closed.append(agent_id)
    state.update({
        "schema": "veriput-rq1-subagent-close-state/v1",
        "updated_ts": time.time(),
        "closed_agent_ids": sorted(closed),
    })
    _write(state_path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--extra", type=Path, default=DEFAULT_EXTRA)
    parser.add_argument("--state", type=Path, default=DEFAULT_CLOSE_STATE)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    ack_cmd = sub.add_parser("ack")
    ack_cmd.add_argument("--agent-id", required=True)
    args = parser.parse_args()
    if args.cmd == "plan":
        print(json.dumps(close_plan(args.primary, args.extra, args.state),
                         indent=2,
                         sort_keys=True))
    elif args.cmd == "ack":
        print(json.dumps(ack(args.state, args.agent_id),
                         indent=2,
                         sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
