#!/usr/bin/env python3
"""Stop local and remote RQ1/ESBMC worker processes and report memory.

This script is intentionally narrow: it kills only known VeriPUT/RQ1 worker
commands plus ESBMC/Forge/Anvil processes used by the RQ1 workers.  It also
prints process RSS separately from Linux buff/cache so cache is not mistaken
for a still-running worker.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


WORKER_PATTERN = re.compile(
    r"(?:^|/|\s)(?:esbmc|forge|anvil)(?:\s|$)"
    r"|rq1_veriput_run\.py"
    r"|certify_all\.py"
    r"|put_all\.py"
    r"|solidity_path_put\.py"
    r"|solidity_path_generalise\.py"
    r"|rq1_local_pump\.py"
    r"|rq1_remote_pump\.py"
    r"|run_rq1",
)
DEFAULT_REMOTE_HOST = "invmut-w2"


def _meminfo() -> dict:
    values: dict[str, float] = {}
    try:
        text = Path("/proc/meminfo").read_text(errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        match = re.match(
            r"^(MemTotal|MemAvailable|MemFree|Buffers|Cached):\s+(\d+)",
            line,
        )
        if match:
            values[match.group(1)] = int(match.group(2)) / 1024 / 1024
    return {
        "total_gib": round(values.get("MemTotal", 0), 3),
        "available_gib": round(values.get("MemAvailable", 0), 3),
        "free_gib": round(values.get("MemFree", 0), 3),
        "buffer_cache_gib":
            round(values.get("Buffers", 0) + values.get("Cached", 0), 3),
    }


def _process_rows() -> list[dict]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,rss=,etimes=,comm=,args="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    rows = []
    self_pid = os.getpid()
    for line in proc.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        pid_s, ppid_s, rss_s, etimes_s, comm, args = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        haystack = f"{comm} {args}"
        if not WORKER_PATTERN.search(haystack):
            continue
        rows.append({
            "pid": pid,
            "ppid": int(ppid_s),
            "rss_mib": round(int(rss_s) / 1024, 3),
            "runtime_s": int(etimes_s),
            "comm": comm,
            "args": args[:800],
        })
    return rows


def _kill_rows(rows: list[dict], dry_run: bool) -> dict:
    targets = [int(row["pid"]) for row in rows]
    if dry_run:
        return {"sent_term": [], "sent_kill": [], "alive_after": targets}
    sent_term = []
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            sent_term.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(1.0)
    alive = []
    sent_kill = []
    for pid in targets:
        if not Path(f"/proc/{pid}").exists():
            continue
        alive.append(pid)
        try:
            os.kill(pid, signal.SIGKILL)
            sent_kill.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(0.3)
    alive_after = [pid for pid in targets if Path(f"/proc/{pid}").exists()]
    return {
        "sent_term": sent_term,
        "sent_kill": sent_kill,
        "alive_after": alive_after,
    }


def local_stop(dry_run: bool) -> dict:
    before = _process_rows()
    kill = _kill_rows(before, dry_run)
    after = _process_rows()
    return {
        "host": "local",
        "dry_run": dry_run,
        "memory_before": _meminfo(),
        "processes_before": before,
        "kill": kill,
        "processes_after": after,
        "memory_after": _meminfo(),
    }


def remote_stop(host: str, dry_run: bool) -> dict:
    script = r'''
import json, os, re, signal, subprocess, time
from pathlib import Path

dry_run = __DRY_RUN__
pattern = re.compile(r"(?:^|/|\s)(?:esbmc|forge|anvil)(?:\s|$)|rq1_veriput_run\.py|certify_all\.py|put_all\.py|solidity_path_put\.py|solidity_path_generalise\.py|rq1_local_pump\.py|rq1_remote_pump\.py|run_rq1")

def meminfo():
    values = {}
    try:
        text = Path("/proc/meminfo").read_text(errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        m = re.match(r"^(MemTotal|MemAvailable|MemFree|Buffers|Cached):\s+(\d+)", line)
        if m:
            values[m.group(1)] = int(m.group(2)) / 1024 / 1024
    return {
        "total_gib": round(values.get("MemTotal", 0), 3),
        "available_gib": round(values.get("MemAvailable", 0), 3),
        "free_gib": round(values.get("MemFree", 0), 3),
        "buffer_cache_gib": round(values.get("Buffers", 0) + values.get("Cached", 0), 3),
    }

def rows():
    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,rss=,etimes=,comm=,args="],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)
    out = []
    self_pid = os.getpid()
    for line in proc.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        pid_s, ppid_s, rss_s, etimes_s, comm, args = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        if not pattern.search(f"{comm} {args}"):
            continue
        out.append({
            "pid": pid,
            "ppid": int(ppid_s),
            "rss_mib": round(int(rss_s) / 1024, 3),
            "runtime_s": int(etimes_s),
            "comm": comm,
            "args": args[:800],
        })
    return out

before = rows()
targets = [int(row["pid"]) for row in before]
sent_term = []
sent_kill = []
if not dry_run:
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            sent_term.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(1.0)
    for pid in targets:
        if not Path(f"/proc/{pid}").exists():
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            sent_kill.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(0.3)
after = rows()
print(json.dumps({
    "host": "remote",
    "dry_run": dry_run,
    "memory_before": meminfo(),
    "processes_before": before,
    "kill": {
        "sent_term": sent_term,
        "sent_kill": sent_kill,
        "alive_after": [pid for pid in targets if Path(f"/proc/{pid}").exists()],
    },
    "processes_after": after,
    "memory_after": meminfo(),
}, indent=2, sort_keys=True))
'''.replace("__DRY_RUN__", "True" if dry_run else "False")
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
         "python3 -"],
        input=script,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc = {
            "host": "remote",
            "dry_run": dry_run,
            "probe_failed": True,
            "stdout_tail": proc.stdout[-1000:],
        }
    doc["returncode"] = proc.returncode
    doc["stderr_tail"] = proc.stderr[-1000:]
    return doc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--remote-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    reports = []
    if not args.remote_only:
        reports.append(local_stop(args.dry_run))
    if not args.local_only:
        reports.append(remote_stop(args.remote_host, args.dry_run))
    doc = {
        "schema": "veriput-rq1-kill-workers/v1",
        "reports": reports,
        "rule": (
            "Worker stopped means processes_after is empty. High used memory "
            "from buffer_cache_gib is not worker RSS."),
    }
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 1 if any(report.get("processes_after") for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
