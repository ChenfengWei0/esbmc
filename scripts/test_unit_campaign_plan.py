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


def test_campaign_preserves_no_unit_audit_metadata_in_next_schedule():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = schedule_doc()
        sched_doc["skipped_units"] = [{
            "row": 1,
            "unit": "fallback",
            "reason": "fallback/receive cannot be selected by --focus-function",
        }]
        sched_doc["no_unit_rows"] = [{
            "row": 1,
            "reason": (
                "target contract exposes only fallback/receive entries; "
                "use deploy-only concrete fallback"),
            "skipped": sched_doc["skipped_units"],
        }]
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        doc = unit_campaign_plan.plan_campaign(str(sched))
    next_schedule = doc["next_schedule"]
    bad = 0
    bad += check(next_schedule["skipped_units"] == sched_doc["skipped_units"],
                 f"campaign schedule preserves skipped-unit audit rows: {next_schedule}")
    bad += check(next_schedule["no_unit_rows"] == sched_doc["no_unit_rows"],
                 f"campaign schedule preserves deploy fallback rows: {next_schedule}")
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


def test_campaign_rewrites_retry_certification_out_by_attempt():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = {
            "schema": "veriput-unit-schedule/v1",
            "summary": {
                "jobs": 1,
            },
            "jobs": [
                job("peer182__retry_out__f"),
            ],
        }
        base_out = str(Path(td) / "certify-results-a1.jsonl")
        sched_doc["jobs"][0]["certify_argv"].extend(["--out", base_out])
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__retry_out__f", "timeout", "timeout after 60s"),
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched), journal_paths=[str(j1)])
    next_job = doc["next_schedule"]["jobs"][0]
    next_argv = next_job["certify_argv"]
    next_out = str(Path(td) / "certify-results-a2.jsonl")
    bad = 0
    bad += check(argv_value(next_argv, "--out") == next_out,
                 f"attempt-2 schedule writes a fresh certification JSONL: {next_argv}")
    bad += check(doc["next_schedule"]["cert_out"] == next_out
                 and doc["next_schedule"]["summary"]["certify_out"] == next_out,
                 f"attempt schedule exposes the rewritten out path: {doc['next_schedule']}")
    bad += check((next_job.get("certification_budget") or {}).get("out") == next_out,
                 f"job budget records the rewritten out path: {next_job}")
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
    next_reasons = {
        job["job_id"]: (job.get("certification_quality") or {}).get("reason")
        for job in doc["next_schedule"]["jobs"]
    }
    next_jobs = {job["job_id"]: job for job in doc["next_schedule"]["jobs"]}
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
    bad += check(next_reasons == {
        "peer182__weak__g": "certification-stage no verdict",
        "peer182__missing__h": "no certification row",
    }, f"next schedule carries per-job weak reasons: {next_reasons}")
    weak_quality = next_jobs["peer182__weak__g"]["certification_quality"]
    weak_argv = next_jobs["peer182__weak__g"]["certify_argv"]
    missing_quality = next_jobs["peer182__missing__h"]["certification_quality"]
    missing_argv = next_jobs["peer182__missing__h"]["certify_argv"]
    bad += check(
        weak_quality.get("retry_strategy") == "certification-first"
        and weak_quality.get("retry_refine_rounds") == 1,
        f"certification-stage retries carry the certification-first strategy: {weak_quality}")
    bad += check(argv_value(weak_argv, "--refine-rounds") == "1",
                 f"certification-stage retries reduce refine rounds: {weak_argv}")
    bad += check("retry_strategy" not in missing_quality
                 and argv_value(missing_argv, "--refine-rounds") == "2",
                 f"missing-certification retries keep the default strategy: {missing_quality}")
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
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    bad += check(quality.get("retry_strategy") == "finish-partial-certification"
                 and quality.get("retry_refine_rounds") == 1,
                 f"partial-only retry has auditable strategy metadata: {quality}")
    bad += check(argv_value(next_job["certify_argv"], "--refine-rounds") == "1",
                 f"partial-only retry spends the attempt on certification: {next_job['certify_argv']}")
    return bad


