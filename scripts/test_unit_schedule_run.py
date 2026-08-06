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


def sleepy_script(path):
    p = Path(path)
    p.write_text("#!/usr/bin/env python3\n"
                 "import sys, time\n"
                 "print('before sleep')\n"
                 "print('stderr before sleep', file=sys.stderr)\n"
                 "sys.stdout.flush()\n"
                 "sys.stderr.flush()\n"
                 "time.sleep(5)\n")
    p.chmod(0o755)
    return str(p)


def job(job_id, cmd, unit="f", out_path="", ast_cache_root=""):
    argv = [
        cmd,
        "--subject-dir",
        "/tmp/repo__C",
        "--unit",
        unit,
    ]
    if out_path:
        argv += ["--out", out_path]
    if ast_cache_root:
        argv += ["--ast-cache-root", ast_cache_root]
    return {
        "schema": "veriput-unit-job/v1",
        "job_id": job_id,
        "priority": 0,
        "ordinal": 0,
        "benchmark": "peer182",
        "subject_id": "repo__C",
        "contract": "C",
        "unit": unit,
        "certify_argv": argv,
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


def campaign_schedule(ok_cmd):
    doc = schedule(ok_cmd)
    doc["source"] = {
        "campaign_policy": "veriput-unit-campaign-policy/v1",
        "campaign_attempt": 1,
    }
    doc["summary"]["campaign_attempt"] = 2
    return doc


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


def test_runner_journals_timeout_with_text_tails():
    with tempfile.TemporaryDirectory() as td:
        slow = sleepy_script(Path(td) / "slow.py")
        journal = Path(td) / "run.jsonl"
        first = unit_schedule_run.run_schedule(schedule(slow),
                                               journal=str(journal),
                                               timeout_s=0.2)
        row = json.loads(journal.read_text().splitlines()[0])
    bad = 0
    bad += check(first["summary"]["status"] == {"timeout": 1},
                 f"timeout command is journaled: {first['summary']}")
    bad += check(row["status"] == "timeout" and isinstance(row["stdout_tail"], str)
                 and "before sleep" in row["stdout_tail"],
                 f"timeout stdout tail is JSON text: {row}")
    bad += check(isinstance(row["stderr_tail"], str),
                 f"timeout stderr tail is JSON text: {row}")
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


def test_runner_journals_campaign_metadata_from_schedule():
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        journal = Path(td) / "run.jsonl"
        first = unit_schedule_run.run_schedule(campaign_schedule(ok),
                                               journal=str(journal),
                                               timeout_s=5)
        row = json.loads(journal.read_text().splitlines()[0])
        dry = unit_schedule_run.dry_run_doc(campaign_schedule(ok), journal=str(Path(td) / "empty"))
    bad = 0
    bad += check(first["summary"]["campaign_attempt"] == 2,
                 f"run summary records the schedule campaign attempt: {first['summary']}")
    bad += check(
        row["campaign_attempt"] == 2
        and row["campaign_policy"] == "veriput-unit-campaign-policy/v1",
        f"journal row records campaign metadata: {row}")
    bad += check(dry["summary"]["campaign_attempt"] == 2,
                 f"dry-run summary records campaign metadata: {dry['summary']}")
    return bad


def test_runner_refuses_protected_write_paths_and_negative_memlimit():
    protected = "/home/samson/workspace/VeriPUT/Results/certify.jsonl"
    with tempfile.TemporaryDirectory() as td:
        ok = fake_script(Path(td) / "ok.py")
        bad_sched = {
            "schema": "veriput-unit-schedule/v1",
            "jobs": [job("peer182__repo__C__f", ok, out_path=protected)],
        }
        bad_cache_sched = {
            "schema":
            "veriput-unit-schedule/v1",
            "jobs": [
                job("peer182__repo__C__f",
                    ok,
                    ast_cache_root="/home/samson/workspace/VeriPUT/Results/ast-cache")
            ],
        }
        bad_workdir_job = job("peer182__repo__C__f", ok)
        bad_workdir_job["certify_argv"] += [
            "--workdir",
            "/home/samson/workspace/VeriPUT/Results/work",
        ]
        bad_workdir_sched = {
            "schema": "veriput-unit-schedule/v1",
            "jobs": [bad_workdir_job],
        }
        try:
            unit_schedule_run.dry_run_doc(bad_sched)
        except unit_schedule_run.UnitRunError as exc:
            refused_out = str(exc)
        else:
            refused_out = ""
        try:
            unit_schedule_run.dry_run_doc(bad_cache_sched)
        except unit_schedule_run.UnitRunError as exc:
            refused_cache = str(exc)
        else:
            refused_cache = ""
        try:
            unit_schedule_run.dry_run_doc(bad_workdir_sched)
        except unit_schedule_run.UnitRunError as exc:
            refused_workdir = str(exc)
        else:
            refused_workdir = ""
        try:
            unit_schedule_run.run_schedule(schedule(ok),
                                           journal=protected,
                                           timeout_s=5)
        except unit_schedule_run.UnitRunError as exc:
            refused_journal = str(exc)
        else:
            refused_journal = ""
        try:
            unit_schedule_run.run_schedule(schedule(ok),
                                           journal=str(Path(td) / "run.jsonl"),
                                           timeout_s=5,
                                           memlimit_gb=-1)
        except unit_schedule_run.UnitRunError as exc:
            refused_mem = str(exc)
        else:
            refused_mem = ""
    bad = 0
    bad += check("--out must not be under" in refused_out,
                 f"protected certify output is refused: {refused_out}")
    bad += check("--ast-cache-root must not be under" in refused_cache,
                 f"protected AST cache is refused: {refused_cache}")
    bad += check("--workdir must not be under" in refused_workdir,
                 f"protected workdir is refused: {refused_workdir}")
    bad += check("--journal must not be under" in refused_journal,
                 f"protected unit journal is refused: {refused_journal}")
    bad += check("--memlimit-gb" in refused_mem,
                 f"negative unit memlimit is refused: {refused_mem}")
    return bad


TESTS = [
    test_runner_executes_and_resumes_from_journal,
    test_runner_records_failures_as_retryable,
    test_runner_journals_timeout_with_text_tails,
    test_runner_dry_run_start_failure_and_fail_closed_modes,
    test_runner_cli_dry_run_reads_schedule,
    test_runner_journals_campaign_metadata_from_schedule,
    test_runner_refuses_protected_write_paths_and_negative_memlimit,
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
