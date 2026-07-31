#!/usr/bin/env python3
"""Block until it is safe to start ONE more path-coverage run. Exit 0 = go.

WHY NOT `pgrep -x esbmc`: `pgrep -x` matches `comm`, which the kernel truncates
to 15 chars, so a snapshot binary named `esbmc_snapshot_unwind` presents as
`esbmc_snapshot_` and NEVER matches. A driver guarded that way reports "nothing
running" while multi-GB solvers are live. Same reasoning as
`esbmc_watch.py`, which is the monitoring counterpart of this file; this one is
the BLOCKING gate a driver calls before each cell.

TWO CORRECTIONS vs a naive cmdline match, both measured:

1. `timeout -k 30s 600s <esbmc> ... --solidity-path-coverage ...` has the flag
   in ITS OWN cmdline too, so a wrapped run counts twice. Observed live:
   `esbmc_watch.py --once` printed "2 path-coverage run(s) live" for a single
   esbmc (`pid 307282 RSS 2 MB` was the `timeout` wrapper, `pid 307283 RSS
   2237 MB` the solver). Processes whose executable basename is `timeout`,
   `setsid`, `sh`, `bash` or `env` are therefore excluded here.
2. This process's own children must not gate it, so an optional --self-pgid
   excludes a process group (a driver passes its own).

usage: esbmc_gate.py [--max-runs N] [--min-avail-mb M] [--self-pgid PGID]
                     [--budget-mb B] [--timeout S] [--once]

  --once   print the census and exit 0 without waiting (for diagnosis)
  --budget-mb  the --memlimit this caller is about to use; the gate additionally
               waits until MemAvailable - budget stays above --min-avail-mb
"""
import argparse
import os
import sys
import time

FLAG = "--solidity-path-coverage"
WRAPPERS = {"timeout", "setsid", "sh", "bash", "env", "nohup", "python3"}


def avail_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return -1


def _exe_base(pid):
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return ""


def _pgid(pid):
    try:
        return os.getpgid(int(pid))
    except (OSError, ValueError):
        return -1


def live_runs(self_pgid=None):
    """(pid, rss_mb, exe) for each REAL path-coverage solver process."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if not any(a == FLAG for a in argv):
            continue
        exe = _exe_base(pid)
        if exe in WRAPPERS:
            continue  # correction 1: the timeout/setsid wrapper is not a run
        if self_pgid is not None and _pgid(pid) == self_pgid:
            continue  # correction 2: our own group
        rss = 0
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) // 1024
        except OSError:
            pass
        out.append((pid, rss, exe))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-runs", type=int, default=1,
                    help="wait while at least this many OTHER runs are live")
    ap.add_argument("--min-avail-mb", type=int, default=8000)
    ap.add_argument("--budget-mb", type=int, default=0)
    ap.add_argument("--self-pgid", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    def census():
        runs = live_runs(a.self_pgid)
        return runs, avail_mb()

    if a.once:
        runs, av = census()
        print(f"MemAvailable {av} MB, {len(runs)} other path-coverage run(s) live")
        for pid, rss, exe in runs:
            print(f"  pid {pid}  RSS {rss} MB  exe {exe}")
        return 0

    deadline = time.time() + a.timeout
    while True:
        runs, av = census()
        ok_runs = len(runs) < a.max_runs
        ok_mem = av < 0 or (av - a.budget_mb) >= a.min_avail_mb
        if ok_runs and ok_mem:
            return 0
        if time.time() > deadline:
            # Fail LOUD, never fall through into a run. A gate that gives up
            # silently is the missing-input failure this project keeps meeting.
            print(f"esbmc_gate: TIMEOUT after {a.timeout}s -- "
                  f"{len(runs)} other run(s) live, MemAvailable {av} MB, "
                  f"budget {a.budget_mb} MB", file=sys.stderr)
            return 2
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
