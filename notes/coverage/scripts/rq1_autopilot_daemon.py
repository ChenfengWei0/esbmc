#!/usr/bin/env python3
"""Persistent RQ1 supervisor.

The repository cannot invoke Codex host lifecycle APIs directly.  This daemon
therefore owns the repository-side loop and delegates host-only actions to
``rq1_host_bridge.py``.  A missing bridge is a hard, recorded block; it is
never treated as a successful spawn or worker start.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
AUTOPILOT = HERE / "rq1_autopilot.py"
FEEDBACK = HERE / "rq1_feedback_controller.py"
BRIDGE = HERE / "rq1_host_bridge.py"
STATUS = HERE / "rq1_mandatory_status.py"
RESOURCE_WATCHDOG = HERE / "rq1_subagent_resource_watchdog.py"
PREFLIGHT = HERE / "rq1_host_preflight.py"
DEFAULT_STATE = Path("/tmp/veriput_rq1_autopilot_daemon.json")
DEFAULT_LOCK = Path("/tmp/veriput_rq1_autopilot_daemon.lock")
DEFAULT_STOP = Path("/tmp/veriput_rq1_autopilot.stop")


def _run(cmd: list[str], timeout_s: float) -> dict:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, timeout_s),
            check=False,
        )
        return {
            "command": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-12000:],
            "stderr_tail": proc.stderr[-4000:],
            "elapsed_s": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd,
            "returncode": 124,
            "stdout_tail": str(exc.stdout or "")[-12000:],
            "stderr_tail": "command timeout",
            "elapsed_s": round(time.time() - started, 3),
        }


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _cycle(args: argparse.Namespace) -> dict:
    results = {}
    results["resource_watchdog"] = _run(
        [sys.executable, str(RESOURCE_WATCHDOG)], args.command_timeout_s)
    results["host_preflight"] = _run(
        [sys.executable, str(PREFLIGHT)], args.command_timeout_s)
    results["feedback"] = _run(
        [sys.executable, str(FEEDBACK), "--scan"], args.command_timeout_s)
    results["autopilot"] = _run(
        [sys.executable, str(AUTOPILOT), "tick", "--apply-safe-actions"],
        args.command_timeout_s)
    results["host_bridge"] = _run(
        [sys.executable, str(BRIDGE), "--once"], args.command_timeout_s)
    results["status"] = _run(
        [sys.executable, str(STATUS), "--no-remote-probe"],
        args.command_timeout_s)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-s", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--stop-file", type=Path, default=DEFAULT_STOP)
    parser.add_argument("--command-timeout-s", type=float, default=120.0)
    args = parser.parse_args()

    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("RQ1 daemon already running") from exc

        stopped = False

        def stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        cycle = 0
        while not stopped:
            if args.stop_file.exists():
                args.stop_file.unlink(missing_ok=True)
                break
            cycle += 1
            state = {
                "schema": "veriput-rq1-autopilot-daemon/v1",
                "pid": os.getpid(),
                "cycle": cycle,
                "started_ts": time.time(),
                "interval_s": args.interval_s,
                "results": _cycle(args),
            }
            state["updated_ts"] = time.time()
            state["host_ready"] = state["results"]["host_preflight"].get(
                "returncode") == 0
            state["healthy"] = state["host_ready"] and all(
                int(item.get("returncode") or 0) in (0, 2)
                for item in state["results"].values())
            _write(args.state, state)
            if args.once:
                break
            deadline = time.time() + max(1.0, args.interval_s)
            while not stopped and time.time() < deadline:
                time.sleep(min(1.0, max(0.05, deadline - time.time())))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
