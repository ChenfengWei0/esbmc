#!/usr/bin/env python3
"""Summarize VeriPUT AST preheat journals and emit retry schedules.

The journal is produced by `ast_preheat_run.py`.  This script is read-only
unless `--out` or `--retry-out` is passed; it never invokes solc, Forge,
fuzzing, ESBMC, or preheat jobs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


class JournalError(ValueError):
    """The journal or schedule cannot be summarized."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JournalError(f"{path} is not valid JSON: {exc}") from exc


def _read_journal(path: str) -> tuple[list[dict], int]:
    p = Path(path)
    rows = []
    bad_lines = 0
    if not p.exists():
        return rows, bad_lines
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        rows.append(row)
    return rows, bad_lines


def _reason_bucket(row: dict) -> str:
    status = row.get("status") or "<missing-status>"
    reason = row.get("reason") or ""
    if status == "ok":
        return "ok"
    if status == "timeout" or reason.startswith("timeout"):
        return "timeout"
    if "could not start" in reason:
        return "start-failure"
    m = re.search(r"row_status=([A-Za-z0-9_.-]+)", reason)
    if m:
        return f"row-status:{m.group(1)}"
    m = re.search(r"rc=([0-9-]+)", reason)
    if m:
        return f"rc:{m.group(1)}"
    if reason:
        return reason.split(":", 1)[0][:80]
    return status


def _load_schedule(path: str) -> dict | None:
    if not path:
        return None
    doc = _load_json(path)
    if doc.get("schema") != "veriput-ast-preheat-schedule/v1":
        raise JournalError(f"unsupported schedule schema {doc.get('schema')!r}")
    return doc


def _retry_schedule(schedule: dict, latest: dict[str, dict]) -> dict:
    retry_jobs = []
    completed = 0
    for job in schedule.get("jobs") or []:
        job_id = job.get("job_id")
        latest_row = latest.get(job_id)
        if latest_row and latest_row.get("status") == "ok":
            completed += 1
            continue
        retry_jobs.append(job)
    return {
        "schema": "veriput-ast-preheat-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schedule_generated_at": schedule.get("generated_at"),
            "schedule_summary": schedule.get("summary"),
            "retry_reason": "latest journal row is not ok or job was never attempted",
        },
        "ast_cache_root": schedule.get("ast_cache_root"),
        "ast_timeout_s": schedule.get("ast_timeout_s"),
        "summary": {
            "jobs": len(retry_jobs),
            "jobs_before_retry_filter": len(schedule.get("jobs") or []),
            "completed_ok": completed,
        },
        "jobs": retry_jobs,
    }


def summarize(journal: str, *, schedule_path: str = "", sample_limit: int = 10) -> dict:
    rows, bad_lines = _read_journal(journal)
    schedule = _load_schedule(schedule_path)
    latest = {}
    attempts_by_job = Counter()
    status_attempts = Counter()
    status_latest = Counter()
    reason_latest = Counter()
    by_benchmark_latest = defaultdict(Counter)
    samples = defaultdict(list)

    for row in rows:
        job_id = row.get("job_id")
        if not job_id:
            continue
        attempts_by_job[job_id] += 1
        status_attempts[row.get("status") or "<missing-status>"] += 1
        latest[job_id] = row

    for row in latest.values():
        status = row.get("status") or "<missing-status>"
        bench = row.get("benchmark") or "<unknown>"
        bucket = _reason_bucket(row)
        status_latest[status] += 1
        reason_latest[bucket] += 1
        by_benchmark_latest[bench][status] += 1
        if status != "ok" and len(samples[bucket]) < sample_limit:
            samples[bucket].append({
                "job_id": row.get("job_id"),
                "benchmark": bench,
                "subject_id": row.get("subject_id"),
                "status": status,
                "reason": row.get("reason"),
                "stderr_tail": (row.get("stderr_tail") or "")[-800:],
            })

    retry = _retry_schedule(schedule, latest) if schedule else None
    never_attempted = 0
    if schedule:
        schedule_ids = {job.get("job_id") for job in schedule.get("jobs") or []}
        never_attempted = len([job_id for job_id in schedule_ids if job_id not in latest])

    return {
        "schema": "veriput-ast-preheat-journal-summary/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal": journal,
        "schedule": schedule_path or None,
        "summary": {
            "attempt_rows": len(rows),
            "bad_lines": bad_lines,
            "jobs_seen": len(latest),
            "status_attempts": dict(sorted(status_attempts.items())),
            "status_latest": dict(sorted(status_latest.items())),
            "reason_latest": dict(sorted(reason_latest.items())),
            "attempts_by_job_max": max(attempts_by_job.values(), default=0),
            "never_attempted": never_attempted,
            "retry_jobs": (retry or {}).get("summary", {}).get("jobs", 0),
        },
        "by_benchmark_latest": {
            bench: dict(sorted(counter.items()))
            for bench, counter in sorted(by_benchmark_latest.items())
        },
        "samples": dict(samples),
        "retry_schedule": retry,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("journal", help="ast_preheat_run.py JSONL journal")
    ap.add_argument("--schedule", default="", help="optional original AST preheat schedule JSON")
    ap.add_argument("--sample-limit",
                    type=int,
                    default=10,
                    help="maximum sample rows per latest failure bucket")
    ap.add_argument("--retry-out",
                    default="",
                    help="write retry schedule here when --schedule is passed")
    ap.add_argument("--out", default="", help="write JSON summary here instead of stdout")
    args = ap.parse_args()
    try:
        doc = summarize(args.journal, schedule_path=args.schedule, sample_limit=args.sample_limit)
    except (OSError, JournalError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    if args.retry_out:
        retry = doc.get("retry_schedule")
        if not retry:
            print("REFUSED: --retry-out requires --schedule", file=sys.stderr)
            return 1
        out = Path(args.retry_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(retry, indent=2, sort_keys=True) + "\n")

    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