def test_campaign_prefers_single_refine_for_refinement_timeouts():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("stress243__refine__f", benchmark="stress243"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__refine__f", "ok", benchmark="stress243", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "f",
                    "bucket": "KILLED",
                    "witnessed": 3,
                    "certified": {},
                    "not_certified": {},
                    "partial_witness_journal": {
                        "path_count": 3,
                        "witness_count": 24,
                        "claims_decided": 67,
                        "claims_total": 116,
                    },
                    "generalise_progress": {
                        "stage": "outer-round-started",
                        "round_kind": "linear-refine",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"refinement timeout remains retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "refinement-stage no verdict": 1,
    }, f"refinement timeout has a precise weak reason: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "single-refine-certification-first"
                 and quality.get("retry_refine_rounds") == 1,
                 f"refinement timeout gets single-refine retry metadata: {quality}")
    bad += check(argv_value(next_job["certify_argv"], "--refine-rounds") == "1",
                 f"refinement timeout retry uses one refine round: {next_job['certify_argv']}")
    return bad


def test_campaign_cheapens_probe_claim_explosion_retries():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = {
            "schema": "veriput-unit-schedule/v1",
            "summary": {
                "jobs": 1,
            },
            "jobs": [
                job("bugfix124__probeheavy__setGoverned",
                    benchmark="bugfix124",
                    unit="setGoverned"),
            ],
        }
        sched_doc["jobs"][0]["certify_argv"].extend([
            "--probe-witnesses",
            "8",
            "--probe-ladder",
            "--probe-ladder-budget",
            "4",
        ])
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("bugfix124__probeheavy__setGoverned",
                    "ok",
                    benchmark="bugfix124",
                    campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "bugfix124",
                    "unit": "setGoverned",
                    "bucket": "KILLED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "path-coverage-probe-claim-explosion",
                        "probe_claims": 370,
                        "branch_arms": 10,
                        "physical_exits": 37,
                        "complete_path_denominator": 37,
                    },
                    "generalise_progress": {
                        "stage": "started",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"probe explosion remains retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "probe enumeration claim explosion": 1,
    }, f"probe explosion has a precise weak reason: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "direct-enumeration-no-probe"
                 and quality.get("retry_probe_witnesses") == 0
                 and quality.get("retry_probe_ladder") is False,
                 f"retry metadata names direct enumeration: {quality}")
    bad += check(argv_value(argv, "--probe-witnesses") == "0"
                 and "--probe-ladder" not in argv
                 and "--probe-ladder-budget" not in argv,
                 f"retry argv disables probe product and drops ladder: {argv}")
    bad += check(quality.get("retry_observed_probe_claims") == 370
                 and quality.get("retry_observed_physical_exits") == 37,
                 f"observed probe product travels with retry metadata: {quality}")
    return bad


def test_campaign_cheapens_probe_goal_cap_retries():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = {
            "schema": "veriput-unit-schedule/v1",
            "summary": {
                "jobs": 1,
            },
            "jobs": [
                job("stress243__probecap__withdraw",
                    benchmark="stress243",
                    unit="withdraw"),
            ],
        }
        sched_doc["jobs"][0]["certify_argv"].extend([
            "--probe-witnesses",
            "8",
            "--probe-ladder",
            "--probe-ladder-budget",
            "4",
        ])
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__probecap__withdraw",
                    "ok",
                    benchmark="stress243",
                    campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "withdraw",
                    "bucket": "KILLED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "path-coverage-probe-goal-cap",
                        "probe_claims": 520,
                        "branch_arms": 13,
                        "physical_exits": 40,
                        "path_cov_max_goals": 500,
                    },
                    "generalise_progress": {
                        "stage": "started",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"probe goal cap remains retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "path coverage probe goal cap": 1,
    }, f"probe goal cap has a precise weak reason: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "direct-enumeration-no-probe"
                 and quality.get("retry_observed_path_cov_max_goals") == 500
                 and quality.get("retry_observed_probe_claims") == 520,
                 f"probe goal cap retry records observed cap data: {quality}")
    bad += check(argv_value(argv, "--probe-witnesses") == "0"
                 and "--probe-ladder" not in argv
                 and "--probe-ladder-budget" not in argv,
                 f"probe goal cap retry disables probe product: {argv}")
    return bad


