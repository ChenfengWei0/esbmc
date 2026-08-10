#!/usr/bin/env python3
"""Reap stale RQ1 worker leases without starting any benchmark work."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_LOCAL_LEASES = Path("/tmp/veriput_rq1_case_leases.json")
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_REMOTE_LEASE_DIR = "/tmp/veriput_rq1_case_leases.d"


def _pid_alive(pid: object) -> bool:
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    return Path(f"/proc/{pid_i}").exists()


def reap_local(path: Path, stale_s: int) -> dict:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "removed": [], "kept": 0}
    leases = doc.get("leases")
    if not isinstance(leases, dict):
        return {"path": str(path), "removed": [], "kept": 0}
    now = time.time()
    removed = []
    kept = {}
    for key, lease in leases.items():
        if not isinstance(lease, dict):
            kept[key] = lease
            continue
        status = str(lease.get("status") or "")
        updated = float(lease.get("updated_ts") or lease.get("ts") or 0)
        age_s = max(0.0, now - updated)
        pid = lease.get("pid")
        worker_id = str(lease.get("worker_id") or "")
        stale = status == "running" and (
            age_s >= stale_s or (pid is not None and not _pid_alive(pid)) or
            worker_id == "local-existing-process")
        if stale:
            removed.append({
                "key": key,
                "bench": lease.get("bench"),
                "subject": lease.get("subject"),
                "age_s": round(age_s, 3),
                "pid": pid,
                "worker_id": worker_id,
            })
        else:
            kept[key] = lease
    if removed:
        doc["leases"] = kept
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "removed": removed, "kept": len(kept)}


def reap_remote(host: str, lease_dir: str, stale_s: int) -> dict:
    if not host:
        return {"enabled": False, "removed": []}
    script = r'''
import json
import shutil
import time
from pathlib import Path

lease_dir = Path("__LEASE_DIR__")
stale_s = __STALE_S__
now = time.time()
removed = []
kept = 0
for state_file in lease_dir.glob("*/state.json") if lease_dir.exists() else []:
    try:
        state = json.loads(state_file.read_text())
    except Exception:
        kept += 1
        continue
    status = str(state.get("status") or "")
    updated = float(state.get("updated_ts") or 0)
    age_s = max(0.0, now - updated)
    pid = state.get("pid")
    alive = False
    cmdline = ""
    try:
        proc_dir = Path(f"/proc/{int(pid)}")
        alive = proc_dir.exists()
        cmdline = (proc_dir / "cmdline").read_text(errors="replace").replace("\0", " ")
    except Exception:
        alive = False
    owns_lease = "rq1_remote_pump.py" in cmdline or "rq1_veriput_run.py" in cmdline
    if status == "running" and (age_s >= stale_s or not alive or not owns_lease):
        removed.append({
            "lease": state_file.parent.name,
            "bench": state.get("bench"),
            "subject": state.get("subject"),
            "age_s": round(age_s, 3),
            "pid": pid,
            "cmdline": cmdline[:180],
        })
        shutil.rmtree(state_file.parent, ignore_errors=True)
    else:
        kept += 1
print(json.dumps({"removed": removed, "kept": kept}, sort_keys=True))
'''.replace("__LEASE_DIR__", lease_dir).replace("__STALE_S__", str(stale_s))
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
         "python3 - <<'PY'\n" + script + "\nPY"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc = {"removed": [], "kept": 0}
    doc.update({
        "enabled": True,
        "host": host,
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-500:],
    })
    return doc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-leases",
                        type=Path,
                        default=DEFAULT_LOCAL_LEASES)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-lease-dir", default=DEFAULT_REMOTE_LEASE_DIR)
    parser.add_argument("--stale-s", type=int, default=1200)
    args = parser.parse_args()
    report = {
        "schema": "veriput-rq1-reap-stale-worker-leases/v1",
        "local": reap_local(args.local_leases, args.stale_s),
        "remote": reap_remote(args.remote_host, args.remote_lease_dir,
                              args.stale_s),
        "rule": (
            "Only stale running leases are removed. This script does not run "
            "ESBMC, RQ1, Foundry, certify_all, put_all, or benchmark cases."),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
