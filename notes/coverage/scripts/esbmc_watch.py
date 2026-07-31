#!/usr/bin/env python3
"""Emit one line whenever the machine's esbmc load crosses a guard.

WHY NOT `pgrep -x esbmc`. `pgrep -x` matches `comm`, which the kernel truncates
to 15 characters, so a binary copied to `esbmc_snapshot_unwind` -- which is
exactly what an agent does to protect itself from a concurrent rebuild --
presents as `esbmc_snapshot_` and NEVER matches. The guard then reports zero
while multi-GB solvers are live: a watchdog that is silent for the wrong reason,
which is worse than no watchdog. This reads /proc/<pid>/cmdline instead, and
matches on the FLAG rather than the program name, so it sees every path-coverage
run whatever the binary is called.

Prints nothing while things are fine; every printed line is an event.
"""
import os
import sys
import time

FLAG = "--solidity-path-coverage"
MIN_AVAIL_MB = 6000
MAX_CONCURRENT = 3


def avail_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return -1


def live_runs():
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if any(a == FLAG for a in argv):
            rss = 0
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss = int(line.split()[1]) // 1024
            except OSError:
                pass
            out.append((pid, rss))
    return out


def main():
    # A WATCHDOG THAT HAS NEVER BEEN SEEN TO FIRE IS NOT A WATCHDOG. `--once`
    # prints the census unconditionally so the detector can be proved against a
    # run that is live right now, rather than trusted because it is silent.
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        runs = live_runs()
        print(f"MemAvailable {avail_mb()} MB, {len(runs)} path-coverage "
              f"run(s) live")
        for pid, rss in runs:
            print(f"  pid {pid}  RSS {rss} MB")
        return
    period = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    while True:
        a = avail_mb()
        runs = live_runs()
        total = sum(r for _, r in runs)
        if a >= 0 and a < MIN_AVAIL_MB:
            print(f"[ALERT] MemAvailable {a} MB with {len(runs)} path-coverage "
                  f"run(s) live using {total} MB", flush=True)
        if len(runs) > MAX_CONCURRENT:
            print(f"[ALERT] {len(runs)} concurrent path-coverage runs "
                  f"({total} MB, MemAvailable {a} MB) -- the serial rule is "
                  f"being broken", flush=True)
        time.sleep(period)


if __name__ == "__main__":
    main()