def test_campaign_deepens_tx_for_bounded_holds_no_witness():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__nowitness__approve", benchmark="peer182",
                        unit="approve"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__nowitness__approve",
                    "ok",
                    benchmark="peer182",
                    campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "approve",
                    "bucket": "NO-PATH",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "empty_witness_verdict": "DECIDED",
                    "generalise_progress": {
                        "stage":
                        "no-witness",
                        "reason":
                        "4 claim(s) for this unit, none witnessed: "
                        "4x bounded-holds -- no counterexample exists WITHIN "
                        "THE BOUND this run used. A deeper --max-tx or "
                        "--unwind may witness it",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"bounded-holds no-witness remains retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "bounded-holds no witness": 1,
    }, f"bounded-holds no-witness has a precise weak reason: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "deepen-witness-search"
                 and quality.get("retry_max_tx") == 2
                 and "retry_scope" not in quality,
                 f"bounded-holds no-witness deepens witness search: {quality}")
    bad += check(argv_value(argv, "--scope") is None
                 and argv_value(argv, "--max-tx") == "2",
                 f"bounded-holds retry changes max-tx only: {argv}")
    bad += check(argv_value(argv, "--refine-rounds") == "2",
                 f"bounded-holds retry keeps normal refinement budget: {argv}")
    return bad


def test_campaign_deepens_unwind_for_gated_unit_depth_obstacle():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__gated__transfer", benchmark="peer182",
                        unit="transfer"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__gated__transfer",
                    "ok",
                    benchmark="peer182",
                    campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "transfer",
                    "bucket": "NO-WITNESS-UNDECIDED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "empty_witness_verdict": "REFUSED",
                    "empty_witness_reason":
                    "132 of 132 claim(s) were named-obstacle paths. "
                    "This is a structural model/chain mismatch for the unit",
                    "empty_witness_obstacles": {
                        "named_obstacle": {
                            "total": 132,
                            "details": {
                                "unit still calls another UNIT's own body "
                                "unexpanded (sol:@C@Animalia@F@balanceOf#1568); "
                                "that body carries the ABI value gate": 132,
                            },
                        },
                    },
                    "generalise_progress": {
                        "stage": "no-witness",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"gated-unit obstacle remains retryable: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "gated unit depth no witness": 1,
    }, f"gated-unit obstacle has a precise weak reason: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "deepen-internal-call-expansion"
                 and quality.get("retry_unwind") == 8
                 and quality.get("retry_max_tx") == 2
                 and quality.get("retry_probe_witnesses") == 0
                 and quality.get("retry_probe_ladder") is False
                 and quality.get("retry_refine_rounds") == 2,
                 f"retry metadata names internal-call expansion: {quality}")
    bad += check(argv_value(argv, "--max-tx") == "2",
                 f"gated-unit retry deepens transaction count once: {argv}")
    bad += check("--esbmc-arg=--unwind=8" in argv,
                 f"gated-unit retry raises ESBMC unwind: {argv}")
    bad += check(argv_value(argv, "--probe-witnesses") == "0"
                 and "--probe-ladder" not in argv
                 and "--probe-ladder-budget" not in argv,
                 f"gated-unit retry disables probe product: {argv}")
    return bad


