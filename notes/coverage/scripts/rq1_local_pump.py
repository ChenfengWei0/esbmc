#!/usr/bin/env python3
"""Conservative local RQ1 worker with memory/progress supervision."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import shlex
import subprocess
import time
from pathlib import Path


DEFAULT_TSV = Path("/tmp/veriput_no_valid_root_causes.tsv")
THEORY_TSV_MARKER = "theory_patch_id"
CE_TSV_MARKER = "ce_collection_id"
DEFAULT_STATE = Path("/tmp/veriput_rq1_local_state.json")
DEFAULT_VERIPUT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
DEFAULT_PROGRESS = Path("/tmp/veriput_local_progress.jsonl")
DEFAULT_INTERPRET_OUT = Path("/tmp/veriput_local_interpret.json")
DEFAULT_ADOPT_OUT = Path("/tmp/veriput_local_adopt.json")
DEFAULT_LEASES = Path("/tmp/veriput_rq1_case_leases.json")
DEFAULT_LEASE_STALE_S = 1200
DEFAULT_LEASE_REFRESH_S = 30
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_BUCKET_STATE = Path("/tmp/veriput_rq1_bucket_state.json")
DEFAULT_REPAIR_DISPATCH = Path(
    "notes/coverage/scripts/rq1_repair_dispatcher.py")
DEFAULT_REVIEW_DISPATCH = Path(
    "notes/coverage/scripts/rq1_review_dispatcher.py")


def mem_available_gib() -> int:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(int(line.split()[1]) / 1024 / 1024)
    return 0


def load_cases(tsv: Path, categories: set[str], limit: int,
               allow_uncovered_tsv: bool, ce_collection_only: bool) -> list[dict]:
    cases = []
    with tsv.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fieldnames = set(reader.fieldnames or [])
        required_marker = CE_TSV_MARKER if ce_collection_only else THEORY_TSV_MARKER
        if not allow_uncovered_tsv and required_marker not in fieldnames:
            raise SystemExit(
                f"{tsv} lacks required manifest marker {required_marker!r}")
        for row in reader:
            if categories and row.get("category") not in categories:
                continue
            if not allow_uncovered_tsv and not row.get(required_marker):
                continue
            if row.get("bench") == "peer182" and "contract080" not in row.get("subject", ""):
                continue
            cases.append(row)
            if limit > 0 and len(cases) >= limit:
                break
    return cases


def emit(progress: Path, row: dict) -> None:
    row = dict(row)
    row.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    row["mem_available_gib"] = mem_available_gib()
    with progress.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def case_key(case: dict) -> str:
    return f"{case.get('bench', '')}/{case.get('subject', '')}"


def _load_lease_doc(stream) -> dict:
    stream.seek(0)
    raw = stream.read()
    if not raw:
        return {"schema": "veriput-rq1-case-leases/v1", "leases": {}}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        doc = {"schema": "veriput-rq1-case-leases/v1", "leases": {}}
    doc.setdefault("schema", "veriput-rq1-case-leases/v1")
    doc.setdefault("leases", {})
    return doc


def _write_lease_doc(stream, doc: dict) -> None:
    stream.seek(0)
    stream.truncate()
    json.dump(doc, stream, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def acquire_case_lease(args, case: dict) -> tuple[bool, str]:
    args.lease_file.parent.mkdir(parents=True, exist_ok=True)
    key = case_key(case)
    now = time.time()
    worker_id = f"local:{os.getpid()}:{args.progress}"
    with args.lease_file.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        doc = _load_lease_doc(stream)
        lease = doc["leases"].get(key)
        if lease:
            status = str(lease.get("status") or "")
            age_s = now - float(lease.get("updated_ts") or lease.get("ts") or 0)
            if status in {"running", "done"} and age_s < args.lease_stale_s:
                fcntl.flock(stream, fcntl.LOCK_UN)
                return False, status
        doc["leases"][key] = {
            "bench": case.get("bench"),
            "subject": case.get("subject"),
            "category": case.get("category"),
            "worker_id": worker_id,
            "status": "running",
            "ts": now,
            "updated_ts": now,
        }
        _write_lease_doc(stream, doc)
        fcntl.flock(stream, fcntl.LOCK_UN)
    return True, "acquired"


def update_case_lease(args, case: dict, status: str, rc: int) -> None:
    key = case_key(case)
    now = time.time()
    args.lease_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lease_file.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        doc = _load_lease_doc(stream)
        lease = doc["leases"].setdefault(key, {})
        lease.update({
            "bench": case.get("bench"),
            "subject": case.get("subject"),
            "category": case.get("category"),
            "worker_id": f"local:{os.getpid()}:{args.progress}",
            "status": status,
            "rc": rc,
            "updated_ts": now,
        })
        _write_lease_doc(stream, doc)
        fcntl.flock(stream, fcntl.LOCK_UN)


def reset_leases(args) -> None:
    args.lease_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lease_file.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        _write_lease_doc(stream, {
            "schema": "veriput-rq1-case-leases/v1",
            "leases": {},
        })
        fcntl.flock(stream, fcntl.LOCK_UN)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _subject_result_from_interpret(path: Path) -> dict:
    doc = _read_json(path)
    rows = doc.get("rows") if isinstance(doc.get("rows"), list) else []
    if rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def progress_result_fields(interpreted: dict) -> dict:
    return {
        "bucket": interpreted.get("bucket"),
        "valid": int(interpreted.get("valid") or 0),
        "put_valid": int(interpreted.get("put_valid") or 0),
        "r1r2": int(interpreted.get("r1r2") or 0),
        "subject_dir": interpreted.get("subject_dir"),
    }


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def _bucket_doc(path: Path) -> dict:
    doc = _read_json(path)
    doc.setdefault("schema", "veriput-rq1-bucket-state/v1")
    doc.setdefault("buckets", {})
    return doc


def bucket_paused(args, category: str) -> bool:
    doc = _bucket_doc(args.bucket_state)
    row = doc["buckets"].get(category) or {}
    return bool(row.get("paused"))


def record_bucket_result(args, case: dict, interpreted: dict) -> dict:
    category = str(case.get("category") or "")
    bucket = str(interpreted.get("bucket") or category or "UNKNOWN")
    valid = int(interpreted.get("valid") or 0)
    put_valid = int(interpreted.get("put_valid") or 0)
    r1r2 = int(interpreted.get("r1r2") or 0)
    doc = _bucket_doc(args.bucket_state)
    row = doc["buckets"].setdefault(category, {
        "consecutive_no_valid": 0,
        "consecutive_valid_no_put": 0,
        "consecutive_valid_put_no_r1r2": 0,
        "paused": False,
    })
    if valid:
        row["consecutive_no_valid"] = 0
        if put_valid:
            row["consecutive_valid_no_put"] = 0
            row["consecutive_valid_put_no_r1r2"] = 0 if r1r2 else (
                int(row.get("consecutive_valid_put_no_r1r2") or 0) + 1)
        else:
            row["consecutive_valid_no_put"] = int(
                row.get("consecutive_valid_no_put") or 0) + 1
    else:
        row["consecutive_no_valid"] = int(
            row.get("consecutive_no_valid") or 0) + 1
    reason = ""
    if not valid and int(row.get("consecutive_no_valid") or 0) >= 2:
        row["paused"] = True
        reason = "two-consecutive-no-valid"
    elif bucket in {"UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING"}:
        row["paused"] = True
        reason = "schema-artifact-bug"
    elif int(row.get("consecutive_valid_no_put") or 0) >= 3:
        reason = "three-consecutive-valid-no-put"
    elif int(row.get("consecutive_valid_put_no_r1r2") or 0) >= 3:
        reason = "three-consecutive-put-no-r1r2"
    row.update({
        "last_bucket": bucket,
        "last_bench": case.get("bench"),
        "last_subject": case.get("subject"),
        "last_valid": valid,
        "last_put_valid": put_valid,
        "last_r1r2": r1r2,
        "last_ts": time.time(),
        "pause_reason": reason or row.get("pause_reason", ""),
    })
    args.bucket_state.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {"category_state": row, "trigger_reason": reason}


def maybe_emit_repair_ticket(args, case: dict, interpreted: dict,
                             bucket_update: dict) -> None:
    bucket = str(interpreted.get("bucket") or case.get("category") or "UNKNOWN")
    valid = int(interpreted.get("valid") or 0)
    put_valid = int(interpreted.get("put_valid") or 0)
    r1r2 = int(interpreted.get("r1r2") or 0)
    trigger = bucket_update.get("trigger_reason") or ""
    if valid and put_valid and r1r2 and not trigger:
        return
    if valid and put_valid and not trigger:
        return
    priority = "normal"
    suggested = ["notes/coverage/scripts/certify_all.py"]
    if bucket == "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING":
        priority = "high"
        suggested = ["notes/coverage/scripts/rq1_veriput_run.py"]
    elif bucket in {"NO_PUT_MATERIALIZATION", "NO_R1R2_ORACLE"}:
        suggested = ["scripts/solidity_path_put.py", "notes/coverage/scripts/put_all.py"]
    elif bucket.startswith("ESBMC_"):
        priority = "high"
        suggested = ["src/solidity-frontend/*.cpp", "src/goto-programs/goto_coverage.cpp"]
    ticket = {
        "schema": "veriput-rq1-repair-ticket/v1",
        "ts": time.time(),
        "bench": case.get("bench"),
        "subject": case.get("subject"),
        "category": case.get("category"),
        "result_bucket": bucket,
        "valid": valid,
        "put_valid": put_valid,
        "r1r2": r1r2,
        "priority": priority,
        "trigger_reason": trigger or "weak-or-failed-result",
        "subject_dir": interpreted.get("subject_dir"),
        "logs": [
            str(Path(interpreted.get("subject_dir") or "") / "driver.log"),
            str(Path(interpreted.get("subject_dir") or "") / "result.json"),
            str(Path(interpreted.get("subject_dir") or "") / "put.json"),
        ],
        "suggested_write_scope": suggested,
        "theoretical_progress_effect": (
            "If this case belongs to an already-covered category and is "
            "no-valid, rq1_no_valid_progress.py subtracts it from net "
            "theoretical_progress until repaired. valid-no-PUT and "
            "PUT-no-R1R2 remain quality debt."),
        "subagent_rule": (
            "Inspect listed failure records and owning source code before "
            "editing; do not run ESBMC/RQ1 as root-cause discovery."),
    }
    _append_jsonl(args.repair_tickets, ticket)


def refresh_dispatch_queues(args, case: dict, interpreted: dict) -> None:
    """Refresh repair/review queues immediately after a weak worker result."""
    valid = int(interpreted.get("valid") or 0)
    put_valid = int(interpreted.get("put_valid") or 0)
    r1r2 = int(interpreted.get("r1r2") or 0)
    if valid and put_valid and r1r2:
        return
    commands = (
        [sys_executable(), str(args.repair_dispatcher)],
        [sys_executable(), str(args.review_dispatcher)],
    )
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                start_new_session=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            emit(args.progress, {
                "bench": case.get("bench"),
                "subject": case.get("subject"),
                "category": case.get("category"),
                "status": "dispatch-refresh-failed",
                "rc": 124,
                "dispatcher": cmd[-1],
                "error": str(exc),
            })
            continue
        emit(args.progress, {
            "bench": case.get("bench"),
            "subject": case.get("subject"),
            "category": case.get("category"),
            "status": "dispatch-refreshed",
            "rc": proc.returncode,
            "dispatcher": cmd[-1],
            "stderr_tail": proc.stderr[-500:],
        })


def sys_executable() -> str:
    return os.environ.get("PYTHON", "python3")


def kill_over_budget(rss_limit_gib: int) -> list[int]:
    limit_kib = int(rss_limit_gib) * 1024 * 1024
    proc = subprocess.run(
        ["ps", "-eo", "pid=,rss=,comm="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    killed = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid_s, rss_s, comm = parts
        if "esbmc" not in comm:
            continue
        try:
            pid = int(pid_s)
            rss = int(rss_s)
        except ValueError:
            continue
        if rss <= limit_kib:
            continue
        subprocess.run(["kill", "-TERM", str(pid)], check=False)
        time.sleep(2)
        subprocess.run(["kill", "-KILL", str(pid)], check=False)
        killed.append(pid)
    return killed


def run_case(args, case: dict) -> int:
    cmd = [
        "python3",
        "notes/coverage/scripts/rq1_veriput_run.py",
        "--veriput-root",
        str(args.veriput_root),
        "--benchmark",
        case["bench"],
        "--subject-id",
        case["subject"],
        "--result-root",
        str(args.result_root),
        "--timeout",
        str(args.timeout),
        "--esbmc-run-timeout",
        str(args.esbmc_run_timeout),
        "--wrapper-grace",
        str(args.wrapper_grace),
        "--forge-timeout",
        str(args.forge_timeout),
        "--memlimit-gib",
        str(args.memlimit_gib),
        "--jobs",
        str(args.jobs),
        "--esbmc",
        str(args.esbmc),
        "--redo",
    ]
    if args.ce_collection_only:
        cmd.append("--ce-collection-only")
    log = Path("/tmp/veriput_local_worker.log")
    with log.open("a") as stream:
        stream.write("[local-rq1] " + shell_join(cmd) + "\n")
        proc = subprocess.Popen(
            cmd,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
        last_refresh = 0.0
        while True:
            now = time.time()
            if now - last_refresh >= args.lease_refresh_s:
                update_case_lease(args, case, "running", 0)
                last_refresh = now
            killed = kill_over_budget(args.esbmc_rss_limit_gib)
            if killed:
                emit(args.progress, {
                    "status": "killed-over-rss",
                    "pids": killed,
                })
            rc = proc.poll()
            if rc is not None:
                return rc
            time.sleep(min(5, max(1, args.lease_refresh_s)))


def result_subject_dir(args, case: dict) -> Path:
    return args.result_root / case["bench"] / "subjects" / case["subject"]


def postprocess_results(args, case: dict) -> dict:
    subject_dir = result_subject_dir(args, case)
    adopt_cmd = [
        "python3",
        "notes/coverage/scripts/rq1_results_adopt.py",
        "--source-root",
        str(args.result_root),
        "--subject-dir",
        str(subject_dir),
        "--results-root",
        str(args.result_root),
        "--out",
        str(args.adopt_out),
    ]
    interpret_cmd = [
        "python3",
        "notes/coverage/scripts/rq1_esbmc_result_interpret.py",
        "--results-root",
        str(args.result_root),
        "--subject-dir",
        str(subject_dir),
        "--out",
        str(args.interpret_out),
    ]
    adopt = subprocess.run(adopt_cmd, check=False, start_new_session=True)
    interpret = subprocess.run(interpret_cmd, check=False, start_new_session=True)
    return {
        "adopt_rc": adopt.returncode,
        "interpret_rc": interpret.returncode,
        "adopt_out": str(args.adopt_out),
        "interpret_out": str(args.interpret_out),
    }


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--interpret-out", type=Path, default=DEFAULT_INTERPRET_OUT)
    parser.add_argument("--adopt-out", type=Path, default=DEFAULT_ADOPT_OUT)
    parser.add_argument("--lease-file", type=Path, default=DEFAULT_LEASES)
    parser.add_argument("--lease-stale-s", type=int, default=DEFAULT_LEASE_STALE_S)
    parser.add_argument("--lease-refresh-s",
                        type=int,
                        default=DEFAULT_LEASE_REFRESH_S)
    parser.add_argument("--reset-leases-on-start",
                        action=argparse.BooleanOptionalAction,
                        default=False)
    parser.add_argument("--repair-tickets", type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--bucket-state", type=Path, default=DEFAULT_BUCKET_STATE)
    parser.add_argument("--repair-dispatcher",
                        type=Path,
                        default=DEFAULT_REPAIR_DISPATCH)
    parser.add_argument("--review-dispatcher",
                        type=Path,
                        default=DEFAULT_REVIEW_DISPATCH)
    parser.add_argument("--veriput-root", type=Path, default=DEFAULT_VERIPUT)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--esbmc", type=Path, default=DEFAULT_ESBMC)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--ce-collection-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--esbmc-run-timeout", type=int, default=60)
    parser.add_argument("--wrapper-grace", type=int, default=60)
    parser.add_argument("--forge-timeout", type=int, default=180)
    parser.add_argument("--memlimit-gib", type=int, default=8)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--min-mem-gib", type=int, default=10)
    parser.add_argument("--esbmc-rss-limit-gib", type=int, default=12)
    parser.add_argument("--sleep-s", type=int, default=20)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--allow-uncovered-tsv", action="store_true")
    args = parser.parse_args()

    cases = load_cases(args.tsv, set(args.category), args.limit,
                       args.allow_uncovered_tsv, args.ce_collection_only)
    if not cases and not args.allow_uncovered_tsv:
        raise SystemExit(
            "theory-covered manifest has zero cases; refusing to start local "
            "worker until a reviewed+committed patch covers concrete subjects")
    state = {
        "schema": "veriput-rq1-local-worker/v1",
        "started": True,
        "started_ts": time.time(),
        "pid": os.getpid(),
        "case_count": len(cases),
        "input_tsv": str(args.tsv),
        "theory_manifest_required": not args.allow_uncovered_tsv,
        "allow_uncovered_tsv": bool(args.allow_uncovered_tsv),
        "case_parallel": 1,
        "memory_watchdog": True,
        "progress_watchdog": True,
        "process_watchdog": True,
        "min_mem_gib": args.min_mem_gib,
        "esbmc_rss_limit_gib": args.esbmc_rss_limit_gib,
        "progress": str(args.progress),
        "lease_file": str(args.lease_file),
        "lease_stale_s": args.lease_stale_s,
        "lease_refresh_s": args.lease_refresh_s,
        "reset_leases_on_start": args.reset_leases_on_start,
        "repair_tickets": str(args.repair_tickets),
        "bucket_state": str(args.bucket_state),
    }
    args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    if args.reset_leases_on_start:
        reset_leases(args)
    while True:
        for case in cases:
            if bucket_paused(args, str(case.get("category") or "")):
                emit(args.progress, {
                    "bench": case.get("bench"),
                    "subject": case.get("subject"),
                    "category": case.get("category"),
                    "status": "skipped-bucket-paused",
                    "rc": 127,
                })
                continue
            acquired, lease_status = acquire_case_lease(args, case)
            if not acquired:
                emit(args.progress, {
                    "bench": case.get("bench"),
                    "subject": case.get("subject"),
                    "category": case.get("category"),
                    "status": "skipped-lease-held",
                    "lease_status": lease_status,
                    "rc": 126,
                })
                continue
            killed = kill_over_budget(args.esbmc_rss_limit_gib)
            if killed:
                emit(args.progress, {
                    "status": "killed-over-rss",
                    "pids": killed,
                })
            mem = mem_available_gib()
            if mem < args.min_mem_gib:
                emit(args.progress, {
                    "bench": case.get("bench"),
                    "subject": case.get("subject"),
                    "category": case.get("category"),
                    "status": "skipped-low-mem",
                    "rc": 125,
                })
                update_case_lease(args, case, "skipped-low-mem", 125)
                time.sleep(args.sleep_s)
                continue
            emit(args.progress, {
                "bench": case.get("bench"),
                "subject": case.get("subject"),
                "category": case.get("category"),
                "status": "running",
                "rc": 0,
            })
            rc = run_case(args, case)
            post = postprocess_results(args, case)
            interpreted = _subject_result_from_interpret(args.interpret_out)
            bucket_update = record_bucket_result(args, case, interpreted)
            maybe_emit_repair_ticket(args, case, interpreted, bucket_update)
            refresh_dispatch_queues(args, case, interpreted)
            done_status = "done" if rc == 0 else "failed"
            update_case_lease(args, case, done_status, rc)
            emit(args.progress, {
                "bench": case.get("bench"),
                "subject": case.get("subject"),
                "category": case.get("category"),
                "status": done_status,
                "rc": rc,
                "postprocess": post,
                **progress_result_fields(interpreted),
            })
        if not args.loop:
            break
        time.sleep(args.sleep_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
