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
import copy
import json
import shlex
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
UNIT_SCHEDULE_RUN = SCRIPT_DIR / "unit_schedule_run.py"
sys.path.insert(0, str(SCRIPT_DIR))
import unit_schedule  # noqa: E402

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
CERTIFY_TIMEOUT_GRACE_S = 10.0
RUNNER_TIMEOUT_GRACE_S = 5.0


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


def _read_jsonl(path: str) -> tuple[list[dict], int]:
    return _read_journal(path)


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


def _cert_subject(row: dict) -> str:
    return row.get("benchmark") or row.get("poc") or "<unknown>"


def _cert_quality_by_unit(paths: list[str], min_certified_path_rate: float) -> tuple[dict, int]:
    latest = {}
    bad_lines = 0
    for path in paths:
        rows, bad = _read_jsonl(path)
        bad_lines += bad
        for row in rows:
            key = (_cert_subject(row), row.get("unit") or "<none>", row.get("path_function"))
            latest[key] = row

    by_unit = defaultdict(list)
    for (subject, unit, _path_function), row in latest.items():
        by_unit[(subject, unit)].append(row)

    quality = {}
    for key, rows in by_unit.items():
        witnessed = 0
        certified = 0
        not_certified = 0
        regions = 0
        buckets = Counter()
        for row in rows:
            buckets[row.get("bucket") or "<missing-bucket>"] += 1
            c = row.get("certified") or {}
            n = row.get("not_certified") or {}
            c_count = len(c) if isinstance(c, dict) else 0
            n_count = len(n) if isinstance(n, dict) else 0
            regions += c_count
            if isinstance(row.get("witnessed"), int):
                witnessed += max(0, row["witnessed"])
                certified += c_count
                not_certified += n_count
        rate = (certified / witnessed) if witnessed else (1.0 if regions else 0.0)
        strong = regions > 0 and rate >= min_certified_path_rate
        reason = ""
        if not regions:
            reason = "no certified regions"
        elif rate < min_certified_path_rate:
            reason = "certified path rate below threshold"
        quality[key] = {
            "strong": strong,
            "reason": reason,
            "rows": len(rows),
            "witnessed_paths": witnessed,
            "certified_paths": certified,
            "not_certified_paths": not_certified,
            "certified_regions": regions,
            "certified_path_rate": rate,
            "bucket_rows": dict(sorted(buckets.items())),
        }
    return quality, bad_lines


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
                 stop_on_failure: bool = False,
                 dry_run: bool = False) -> list[str]:
    runner_timeout_s = (
        float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S +
        RUNNER_TIMEOUT_GRACE_S)
    argv = [
        sys.executable,
        str(UNIT_SCHEDULE_RUN),
        schedule_path,
        "--journal",
        journal_path,
        "--timeout",
        str(runner_timeout_s),
        "--memlimit-gb",
        str(attempt_cfg["memlimit_gb"]),
        "--jobs",
        str(jobs),
    ]
    if stop_on_failure:
        argv.append("--stop-on-failure")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _cmd(argv: list[str]) -> str:
    return shlex.join(str(arg) for arg in argv)


def _argv_value(argv: list[str], flag: str) -> str:
    try:
        idx = argv.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(argv):
        return ""
    return argv[idx + 1]


