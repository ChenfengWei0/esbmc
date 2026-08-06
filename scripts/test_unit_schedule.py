#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_schedule  # noqa: E402
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
    bad += check("--recipe-version" in argv
                 and veriput_recipe.STRONG_RECIPE_VERSION in argv,
                 f"certifier argv uses the shared strong recipe: {argv}")
    bad += check("--skip-bracket" in argv and "--probe-ladder" in argv
                 and "--pin-agreed-establishable-env" in argv
                 and "--pin-agreed-state" in argv and "--slot-coords" in argv,
                 f"strong region controls are scheduled for benchmarks: {argv}")
    bad += check("--no-auto-pin-value" not in argv,
                 f"main benchmark recipe keeps the nonpayable body slice cheap: {argv}")
    bad += check(argv_value(argv, "--timeout") == "60"
                 and argv_value(argv, "--run-timeout") == "60"
                 and argv_value(argv, "--memlimit-gib") == "8",
                 f"schedule embeds the first-attempt certify budget: {argv}")
    default_workdir = f"/tmp/certify_all/{DEFAULT_RECIPE_DIR}/t60_r60_m8"
    bad += check(argv_value(argv, "--workdir") == default_workdir,
                 f"schedule embeds a recipe/budget-specific scratch root: {argv}")
    bad += check(hinted["certification_budget"] == {
        "timeout_s": 60,
        "run_timeout_s": 60,
        "memlimit_gib": 8,
        "workdir": default_workdir,
    }, f"job records the embedded certify budget: {hinted['certification_budget']}")
    bad += check("--dry-run" not in argv and "--dry-run" in hinted["dry_run_argv"],
                 f"normal and dry-run argv are separate: {hinted}")
    bad += check(argv_value(hinted["dry_run_argv"], "--timeout") == "60",
                 f"dry-run argv carries the same budget: {hinted['dry_run_argv']}")
    bad += check(doc["recipe_version"] == veriput_recipe.STRONG_RECIPE_VERSION,
                 f"schedule records the recipe version: {doc.get('recipe_version')}")
    bad += check(doc["certification_budget"] == {
        "timeout_s": 60,
        "run_timeout_s": 60,
        "memlimit_gib": 8,
        "workdir": default_workdir,
    }, f"schedule records the default certification budget: {doc['certification_budget']}")
    return bad


def test_schedule_prioritizes_semantic_units_before_getter_like_units():
    data = manifest()
    row = data["subjects"][0]
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["target"]["units_hint"] = []
    row["units"]["units"] = ["name", "setX", "quote"]
    row["units"]["unit_info"] = [
        {
            "name": "name",
            "state_mutability": "view",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "setX",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "quote",
            "state_mutability": "pure",
            "parameter_count": 1,
            "return_count": 1,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["priority"], job["priority_reason"])
           for job in doc["jobs"]]
    bad = 0
    bad += check(got == [("setX", 1, "state-changing"),
                         ("quote", 2, "pure/view-with-interface"),
                         ("name", 3, "zero-arg-view")],
                 f"semantic units are sampled before getter-like rows: {got}")
    bad += check(doc["jobs"][0]["unit_info"]["parameter_count"] == 1,
                 f"job keeps unit metadata for later audit: {doc['jobs'][0]}")
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
            "--timeout",
            "120",
            "--run-timeout",
            "30",
            "--memlimit-gib",
            "10",
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
    bad += check(argv_value(job["certify_argv"], "--timeout") == "120"
                 and argv_value(job["certify_argv"], "--run-timeout") == "30"
                 and argv_value(job["certify_argv"], "--memlimit-gib") == "10",
                 f"CLI budget flags are threaded into certify argv: {job['certify_argv']}")
    bad += check(argv_value(job["certify_argv"], "--workdir")
                 == str(Path(td) / "certify-work-t120_r30_m10"),
                 f"CLI cert-out derives an isolated workdir: {job['certify_argv']}")
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


def test_schedule_can_round_robin_across_benchmarks():
    data = manifest()
    rows = []
    for bench, subject_id, units in [
            ("bugfix124", "bug__A", ["b0", "b1", "b2"]),
            ("peer182", "peer__B", ["p0", "p1", "p2"]),
            ("stress243", "stress__C", ["s0", "s1", "s2"]),
    ]:
        subject = subject_record()
        subject["benchmark"] = bench
        subject["subject_id"] = subject_id
        subject["benchmark_key"] = f"{bench}__{subject_id}"
        subject["root"] = f"/tmp/{subject_id}"
        subject["contract"] = subject_id.rsplit("__", 1)[-1]
        rows.append({
            "subject": subject,
            "status": "ok",
            "target": {
                "benchmark": bench,
                "subject_id": subject_id,
                "contract": subject["contract"],
                "units_hint": units if bench == "bugfix124" else [],
            },
            "unit_hints": {
                "hinted_units": units if bench == "bugfix124" else [],
                "missing_unit_hints": [],
                "pending_unit_hints": [],
            },
            "units": {
                "schema": "veriput-subject-units/v1",
                "contract": subject["contract"],
                "units": units,
                "skipped": [],
            },
        })
    data["subjects"] = rows
    default_doc = unit_schedule.build_schedule(data, limit=3)
    rr_doc = unit_schedule.build_schedule(
        data, limit=6, selection_strategy="round-robin-benchmark")
    got_default = [job["benchmark"] for job in default_doc["jobs"]]
    got_rr = [(job["benchmark"], job["unit"]) for job in rr_doc["jobs"]]
    bad = 0
    bad += check(got_default == ["bugfix124", "bugfix124", "bugfix124"],
                 f"default priority sampling stays unchanged: {got_default}")
    bad += check(got_rr == [
        ("bugfix124", "b0"),
        ("peer182", "p0"),
        ("stress243", "s0"),
        ("bugfix124", "b1"),
        ("peer182", "p1"),
        ("stress243", "s1"),
    ], f"round-robin sampling balances benchmarks after priority sorting: {got_rr}")
    bad += check(rr_doc["selection_strategy"] == "round-robin-benchmark",
                 f"schedule records the selection strategy: {rr_doc.get('selection_strategy')}")
    bad += check(rr_doc["summary"]["by_benchmark"] == {
        "bugfix124": 2,
        "peer182": 2,
        "stress243": 2,
    }, f"round-robin summary is balanced: {rr_doc['summary']}")
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
    try:
        unit_schedule.build_schedule(
            manifest(), workdir="/home/samson/workspace/VeriPUT/Results/work")
    except unit_schedule.ScheduleError as exc:
        refused_work = str(exc)
    else:
        refused_work = ""
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
    bad += check("--workdir must not be under" in refused_work,
                 f"protected workdir is refused: {refused_work}")
    bad += check(cp.returncode != 0 and "--out must not be under" in cp.stderr,
                 f"protected unit schedule output is refused: {cp.stderr.strip()}")
    return bad


TESTS = [
    test_schedule_prioritizes_hinted_units_and_preserves_argv,
    test_schedule_prioritizes_semantic_units_before_getter_like_units,
    test_schedule_cli_reads_stdin_and_applies_limit,
    test_schedule_deduplicates_prepared_subject_units,
    test_schedule_can_round_robin_across_benchmarks,
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
