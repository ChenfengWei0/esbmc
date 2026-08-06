#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_campaign_plan  # noqa: E402
import veriput_recipe  # noqa: E402

DEFAULT_RECIPE_DIR = veriput_recipe.STRONG_RECIPE_VERSION.replace("/", "_")


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def argv_value(argv, flag):
    try:
        idx = argv.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


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
            "jobs": 5,
        },
        "jobs": [
            job("peer182__new__f"),
            job("bugfix124__retry2__g", "bugfix124", "g"),
            job("stress243__retry3__h", "stress243", "h", priority=1),
            job("stress243__done__i", "stress243", "i", priority=1),
            job("stress243__exhausted__j", "stress243", "j", priority=1),
        ],
    }


def row(job_id, status, reason="", benchmark="peer182", campaign_attempt=None):
    item = {
        "schema": "veriput-unit-run-row/v1",
        "job_id": job_id,
        "benchmark": benchmark,
        "subject_id": job_id,
        "contract": "C",
        "unit": "f",
        "status": status,
        "reason": reason,
    }
    if campaign_attempt is not None:
        item["campaign_attempt"] = campaign_attempt
    return item


def write_json(path, doc):
    p = Path(path)
    p.write_text(json.dumps(doc) + "\n")
    return p


def write_journal(path, rows):
    p = Path(path)
    p.write_text("\n".join(json.dumps(item) for item in rows) + "\nnot-json\n")
    return p


def write_clean_jsonl(path, rows):
    p = Path(path)
    p.write_text("\n".join(json.dumps(item) for item in rows) + "\n")
    return p


def test_campaign_partitions_attempts_and_auto_selects_earliest():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(Path(td) / "schedule.json", schedule_doc())
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("bugfix124__retry2__g", "timeout", "timeout after 60s", "bugfix124"),
                row("stress243__retry3__h", "error", "rc=9", "stress243"),
                row("stress243__done__i", "ok", "", "stress243"),
                row("stress243__exhausted__j", "timeout", "timeout after 60s", "stress243"),
                row("orphan__x", "timeout"),
            ])
        j2 = write_journal(
            Path(td) / "a2.jsonl", [
                row("stress243__retry3__h", "timeout", "timeout after 120s", "stress243"),
                row("stress243__exhausted__j", "timeout", "timeout after 120s", "stress243"),
            ])
        j3 = write_journal(
            Path(td) / "a3.jsonl", [
                row("stress243__exhausted__j", "timeout", "timeout after 600s", "stress243"),
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1), str(j2),
                                                              str(j3)])
    bad = 0
    bad += check(doc["schema"] == "veriput-unit-campaign-plan/v1",
                 f"campaign schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {
        "1": 1,
        "2": 1,
        "3": 1,
    }, f"pending jobs are partitioned by next attempt: {doc['summary']}")
    bad += check(doc["summary"]["completed_ok"] == 1 and doc["summary"]["exhausted"] == 1,
                 f"done and exhausted jobs are counted: {doc['summary']}")
    bad += check(doc["summary"]["selected_attempt"] == 1 and doc["summary"]["selected_jobs"] == 1,
                 f"auto mode selects earliest pending attempt: {doc['summary']}")
    bad += check(doc["summary"]["bad_journal_lines"] == 3,
                 f"bad journal lines are counted across journals: {doc['summary']}")
    bad += check(doc["summary"]["orphan_journal_rows"] == 1,
                 f"orphan rows are reported: {doc['summary']}")
    bad += check(doc["next_run"]["timeout_s"] == 60.0 and doc["next_run"]["memlimit_gb"] == 8.0,
                 f"attempt 1 uses the agreed short budget: {doc['next_run']}")
    bad += check([job["job_id"] for job in doc["next_schedule"]["jobs"]] == ["peer182__new__f"],
                 f"next schedule keeps only attempt-1 jobs: {doc['next_schedule']['jobs']}")
    next_argv = doc["next_schedule"]["jobs"][0]["certify_argv"]
    bad += check(argv_value(next_argv, "--timeout") == "70"
                 and argv_value(next_argv, "--run-timeout") == "60"
                 and argv_value(next_argv, "--memlimit-gib") == "8",
                 f"attempt-1 schedule keeps ESBMC at 60s with wrapper grace: {next_argv}")
    bad += check(argv_value(next_argv, "--workdir")
                 == f"/tmp/certify_all/{DEFAULT_RECIPE_DIR}/a1_t70_r60_m8",
                 f"attempt-1 schedule uses an attempt-specific workdir: {next_argv}")
    return bad