def _attempt_budgeted_jobs(jobs: list[dict], attempt_cfg: dict | None) -> list[dict]:
    if not attempt_cfg:
        return [copy.deepcopy(job) for job in jobs]
    attempt = int(attempt_cfg["attempt"])
    run_timeout_s = int(attempt_cfg["timeout_s"])
    certify_timeout_s = int(run_timeout_s + CERTIFY_TIMEOUT_GRACE_S)
    memlimit_gib = int(attempt_cfg["memlimit_gb"])
    budgeted = []
    for job in jobs:
        item = copy.deepcopy(job)
        out_path = _argv_value([str(arg) for arg in item.get("certify_argv") or []], "--out")
        workdir = unit_schedule.default_workdir_root(out_path,
                                                     timeout_s=certify_timeout_s,
                                                     run_timeout_s=run_timeout_s,
                                                     memlimit_gib=memlimit_gib,
                                                     attempt=attempt)
        item["certify_argv"] = unit_schedule.budgeted_certify_argv(
            [str(arg) for arg in item.get("certify_argv") or []],
            timeout_s=certify_timeout_s,
            run_timeout_s=run_timeout_s,
            memlimit_gib=memlimit_gib,
            workdir=workdir)
        if "dry_run_argv" in item:
            dry = unit_schedule.budgeted_certify_argv(
                [str(arg) for arg in item.get("dry_run_argv") or []],
                timeout_s=certify_timeout_s,
                run_timeout_s=run_timeout_s,
                memlimit_gib=memlimit_gib,
                workdir=workdir)
            if "--dry-run" not in dry:
                dry.append("--dry-run")
            item["dry_run_argv"] = dry
        item["certification_budget"] = {
            "timeout_s": certify_timeout_s,
            "run_timeout_s": run_timeout_s,
            "memlimit_gib": memlimit_gib,
            "workdir": workdir,
        }
        budgeted.append(item)
    return budgeted


def _schedule_for_attempt(base_schedule: dict, selected_jobs: list[dict], attempt_cfg: dict | None,
                          source_journals: list[str]) -> dict:
    attempt = (attempt_cfg or {}).get("attempt")
    run_timeout_s = (attempt_cfg or {}).get("timeout_s")
    certify_timeout_s = (
        int(run_timeout_s + CERTIFY_TIMEOUT_GRACE_S) if run_timeout_s else None)
    memlimit_gb = (attempt_cfg or {}).get("memlimit_gb")
    budgeted_jobs = _attempt_budgeted_jobs(selected_jobs, attempt_cfg)
    by_benchmark = Counter(job.get("benchmark") for job in budgeted_jobs)
    by_priority = Counter(str(job.get("priority", "<missing>")) for job in budgeted_jobs)
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
        "workdir": (budgeted_jobs[0].get("certification_budget") or {}).get("workdir")
        if budgeted_jobs else None,
        "summary": {
            "jobs": len(budgeted_jobs),
            "jobs_before_campaign_filter": len(base_schedule.get("jobs") or []),
            "campaign_attempt": attempt,
            "timeout_s": certify_timeout_s,
            "run_timeout_s": run_timeout_s,
            "memlimit_gb": memlimit_gb,
            "certify_timeout_s": certify_timeout_s,
            "certify_run_timeout_s": int(run_timeout_s) if run_timeout_s else None,
            "certify_memlimit_gib": int(memlimit_gb) if memlimit_gb else None,
            "certify_workdir": (budgeted_jobs[0].get("certification_budget")
                                or {}).get("workdir") if budgeted_jobs else None,
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_priority": dict(sorted(by_priority.items())),
        },
        "skipped_rows": base_schedule.get("skipped_rows") or [],
        "duplicate_jobs": base_schedule.get("duplicate_jobs") or [],
        "jobs": budgeted_jobs,
    }


