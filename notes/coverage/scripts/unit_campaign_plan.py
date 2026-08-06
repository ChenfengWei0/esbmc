#!/usr/bin/env python3
"""Plan VeriPUT unit certification attempts under the campaign budget.

This script is a read-only controller for the agreed three-attempt policy:
60s/8GiB, 120s/8GiB, then 600s/10GiB.  It consumes a base
`veriput-unit-schedule/v1` plus zero or more `unit_schedule_run.py` JSONL
journals, classifies every job by latest status and attempt count, and can emit
the next retry schedule without invoking solc, Forge, fuzzing, ESBMC, or
certification jobs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
UNIT_SCHEDULE_RUN = SCRIPT_DIR / "unit_schedule_run.py"

DEFAULT_POLICY = (
    {
        "attempt": 1,
        "timeout_s": 60.0,
        "memlimit_gb": 8.0,
    },
    {
        "attempt": 2,
        "timeout_s": 120.0,
        "memlimit_gb": 8.0,
    },
    {
        "attempt": 3,
        "timeout_s": 600.0,
        "memlimit_gb": 10.0,
    },
)


class CampaignError(ValueError):
    """The schedule, journals, or requested attempt cannot be planned."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CampaignError(f"{path} is not valid JSON: {exc}") from exc


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


def _row_attempt(row: dict, fallback_attempt: int, policy: dict[int, dict]) -> int:
    raw = row.get("campaign_attempt")
    try:
        attempt = int(raw)
    except (TypeError, ValueError):
        attempt = fallback_attempt
    if attempt not in policy:
        return fallback_attempt
    return attempt


def _load_schedule(path: str) -> dict:
    doc = _load_json(path)
    if doc.get("schema") != "veriput-unit-schedule/v1":
        raise CampaignError(f"unsupported schedule schema {doc.get('schema')!r}")
    return doc


def _policy_by_attempt() -> dict[int, dict]:
    return {item["attempt"]: dict(item) for item in DEFAULT_POLICY}


def _selected_attempt(pending_by_attempt: dict[int, list[dict]], requested: int) -> int | None:
    if requested:
        if requested not in _policy_by_attempt():
            raise CampaignError("--attempt must be 1, 2, or 3")
        return requested
    for attempt in sorted(_policy_by_attempt()):
        if pending_by_attempt.get(attempt):
            return attempt
    return None


def _runner_argv(schedule_path: str,
                 journal_path: str,
                 attempt_cfg: dict,
                 *,
                 jobs: int = 1,
                 stop_on_failure: bool = False) -> list[str]:
    argv = [
        sys.executable,
        str(UNIT_SCHEDULE_RUN),
        schedule_path,
        "--journal",
        journal_path,
        "--timeout",
        str(attempt_cfg["timeout_s"]),
        "--memlimit-gb",
        str(attempt_cfg["memlimit_gb"]),
        "--jobs",
        str(jobs),
    ]
    if stop_on_failure:
        argv.append("--stop-on-failure")
    return argv


def _schedule_for_attempt(base_schedule: dict, selected_jobs: list[dict], attempt_cfg: dict | None,
                          source_journals: list[str]) -> dict:
    by_benchmark = Counter(job.get("benchmark") for job in selected_jobs)
    by_priority = Counter(str(job.get("priority", "<missing>")) for job in selected_jobs)
    attempt = (attempt_cfg or {}).get("attempt")
    timeout_s = (attempt_cfg or {}).get("timeout_s")
    memlimit_gb = (attempt_cfg or {}).get("memlimit_gb")
    return {
        "schema": "veriput-unit-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": base_schedule.get("schema"),
            "schedule_generated_at": base_schedule.get("generated_at"),
            "schedule_source": base_schedule.get("source"),
            "schedule_summary": base_schedule.get("summary"),
            "campaign_policy": "veriput-unit-campaign-policy/v1",
            "campaign_attempt": attempt,
            "campaign_journals": source_journals,
        },
        "shard": base_schedule.get("shard"),
        "limit": base_schedule.get("limit"),
        "cert_out": base_schedule.get("cert_out"),
        "summary": {
            "jobs": len(selected_jobs),
            "jobs_before_campaign_filter": len(base_schedule.get("jobs") or []),
            "campaign_attempt": attempt,
            "timeout_s": timeout_s,
            "memlimit_gb": memlimit_gb,
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_priority": dict(sorted(by_priority.items())),
        },
        "skipped_rows": base_schedule.get("skipped_rows") or [],
        "duplicate_jobs": base_schedule.get("duplicate_jobs") or [],
        "jobs": selected_jobs,
    }