def test_campaign_can_emit_attempt_three_schedule_and_runner_argv():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(Path(td) / "schedule.json", schedule_doc())
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__retry3__h", "error", "rc=9", "stress243"),
            ])
        j2 = write_journal(
            Path(td) / "a2.jsonl", [
                row("stress243__retry3__h", "timeout", "timeout after 120s", "stress243"),
            ])
        out = Path(td) / "attempt3.json"
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1), str(j2)],
                                               attempt=3,
                                               next_schedule_out=str(out),
                                               next_journal=str(Path(td) / "a3.jsonl"),
                                               jobs=2)
        out_doc = json.loads(out.read_text())
    runner = doc["next_run"]["runner_argv"]
    certify_argv = out_doc["jobs"][0]["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["selected_attempt"] == 3,
                 f"explicit attempt 3 is selected: {doc['summary']}")
    bad += check(doc["next_run"]["timeout_s"] == 600.0 and doc["next_run"]["memlimit_gb"] == 10.0,
                 f"attempt 3 uses the agreed long budget: {doc['next_run']}")
    bad += check(
        "--timeout" in runner and "615.0" in runner and "--memlimit-gb" in runner
        and "10.0" in runner and "--jobs" in runner and "2" in runner,
        f"runner argv carries outer grace budget and jobs: {runner}")
    bad += check("--dry-run" in doc["next_run"]["dry_run_argv"]
                 and "--dry-run" not in runner,
                 f"dry-run argv is explicit and separate: {doc['next_run']}")
    bad += check("--dry-run" in doc["next_run"]["dry_run_cmd"]
                 and "--dry-run" not in doc["next_run"]["runner_cmd"],
                 f"shell commands mirror dry-run/runner argv: {doc['next_run']}")
    bad += check(
        out_doc["summary"]["campaign_attempt"] == 3
        and [job["job_id"] for job in out_doc["jobs"]] == ["stress243__retry3__h"],
        f"attempt schedule is written: {out_doc['summary']}")
    bad += check(argv_value(certify_argv, "--timeout") == "610"
                 and argv_value(certify_argv, "--run-timeout") == "600"
                 and argv_value(certify_argv, "--memlimit-gib") == "10"
                 and certify_argv.count("--timeout") == 1,
                 f"attempt-3 schedule keeps ESBMC at 600s with wrapper grace: {certify_argv}")
    bad += check(argv_value(certify_argv, "--workdir")
                 == f"/tmp/certify_all/{DEFAULT_RECIPE_DIR}/a3_t610_r600_m10",
                 f"attempt-3 schedule rewrites scratch root: {certify_argv}")
    return bad


