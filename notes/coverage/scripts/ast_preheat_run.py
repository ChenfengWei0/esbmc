#!/usr/bin/env python3
"""Run a VeriPUT AST preheat schedule with journaled resume.

The input must be a `veriput-ast-preheat-schedule/v1` document produced by
`ast_preheat_schedule.py`.  This runner executes each job's `preheat_argv`,
records one JSONL row per attempt, and resumes only rows whose prior journaled
status is `ok`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


class PreheatRunError(ValueError):
    """The schedule or requested run mode is unsafe."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreheatRunError(f"{path} is not valid JSON: {exc}") from exc


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (AttributeError, ValueError):
        raise PreheatRunError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise PreheatRunError("--shard needs 0 <= i < n")
    return idx, total


def _apply_shard(items, shard):
    if shard is None:
        return items
    idx, total = shard
    return [item for pos, item in enumerate(items) if pos % total == idx]


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _completed_from_journal(path: str) -> set[str]:
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    done = set()
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "ok" and row.get("job_id"):
            done.add(row["job_id"])
    return done


def _validate_job(job: dict):
    argv = job.get("preheat_argv")
    if not isinstance(argv, list) or not argv:
        raise PreheatRunError(f"job {job.get('job_id')!r} has no preheat_argv")
    if "--generate-ast" not in argv:
        raise PreheatRunError(f"job {job.get('job_id')!r} is not an AST preheat argv")
    if "--ast-cache-root" not in argv:
        raise PreheatRunError(f"job {job.get('job_id')!r} has no external --ast-cache-root")
    for name in ("job_id", "benchmark", "subject_id"):
        if not job.get(name):
            raise PreheatRunError(f"job is missing {name}")


def _selected_jobs(schedule: dict, *, shard: str = "", limit: int = 0):
    if schedule.get("schema") != "veriput-ast-preheat-schedule/v1":
        raise PreheatRunError(f"unsupported schema {schedule.get('schema')!r}; expected "
                              "veriput-ast-preheat-schedule/v1")
    jobs = list(schedule.get("jobs") or [])
    jobs.sort(key=lambda item: (item.get("priority", 999999), item.get("ordinal", 999999)))
    jobs = _apply_shard(jobs, _parse_shard(shard))
    if limit:
        jobs = jobs[:limit]
    for job in jobs:
        _validate_job(job)
    return jobs


def _status_from_stdout(stdout: str) -> tuple[str, dict | None]:
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError:
        return "bad-json", None
    if doc.get("schema") != "veriput-unit-manifest/v1":
        return "bad-schema", doc
    subjects = doc.get("subjects") or []
    if len(subjects) != 1:
        return "bad-row-count", doc
    return subjects[0].get("status") or "missing-status", doc


def _run_one(job: dict, timeout_s: float) -> dict:
    start = time.monotonic()
    argv = [str(arg) for arg in job["preheat_argv"]]
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
        row_status, doc = _status_from_stdout(cp.stdout)
        status = "ok" if cp.returncode == 0 and row_status == "ok" else \
            "error"
        reason = "" if status == "ok" else \
            f"rc={cp.returncode} row_status={row_status}"
        result_summary = doc.get("summary") if isinstance(doc, dict) else None
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        reason = f"timeout after {timeout_s}s"
        cp = exc
        result_summary = None
    except OSError as exc:
        status = "error"
        reason = f"could not start: {exc}"
        cp = None
        result_summary = None
    wall_s = round(time.monotonic() - start, 3)
    return {
        "schema": "veriput-ast-preheat-run-row/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job["job_id"],
        "benchmark": job.get("benchmark"),
        "subject_id": job.get("subject_id"),
        "status": status,
        "reason": reason,
        "wall_s": wall_s,
        "timeout_s": timeout_s,
        "returncode": getattr(cp, "returncode", None),
        "result_summary": result_summary,
        "stdout_tail": _tail(getattr(cp, "stdout", "") or ""),
        "stderr_tail": _tail(getattr(cp, "stderr", "") or ""),
    }


def _write_journal(path: str, row: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def dry_run_doc(schedule: dict, *, shard: str = "", limit: int = 0, journal: str = "") -> dict:
    jobs = _selected_jobs(schedule, shard=shard, limit=limit)
    done = _completed_from_journal(journal)
    pending = [job for job in jobs if job["job_id"] not in done]
    return {
        "schema":
        "veriput-ast-preheat-run-plan/v1",
        "generated_at":
        datetime.now(timezone.utc).isoformat(),
        "summary": {
            "selected": len(jobs),
            "already_done": len(jobs) - len(pending),
            "pending": len(pending),
        },
        "jobs": [{
            "job_id": job["job_id"],
            "benchmark": job.get("benchmark"),
            "subject_id": job.get("subject_id"),
            "preheat_argv": job["preheat_argv"],
        } for job in pending],
    }


def run_schedule(schedule: dict,
                 *,
                 journal: str,
                 shard: str = "",
                 limit: int = 0,
                 jobs: int = 1,
                 timeout_s: float = 90.0,
                 stop_on_failure: bool = False) -> dict:
    if not journal:
        raise PreheatRunError("pass --journal for real preheat execution")
    if jobs <= 0:
        raise PreheatRunError("--jobs must be positive")
    if stop_on_failure and jobs != 1:
        raise PreheatRunError("--stop-on-failure requires --jobs 1")
    selected = _selected_jobs(schedule, shard=shard, limit=limit)
    done = _completed_from_journal(journal)
    pending = [job for job in selected if job["job_id"] not in done]
    rows = []
    counts = Counter()

    if jobs <= 1:
        for job in pending:
            row = _run_one(job, timeout_s)
            _write_journal(journal, row)
            rows.append(row)
            counts[row["status"]] += 1
            if stop_on_failure and row["status"] != "ok":
                break
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {executor.submit(_run_one, job, timeout_s): job for job in pending}
            for future in as_completed(futures):
                row = future.result()
                _write_journal(journal, row)
                rows.append(row)
                counts[row["status"]] += 1
                if stop_on_failure and row["status"] != "ok":
                    break

    return {
        "schema": "veriput-ast-preheat-run-summary/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal": journal,
        "summary": {
            "selected": len(selected),
            "already_done": len(selected) - len(pending),
            "attempted": len(rows),
            "status": dict(sorted(counts.items())),
            "not_attempted": max(0,
                                 len(pending) - len(rows)),
        },
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="AST preheat schedule JSON path, or '-' for stdin")
    ap.add_argument("--journal", default="", help="JSONL run journal. Required without --dry-run")
    ap.add_argument("--dry-run",
                    action="store_true",
                    help="print selected pending jobs without executing them")
    ap.add_argument("--shard", default="", help="select job positions i/n after priority sorting")
    ap.add_argument("--limit", type=int, default=0, help="keep only the first N selected jobs")
    ap.add_argument("--jobs", type=int, default=1, help="number of concurrent preheat jobs")
    ap.add_argument("--timeout",
                    type=float,
                    default=90.0,
                    help="outer timeout for one preheat_argv process")
    ap.add_argument("--stop-on-failure",
                    action="store_true",
                    help="stop after the first non-ok row")
    args = ap.parse_args()
    try:
        schedule = _load_json(args.schedule)
        if args.dry_run:
            doc = dry_run_doc(schedule, shard=args.shard, limit=args.limit, journal=args.journal)
        else:
            doc = run_schedule(schedule,
                               journal=args.journal,
                               shard=args.shard,
                               limit=args.limit,
                               jobs=args.jobs,
                               timeout_s=args.timeout,
                               stop_on_failure=args.stop_on_failure)
    except (OSError, PreheatRunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
