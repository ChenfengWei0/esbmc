#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_schedule_run  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def fake_script(path, rc=0):
    p = Path(path)
    p.write_text("#!/usr/bin/env python3\n"
                 "import sys\n"
                 "print('unit command stdout')\n"
                 "print('unit command stderr', file=sys.stderr)\n"
                 f"raise SystemExit({rc})\n")
    p.chmod(0o755)
    return str(p)


def job(job_id, cmd, unit="f"):
    return {
        "schema": "veriput-unit-job/v1",
        "job_id": job_id,
        "priority": 0,
        "ordinal": 0,
        "benchmark": "peer182",
        "subject_id": "repo__C",
        "contract": "C",
        "unit": unit,
        "certify_argv": [
            cmd,
            "--subject-dir",
            "/tmp/repo__C",
            "--unit",
            unit,
        ],
    }


def schedule(ok_cmd, fail_cmd=None):
    jobs = [job("peer182__repo__C__f", ok_cmd)]
    if fail_cmd:
        jobs.append(job("peer182__repo__C__g", fail_cmd, unit="g"))
    return {
        "schema": "veriput-unit-schedule/v1",
        "summary": {
            "jobs": len(jobs),
        },
        "jobs": jobs,
    }


def test_runner_executes_and_resumes_from_journal():
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        journal = Path(td) / "run.jsonl"
        first = unit_schedule_run.run_schedule(schedule(ok), journal=str(journal), timeout_s=5)
        second = unit_schedule_run.run_schedule(schedule(ok), journal=str(journal), timeout_s=5)
        lines = journal.read_text().splitlines()
    bad = 0
    bad += check(first["summary"]["status"] == {"ok": 1},
                 f"completed command is ok: {first['summary']}")
    bad += check(second["summary"]["already_done"] == 1 and second["summary"]["attempted"] == 0,
                 f"resume skips ok unit job: {second['summary']}")
    bad += check(len(lines) == 1, f"resume does not append duplicate rows: {lines}")
    return bad


def test_runner_records_failures_as_retryable():
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        fail = fake_script(Path(td) / "fail.py", rc=7)
        journal = Path(td) / "run.jsonl"
        first = unit_schedule_run.run_schedule(schedule(ok, fail),
                                               journal=str(journal),
                                               timeout_s=5)
        second = unit_schedule_run.run_schedule(schedule(ok, fail),
                                                journal=str(journal),
                                                timeout_s=5)
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
    bad = 0
    bad += check(first["summary"]["status"] == {
        "error": 1,
        "ok": 1
    }, f"nonzero command is an error row: {first['summary']}")
    bad += check(second["summary"]["already_done"] == 1 and second["summary"]["attempted"] == 1,
                 f"failed unit job remains retryable: {second['summary']}")
    bad += check(
        sum(1 for row in rows if row["status"] == "error") == 2,
        f"failure is journaled on each attempt: {rows}")
    return bad


def test_runner_dry_run_start_failure_and_fail_closed_modes():
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        journal = Path(td) / "run.jsonl"
        sched = schedule(ok)
        unit_schedule_run.run_schedule(sched, journal=str(journal), timeout_s=5)
        dry = unit_schedule_run.dry_run_doc(sched, journal=str(journal))
        try:
            unit_schedule_run.run_schedule(sched, journal="", timeout_s=5)
        except unit_schedule_run.UnitRunError as exc:
            refused = str(exc)
        else:
            refused = ""
        start = unit_schedule_run.run_schedule(schedule(str(Path(td) / "missing")),
                                               journal=str(Path(td) / "start.jsonl"),
                                               timeout_s=5)
    bad = 0
    bad += check(dry["summary"]["already_done"] == 1 and dry["summary"]["pending"] == 0,
                 f"dry-run honors completed journal rows: {dry['summary']}")
    bad += check("pass --journal" in refused, f"real execution requires journal: {refused}")
    bad += check(start["summary"]["status"] == {"error": 1},
                 f"start failure is journaled as error: {start['summary']}")
    return bad


def test_runner_cli_dry_run_reads_schedule():
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        sched = Path(td) / "schedule.json"
        sched.write_text(json.dumps(schedule(ok)) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule_run.py"),
            str(sched),
            "--dry-run",
        ],
                            capture_output=True,
                            text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    bad = 0
    bad += check(doc["schema"] == "veriput-unit-run-plan/v1", f"CLI dry-run emits a plan: {doc}")
    bad += check(doc["summary"]["pending"] == 1,
                 f"CLI dry-run selects pending unit job: {doc['summary']}")
    return bad


TESTS = [
    test_runner_executes_and_resumes_from_journal,
    test_runner_records_failures_as_retryable,
    test_runner_dry_run_start_failure_and_fail_closed_modes,
    test_runner_cli_dry_run_reads_schedule,
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
