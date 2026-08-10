#!/usr/bin/env python3
"""Mandatory status prelude for every RQ1 progress reply.

Use this script before every user-facing progress update.  It calls the hard
ledger, so countdown/resource/subagent/remote/theoretical/actual RQ1 fields are
not reconstructed from memory.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LEDGER = HERE / "rq1_no_valid_progress.py"
WATCHDOG = HERE / "rq1_watchdog_status.py"
DISPATCHER = HERE / "rq1_repair_dispatcher.py"
REVIEW_DISPATCHER = HERE / "rq1_review_dispatcher.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--applied", default="")
    parser.add_argument("--no-remote-probe", action="store_true")
    args = parser.parse_args()

    dispatch_proc = subprocess.run(
        [sys.executable, str(DISPATCHER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print("dispatch_refresh_status:")
    print(f"  returncode={dispatch_proc.returncode}")
    if dispatch_proc.stderr:
        print(f"  stderr_tail={dispatch_proc.stderr[-1000:]}")
    review_proc = subprocess.run(
        [sys.executable, str(REVIEW_DISPATCHER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print("review_dispatch_refresh_status:")
    print(f"  returncode={review_proc.returncode}")
    if review_proc.stderr:
        print(f"  stderr_tail={review_proc.stderr[-1000:]}")

    cmd = [sys.executable, str(LEDGER), "--init-subagents"]
    if args.applied:
        cmd.extend(["--applied", args.applied])
    if args.no_remote_probe:
        cmd.append("--no-remote-probe")
    proc = subprocess.run(cmd, check=False)
    print("watchdog_status:")
    watchdog_proc = subprocess.run([sys.executable, str(WATCHDOG)], check=False)
    if proc.returncode != 0:
        return proc.returncode
    return watchdog_proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
