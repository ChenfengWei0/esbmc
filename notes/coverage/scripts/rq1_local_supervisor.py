#!/usr/bin/env python3
"""Restarting supervisor for the local VeriPUT RQ1 worker."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_SUPERVISOR_STATE = Path("/tmp/veriput_rq1_local_supervisor_state.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_SUPERVISOR_STATE)
    parser.add_argument("--restart-sleep-s", type=int, default=5)
    parser.add_argument("worker_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    worker_args = list(args.worker_args)
    if worker_args and worker_args[0] == "--":
        worker_args = worker_args[1:]
    if not worker_args:
        raise SystemExit("worker command required after --")

    restarts = 0
    while True:
        restarts += 1
        state = {
            "schema": "veriput-rq1-local-supervisor/v1",
            "started": True,
            "restarts": restarts,
            "worker_args": worker_args,
            "last_start_ts": time.time(),
        }
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        proc = subprocess.run(worker_args, check=False, start_new_session=True)
        state["last_exit_ts"] = time.time()
        state["last_returncode"] = proc.returncode
        state["sleeping_before_restart_s"] = args.restart_sleep_s
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        time.sleep(max(1, args.restart_sleep_s))


if __name__ == "__main__":
    raise SystemExit(main())
