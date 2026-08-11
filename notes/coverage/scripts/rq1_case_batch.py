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
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
SUPERVISOR = HERE / "rq1_worker_supervisor.py"
DEFAULT_INVENTORY = Path("notes/coverage/rq1_no_valid_each_case.json")
DEFAULT_RUN_ROOT = Path("notes/coverage/rq1_runs")
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


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


def manifest_rows(args: argparse.Namespace) -> list[dict]:
    path = manifest_path(args)
    if not path.exists():
        return []
    with path.open(newline="") as stream:
        return [
            row for row in csv.DictReader(stream, delimiter="\t")
            if row.get("bench") and row.get("subject")
        ]


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


def active_process_lines() -> list[str]:
    proc = subprocess.run(
        [
            "pgrep",
            "-af",
            (
                "rq1_local_pump.py|rq1_remote_pump.py|rq1_veriput_run.py|"
                "certify_all.py|put_all.py|solidity_path_generalise.py|"
                "solidity_path_put.py|build/src/esbmc/esbmc"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return [line for line in proc.stdout.splitlines() if "pgrep -af" not in line]


def read_jsonl_tail(path: Path, limit: int = 5) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line[:500]})
    return rows


def result_numbers(result: dict) -> dict:
    adoption = result.get("adoption") if isinstance(result.get("adoption"), dict) else {}
    put = result.get("put") if isinstance(result.get("put"), dict) else {}
    artifact_counts = (put.get("artifact_counts")
                       if isinstance(put.get("artifact_counts"), dict) else {})
    return {
        "valid": int(adoption.get("valid") or result.get("valid") or
                     artifact_counts.get("valid") or put.get("valid") or 0),
        "put": int(adoption.get("put_valid") or result.get("put_valid") or
                   artifact_counts.get("put_valid") or put.get("put_valid") or 0),
        "r1r2": int(adoption.get("valid_put_with_R1_or_R2") or
                    result.get("r1r2") or
                    artifact_counts.get("valid_put_with_R1_or_R2") or
                    put.get("valid_put_with_R1_or_R2") or 0),
        "quality_bucket": put.get("quality_bucket") or result.get("bucket"),
    }


def latest_put_summaries(subject_dir: Path, limit: int = 3) -> list[dict]:
    out = []
    paths = sorted((subject_dir / "put").glob("*/put-summary.json"),
                   key=lambda path: path.stat().st_mtime if path.exists() else 0)
    for path in paths[-limit:]:
        doc = read_json(path)
        deliverable = doc.get("deliverable_b") if isinstance(doc.get("deliverable_b"), dict) else {}
        quality = deliverable.get("quality") if isinstance(deliverable.get("quality"), dict) else {}
        out.append({
            "unit": path.parent.name,
            "b": deliverable.get("b"),
            "valid_reference_rows": quality.get("valid_reference_rows"),
            "put_rows": quality.get("put_rows"),
            "r1r2_rows": quality.get("r1r2_rows"),
        })
    return out


def infer_stage(result: dict, cert_path: Path, put_summaries: list[dict],
                active: list[str]) -> str:
    nums = result_numbers(result)
    if nums["valid"]:
        return "final/adopted"
    if put_summaries:
        return "Stage4/PUT"
    cert_rows = read_jsonl_tail(cert_path, limit=1)
    if cert_rows:
        return "Stage2/certify"
    if active:
        return "Stage1/wrapper"
    return "not-running/no-final-result"


def monitor_decision(nums: dict, cert_summary: dict, put_summaries: list[dict],
                     active: list[str]) -> tuple[str, str]:
    if nums["valid"] and nums["put"] and nums["r1r2"]:
        return "已完成", "valid+PUT+R1/R2 已满足"
    if nums["valid"] and nums["put"]:
        return "转代码修复", "已有 valid PUT，但缺 R1/R2；继续跑同轮收益低"
    if nums["valid"]:
        return "转代码修复", "已有 valid 但不是 PUT；继续跑同轮不解决泛化"
    if not active and not nums["valid"]:
        return "转代码修复", "进程已结束且 no-valid"
    killed = int((cert_summary.get("bucket_counts") or {}).get("KILLED") or 0)
    certified = int(cert_summary.get("certified_regions") or 0)
    if put_summaries and any((row.get("put_rows") or 0) > 0 for row in put_summaries):
        return "继续跑", "Stage4 已有 PUT 候选，等待最终 adopt/result"
    if killed >= 3 and certified == 0:
        return "建议停止", "Stage2 多个 KILLED 且无 certified region，继续跑大概率浪费"
    if active:
        return "继续跑", "仍有活进程且尚未出现终局失败信号"
    return "观察", "证据不足"


def monitor(args: argparse.Namespace) -> dict:
    rows = manifest_rows(args)
    processes = active_process_lines()
    cases = []
    for row in rows:
        bench = row["bench"]
        subject = row["subject"]
        subject_dir = args.results_root / bench / "subjects" / subject
        result = read_json(subject_dir / "result.json")
        nums = result_numbers(result)
        cert = result.get("certification") if isinstance(result.get("certification"), dict) else {}
        cert_path = subject_dir / "cert/certify-results.jsonl"
        cert_tail = read_jsonl_tail(cert_path, limit=3)
        if not cert and cert_tail:
            buckets = Counter(str(item.get("bucket") or "UNKNOWN") for item in cert_tail)
            cert = {
                "rows_seen_tail": len(cert_tail),
                "bucket_counts_tail": dict(buckets),
            }
        put_summaries = latest_put_summaries(subject_dir)
        active = [line for line in processes if subject in line]
        stage = infer_stage(result, cert_path, put_summaries, active)
        decision, reason = monitor_decision(nums, cert, put_summaries, active)
        cases.append({
            "bench": bench,
            "subject": subject,
            "stage": stage,
            "valid": nums["valid"],
            "put": nums["put"],
            "r1r2": nums["r1r2"],
            "quality_bucket": nums["quality_bucket"],
            "cert_rows": cert.get("rows"),
            "certified_regions": cert.get("certified_regions"),
            "cert_bucket_counts": cert.get("bucket_counts") or
                                  cert.get("bucket_counts_tail") or {},
            "timed_out_units": cert.get("timed_out_units") or [],
            "latest_cert": [
                {
                    "unit": item.get("unit"),
                    "bucket": item.get("bucket"),
                    "exit": item.get("exit"),
                    "progress": ((item.get("driver_diagnostic") or {}).get(
                        "progress_stage") if isinstance(item.get("driver_diagnostic"),
                                                        dict) else None),
                }
                for item in cert_tail
            ],
            "put_summaries": put_summaries,
            "active_processes": len(active),
            "decision": decision,
            "decision_reason": reason,
        })
    return {
        "schema": "veriput-rq1-case-batch-monitor/v1",
        "batch_id": args.batch_id,
        "run_dir": str(run_dir(args)),
        "case_count": len(cases),
        "local_resources": local_resource_snapshot(),
        "cases": cases,
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
    if doc.get("schema") == "veriput-rq1-case-batch-monitor/v1":
        local = doc.get("local_resources") or {}
        print(f"批次：{doc.get('batch_id')}")
        print(f"运行目录：{doc.get('run_dir')}")
        print(f"本机可用内存 GiB：{local.get('mem_available_gib')}")
        print(f"本机相关进程数：{local.get('matching_process_count')}")
        for case in doc.get("cases") or []:
            print(f"- {case.get('subject')}")
            print(f"  阶段：{case.get('stage')}")
            print(f"  valid/PUT/R1R2：{case.get('valid')}/{case.get('put')}/{case.get('r1r2')}")
            print(f"  quality：{case.get('quality_bucket')}")
            print(f"  Stage2：rows={case.get('cert_rows')} certified={case.get('certified_regions')} buckets={case.get('cert_bucket_counts')}")
            if case.get("timed_out_units"):
                print(f"  timeout units：{', '.join(case.get('timed_out_units'))}")
            for item in case.get("latest_cert") or []:
                print(f"  latest cert：unit={item.get('unit')} bucket={item.get('bucket')} exit={item.get('exit')} progress={item.get('progress')}")
            for item in case.get("put_summaries") or []:
                print(f"  Stage4：unit={item.get('unit')} b={item.get('b')} valid_rows={item.get('valid_reference_rows')} put_rows={item.get('put_rows')} r1r2={item.get('r1r2_rows')}")
            print(f"  活进程：{case.get('active_processes')}")
            print(f"  决策：{case.get('decision')}，原因：{case.get('decision_reason')}")
        return
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "start", "status", "monitor",
                                            "stop"))
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
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
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
    elif args.command == "monitor":
        result = monitor(args)
    else:
        result = status(args)
    print_chinese(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