def test_campaign_cli_writes_plan_and_schedule():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(Path(td) / "schedule.json", schedule_doc())
        out_sched = Path(td) / "next.json"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_campaign_plan.py"),
            str(sched),
            "--next-schedule-out",
            str(out_sched),
            "--next-journal",
            str(Path(td) / "run.jsonl"),
        ],
                            capture_output=True,
                            text=True)
        out_doc = json.loads(out_sched.read_text()) if out_sched.exists() else {}
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    bad = 0
    bad += check(doc["summary"]["selected_attempt"] == 1,
                 f"CLI emits a campaign plan: {doc['summary']}")
    bad += check(out_doc["summary"]["jobs"] == 5,
                 f"CLI writes attempt-1 schedule for never-attempted jobs: {out_doc['summary']}")
    bad += check(
        str(out_sched) in doc["next_run"]["runner_argv"],
        f"runner argv points to written schedule: {doc['next_run']['runner_argv']}")
    bad += check(
        str(out_sched) in doc["next_run"]["dry_run_argv"]
        and "--dry-run" in doc["next_run"]["dry_run_argv"],
        f"dry-run argv points to written schedule: {doc['next_run']['dry_run_argv']}")
    bad += check(
        str(out_sched) in doc["next_run"]["dry_run_cmd"]
        and str(out_sched) in doc["next_run"]["runner_cmd"],
        f"copyable commands point to written schedule: {doc['next_run']}")
    return bad


def test_campaign_writes_empty_schedule_when_no_jobs_are_pending():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 0,
                },
                "jobs": [],
            })
        out = Path(td) / "empty-next.json"
        doc = unit_campaign_plan.plan_campaign(str(sched), next_schedule_out=str(out))
        out_doc = json.loads(out.read_text())
    bad = 0
    bad += check(
        doc["summary"]["selected_attempt"] is None and doc["summary"]["selected_jobs"] == 0,
        f"no pending jobs means no selected attempt: {doc['summary']}")
    bad += check(doc["next_run"] is None, f"no runner argv is suggested: {doc['next_run']}")
    bad += check(out_doc["summary"]["jobs"] == 0 and out_doc["summary"]["campaign_attempt"] is None,
                 f"empty next schedule is still written: {out_doc['summary']}")
    return bad


def test_campaign_counts_distinct_attempts_not_duplicate_rows():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__dup__f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__dup__f", "timeout", "timeout after 60s"),
                row("peer182__dup__f", "error", "rc=9"),
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched), journal_paths=[str(j1)])
    bad = 0
    bad += check(doc["summary"]["status_attempts"] == {
        "error": 1,
        "timeout": 1,
    }, f"all duplicate rows remain visible diagnostically: {doc['summary']}")
    bad += check(doc["summary"]["distinct_attempts_max"] == 1,
                 f"one journal still counts as one attempt: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"duplicate rows do not skip attempt 2: {doc['summary']}")
    return bad


def test_campaign_uses_explicit_attempt_metadata_for_budget_state():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__late__f"),
                ],
            })
        j3 = write_journal(
            Path(td) / "only-a3.jsonl", [
                row("peer182__late__f", "timeout", "timeout after 600s", campaign_attempt=3),
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched), journal_paths=[str(j3)])
    bad = 0
    bad += check(doc["summary"]["exhausted"] == 1,
                 f"campaign_attempt=3 is exhausted after failure: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"explicit attempt metadata prevents fallback attempt 2: {doc['summary']}")
    return bad


def test_campaign_retries_runner_ok_when_certification_is_weak():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema":
                "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 3,
                },
                "jobs": [
                    job("peer182__strong__f"),
                    job("peer182__weak__g", unit="g"),
                    job("peer182__missing__h", unit="h"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__strong__f", "ok", campaign_attempt=1),
                row("peer182__weak__g", "ok", campaign_attempt=1),
                row("peer182__missing__h", "ok", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "f",
                    "bucket": "CERTIFIED",
                    "witnessed": 2,
                    "certified": {
                        "1": "x in [0, 9]",
                        "2": "x == 1",
                    },
                    "not_certified": {},
                },
                {
                    "benchmark": "peer182",
                    "unit": "g",
                    "bucket": "CERTIFIED",
                    "witnessed": 4,
                    "certified": {
                        "1": "x in [0, 9]",
                    },
                    "not_certified": {
                        "2": "refuted",
                    },
                    "generalise_progress": {
                        "stage": "certify-query-started",
                        "enc": 3,
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_ids = [job["job_id"] for job in doc["next_schedule"]["jobs"]]
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 1,
                 f"only strong certification completes a runner-ok job: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 2},
                 f"weak or missing certification is retryable: {doc['summary']}")
    bad += check(
        doc["summary"]["cert_weak"] == {
            "certification-stage no verdict": 1,
            "no certification row": 1,
        }, f"weak certification reasons are counted: {doc['summary']}")
    bad += check(next_ids == ["peer182__weak__g", "peer182__missing__h"],
                 f"next schedule keeps weak runner-ok jobs: {next_ids}")
    return bad


def test_campaign_names_partial_journal_only_as_weak_certification():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__partial__f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__partial__f", "ok", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182__partial",
                    "unit": "f",
                    "subject": {
                        "benchmark": "peer182",
                        "subject_id": "partial",
                        "benchmark_key": "peer182__partial",
                    },
                    "bucket": "KILLED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "partial_witness_journal": {
                        "path_count": 1,
                        "witness_count": 8,
                        "claims_decided": 6,
                        "claims_total": 277,
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 0,
                 f"partial-only row does not complete certification: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"partial-only row is retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "partial witness journal only": 1,
    }, f"partial-only reason is visible: {doc['summary']}")
    return bad


def test_campaign_accepts_strong_certification_without_runner_journal():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__historical__f"),
                ],
            })
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "f",
                    "bucket": "CERTIFIED",
                    "witnessed": 1,
                    "certified": {
                        "1": "x in [0, 9]",
                    },
                    "not_certified": {},
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched), cert_jsonl_paths=[str(cert)])
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 1,
                 f"strong historical cert row completes the unit: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"strong cert row prevents attempt1 rerun: {doc['summary']}")
    bad += check(doc["by_benchmark_state"] == {
        "peer182": {
            "completed-certified": 1,
        },
    }, f"state records completion source: {doc['by_benchmark_state']}")
    return bad


