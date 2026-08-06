#!/usr/bin/env python3
"""Plan bounded VeriPUT AST preheat batches without running compilers.

This is a read-only controller for `ast_preheat_run.py`.  It consumes a base
`veriput-ast-preheat-schedule/v1` plus zero or more run journals, filters out
jobs whose latest journal row is `ok`, and emits the next bounded batch.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AST_PREHEAT_RUN = SCRIPT_DIR / "ast_preheat_run.py"
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_ATTEMPTS = 3


class PreheatCampaignError(ValueError):
    """The preheat schedule, journals, or requested batch cannot be planned."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreheatCampaignError(f"{path} is not valid JSON: {exc}") from exc


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
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines += 1
    return rows, bad_lines


def _load_schedule(path: str) -> dict:
    doc = _load_json(path)
    if doc.get("schema") != "veriput-ast-preheat-schedule/v1":
        raise PreheatCampaignError(f"unsupported schedule schema {doc.get('schema')!r}")
    return doc


def _runner_argv(schedule_arg: str,
                 journal_arg: str,
                 *,
                 timeout_s: float,
                 jobs: int,
                 stop_on_failure: bool,
                 dry_run: bool = False) -> list[str]:
    argv = [
        sys.executable,
        str(AST_PREHEAT_RUN),
        schedule_arg,
        "--journal",
        journal_arg,
        "--timeout",
        str(timeout_s),
        "--jobs",
        str(jobs),
    ]
    if stop_on_failure:
        argv.append("--stop-on-failure")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _schedule_for_batch(base_schedule: dict,
                        selected_jobs: list[dict],
                        *,
                        timeout_s: float,
                        batch_size: int,
                        max_attempts: int) -> dict:
    by_benchmark = Counter(job.get("benchmark") or "<unknown>" for job in selected_jobs)
    by_solc_source = Counter(job.get("solc_source") or "<unknown>" for job in selected_jobs)
    return {
        "schema": "veriput-ast-preheat-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": base_schedule.get("schema"),
            "schedule_generated_at": base_schedule.get("generated_at"),
            "schedule_summary": base_schedule.get("summary"),
            "campaign_filter": "latest journal row is not ok and attempts < max_attempts",
        },
        "ast_cache_root": base_schedule.get("ast_cache_root"),
        "ast_timeout_s": base_schedule.get("ast_timeout_s"),
        "summary": {
            "jobs": len(selected_jobs),
            "jobs_before_batch_filter": len(base_schedule.get("jobs") or []),
            "batch_size": batch_size,
            "max_attempts": max_attempts,
            "outer_timeout_s": timeout_s,
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_solc_source": dict(sorted(by_solc_source.items())),
        },
        "jobs": selected_jobs,
    }


