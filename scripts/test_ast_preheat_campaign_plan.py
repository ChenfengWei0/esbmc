#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import ast_preheat_campaign_plan  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def job(job_id, benchmark="peer182", ordinal=0, solc_source="explicit"):
    return {
        "schema": "veriput-ast-preheat-job/v1",
        "job_id": job_id,
        "priority": ordinal,
        "ordinal": ordinal,
        "benchmark": benchmark,
        "subject_id": job_id.split("__", 1)[-1],
        "solc_source": solc_source,
        "preheat_argv": [
            "/bin/false",
            "--generate-ast",
            "--ast-cache-root",
            "/tmp/cache",
        ],
    }


def schedule_doc():
    return {
        "schema":
        "veriput-ast-preheat-schedule/v1",
        "generated_at":
        "2026-08-06T00:00:00+00:00",
        "ast_cache_root":
        "/tmp/cache",
        "ast_timeout_s":
        60.0,
        "summary": {
            "jobs": 5,
        },
        "jobs": [
            job("peer182__new", "peer182", 0),
            job("bugfix124__retry", "bugfix124", 1),
            job("stress243__done", "stress243", 2, "inferred"),
            job("stress243__exhausted", "stress243", 3, "inferred"),
            job("stress243__later", "stress243", 4, "explicit"),
        ],
    }


def row(job_id, status, benchmark="peer182", reason=""):
    return {
        "schema": "veriput-ast-preheat-run-row/v1",
        "job_id": job_id,
        "benchmark": benchmark,
        "subject_id": job_id,
        "status": status,
        "reason": reason,
    }


def write_json(path, doc):
    p = Path(path)
    p.write_text(json.dumps(doc) + "\n")
    return p


def write_journal(path, rows, bad_line=True):
    p = Path(path)
    text = "\n".join(json.dumps(item) for item in rows) + "\n"
    if bad_line:
        text += "not-json\n"
    p.write_text(text)
    return p


def test_preheat_campaign_partitions_and_batches_pending_jobs():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(Path(td) / "schedule.json", schedule_doc())
        j1 = write_journal(
            Path(td) / "run.jsonl", [
                row("bugfix124__retry", "error", "bugfix124", "row_status=missing-ast"),
                row("stress243__done", "ok", "stress243"),
                row("stress243__exhausted", "timeout", "stress243", "timeout after 90s"),
                row("stress243__exhausted", "error", "stress243", "rc=1"),
                row("orphan__x", "ok"),
            ])
        doc = ast_preheat_campaign_plan.plan_preheat(str(sched),
                                                     journal_paths=[str(j1)],
                                                     batch_size=2,
                                                     max_attempts=2,
                                                     timeout_s=75.0,
                                                     jobs=3)
    selected = [job["job_id"] for job in doc["next_schedule"]["jobs"]]
    bad = 0
    bad += check(doc["schema"] == "veriput-ast-preheat-campaign-plan/v1",
                 f"schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["completed_ok"] == 1
                 and doc["summary"]["exhausted"] == 1
                 and doc["summary"]["pending"] == 3,
                 f"states are counted: {doc['summary']}")
    bad += check(doc["summary"]["selected_jobs"] == 2,
                 f"batch size limits selected work: {doc['summary']}")
    bad += check(selected == ["peer182__new", "bugfix124__retry"],
                 f"selected jobs preserve priority order: {selected}")
    bad += check(doc["summary"]["bad_journal_lines"] == 1
                 and doc["summary"]["orphan_journal_rows"] == 1,
                 f"journal quality is reported: {doc['summary']}")
    bad += check(doc["next_run"]["runner_workers"] == 3
                 and "--timeout" in doc["next_run"]["runner_argv"]
                 and "75.0" in doc["next_run"]["runner_argv"],
                 f"runner argv carries budget: {doc['next_run']}")
    bad += check("--dry-run" in doc["next_run"]["dry_run_argv"]
                 and "--dry-run" not in doc["next_run"]["runner_argv"],
                 f"dry-run argv is explicit and separate: {doc['next_run']}")
    return bad


def test_preheat_campaign_writes_next_schedule_and_cli_plan():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(Path(td) / "schedule.json", schedule_doc())
        next_sched = Path(td) / "next.json"
        out = Path(td) / "plan.json"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "ast_preheat_campaign_plan.py"),
            str(sched),
            "--batch-size",
            "1",
            "--next-schedule-out",
            str(next_sched),
            "--next-journal",
            str(Path(td) / "run.jsonl"),
            "--out",
            str(out),
        ],
                            capture_output=True,
                            text=True)
        plan = json.loads(out.read_text()) if out.exists() else {}
        next_doc = json.loads(next_sched.read_text()) if next_sched.exists() else {}
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    bad = 0
    bad += check(plan["summary"]["selected_jobs"] == 1,
                 f"CLI writes plan: {plan['summary']}")
    bad += check(next_doc["summary"]["jobs"] == 1
                 and next_doc["jobs"][0]["job_id"] == "peer182__new",
                 f"CLI writes bounded next schedule: {next_doc['summary']}")
    bad += check(str(next_sched) in plan["next_run"]["runner_argv"],
                 f"runner argv points to next schedule: {plan['next_run']}")
    bad += check(str(next_sched) in plan["next_run"]["dry_run_argv"]
                 and "--dry-run" in plan["next_run"]["dry_run_argv"],
                 f"dry-run argv points to next schedule: {plan['next_run']}")
    return bad


def test_preheat_campaign_can_plan_from_in_memory_schedule():
    doc = ast_preheat_campaign_plan.plan_preheat_for_schedule(schedule_doc(),
                                                              "<preheat-schedule>",
                                                              batch_size=0,
                                                              timeout_s=90.0)
    bad = 0
    bad += check(doc["schedule"] == "<preheat-schedule>",
                 f"in-memory schedule label is preserved: {doc['schedule']}")
    bad += check(doc["summary"]["selected_jobs"] == 5,
                 f"batch-size 0 selects all pending jobs: {doc['summary']}")
    return bad


TESTS = [
    test_preheat_campaign_partitions_and_batches_pending_jobs,
    test_preheat_campaign_writes_next_schedule_and_cli_plan,
    test_preheat_campaign_can_plan_from_in_memory_schedule,
]


if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            failures += test()
        except Exception as exc:  # pragma: no cover - tiny script harness
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    raise SystemExit(1 if failures else 0)