def plan_campaign_for_schedule(schedule: dict,
                               schedule_label: str,
                               *,
                               journal_paths: list[str] | None = None,
                               cert_jsonl_paths: list[str] | None = None,
                               min_certified_path_rate: float = 0.70,
                               attempt: int = 0,
                               next_schedule_out: str = "",
                               next_journal: str = "",
                               jobs: int = 1,
                               stop_on_failure: bool = False) -> dict:
    if schedule.get("schema") != "veriput-unit-schedule/v1":
        raise CampaignError(f"unsupported schedule schema {schedule.get('schema')!r}")
    journals = journal_paths or []
    cert_jsonls = cert_jsonl_paths or []
    cert_quality, bad_cert_lines = _cert_quality_by_unit(cert_jsonls, min_certified_path_rate)
    policy = _policy_by_attempt()
    jobs_by_id = {job.get("job_id"): job for job in schedule.get("jobs") or [] if job.get("job_id")}
    latest = {}
    attempts_by_job = defaultdict(set)
    status_attempts = Counter()
    bad_lines = 0
    orphan_rows = 0
    cert_weak = Counter()

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
        cert_key = (job.get("benchmark") or job.get("poc") or "<unknown>", job.get("unit")
                    or "<none>")
        quality = cert_quality.get(cert_key)
        cert_strong = (not cert_jsonls) or (quality and quality.get("strong"))
        has_completion_source = ((latest_row and latest_row.get("status") == "ok")
                                 or (cert_jsonls and not latest_row and cert_strong))
        if has_completion_source and cert_strong:
            state = "completed-ok" if latest_row else "completed-certified"
            completed.append(job)
            latest_status["ok" if latest_row else "certified-without-runner-journal"] += 1
        elif attempts >= max_attempt:
            state = "exhausted"
            exhausted.append(job)
            latest_status[(latest_row or {}).get("status") or "never"] += 1
        else:
            next_attempt = attempts + 1
            state = f"pending-attempt-{next_attempt}"
            if latest_row and latest_row.get("status") == "ok" and cert_jsonls:
                reason = (quality or {}).get("reason") or "no certification row"
                cert_weak[reason] += 1
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
        dry_run_argv = _runner_argv(schedule_arg,
                                    journal_arg,
                                    attempt_cfg,
                                    jobs=jobs,
                                    stop_on_failure=stop_on_failure,
                                    dry_run=True)
        runner_argv = _runner_argv(schedule_arg,
                                   journal_arg,
                                   attempt_cfg,
                                   jobs=jobs,
                                   stop_on_failure=stop_on_failure,
                                   dry_run=False)
        next_run = {
            "attempt":
            attempt_cfg["attempt"],
            "timeout_s":
            attempt_cfg["timeout_s"],
            "certify_timeout_s":
            float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S,
            "runner_timeout_s":
            (float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S +
             RUNNER_TIMEOUT_GRACE_S),
            "memlimit_gb":
            attempt_cfg["memlimit_gb"],
            "jobs":
            len(selected_jobs),
            "dry_run_argv":
            dry_run_argv,
            "dry_run_cmd":
            _cmd(dry_run_argv),
            "runner_argv":
            runner_argv,
            "runner_cmd":
            _cmd(runner_argv),
        }

    return {
        "schema": "veriput-unit-campaign-plan/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule_label,
        "journals": journals,
        "cert_jsonls": cert_jsonls,
        "policy": {
            "schema": "veriput-unit-campaign-policy/v1",
            "attempts": list(DEFAULT_POLICY),
        },
        "summary": {
            "jobs": len(jobs_by_id),
            "completed_ok": len(completed),
            "exhausted": len(exhausted),
            "bad_journal_lines": bad_lines,
            "bad_cert_jsonl_lines": bad_cert_lines,
            "orphan_journal_rows": orphan_rows,
            "cert_quality_enabled": bool(cert_jsonls),
            "cert_weak": dict(sorted(cert_weak.items())),
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


def plan_campaign(schedule_path: str,
                  *,
                  journal_paths: list[str] | None = None,
                  cert_jsonl_paths: list[str] | None = None,
                  min_certified_path_rate: float = 0.70,
                  attempt: int = 0,
                  next_schedule_out: str = "",
                  next_journal: str = "",
                  jobs: int = 1,
                  stop_on_failure: bool = False) -> dict:
    schedule = _load_schedule(schedule_path)
    return plan_campaign_for_schedule(schedule,
                                      schedule_path,
                                      journal_paths=journal_paths,
                                      cert_jsonl_paths=cert_jsonl_paths,
                                      min_certified_path_rate=min_certified_path_rate,
                                      attempt=attempt,
                                      next_schedule_out=next_schedule_out,
                                      next_journal=next_journal,
                                      jobs=jobs,
                                      stop_on_failure=stop_on_failure)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="base veriput-unit-schedule/v1 JSON")
    ap.add_argument("--journal",
                    action="append",
                    default=[],
                    help="unit_schedule_run.py JSONL journal; repeat in attempt order")
    ap.add_argument("--cert-jsonl",
                    action="append",
                    default=[],
                    help="certify_all.py --out JSONL; when present, runner-ok jobs "
                    "must also meet the certification quality threshold")
    ap.add_argument("--min-certified-path-rate",
                    type=float,
                    default=0.70,
                    help="quality threshold used with --cert-jsonl")
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
                            cert_jsonl_paths=args.cert_jsonl,
                            min_certified_path_rate=args.min_certified_path_rate,
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
