#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_schedule_journal  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def job(job_id, benchmark="peer182", unit="f", priority=0):
    subject_id = job_id.split("__", 1)[-1].rsplit("__", 1)[0]
    return {
        "schema":
        "veriput-unit-job/v1",
        "job_id":
        job_id,
        "priority":
        priority,
        "ordinal":
        priority,
        "benchmark":
        benchmark,
        "subject_id":
        subject_id,
        "contract":
        "C",
        "unit":
        unit,
        "certify_argv": [
            sys.executable,
            "/tmp/certify_all.py",
            "--subject-dir",
            f"/tmp/{subject_id}",
            "--unit",
            unit,
        ],
    }


def schedule_doc():
    return {
        "schema":
        "veriput-unit-schedule/v1",
        "generated_at":
        "2026-08-06T00:00:00+00:00",
        "source": {
            "schema": "veriput-unit-manifest/v1",
        },
        "cert_out":
        "/tmp/cert.jsonl",
        "summary": {
            "jobs": 4,
        },
        "jobs": [
            job("peer182__ok__f"),
            job("bugfix124__fixed__g", "bugfix124", "g"),
            job("stress243__bad__h", "stress243", "h", priority=1),
            job("stress243__never__i", "stress243", "i", priority=1),
        ],
    }


def write_journal(path):
    rows = [
        {
            "schema": "veriput-unit-run-row/v1",
            "job_id": "peer182__ok__f",
            "benchmark": "peer182",
            "subject_id": "ok",
            "contract": "C",
            "unit": "f",
            "status": "ok",
            "reason": "",
            "returncode": 0,
        },
        {
            "schema": "veriput-unit-run-row/v1",
            "job_id": "bugfix124__fixed__g",
            "benchmark": "bugfix124",
            "subject_id": "fixed",
            "contract": "C",
            "unit": "g",
            "status": "error",
            "reason": "rc=9",
            "returncode": 9,
        },
        {
            "schema": "veriput-unit-run-row/v1",
            "job_id": "bugfix124__fixed__g",
            "benchmark": "bugfix124",
            "subject_id": "fixed",
            "contract": "C",
            "unit": "g",
            "status": "ok",
            "reason": "",
            "returncode": 0,
        },
        {
            "schema": "veriput-unit-run-row/v1",
            "job_id": "stress243__bad__h",
            "benchmark": "stress243",
            "subject_id": "bad",
            "contract": "C",
            "unit": "h",
            "status": "timeout",
            "reason": "timeout after 60.0s",
            "stdout_tail": "partial stdout",
            "stderr_tail": "partial stderr",
        },
    ]
    p = Path(path)
    p.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n")
    return p


def test_journal_summary_uses_latest_status_and_retry_schedule():
    with tempfile.TemporaryDirectory() as td:
        journal = write_journal(Path(td) / "run.jsonl")
        sched = Path(td) / "schedule.json"
        sched.write_text(json.dumps(schedule_doc()) + "\n")
        doc = unit_schedule_journal.summarize(str(journal), schedule_path=str(sched))
    retry_ids = [job["job_id"] for job in doc["retry_schedule"]["jobs"]]
    bad = 0
    bad += check(doc["schema"] == "veriput-unit-journal-summary/v1",
                 f"summary schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["attempt_rows"] == 4 and doc["summary"]["bad_lines"] == 1,
                 f"journal rows and bad lines are counted: {doc['summary']}")
    bad += check(doc["summary"]["status_attempts"] == {
        "error": 1,
        "ok": 2,
        "timeout": 1,
    }, f"attempt status counts retain history: {doc['summary']}")
    bad += check(doc["summary"]["status_latest"] == {
        "ok": 2,
        "timeout": 1,
    }, f"latest status wins for resume: {doc['summary']}")
    bad += check(doc["summary"]["reason_latest"] == {
        "ok": 2,
        "timeout": 1,
    }, f"latest reason buckets are counted: {doc['summary']}")
    bad += check(doc["summary"]["never_attempted"] == 1,
                 f"schedule-only job is detected: {doc['summary']}")
    bad += check(retry_ids == ["stress243__bad__h", "stress243__never__i"],
                 f"retry schedule keeps latest-non-ok and never-attempted: {retry_ids}")
    bad += check(doc["by_priority_latest"] == {
        "0": {
            "ok": 2,
        },
        "1": {
            "timeout": 1,
        },
    }, f"latest results are grouped by priority: {doc['by_priority_latest']}")
    return bad


def test_journal_cli_writes_retry_schedule():
    with tempfile.TemporaryDirectory() as td:
        journal = write_journal(Path(td) / "run.jsonl")
        sched = Path(td) / "schedule.json"
        sched.write_text(json.dumps(schedule_doc()) + "\n")
        retry = Path(td) / "retry.json"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule_journal.py"),
            str(journal),
            "--schedule",
            str(sched),
            "--retry-out",
            str(retry),
        ],
                            capture_output=True,
                            text=True)
        retry_doc = json.loads(retry.read_text()) if retry.exists() else {}
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    bad = 0
    bad += check(doc["summary"]["retry_jobs"] == 2,
                 f"CLI summary reports retry jobs: {doc['summary']}")
    bad += check(retry_doc["summary"]["jobs"] == 2,
                 f"CLI writes retry schedule: {retry_doc['summary']}")
    bad += check(retry_doc["cert_out"] == "/tmp/cert.jsonl",
                 f"retry schedule preserves cert_out: {retry_doc.get('cert_out')}")
    return bad


TESTS = [
    test_journal_summary_uses_latest_status_and_retry_schedule,
    test_journal_cli_writes_retry_schedule,
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
