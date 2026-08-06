#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import ast_preheat_schedule  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def subject(benchmark, sid, root, **overrides):
    record = {
        "schema": "veriput-subject/v1",
        "benchmark": benchmark,
        "subject_id": sid,
        "benchmark_key": f"{benchmark}__{sid}",
        "root": str(Path(root) / sid),
        "flat_sol": str(Path(root) / sid / "flat.sol"),
        "solast": str(Path(root) / sid / "flat.sol.solast"),
        "solast_source": "prepared",
        "contract": "C",
        "unit": "",
        "solc_bin": "/bin/false",
        "solc_bin_source": "explicit",
        "solc": "0.8.29",
        "inferred_solc_bin": None,
        "solc_extra": [],
        "meta_status": "ok",
    }
    record.update(overrides)
    return record


def manifest(root="/tmp/preheat-root", ast_cache_root="/tmp/ast-cache"):
    return {
        "schema":
        "veriput-unit-manifest/v1",
        "benchmark":
        None,
        "target_manifest":
        "/tmp/targets.json",
        "generate_ast":
        False,
        "ast_timeout_s":
        60.0,
        "ast_cache_root":
        ast_cache_root,
        "summary": {
            "subjects": 4,
            "ok": 1,
            "missing_ast": 3,
            "error": 0,
            "units": 1,
        },
        "subjects": [
            {
                "subject": subject("stress243", "stress__C", root),
                "status": "missing-ast",
                "reason": "flat.sol.solast does not exist",
            },
            {
                "subject":
                subject("peer182",
                        "peer__C",
                        root,
                        solc_bin=None,
                        solc_bin_source="inferred",
                        inferred_solc_bin="/opt/solc-0.8.29"),
                "status":
                "missing-ast",
                "reason":
                "flat.sol.solast does not exist",
            },
            {
                "subject":
                subject("bugfix124", "bug__C", root, solc_bin=None, solc_bin_source="missing"),
                "status":
                "missing-ast",
                "reason":
                "flat.sol.solast does not exist",
            },
            {
                "subject": subject("peer182", "done__C", root),
                "status": "ok",
                "units": {
                    "schema": "veriput-subject-units/v1",
                    "contract": "C",
                    "units": ["f"],
                    "skipped": [],
                },
            },
        ],
    }


def test_preheat_schedule_distinguishes_explicit_and_inferred_solc():
    doc = ast_preheat_schedule.build_schedule(manifest(), ast_timeout=7.5)
    jobs = doc["jobs"]
    bad = 0
    bad += check(doc["schema"] == "veriput-ast-preheat-schedule/v1",
                 f"schedule schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["jobs"] == 2,
                 f"two preheatable rows are scheduled: {doc['summary']}")
    bad += check(doc["summary"]["unschedulable"] == 1,
                 f"missing solc path is not scheduled: {doc['summary']}")
    bad += check(doc["summary"]["skipped_by_status"] == {"ok": 1},
                 f"non-missing rows are skipped: {doc['summary']}")
    peer, stress = jobs
    bad += check(peer["benchmark"] == "peer182", f"peer rows are prioritized before stress: {jobs}")
    bad += check(
        peer["solc_source"] == "inferred" and "--use-inferred-solc-bin" in peer["preheat_argv"],
        f"inferred solc job opts in explicitly: {peer}")
    bad += check(
        stress["solc_source"] == "explicit"
        and "--use-inferred-solc-bin" not in stress["preheat_argv"],
        f"explicit solc job does not opt into inference: {stress}")
    argv = stress["preheat_argv"]
    bad += check("--subject-root" in argv and "/tmp/preheat-root" in argv,
                 f"job points at the subject population root: {argv}")
    bad += check("--ast-cache-root" in argv and "/tmp/ast-cache" in argv,
                 f"job writes only to the external AST cache: {argv}")
    bad += check("--generate-ast" in argv and "--ast-timeout" in argv and "7.5" in argv,
                 f"job is an AST preheat invocation: {argv}")
    bad += check("--generate-ast" not in stress["inspect_argv"],
                 f"inspect argv remains non-mutating: {stress['inspect_argv']}")
    return bad


def test_preheat_schedule_refuses_prepared_subject_writes_without_cache():
    try:
        ast_preheat_schedule.build_schedule(manifest(ast_cache_root=""))
    except ast_preheat_schedule.PreheatScheduleError as exc:
        return check("refusing to schedule prepared-subject writes" in str(exc),
                     f"missing AST cache root fails closed: {exc}")
    print("FAIL: missing AST cache root was accepted")
    return 1


def test_preheat_schedule_deduplicates_prepared_subjects():
    data = manifest()
    data["subjects"].append({
        "subject": subject("stress243", "stress__C", "/tmp/preheat-root"),
        "status": "missing-ast",
        "reason": "flat.sol.solast does not exist",
    })
    doc = ast_preheat_schedule.build_schedule(data)
    ids = [job["job_id"] for job in doc["jobs"]]
    bad = 0
    bad += check(
        ids.count("stress243__stress__C") == 1,
        f"duplicate prepared subject is scheduled once: {ids}")
    bad += check(doc["summary"]["duplicate_rows"] == 1,
                 f"duplicate row is reported: {doc['summary']}")
    bad += check(doc["duplicate_rows"][0]["subject"]["subject_id"] == "stress__C",
                 f"duplicate sample keeps subject identity: {doc['duplicate_rows']}")
    return bad


def test_preheat_schedule_cli_reads_stdin_and_overrides_cache():
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "ast_preheat_schedule.py"),
            "-",
            "--ast-cache-root",
            str(Path(td) / "cache"),
            "--limit",
            "1",
        ],
                            input=json.dumps(manifest(ast_cache_root="")),
                            capture_output=True,
                            text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    job = doc["jobs"][0]
    bad = 0
    bad += check(doc["summary"]["jobs"] == 1, f"CLI limit keeps one preheat job: {doc['summary']}")
    bad += check(doc["summary"]["jobs_before_shard"] == 2,
                 f"pre-limit denominator is retained: {doc['summary']}")
    bad += check(
        job["preheat_argv"][job["preheat_argv"].index("--ast-cache-root") + 1].endswith("/cache"),
        f"CLI override supplies external cache root: {job}")
    return bad


TESTS = [
    test_preheat_schedule_distinguishes_explicit_and_inferred_solc,
    test_preheat_schedule_refuses_prepared_subject_writes_without_cache,
    test_preheat_schedule_deduplicates_prepared_subjects,
    test_preheat_schedule_cli_reads_stdin_and_overrides_cache,
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
