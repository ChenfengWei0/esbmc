#!/usr/bin/env python3
"""Run VeriPUT unit certification schedules with journaled resume.

The input must be a `veriput-unit-schedule/v1` document produced by
`unit_schedule.py`.  This runner executes each job's `certify_argv`, records one
JSONL row per attempt, and resumes only rows whose prior journaled status is
`ok`.  A runner `ok` means the certification command completed successfully; the
actual certified/not-certified PUT verdict remains in `certify_all.py`'s output.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from veriput_path_guard import argv_value, ensure_path_not_protected  # noqa: E402


class UnitRunError(ValueError):
    """The schedule or requested run mode is unsafe."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitRunError(f"{path} is not valid JSON: {exc}") from exc


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (AttributeError, ValueError):
        raise UnitRunError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise UnitRunError("--shard needs 0 <= i < n")
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
    argv = job.get("certify_argv")
    if not isinstance(argv, list) or not argv:
        raise UnitRunError(f"job {job.get('job_id')!r} has no certify_argv")
    if "--dry-run" in argv:
        raise UnitRunError(f"job {job.get('job_id')!r} certify_argv is dry-run")
    try:
        ensure_path_not_protected("--out", argv_value(argv, "--out"))
    except ValueError as exc:
        raise UnitRunError(f"job {job.get('job_id')!r}: {exc}") from exc
    for flag in ("--subject-dir", "--unit"):
        if flag not in argv:
            raise UnitRunError(f"job {job.get('job_id')!r} missing {flag}")
    for name in ("job_id", "benchmark", "subject_id", "unit"):
        if not job.get(name):
            raise UnitRunError(f"job is missing {name}")


def _selected_jobs(schedule: dict, *, shard: str = "", limit: int = 0):
    if schedule.get("schema") != "veriput-unit-schedule/v1":
        raise UnitRunError(f"unsupported schema {schedule.get('schema')!r}; expected "
                           "veriput-unit-schedule/v1")
    jobs = list(schedule.get("jobs") or [])
    jobs.sort(key=lambda item: (item.get("priority", 999999), item.get("ordinal", 999999)))
    jobs = _apply_shard(jobs, _parse_shard(shard))
    if limit:
        jobs = jobs[:limit]
    for job in jobs:
        _validate_job(job)
    return jobs