def plan_preheat_for_schedule(schedule: dict,
                              schedule_label: str,
                              *,
                              journal_paths: list[str] | None = None,
                              batch_size: int = DEFAULT_BATCH_SIZE,
                              max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                              timeout_s: float = 90.0,
                              next_schedule_out: str = "",
                              next_journal: str = "",
                              jobs: int = 1,
                              stop_on_failure: bool = False) -> dict:
    if schedule.get("schema") != "veriput-ast-preheat-schedule/v1":
        raise PreheatCampaignError(f"unsupported schedule schema {schedule.get('schema')!r}")
    if batch_size < 0:
        raise PreheatCampaignError("--batch-size must be non-negative")
    if max_attempts <= 0:
        raise PreheatCampaignError("--max-attempts must be positive")
    if jobs <= 0:
        raise PreheatCampaignError("--jobs must be positive")
    if stop_on_failure and jobs != 1:
        raise PreheatCampaignError("--stop-on-failure requires --jobs 1")

    journals = journal_paths or []
    jobs_by_id = {
        job.get("job_id"): job
        for job in schedule.get("jobs") or []
        if job.get("job_id")
    }
    latest = {}
    attempts_by_job = Counter()
    status_attempts = Counter()
    bad_lines = 0
    orphan_rows = 0

    for journal in journals:
        rows, bad = _read_journal(journal)
        bad_lines += bad
        for row in rows:
            job_id = row.get("job_id")
            if not job_id:
                continue
            if job_id not in jobs_by_id:
                orphan_rows += 1
                continue
            attempts_by_job[job_id] += 1
            status_attempts[row.get("status") or "<missing-status>"] += 1
            latest[job_id] = row

    completed = []
    exhausted = []
    pending = []
    latest_status = Counter()
    by_benchmark_state = defaultdict(Counter)
    by_solc_source_state = defaultdict(Counter)

    ordered_jobs = list(jobs_by_id.values())
    ordered_jobs.sort(key=lambda item: (item.get("priority", 999999), item.get("ordinal", 999999)))
    for job in ordered_jobs:
        job_id = job["job_id"]
        row = latest.get(job_id)
        attempts = attempts_by_job[job_id]
        if row and row.get("status") == "ok":
            state = "completed-ok"
            completed.append(job)
            latest_status["ok"] += 1
        elif attempts >= max_attempts:
            state = "exhausted"
            exhausted.append(job)
            latest_status[(row or {}).get("status") or "never"] += 1
        else:
            state = "pending"
            pending.append(job)
            latest_status[(row or {}).get("status") or "never"] += 1
        by_benchmark_state[job.get("benchmark") or "<unknown>"][state] += 1
        by_solc_source_state[job.get("solc_source") or "<unknown>"][state] += 1

    selected_jobs = pending if batch_size == 0 else pending[:batch_size]
    next_schedule = _schedule_for_batch(schedule,
                                        selected_jobs,
                                        timeout_s=timeout_s,
                                        batch_size=batch_size,
                                        max_attempts=max_attempts)
    if next_schedule_out:
        out = Path(next_schedule_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(next_schedule, indent=2, sort_keys=True) + "\n")

    next_run = None
    if selected_jobs:
        schedule_arg = next_schedule_out or "<next-ast-preheat-schedule.json>"
        journal_arg = next_journal or "<ast-preheat-journal.jsonl>"
        next_run = {
            "timeout_s": timeout_s,
            "jobs": len(selected_jobs),
            "runner_workers": jobs,
            "dry_run_argv": _runner_argv(schedule_arg,
                                        journal_arg,
                                        timeout_s=timeout_s,
                                        jobs=jobs,
                                        stop_on_failure=stop_on_failure,
                                        dry_run=True),
            "runner_argv": _runner_argv(schedule_arg,
                                        journal_arg,
                                        timeout_s=timeout_s,
                                        jobs=jobs,
                                        stop_on_failure=stop_on_failure,
                                        dry_run=False),
        }

    return {
        "schema": "veriput-ast-preheat-campaign-plan/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule_label,
        "journals": journals,
        "policy": {
            "schema": "veriput-ast-preheat-campaign-policy/v1",
            "batch_size": batch_size,
            "max_attempts": max_attempts,
            "timeout_s": timeout_s,
        },
        "summary": {
            "jobs": len(jobs_by_id),
            "completed_ok": len(completed),
            "exhausted": len(exhausted),
            "pending": len(pending),
            "selected_jobs": len(selected_jobs),
            "bad_journal_lines": bad_lines,
            "orphan_journal_rows": orphan_rows,
            "status_attempts": dict(sorted(status_attempts.items())),
            "latest_status": dict(sorted(latest_status.items())),
            "attempts_by_job_max": max(attempts_by_job.values(), default=0),
        },
        "by_benchmark_state": {
            bench: dict(sorted(counter.items()))
            for bench, counter in sorted(by_benchmark_state.items())
        },
        "by_solc_source_state": {
            source: dict(sorted(counter.items()))
            for source, counter in sorted(by_solc_source_state.items())
        },
        "next_run": next_run,
        "next_schedule": next_schedule,
    }


def plan_preheat(schedule_path: str,
                 *,
                 journal_paths: list[str] | None = None,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 timeout_s: float = 90.0,
                 next_schedule_out: str = "",
                 next_journal: str = "",
                 jobs: int = 1,
                 stop_on_failure: bool = False) -> dict:
    schedule = _load_schedule(schedule_path)
    return plan_preheat_for_schedule(schedule,
                                     schedule_path,
                                     journal_paths=journal_paths,
                                     batch_size=batch_size,
                                     max_attempts=max_attempts,
                                     timeout_s=timeout_s,
                                     next_schedule_out=next_schedule_out,
                                     next_journal=next_journal,
                                     jobs=jobs,
                                     stop_on_failure=stop_on_failure)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="base veriput-ast-preheat-schedule/v1 JSON")
    ap.add_argument("--journal",
                    action="append",
                    default=[],
                    help="ast_preheat_run.py JSONL journal; repeatable")
    ap.add_argument("--batch-size",
                    type=int,
                    default=DEFAULT_BATCH_SIZE,
                    help="number of pending jobs to put in the next schedule; 0 means all")
    ap.add_argument("--max-attempts",
                    type=int,
                    default=DEFAULT_MAX_ATTEMPTS,
                    help="do not schedule a job after this many non-ok attempts")
    ap.add_argument("--timeout",
                    type=float,
                    default=90.0,
                    help="outer timeout to include in the suggested runner argv")
    ap.add_argument("--next-schedule-out",
                    default="",
                    help="write the selected next batch schedule here")
    ap.add_argument("--next-journal",
                    default="",
                    help="journal path to include in the suggested runner argv")
    ap.add_argument("--jobs",
                    type=int,
                    default=1,
                    help="worker count to include in the suggested runner argv")
    ap.add_argument("--stop-on-failure",
                    action="store_true",
                    help="include --stop-on-failure in the suggested runner argv")
    ap.add_argument("--out", default="", help="write JSON plan here instead of stdout")
    args = ap.parse_args()
    try:
        doc = plan_preheat(args.schedule,
                           journal_paths=args.journal,
                           batch_size=args.batch_size,
                           max_attempts=args.max_attempts,
                           timeout_s=args.timeout,
                           next_schedule_out=args.next_schedule_out,
                           next_journal=args.next_journal,
                           jobs=args.jobs,
                           stop_on_failure=args.stop_on_failure)
    except (OSError, PreheatCampaignError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
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