def test_campaign_can_plan_from_in_memory_schedule():
    doc = unit_campaign_plan.plan_campaign_for_schedule(schedule_doc(), "<unit-schedule>",
                                                        jobs=3)
    bad = 0
    bad += check(doc["schedule"] == "<unit-schedule>",
                 f"in-memory schedule label is preserved: {doc['schedule']}")
    bad += check(doc["summary"]["selected_attempt"] == 1
                 and doc["summary"]["selected_jobs"] == 5,
                 f"in-memory schedule is partitioned without a temp file: {doc['summary']}")
    bad += check("--jobs" in doc["next_run"]["runner_argv"]
                 and "3" in doc["next_run"]["runner_argv"],
                 f"runner argv still carries worker count: {doc['next_run']}")
    bad += check("--dry-run" in doc["next_run"]["dry_run_argv"],
                 f"in-memory campaign exposes a dry-run command: {doc['next_run']}")
    bad += check("--dry-run" in doc["next_run"]["dry_run_cmd"]
                 and "--dry-run" not in doc["next_run"]["runner_cmd"],
                 f"in-memory campaign exposes copyable commands: {doc['next_run']}")
    return bad


TESTS = [
    test_campaign_partitions_attempts_and_auto_selects_earliest,
    test_campaign_can_emit_attempt_three_schedule_and_runner_argv,
    test_campaign_cli_writes_plan_and_schedule,
    test_campaign_writes_empty_schedule_when_no_jobs_are_pending,
    test_campaign_counts_distinct_attempts_not_duplicate_rows,
    test_campaign_uses_explicit_attempt_metadata_for_budget_state,
    test_campaign_retries_runner_ok_when_certification_is_weak,
    test_campaign_names_partial_journal_only_as_weak_certification,
    test_campaign_accepts_strong_certification_without_runner_journal,
    test_campaign_can_plan_from_in_memory_schedule,
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
