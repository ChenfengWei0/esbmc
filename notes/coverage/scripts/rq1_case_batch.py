#!/usr/bin/env python3
"""Run a fixed RQ1 no-valid batch through the repo-managed supervisor."""

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
SUPERVISOR = HERE / "rq1_worker_supervisor.py"
DEFAULT_INVENTORY = Path("notes/coverage/rq1_no_valid_each_case.json")
DEFAULT_RUN_ROOT = Path("notes/coverage/rq1_runs")
DEFAULT_REMOTE_HOST = "invmut-w2"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def run_dir(args: argparse.Namespace) -> Path:
    return args.run_root / args.batch_id


def manifest_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "manifest.tsv"


def state_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "supervisor.json"


def lease_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "leases.json"


def prepare(args: argparse.Namespace) -> dict:
    inventory = read_json(args.inventory)
    rows = inventory.get("rows") if isinstance(inventory.get("rows"), list) else []
    selected = rows[args.start_index - 1:args.end_index]
    if len(selected) != args.end_index - args.start_index + 1:
        raise SystemExit("inventory range is incomplete")
    out = manifest_path(args)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("bench", "subject", "category", "theory_patch_id"),
            delimiter="\t",
        )
        writer.writeheader()
        for absolute_index, row in zip(range(args.start_index, args.end_index + 1),
                                       selected):
            writer.writerow({
                "bench": row.get("bench"),
                "subject": row.get("subject"),
                "category": f"manual{absolute_index:03d}_{args.batch_id}",
                "theory_patch_id": args.batch_id,
            })
    meta = {
        "schema": "veriput-rq1-case-batch/v1",
        "batch_id": args.batch_id,
        "inventory": str(args.inventory),
        "start_index": args.start_index,
        "end_index": args.end_index,
        "case_count": len(selected),
        "local_parallel": args.local_parallel,
        "remote_parallel": args.remote_parallel,
        "manifest": str(out),
        "state": str(state_path(args)),
        "lease_file": str(lease_path(args)),
        "run_dir": str(run_dir(args)),
        "prepared_ts": time.time(),
    }
    write_json(run_dir(args) / "batch.json", meta)
    return meta


def supervisor_cmd(args: argparse.Namespace, command: str) -> list[str]:
    return [
        sys.executable,
        str(SUPERVISOR),
        command,
        "--manifest",
        str(manifest_path(args)),
        "--state",
        str(state_path(args)),
        "--run-dir",
        str(run_dir(args)),
        "--lease-file",
        str(lease_path(args)),
        "--remote-host",
        args.remote_host,
        "--local-parallel",
        str(args.local_parallel),
        "--remote-parallel",
        str(args.remote_parallel),
        "--timeout-s",
        str(args.timeout_s),
        "--local-memlimit-gib",
        str(args.local_memlimit_gib),
        "--remote-memlimit-gib",
        str(args.remote_memlimit_gib),
        "--local-rss-limit-gib",
        str(args.local_rss_limit_gib),
        "--remote-rss-limit-gib",
        str(args.remote_rss_limit_gib),
    ]


def command_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout}
    payload.update({"returncode": proc.returncode, "stderr": proc.stderr.strip()})
    return payload


def start(args: argparse.Namespace) -> dict:
    if not manifest_path(args).exists():
        prepare(args)
    state = read_json(state_path(args))
    live_workers = [
        row for row in state.get("workers") or []
        if isinstance(row.get("pid"), int) and Path(f"/proc/{row['pid']}").exists()
    ]
    if args.reset_leases and not live_workers:
        write_json(lease_path(args), {
            "schema": "veriput-rq1-case-leases/v1",
            "leases": {},
            "reset_ts": time.time(),
            "reset_by": "rq1_case_batch.py start",
        })
        for path in run_dir(args).glob("local_worker_*"):
            if path.suffix in {".jsonl", ".log", ".json"}:
                path.write_text("")
        remote_log = run_dir(args) / "remote_worker_supervisor.log"
        if remote_log.exists():
            remote_log.write_text("")
    return command_json(supervisor_cmd(args, "start"))


def stop(args: argparse.Namespace) -> dict:
    result = command_json(supervisor_cmd(args, "stop"))
    # Best-effort cleanup for child processes that survived process-group TERM.
    state = read_json(state_path(args))
    pids = []
    for worker in state.get("workers") or []:
        pid = worker.get("pid")
        if isinstance(pid, int) and Path(f"/proc/{pid}").exists():
            pids.append(pid)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return result


def local_resource_snapshot() -> dict:
    mem_available = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available = round(int(line.split()[1]) / 1024 / 1024, 2)
                break
    except OSError:
        pass
    proc = subprocess.run(
        [
            "pgrep",
            "-af",
            "rq1_local_pump.py|rq1_remote_pump.py|rq1_veriput_run.py|certify_all.py|solidity_path_generalise.py|solidity_path_put.py|build/src/esbmc/esbmc",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    lines = [line for line in proc.stdout.splitlines() if "pgrep -af" not in line]
    return {
        "mem_available_gib": mem_available,
        "matching_process_count": len(lines),
        "matching_processes": lines[:50],
    }


def status(args: argparse.Namespace) -> dict:
    sup = command_json(supervisor_cmd(args, "status"))
    state = read_json(state_path(args))
    progress = []
    for path in sorted(run_dir(args).glob("local_worker_*_progress.jsonl")):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        progress.append({"path": str(path), "events": len(lines),
                         "last": lines[-1] if lines else ""})
    return {
        "schema": "veriput-rq1-case-batch-status/v1",
        "batch_id": args.batch_id,
        "run_dir": str(run_dir(args)),
        "manifest": str(manifest_path(args)),
        "supervisor": sup,
        "state": state,
        "local_resources": local_resource_snapshot(),
        "local_progress": progress,
    }


def print_chinese(doc: dict) -> None:
    if doc.get("schema") == "veriput-rq1-case-batch-status/v1":
        sup = doc.get("supervisor") or {}
        workers = ((doc.get("state") or {}).get("workers") or [])
        alive = sum(1 for row in workers if row.get("pid"))
        local = doc.get("local_resources") or {}
        print(f"批次：{doc.get('batch_id')}")
        print(f"运行目录：{doc.get('run_dir')}")
        print(f"supervisor running：{sup.get('running')}")
        print(f"worker 槽位：{alive}")
        print(f"本机可用内存 GiB：{local.get('mem_available_gib')}")
        print(f"本机相关进程数：{local.get('matching_process_count')}")
        for row in doc.get("local_progress") or []:
            print(f"进度文件：{row.get('path')} events={row.get('events')} last={row.get('last')}")
        return
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "start", "status", "stop"))
    parser.add_argument("--batch-id", default="manual-005-012")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--start-index", type=int, default=5)
    parser.add_argument("--end-index", type=int, default=12)
    parser.add_argument("--local-parallel", type=int, default=5)
    parser.add_argument("--remote-parallel", type=int, default=3)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--local-memlimit-gib", type=int, default=12)
    parser.add_argument("--remote-memlimit-gib", type=float, default=5.5)
    parser.add_argument("--local-rss-limit-gib", type=int, default=18)
    parser.add_argument("--remote-rss-limit-gib", type=float, default=9.0)
    parser.add_argument("--reset-leases",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args)
    elif args.command == "start":
        result = start(args)
    elif args.command == "stop":
        result = stop(args)
    else:
        result = status(args)
    print_chinese(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
