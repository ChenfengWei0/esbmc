#!/usr/bin/env python3
"""Is anything else already running ESBMC? Prints the count and lists them.

WHY THIS AND NOT A HEADROOM CALCULATION. certify_all.py discharges the "never
run ESBMC concurrently" rule into arithmetic -- jobs x memlimit inside 60% of
MemAvailable -- and that is right for ITS OWN parallel jobs, whose limit it
sets. It does not transfer to a serial sweep sharing the machine with an
unrelated job: MemAvailable is a reading of NOW, and the other job's peak is in
the future. A regression suite measured at 0.7 GiB per process in one family
reached 3.6 GiB per process in another, so a gate that read the low moment would
have said yes and been wrong by 12 GiB.

So the check is the rule as originally stated: refuse while another ESBMC is
running, whoever started it.

Walks /proc rather than shelling to pgrep, and matches on the EXECUTABLE, not on
a command-line substring -- `pgrep -f esbmc` matches this script's own command
line, which is a mistake this project has already made in a wait loop.

Exit 0 = nothing else running (safe), 1 = busy.
"""
import os
import sys
from pathlib import Path

me = os.getpid()
mine = {me, os.getppid()}
found = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    pid = int(entry.name)
    if pid in mine:
        continue
    try:
        exe = os.readlink(entry / "exe")
    except (OSError, PermissionError):
        continue
    if os.path.basename(exe) != "esbmc":
        continue
    try:
        cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace").strip()
    except OSError:
        cmd = "(unreadable)"
    found.append((pid, cmd))

print(f"{len(found)} other ESBMC process(es) running")
for pid, cmd in found:
    print(f"  {pid}  {cmd[:160]}")
sys.exit(1 if found else 0)
