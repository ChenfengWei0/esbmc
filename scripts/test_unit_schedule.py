#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_schedule  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def subject_record(unit=""):
    return {
        "schema": "veriput-subject/v1",
        "benchmark": "stress243",
        "subject_id": "repo__C",
        "benchmark_key": "stress243__repo__C",
        "root": "/tmp/repo__C",
        "flat_sol": "/tmp/repo__C/flat.sol",
        "solast": "/tmp/cache/stress243/stress243__repo__C/flat.sol.solast",
        "solast_source": "cache",
        "contract": "C",
        "unit": unit,
        "solc_bin": "/bin/false",
        "solc_bin_source": "explicit",
        "solc": "0.8.29",
        "inferred_solc_bin": None,
        "solc_extra": [],
        "meta_status": "ok",
    }


def manifest():
    return {
        "schema":
        "veriput-unit-manifest/v1",
        "benchmark":
        None,
        "target_manifest":
        "/tmp/targets.json",
        "generate_ast":
        False,
        "ast_cache_root":
        "/tmp/cache",
        "summary": {
            "subjects": 2,
            "ok": 1,
            "missing_ast": 1,
            "error": 0,
            "units": 2,
        },
        "subjects": [
            {
                "subject": subject_record(),
                "status": "ok",
                "target": {
                    "benchmark": "stress243",
                    "subject_id": "repo__C",
                    "contract": "C",
                    "units_hint": ["setX", "changedMissing"],
                },
                "unit_hints": {
                    "hinted_units": ["setX"],
                    "missing_unit_hints": ["changedMissing"],
                    "pending_unit_hints": [],
                },
                "units": {
                    "schema": "veriput-subject-units/v1",
                    "contract": "C",
                    "units": ["getX", "setX"],
                    "skipped": [],
                },
            },
            {
                "subject": subject_record(),
                "status": "missing-ast",
                "reason": "flat.sol.solast does not exist",
            },
        ],
    }


def test_schedule_prioritizes_hinted_units_and_preserves_argv():
    doc = unit_schedule.build_schedule(manifest())
    bad = 0
    bad += check(doc["schema"] == "veriput-unit-schedule/v1",
                 f"schedule schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["jobs"] == 2, f"two unit jobs are emitted: {doc['summary']}")
    bad += check(doc["summary"]["skipped_by_status"] == {"missing-ast": 1},
                 f"non-ok rows are skipped explicitly: {doc['summary']}")
    jobs = doc["jobs"]
    hinted, enumerated = jobs[0], jobs[1]
    bad += check(hinted["unit"] == "setX" and hinted["priority"] == 0,
                 f"target-hinted unit is first: {jobs}")
    bad += check(enumerated["unit"] == "getX" and enumerated["priority_reason"] == "enumerated",
                 f"non-hinted unit remains scheduled: {jobs}")
    bad += check(hinted["subject"]["unit"] == "setX" and enumerated["subject"]["unit"] == "getX",
                 f"each job carries a concrete subject unit: {jobs}")
    argv = hinted["certify_argv"]
    bad += check("--subject-dir" in argv and "/tmp/repo__C" in argv,
                 f"certifier argv resolves the prepared subject: {argv}")
    bad += check("--subject-benchmark" in argv and "stress243" in argv,
                 f"certifier argv labels the benchmark: {argv}")
    bad += check("--unit" in argv and "setX" in argv, f"certifier argv selects the unit: {argv}")
    bad += check("--ast-cache-root" in argv and "/tmp/cache" in argv,
                 f"certifier argv preserves AST cache root: {argv}")
    bad += check("--recipe-version" in argv and "veriput-strong/7" in argv,
                 f"certifier argv uses the shared strong recipe: {argv}")
    bad += check("--skip-bracket" in argv and "--probe-ladder" in argv
                 and "--pin-agreed-state" in argv and "--slot-coords" in argv,
                 f"strong region controls are scheduled for benchmarks: {argv}")
    bad += check("--dry-run" not in argv and "--dry-run" in hinted["dry_run_argv"],
                 f"normal and dry-run argv are separate: {hinted}")
    bad += check(doc["recipe_version"] == "veriput-strong/7",
                 f"schedule records the recipe version: {doc.get('recipe_version')}")
    return bad


def test_schedule_cli_reads_stdin_and_applies_limit():
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule.py"),
            "-",
            "--limit",
            "1",
            "--cert-out",
            str(Path(td) / "results.jsonl"),
        ],
                            input=json.dumps(manifest()),
                            capture_output=True,
                            text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    job = doc["jobs"][0]
    bad = 0
    bad += check(doc["summary"]["jobs"] == 1, f"limit keeps one scheduled job: {doc['summary']}")
    bad += check(doc["summary"]["jobs_before_shard"] == 2,
                 f"pre-limit denominator is retained: {doc['summary']}")
    bad += check(job["unit"] == "setX" and job["priority"] == 0,
                 f"limit is applied after priority sorting: {job}")
    out_idx = job["certify_argv"].index("--out") if "--out" in job["certify_argv"] else -1
    bad += check(
        out_idx >= 0 and job["certify_argv"][out_idx + 1].endswith("results.jsonl"),
        f"cert output path is threaded into argv: {job['certify_argv']}")
    return bad


def test_schedule_deduplicates_prepared_subject_units():
    data = manifest()
    data["subjects"].append(json.loads(json.dumps(data["subjects"][0])))
    doc = unit_schedule.build_schedule(data)
    ids = [job["job_id"] for job in doc["jobs"]]
    bad = 0
    bad += check(ids == ["stress243__repo__C__setX", "stress243__repo__C__getX"],
                 f"duplicate subject units are scheduled once: {ids}")
    bad += check(doc["summary"]["duplicate_jobs"] == 2,
                 f"duplicate unit jobs are reported: {doc['summary']}")
    bad += check(doc["duplicate_jobs"][0]["unit"] in ("getX", "setX"),
                 f"duplicate sample names the unit: {doc['duplicate_jobs']}")
    return bad


def test_schedule_refuses_protected_write_paths():
    protected = "/home/samson/workspace/VeriPUT/Results/certify.jsonl"
    try:
        unit_schedule.build_schedule(manifest(), cert_out=protected)
    except unit_schedule.ScheduleError as exc:
        refused_cert = str(exc)
    else:
        refused_cert = ""
    data = manifest()
    data["ast_cache_root"] = "/home/samson/workspace/VeriPUT/Results/ast-cache"
    try:
        unit_schedule.build_schedule(data)
    except unit_schedule.ScheduleError as exc:
        refused_cache = str(exc)
    else:
        refused_cache = ""
    cp = subprocess.run([
        sys.executable,
        str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule.py"),
        "-",
        "--out",
        "/home/samson/workspace/VeriPUT/Results/unit-schedule.json",
    ],
                        input=json.dumps(manifest()),
                        capture_output=True,
                        text=True)
    bad = 0
    bad += check("--cert-out must not be under" in refused_cert,
                 f"protected cert output is refused: {refused_cert}")
    bad += check("--ast-cache-root must not be under" in refused_cache,
                 f"protected AST cache is refused: {refused_cache}")
    bad += check(cp.returncode != 0 and "--out must not be under" in cp.stderr,
                 f"protected unit schedule output is refused: {cp.stderr.strip()}")
    return bad


TESTS = [
    test_schedule_prioritizes_hinted_units_and_preserves_argv,
    test_schedule_cli_reads_stdin_and_applies_limit,
    test_schedule_deduplicates_prepared_subject_units,
    test_schedule_refuses_protected_write_paths,
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
