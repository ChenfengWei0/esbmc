#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import ast_preheat_run  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def fake_preheat_script(path, *, status="ok", rc=0):
    body = {
        "schema":
        "veriput-unit-manifest/v1",
        "summary": {
            "subjects": 1,
            "ok": 1 if status == "ok" else 0,
            "missing_ast": 1 if status == "missing-ast" else 0,
            "error": 1 if status == "error" else 0,
            "units": 1 if status == "ok" else 0,
        },
        "subjects": [{
            "subject": {
                "schema": "veriput-subject/v1",
                "benchmark": "peer182",
                "subject_id": "repo__C",
                "contract": "C",
                "root": "/tmp/repo__C",
                "flat_sol": "/tmp/repo__C/flat.sol",
                "solast": "/tmp/cache/peer182/peer182__repo__C/flat.sol.solast",
                "unit": "",
            },
            "status": status,
            "units": {
                "schema": "veriput-subject-units/v1",
                "contract": "C",
                "units": ["f"],
                "skipped": [],
            } if status == "ok" else None,
        }],
    }
    p = Path(path)
    p.write_text("#!/usr/bin/env python3\n"
                 "import json, sys\n"
                 f"print({json.dumps(json.dumps(body))})\n"
                 f"raise SystemExit({rc})\n")
    p.chmod(0o755)
    return str(p)


def schedule(ok_cmd, fail_cmd=None):
    jobs = [{
        "schema": "veriput-ast-preheat-job/v1",
        "job_id": "peer182__repo__C",
        "priority": 0,
        "ordinal": 0,
        "benchmark": "peer182",
        "subject_id": "repo__C",
        "preheat_argv": [
            ok_cmd,
            "--generate-ast",
            "--ast-cache-root",
            "/tmp/cache",
        ],
    }]
    if fail_cmd:
        jobs.append({
            "schema":
            "veriput-ast-preheat-job/v1",
            "job_id":
            "bugfix124__repo__D",
            "priority":
            1,
            "ordinal":
            1,
            "benchmark":
            "bugfix124",
            "subject_id":
            "repo__D",
            "preheat_argv": [
                fail_cmd,
                "--generate-ast",
                "--ast-cache-root",
                "/tmp/cache",
            ],
        })
    return {
        "schema": "veriput-ast-preheat-schedule/v1",
        "summary": {
            "jobs": len(jobs),
        },
        "jobs": jobs,
    }


def test_runner_executes_and_resumes_from_journal():
    with tempfile.TemporaryDirectory() as td:
        ok_cmd = fake_preheat_script(Path(td) / "ok.py")
        journal = Path(td) / "run.jsonl"
        first = ast_preheat_run.run_schedule(schedule(ok_cmd), journal=str(journal), timeout_s=5)
        second = ast_preheat_run.run_schedule(schedule(ok_cmd), journal=str(journal), timeout_s=5)
        lines = journal.read_text().splitlines()
    bad = 0
    bad += check(first["summary"]["attempted"] == 1,
                 f"first run executes one job: {first['summary']}")
    bad += check(first["summary"]["status"] == {"ok": 1},
                 f"successful row is counted: {first['summary']}")
    bad += check(second["summary"]["already_done"] == 1 and second["summary"]["attempted"] == 0,
                 f"resume skips journaled ok job: {second['summary']}")
    bad += check(len(lines) == 1, f"resume does not append another row: {lines}")
    return bad


def test_runner_records_non_ok_row_and_keeps_it_retryable():
    with tempfile.TemporaryDirectory() as td:
        ok_cmd = fake_preheat_script(Path(td) / "ok.py")
        bad_cmd = fake_preheat_script(Path(td) / "bad.py", status="missing-ast")
        journal = Path(td) / "run.jsonl"
        first = ast_preheat_run.run_schedule(schedule(ok_cmd, bad_cmd),
                                             journal=str(journal),
                                             timeout_s=5)
        second = ast_preheat_run.run_schedule(schedule(ok_cmd, bad_cmd),
                                              journal=str(journal),
                                              timeout_s=5)
        lines = [json.loads(line) for line in journal.read_text().splitlines()]
    bad = 0
    bad += check(first["summary"]["status"] == {
        "error": 1,
        "ok": 1
    }, f"non-ok subject row is recorded as error: {first['summary']}")
    bad += check(second["summary"]["already_done"] == 1 and second["summary"]["attempted"] == 1,
                 f"only ok rows are skipped on resume: {second['summary']}")
    bad += check(
        sum(1 for row in lines if row["status"] == "error") == 2,
        f"retryable non-ok row is journaled each attempt: {lines}")
    return bad


def test_runner_dry_run_and_fail_closed_modes():
    with tempfile.TemporaryDirectory() as td:
        ok_cmd = fake_preheat_script(Path(td) / "ok.py")
        sched = schedule(ok_cmd)
        journal = Path(td) / "run.jsonl"
        ast_preheat_run.run_schedule(sched, journal=str(journal), timeout_s=5)
        dry = ast_preheat_run.dry_run_doc(sched, journal=str(journal))
        try:
            ast_preheat_run.run_schedule(sched, journal="", timeout_s=5)
        except ast_preheat_run.PreheatRunError as exc:
            refused = str(exc)
        else:
            refused = ""
    bad = 0
    bad += check(dry["summary"]["already_done"] == 1 and dry["summary"]["pending"] == 0,
                 f"dry-run honors journal resume: {dry['summary']}")
    bad += check("pass --journal" in refused, f"real execution requires a journal: {refused}")
    return bad


def test_runner_journals_start_failure():
    with tempfile.TemporaryDirectory() as td:
        journal = Path(td) / "run.jsonl"
        missing = str(Path(td) / "missing-command")
        result = ast_preheat_run.run_schedule(schedule(missing), journal=str(journal), timeout_s=5)
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
    bad = 0
    bad += check(result["summary"]["status"] == {"error": 1},
                 f"start failure is counted: {result['summary']}")
    bad += check("could not start" in rows[0]["reason"], f"start failure is journaled: {rows}")
    return bad


def test_runner_cli_dry_run_reads_schedule():
    with tempfile.TemporaryDirectory() as td:
        ok_cmd = fake_preheat_script(Path(td) / "ok.py")
        sched = Path(td) / "schedule.json"
        sched.write_text(json.dumps(schedule(ok_cmd)) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "ast_preheat_run.py"),
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
    bad += check(doc["schema"] == "veriput-ast-preheat-run-plan/v1",
                 f"CLI dry-run emits a run plan: {doc}")
    bad += check(doc["summary"]["pending"] == 1,
                 f"CLI dry-run selects the pending job: {doc['summary']}")
    return bad


TESTS = [
    test_runner_executes_and_resumes_from_journal,
    test_runner_records_non_ok_row_and_keeps_it_retryable,
    test_runner_dry_run_and_fail_closed_modes,
    test_runner_journals_start_failure,
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