def test_campaign_restores_default_refine_rounds_after_strategy_attempt():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = {
            "schema": "veriput-unit-schedule/v1",
            "summary": {
                "jobs": 1,
            },
            "jobs": [
                job("stress243__after_strategy__f", benchmark="stress243"),
            ],
        }
        sched_doc["jobs"][0]["certify_argv"].extend(["--refine-rounds", "0"])
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        j2 = write_journal(
            Path(td) / "a2.jsonl", [
                row("stress243__after_strategy__f",
                    "ok",
                    benchmark="stress243",
                    campaign_attempt=2),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "f",
                    "bucket": "NOT-CERTIFIED",
                    "witnessed": 4,
                    "certified": {},
                    "not_certified": {
                        "12": "no fully bounded region was measured",
                        "14": "no fully bounded region was measured",
                        "26": "no fully bounded region was measured",
                        "54": "no fully bounded region was measured",
                    },
                    "partial_witness_journal": {
                        "path_count": 4,
                        "witness_count": 32,
                    },
                    "generalise_progress": {
                        "stage": "complete",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j2)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    bad = 0
    bad += check(doc["summary"]["pending_by_attempt"] == {"3": 1},
                 f"failed strategy attempt advances to attempt 3: {doc['summary']}")
    bad += check(argv_value(next_job["certify_argv"], "--refine-rounds") == "2",
                 f"default retry restores recipe refine rounds: {next_job['certify_argv']}")
    return bad


def test_campaign_treats_slice_excluded_paths_as_body_slice_ready():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__slice__f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__slice__f", "ok", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "f",
                    "bucket": "CERTIFIED",
                    "witnessed": 2,
                    "certified": {
                        "3": "x in [0, 9]",
                    },
                    "not_certified": {
                        "2": "EXCLUDED FROM THE SLICE by the pins "
                             "(msg.value: CE 1 outside [0, 0]), which is why its "
                             "region came back EMPTY on x. This is NOT a failure to certify",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 1,
                 f"slice-excluded value-gate path does not force a retry: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"body-slice-ready unit has no pending retry: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {},
                 f"slice exclusions are not counted as weak certification: {doc['summary']}")
    return bad


def test_campaign_treats_method_unsupported_paths_as_non_retryable():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__method__f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__method__f", "ok", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "f",
                    "bucket": "CERTIFIED",
                    "witnessed": 2,
                    "certified": {
                        "3": "x in [0, 9]",
                    },
                    "not_certified": {
                        "12": "STATICALLY INSEPARABLE: this path has a witnessed sibling "
                              "whose source-level split is driven by an ESBMC hash/nondet/"
                              "external-call decision rather than by a generated-test-settable "
                              "coordinate (decision#3 random == 0).",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 1,
                 f"method-unsupported sibling path does not force a retry: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"method-limited unit has no pending retry: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {},
                 f"method limitations are not counted as retry-weak: {doc['summary']}")
    return bad


def test_campaign_does_not_retry_witness_preflight_refusals():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("peer182__recursive__f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("peer182__recursive__f", "ok", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "peer182",
                    "unit": "f",
                    "bucket": "NO-WITNESS-UNDECIDED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "empty_witness_verdict": "REFUSED",
                    "empty_witness_reason":
                    "target call closure reaches direct self-recursive function/helper "
                    "wrapper(s): SafeMath.div/2, SafeMath.sub/2",
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 0 and doc["summary"]["non_retryable"] == 1,
                 f"preflight refusal is separated from completed certification: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"preflight refusal does not consume the next ESBMC attempt: {doc['summary']}")
    bad += check(doc["summary"]["cert_non_retryable"] == {
        "witness preflight refused": 1,
    }, f"preflight refusal reason is counted: {doc['summary']}")
    return bad


def test_campaign_does_not_retry_named_obstacle_no_witness():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("stress243__obstacle__f", "stress243", "f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__obstacle__f", "ok",
                    benchmark="stress243", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "f",
                    "bucket": "NO-WITNESS-UNDECIDED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "empty_witness_verdict": "REFUSED",
                    "empty_witness_reason":
                    "2 of 2 claim(s) were named-obstacle paths. This is a "
                    "structural model/chain mismatch for the unit",
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 0 and doc["summary"]["non_retryable"] == 1,
                 f"named obstacle no-witness is separated from completed certification: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"named obstacle no-witness does not consume a retry: {doc['summary']}")
    bad += check(doc["summary"]["cert_non_retryable"] == {
        "named obstacle no witness": 1,
    }, f"named obstacle reason is counted separately: {doc['summary']}")
    return bad


def test_campaign_retries_path_coverage_no_claims_solver_gap():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("stress243__pathcovdefect__f", "stress243", "f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__pathcovdefect__f", "ok",
                    benchmark="stress243", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "f",
                    "bucket": "NO-WITNESS-UNKNOWN",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "path-coverage-no-claims-reached-solver",
                        "reason": "path coverage instrumentation emitted claims, but none reached the solver",
                    },
                    "generalise_progress": {
                        "stage": "started",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 0 and doc["summary"]["non_retryable"] == 0,
                 f"path coverage solver gap is not completed or non-retryable: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"path coverage solver gap stays in the retry queue: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "path coverage no claims reached solver": 1,
    }, f"path coverage solver gap reason is counted as weak: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "direct-enumeration-after-no-claims"
                 and quality.get("retry_probe_witnesses") == 0
                 and quality.get("retry_probe_ladder") is False,
                 f"path coverage retry disables probe enumeration: {quality}")
    bad += check(argv_value(argv, "--probe-witnesses") == "0"
                 and "--probe-ladder" not in argv
                 and "--probe-ladder-budget" not in argv,
                 f"path coverage retry argv disables probes: {argv}")
    return bad


def test_campaign_retries_focus_function_matched_none_with_whole_scope():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("real203__focusnone__decimals", "real203", "decimals"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("real203__focusnone__decimals", "ok",
                    benchmark="real203", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "real203",
                    "unit": "decimals",
                    "bucket": "DRIVER-REFUSED",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "focus-function-matched-none",
                        "reason": "ESBMC accepted the function name at frontend validation but path coverage enumerated no unit for it",
                        "focus_function": "decimals",
                        "available_units": [
                            "constructor", "totalSupply", "balanceOf"
                        ],
                        "available_unit_count": 3,
                    },
                    "generalise_progress": {
                        "stage": "started",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    next_job = doc["next_schedule"]["jobs"][0]
    quality = next_job.get("certification_quality") or {}
    argv = next_job["certify_argv"]
    bad = 0
    bad += check(doc["summary"]["completed_ok"] == 0 and doc["summary"]["non_retryable"] == 0,
                 f"matched-none driver refusal is not completed or non-retryable: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {"2": 1},
                 f"matched-none driver refusal stays in the retry queue: {doc['summary']}")
    bad += check(doc["summary"]["cert_weak"] == {
        "focus function matched no path-coverage unit": 1,
    }, f"matched-none reason is counted as weak: {doc['summary']}")
    bad += check(quality.get("retry_strategy") == "whole-scope-after-focus-miss"
                 and quality.get("retry_scope") == "whole"
                 and quality.get("retry_observed_focus_function") == "decimals"
                 and quality.get("retry_observed_available_unit_count") == 3,
                 f"matched-none retry records focus miss details: {quality}")
    bad += check(argv_value(argv, "--scope") == "whole"
                 and argv_value(argv, "--probe-witnesses") == "0"
                 and "--probe-ladder" not in argv,
                 f"matched-none retry switches to whole-scope no-probe enumeration: {argv}")
    return bad


def test_campaign_does_not_retry_legacy_pre_enumeration_stop():
    with tempfile.TemporaryDirectory() as td:
        sched = write_json(
            Path(td) / "schedule.json", {
                "schema": "veriput-unit-schedule/v1",
                "summary": {
                    "jobs": 1,
                },
                "jobs": [
                    job("stress243__legacydefect__f", "stress243", "f"),
                ],
            })
        j1 = write_journal(
            Path(td) / "a1.jsonl", [
                row("stress243__legacydefect__f", "ok",
                    benchmark="stress243", campaign_attempt=1),
            ])
        cert = write_clean_jsonl(
            Path(td) / "cert.jsonl", [
                {
                    "benchmark": "stress243",
                    "unit": "f",
                    "bucket": "NO-WITNESS-UNKNOWN",
                    "witnessed": None,
                    "certified": {},
                    "not_certified": {},
                    "exit": 1,
                    "generalise_progress": {
                        "stage": "started",
                    },
                },
            ])
        doc = unit_campaign_plan.plan_campaign(str(sched),
                                               journal_paths=[str(j1)],
                                               cert_jsonl_paths=[str(cert)],
                                               min_certified_path_rate=0.70)
    bad = 0
    bad += check(doc["summary"]["non_retryable"] == 1,
                 f"legacy pre-enumeration stop is non-retryable: {doc['summary']}")
    bad += check(doc["summary"]["pending_by_attempt"] == {},
                 f"legacy pre-enumeration stop does not consume a retry: {doc['summary']}")
    bad += check(doc["summary"]["cert_non_retryable"] == {
        "driver stopped before enumeration": 1,
    }, f"legacy pre-enumeration reason is counted: {doc['summary']}")
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


def test_campaign_can_emit_limited_round_robin_next_schedule():
    with tempfile.TemporaryDirectory() as td:
        sched_doc = {
            "schema":
            "veriput-unit-schedule/v1",
            "summary": {
                "jobs": 5,
            },
            "jobs": [
                job("peer182__p1__f", "peer182", "f"),
                job("peer182__p2__g", "peer182", "g"),
                job("bugfix124__b1__h", "bugfix124", "h"),
                job("bugfix124__b2__i", "bugfix124", "i"),
                job("stress243__s1__j", "stress243", "j"),
            ],
        }
        for pos, item in enumerate(sched_doc["jobs"]):
            item["priority"] = 1
            item["ordinal"] = pos
        sched = write_json(Path(td) / "schedule.json", sched_doc)
        out = Path(td) / "next.json"
        doc = unit_campaign_plan.plan_campaign(
            str(sched),
            selection_strategy="round-robin-benchmark",
            limit=3,
            next_schedule_out=str(out))
        out_doc = json.loads(out.read_text())
    ids = [item["job_id"] for item in out_doc["jobs"]]
    ordinals = [item["ordinal"] for item in out_doc["jobs"]]
    bad = 0
    bad += check(ids == [
        "peer182__p1__f",
        "bugfix124__b1__h",
        "stress243__s1__j",
    ], f"limited next schedule is balanced across benchmarks: {ids}")
    bad += check(ordinals == [0, 1, 2],
                 f"ordinals are rewritten so the runner preserves balance: {ordinals}")
    bad += check(doc["summary"]["selected_jobs"] == 3
                 and doc["summary"]["selected_jobs_before_limit"] == 5,
                 f"limit affects selected jobs but records the denominator: {doc['summary']}")
    bad += check(doc["summary"]["selection_strategy"] == "round-robin-benchmark"
                 and doc["summary"]["selection_limit"] == 3,
                 f"selection policy is recorded: {doc['summary']}")
    bad += check(out_doc["summary"]["by_benchmark"] == {
        "bugfix124": 1,
        "peer182": 1,
        "stress243": 1,
    }, f"written schedule summary is balanced: {out_doc['summary']}")
    return bad


TESTS = [
    test_campaign_partitions_attempts_and_auto_selects_earliest,
    test_campaign_preserves_no_unit_audit_metadata_in_next_schedule,
    test_campaign_can_emit_attempt_three_schedule_and_runner_argv,
    test_campaign_cli_writes_plan_and_schedule,
    test_campaign_writes_empty_schedule_when_no_jobs_are_pending,
    test_campaign_counts_distinct_attempts_not_duplicate_rows,
    test_campaign_rewrites_retry_certification_out_by_attempt,
    test_campaign_uses_explicit_attempt_metadata_for_budget_state,
    test_campaign_retries_runner_ok_when_certification_is_weak,
    test_campaign_names_partial_journal_only_as_weak_certification,
    test_campaign_prefers_single_refine_for_refinement_timeouts,
    test_campaign_cheapens_probe_claim_explosion_retries,
    test_campaign_cheapens_probe_goal_cap_retries,
    test_campaign_deepens_tx_for_bounded_holds_no_witness,
    test_campaign_deepens_unwind_for_gated_unit_depth_obstacle,
    test_campaign_restores_default_refine_rounds_after_strategy_attempt,
    test_campaign_treats_slice_excluded_paths_as_body_slice_ready,
    test_campaign_treats_method_unsupported_paths_as_non_retryable,
    test_campaign_does_not_retry_witness_preflight_refusals,
    test_campaign_does_not_retry_named_obstacle_no_witness,
    test_campaign_retries_path_coverage_no_claims_solver_gap,
    test_campaign_retries_focus_function_matched_none_with_whole_scope,
    test_campaign_does_not_retry_legacy_pre_enumeration_stop,
    test_campaign_accepts_strong_certification_without_runner_journal,
    test_campaign_can_plan_from_in_memory_schedule,
    test_campaign_can_emit_limited_round_robin_next_schedule,
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
