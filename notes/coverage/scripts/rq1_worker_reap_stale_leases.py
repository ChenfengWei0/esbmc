#!/usr/bin/env python3
"""Reap RQ1 running leases that have no live owning worker process."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_LOCAL_LEASES = Path("/tmp/veriput_rq1_case_leases.json")
DEFAULT_REMOTE_LEASE_DIR = "/tmp/veriput_rq1_case_leases.d"


def pid_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    return Path(f"/proc/{value}").exists()


def reap_local(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"lease_file": str(path), "reaped": 0, "reason": "missing-or-invalid"}
    leases = doc.get("leases") if isinstance(doc.get("leases"), dict) else {}
    reaped = []
    now = time.time()
    for key, lease in leases.items():
        if not isinstance(lease, dict) or lease.get("status") != "running":
            continue
        pid = lease.get("pid")
        worker_id = str(lease.get("worker_id") or "")
        if pid_alive(pid) or (pid is None and worker_id != "local-existing-process"):
            continue
        lease["status"] = "abandoned-stale-no-process"
        lease["reaped_ts"] = now
        lease["reap_reason"] = "no live local worker/esbmc process owns this lease"
        reaped.append({
            "key": key,
            "bench": lease.get("bench"),
            "subject": lease.get("subject"),
            "old_worker_id": worker_id,
            "old_pid": pid,
        })
    if reaped:
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {"lease_file": str(path), "reaped": len(reaped), "items": reaped}


def run(cmd: list[str], timeout: int = 20) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "timeout",
        }


def remote_script(lease_dir: str) -> str:
    return f"""
python3 - <<'PY'
import json
import os
import time
from pathlib import Path

root = Path({lease_dir!r})
reaped = []
now = time.time()
for state_path in root.glob('*/state.json') if root.exists() else []:
    try:
        doc = json.loads(state_path.read_text())
    except Exception:
        continue
    if doc.get('status') != 'running':
        continue
    try:
        pid = int(doc.get('pid'))
    except Exception:
        pid = 0
    alive = pid > 0 and Path(f'/proc/{{pid}}').exists()
    if alive:
        continue
    doc['status'] = 'abandoned-stale-no-process'
    doc['reaped_ts'] = now
    doc['reap_reason'] = 'no live remote worker/esbmc process owns this lease'
    state_path.write_text(json.dumps(doc, sort_keys=True) + '\\n')
    updated = state_path.parent / 'updated_ts'
    updated.write_text(str(int(now)) + '\\n')
    reaped.append({{
        'lease': state_path.parent.name,
        'bench': doc.get('bench'),
        'subject': doc.get('subject'),
        'old_pid': pid,
    }})
print(json.dumps({{'lease_dir': str(root), 'reaped': len(reaped), 'items': reaped}}, ensure_ascii=False, sort_keys=True))
PY
"""


def reap_remote(host: str, lease_dir: str) -> dict:
    proc = run([
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        host,
        remote_script(lease_dir),
    ])
    try:
        payload = json.loads(proc["stdout"])
    except json.JSONDecodeError:
        payload = {}
    return {
        "host": host,
        "returncode": proc["returncode"],
        "stderr_tail": proc["stderr"][-1000:],
        **payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-leases", type=Path, default=DEFAULT_LOCAL_LEASES)
    parser.add_argument("--host", default="invmut-w2")
    parser.add_argument("--remote-lease-dir", default=DEFAULT_REMOTE_LEASE_DIR)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--remote-only", action="store_true")
    args = parser.parse_args()
    report = {
        "schema": "veriput-rq1-stale-lease-reaper/v1",
        "说明": "只清理没有活进程持有的 running lease；不运行 benchmark，不修改 Datasets/Results",
    }
    if not args.remote_only:
        report["本机"] = reap_local(args.local_leases)
    if not args.local_only:
        report["远程"] = reap_remote(args.host, args.remote_lease_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