def plan_campaign(schedule_path: str,
                  *,
                  journal_paths: list[str] | None = None,
                  attempt: int = 0,
                  next_schedule_out: str = "",
                  next_journal: str = "",
                  jobs: int = 1,
                  stop_on_failure: bool = False) -> dict:
    schedule = _load_schedule(schedule_path)
    journals = journal_paths or []
    policy = _policy_by_attempt()
    jobs_by_id = {job.get("job_id"): job for job in schedule.get("jobs") or [] if job.get("job_id")}
    latest = {}
    attempts_by_job = defaultdict(set)
    status_attempts = Counter()
    bad_lines = 0
    orphan_rows = 0

    for fallback_attempt, journal in enumerate(journals, start=1):
        rows, bad = _read_journal(journal)
        bad_lines += bad
        for row in rows:
            job_id = row.get("job_id")
            if not job_id:
                continue
            if job_id not in jobs_by_id:
                orphan_rows += 1
                continue
            attempts_by_job[job_id].add(_row_attempt(row, fallback_attempt, policy))
            status_attempts[row.get("status") or "<missing-status>"] += 1
            latest[job_id] = row

    pending_by_attempt = defaultdict(list)
    completed = []
    exhausted = []
    latest_status = Counter()
    by_benchmark_state = defaultdict(Counter)
    by_priority_state = defaultdict(Counter)
    max_attempt = max(policy)

    for job_id, job in jobs_by_id.items():
        latest_row = latest.get(job_id)
        attempts = max(attempts_by_job[job_id], default=0)
        if latest_row and latest_row.get("status") == "ok":
            state = "completed-ok"
            completed.append(job)
            latest_status["ok"] += 1
        elif attempts >= max_attempt:
            state = "exhausted"
            exhausted.append(job)
            latest_status[(latest_row or {}).get("status") or "never"] += 1
        else:
            next_attempt = attempts + 1
            state = f"pending-attempt-{next_attempt}"
            pending_by_attempt[next_attempt].append(job)
            latest_status[(latest_row or {}).get("status") or "never"] += 1
        by_benchmark_state[job.get("benchmark") or "<unknown>"][state] += 1
        by_priority_state[str(job.get("priority", "<missing>"))][state] += 1

    selected = _selected_attempt(pending_by_attempt, attempt)
    selected_jobs = list(pending_by_attempt.get(selected, [])) if selected else []
    attempt_cfg = policy.get(selected) if selected else None
    next_schedule = _schedule_for_attempt(schedule, selected_jobs, attempt_cfg, journals)
    if next_schedule_out and next_schedule:
        out = Path(next_schedule_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(next_schedule, indent=2, sort_keys=True) + "\n")

    next_run = None
    if attempt_cfg:
        schedule_arg = next_schedule_out or "<next-schedule.json>"
        journal_arg = next_journal or f"<attempt-{attempt_cfg['attempt']}-journal.jsonl>"
        next_run = {
            "attempt":
            attempt_cfg["attempt"],
            "timeout_s":
            attempt_cfg["timeout_s"],
            "memlimit_gb":
            attempt_cfg["memlimit_gb"],
            "jobs":
            len(selected_jobs),
            "runner_argv":
            _runner_argv(schedule_arg,
                         journal_arg,
                         attempt_cfg,
                         jobs=jobs,
                         stop_on_failure=stop_on_failure),
        }

    return {
        "schema": "veriput-unit-campaign-plan/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule_path,
        "journals": journals,
        "policy": {
            "schema": "veriput-unit-campaign-policy/v1",
            "attempts": list(DEFAULT_POLICY),
        },
        "summary": {
            "jobs": len(jobs_by_id),
            "completed_ok": len(completed),
            "exhausted": len(exhausted),
            "bad_journal_lines": bad_lines,
            "orphan_journal_rows": orphan_rows,
            "status_attempts": dict(sorted(status_attempts.items())),
            "distinct_attempts_max": max((len(value) for value in attempts_by_job.values()),
                                         default=0),
            "latest_status": dict(sorted(latest_status.items())),
            "pending_by_attempt": {
                str(key): len(value)
                for key, value in sorted(pending_by_attempt.items())
            },
            "selected_attempt": selected,
            "selected_jobs": len(selected_jobs),
        },
        "by_benchmark_state": {
            bench: dict(sorted(counter.items()))
            for bench, counter in sorted(by_benchmark_state.items())
        },
        "by_priority_state": {
            priority: dict(sorted(counter.items()))
            for priority, counter in sorted(by_priority_state.items())
        },
        "next_run": next_run,
        "next_schedule": next_schedule,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="base veriput-unit-schedule/v1 JSON")
    ap.add_argument("--journal",
                    action="append",
                    default=[],
                    help="unit_schedule_run.py JSONL journal; repeat in attempt order")
    ap.add_argument("--attempt",
                    type=int,
                    default=0,
                    help="plan this exact attempt, or auto-select the earliest pending attempt")
    ap.add_argument("--next-schedule-out",
                    default="",
                    help="write the selected next-attempt schedule here")
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
        doc = plan_campaign(args.schedule,
                            journal_paths=args.journal,
                            attempt=args.attempt,
                            next_schedule_out=args.next_schedule_out,
                            next_journal=args.next_journal,
                            jobs=args.jobs,
                            stop_on_failure=args.stop_on_failure)
    except (OSError, CampaignError) as exc:
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
