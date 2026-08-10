#!/usr/bin/env python3
"""Start, inspect, and stop the theory-gated RQ1 worker set."""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCAL = HERE / "rq1_local_pump.py"
REMOTE = HERE / "rq1_remote_pump.py"
DEFAULT_STATE = Path("/tmp/veriput_rq1_worker_supervisor.json")
DEFAULT_MANIFEST = Path("/tmp/veriput_rq1_theory_covered_cases.tsv")
DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _manifest_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if "theory_patch_id" not in set(reader.fieldnames or []):
            raise SystemExit("theory manifest missing theory_patch_id")
        return sum(1 for row in reader if row.get("bench") and row.get("subject"))


def _partition_manifest(path: Path, local_parallel: int,
                        remote_parallel: int) -> tuple[Path, Path, int, int]:
    """Write disjoint host shards so local and remote never repeat a case."""
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = list(reader.fieldnames or [])
        rows = [row for row in reader if row.get("bench") and row.get("subject")]
    total_slots = max(1, local_parallel + remote_parallel)
    local_target = min(len(rows), max(0, round(len(rows) * local_parallel /
                                                total_slots)))
    if rows and local_parallel and local_target == 0:
        local_target = 1
    if len(rows) > 1 and remote_parallel and local_target == len(rows):
        local_target -= 1
    local_rows, remote_rows = rows[:local_target], rows[local_target:]
    stem = path.with_suffix("")
    local_path = Path(f"{stem}.local.tsv")
    remote_path = Path(f"{stem}.remote.tsv")
    for out, shard in ((local_path, local_rows), (remote_path, remote_rows)):
        with out.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(shard)
    return local_path, remote_path, len(local_rows), len(remote_rows)


def _alive(pid: object) -> bool:
    try:
        return int(pid) > 0 and Path(f"/proc/{int(pid)}").exists()
    except (TypeError, ValueError):
        return False


def _base_local_args(args: argparse.Namespace, index: int, manifest: Path) -> list[str]:
    stem = f"/tmp/veriput_rq1_local_worker_{index}"
    return [
        sys.executable, str(LOCAL), "--tsv", str(manifest),
        "--state", f"{stem}_state.json", "--progress", f"{stem}_progress.jsonl",
        "--interpret-out", f"{stem}_interpret.json", "--adopt-out",
        f"{stem}_adopt.json", "--lease-file", "/tmp/veriput_rq1_case_leases.json",
        "--result-root", str(args.results_root), "--limit", "0", "--loop",
        "--timeout", str(args.timeout_s), "--esbmc-run-timeout", str(args.timeout_s),
        "--memlimit-gib", str(args.local_memlimit_gib), "--jobs", "1",
        "--esbmc-rss-limit-gib", str(args.local_rss_limit_gib),
    ]


def start(args: argparse.Namespace, action: dict | None = None) -> dict:
    if action:
        args.manifest = Path(action.get("tsv") or args.manifest)
    count = _manifest_count(args.manifest)
    if count <= 0:
        return {"started": False, "reason": "empty-theory-manifest", "case_count": 0}
    existing = _read(args.state)
    existing_workers = existing.get("workers") if isinstance(existing.get("workers"), list) else []
    if existing_workers and all(_alive(row.get("pid")) for row in existing_workers):
        return {"started": False, "reason": "already-running", "case_count": count,
                "workers": existing_workers}
    local_manifest, remote_manifest, local_cases, remote_cases = (
        _partition_manifest(args.manifest, args.local_parallel,
                            args.remote_parallel))
    workers = []
    for index in range(min(args.local_parallel, local_cases)):
        log = Path(f"/tmp/veriput_rq1_local_worker_{index}.log")
        log_stream = log.open("ab")
        command = _base_local_args(args, index, local_manifest)
        proc = subprocess.Popen(command, stdout=log_stream,
                                stderr=subprocess.STDOUT, start_new_session=True)
        log_stream.close()
        workers.append({"kind": "local", "index": index, "pid": proc.pid,
                        "log": str(log), "command": command})
    remote_cmd = [
        sys.executable, str(REMOTE), "--tsv", str(remote_manifest),
        "--host", args.remote_host, "--limit", "0", "--loop",
        "--timeout", str(args.timeout_s), "--esbmc-run-timeout", str(args.timeout_s),
        "--case-parallel", str(args.remote_parallel),
        "--max-case-parallel", str(args.remote_parallel),
        "--memlimit-gib", str(args.remote_memlimit_gib),
        "--esbmc-rss-limit-gib", str(args.remote_rss_limit_gib),
        "--sync-code", "--sync-veriput", "--remote-build",
        "--sync-results-back", "--start-pull-loop",
    ]
    if remote_cases:
        log = Path("/tmp/veriput_rq1_remote_worker_supervisor.log")
        log_stream = log.open("ab")
        proc = subprocess.Popen(remote_cmd, stdout=log_stream, stderr=subprocess.STDOUT,
                                start_new_session=True)
        log_stream.close()
        workers.append({"kind": "remote", "pid": proc.pid, "log": str(log),
                        "command": remote_cmd})
    state = {"schema": "veriput-rq1-worker-supervisor/v1", "started_ts": time.time(),
             "manifest": str(args.manifest), "local_manifest": str(local_manifest),
             "remote_manifest": str(remote_manifest), "case_count": count,
             "local_case_count": local_cases, "remote_case_count": remote_cases,
             "workers": workers,
             "local_parallel": args.local_parallel, "remote_parallel": args.remote_parallel}
    _write(args.state, state)
    return {"started": True, **state}


def stop(args: argparse.Namespace) -> dict:
    state = _read(args.state)
    stopped = []
    for worker in state.get("workers") or []:
        pid = worker.get("pid")
        if _alive(pid):
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except (OSError, ValueError):
                pass
            stopped.append(pid)
    state["stopped_ts"] = time.time()
    state["stopped_pids"] = stopped
    state["workers"] = []
    _write(args.state, state)
    return {"stopped": stopped, "state": str(args.state)}


def status(args: argparse.Namespace) -> dict:
    state = _read(args.state)
    workers = [{**worker, "alive": _alive(worker.get("pid"))}
               for worker in state.get("workers") or []]
    return {"schema": "veriput-rq1-worker-supervisor-status/v1",
            "state": str(args.state), "workers": workers,
            "running": sum(bool(row["alive"]) for row in workers)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop", "status"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--remote-host", default="invmut-w2")
    parser.add_argument("--local-parallel", type=int, default=3)
    parser.add_argument("--remote-parallel", type=int, default=2)
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--local-memlimit-gib", type=int, default=8)
    parser.add_argument("--remote-memlimit-gib", type=float, default=5.5)
    parser.add_argument("--local-rss-limit-gib", type=int, default=12)
    parser.add_argument("--remote-rss-limit-gib", type=float, default=10.0)
    parser.add_argument("--action-stdin", action="store_true")
    args = parser.parse_args()
    action = None
    if args.action_stdin:
        try:
            action = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid action JSON: {exc}") from exc
    result = (start(args, action) if args.command == "start" else
              stop(args) if args.command == "stop" else status(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