def _preexec_memlimit(memlimit_gb: float):
    if not memlimit_gb:
        return None

    def set_limits():
        import resource
        limit = int(memlimit_gb * 1024 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return set_limits


def _campaign_meta(schedule: dict) -> dict:
    source = schedule.get("source") or {}
    summary = schedule.get("summary") or {}
    attempt = summary.get("campaign_attempt", source.get("campaign_attempt"))
    return {
        "campaign_policy": source.get("campaign_policy"),
        "campaign_attempt": attempt,
    }


def _run_one(job: dict, timeout_s: float, memlimit_gb: float, campaign_meta: dict) -> dict:
    start = time.monotonic()
    argv = [str(arg) for arg in job["certify_argv"]]
    try:
        cp = subprocess.run(argv,
                            capture_output=True,
                            text=True,
                            timeout=timeout_s,
                            preexec_fn=_preexec_memlimit(memlimit_gb))
        status = "ok" if cp.returncode == 0 else "error"
        reason = "" if status == "ok" else f"rc={cp.returncode}"
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        reason = f"timeout after {timeout_s}s"
        cp = exc
    except OSError as exc:
        status = "error"
        reason = f"could not start: {exc}"
        cp = None
    wall_s = round(time.monotonic() - start, 3)
    return {
        "schema": "veriput-unit-run-row/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job["job_id"],
        "benchmark": job.get("benchmark"),
        "subject_id": job.get("subject_id"),
        "contract": job.get("contract"),
        "unit": job.get("unit"),
        "campaign_policy": campaign_meta.get("campaign_policy"),
        "campaign_attempt": campaign_meta.get("campaign_attempt"),
        "status": status,
        "reason": reason,
        "wall_s": wall_s,
        "timeout_s": timeout_s,
        "memlimit_gb": memlimit_gb or None,
        "returncode": getattr(cp, "returncode", None),
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
    campaign_meta = _campaign_meta(schedule)
    return {
        "schema":
        "veriput-unit-run-plan/v1",
        "generated_at":
        datetime.now(timezone.utc).isoformat(),
        "summary": {
            "selected": len(jobs),
            "already_done": len(jobs) - len(pending),
            "pending": len(pending),
            "campaign_policy": campaign_meta.get("campaign_policy"),
            "campaign_attempt": campaign_meta.get("campaign_attempt"),
        },
        "jobs": [{
            "job_id": job["job_id"],
            "benchmark": job.get("benchmark"),
            "subject_id": job.get("subject_id"),
            "unit": job.get("unit"),
            "certify_argv": job["certify_argv"],
        } for job in pending],
    }


def run_schedule(schedule: dict,
                 *,
                 journal: str,
                 shard: str = "",
                 limit: int = 0,
                 jobs: int = 1,
                 timeout_s: float = 600.0,
                 memlimit_gb: float = 0.0,
                 stop_on_failure: bool = False) -> dict:
    if not journal:
        raise UnitRunError("pass --journal for real unit execution")
    try:
        ensure_path_not_protected("--journal", journal)
    except ValueError as exc:
        raise UnitRunError(str(exc)) from exc
    if jobs <= 0:
        raise UnitRunError("--jobs must be positive")
    if memlimit_gb < 0:
        raise UnitRunError("--memlimit-gb must be non-negative")
    if stop_on_failure and jobs != 1:
        raise UnitRunError("--stop-on-failure requires --jobs 1")
    selected = _selected_jobs(schedule, shard=shard, limit=limit)
    done = _completed_from_journal(journal)
    pending = [job for job in selected if job["job_id"] not in done]
    campaign_meta = _campaign_meta(schedule)
    rows = []
    counts = Counter()

    if jobs <= 1:
        for job in pending:
            row = _run_one(job, timeout_s, memlimit_gb, campaign_meta)
            _write_journal(journal, row)
            rows.append(row)
            counts[row["status"]] += 1
            if stop_on_failure and row["status"] != "ok":
                break
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(_run_one, job, timeout_s, memlimit_gb, campaign_meta): job
                for job in pending
            }
            for future in as_completed(futures):
                row = future.result()
                _write_journal(journal, row)
                rows.append(row)
                counts[row["status"]] += 1

    return {
        "schema": "veriput-unit-run-summary/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal": journal,
        "summary": {
            "selected": len(selected),
            "already_done": len(selected) - len(pending),
            "attempted": len(rows),
            "status": dict(sorted(counts.items())),
            "not_attempted": max(0,
                                 len(pending) - len(rows)),
            "campaign_policy": campaign_meta.get("campaign_policy"),
            "campaign_attempt": campaign_meta.get("campaign_attempt"),
        },
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="unit schedule JSON path, or '-' for stdin")
    ap.add_argument("--journal", default="", help="JSONL run journal. Required without --dry-run")
    ap.add_argument("--dry-run",
                    action="store_true",
                    help="print selected pending jobs without executing them")
    ap.add_argument("--shard", default="", help="select job positions i/n after priority sorting")
    ap.add_argument("--limit", type=int, default=0, help="keep only the first N selected jobs")
    ap.add_argument("--jobs", type=int, default=1, help="number of concurrent unit jobs")
    ap.add_argument("--timeout",
                    type=float,
                    default=600.0,
                    help="outer timeout for one certify_argv process")
    ap.add_argument("--memlimit-gb",
                    type=float,
                    default=0.0,
                    help="optional RLIMIT_AS memory cap inherited by children")
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
                               memlimit_gb=args.memlimit_gb,
                               stop_on_failure=args.stop_on_failure)
    except (OSError, UnitRunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
