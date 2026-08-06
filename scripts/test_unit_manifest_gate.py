#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_manifest_gate  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def subject(benchmark, sid, contract="C"):
    return {
        "schema": "veriput-subject/v1",
        "benchmark": benchmark,
        "subject_id": sid,
        "benchmark_key": f"{benchmark}__{sid}",
        "root": f"/tmp/{sid}",
        "flat_sol": f"/tmp/{sid}/flat.sol",
        "solast": f"/tmp/cache/{benchmark}/{benchmark}__{sid}/flat.sol.solast",
        "solast_source": "cache",
        "contract": contract,
        "unit": "",
        "solc_bin": "/bin/false",
        "solc_bin_source": "explicit",
        "solc_extra": [],
        "meta_status": "ok",
    }


def ok_row(benchmark, sid, units, *, hints=None):
    return {
        "subject": subject(benchmark, sid),
        "status": "ok",
        "unit_hints": hints or {
            "hinted_units": [],
            "missing_unit_hints": [],
            "pending_unit_hints": [],
        },
        "units": {
            "schema": "veriput-subject-units/v1",
            "contract": "C",
            "units": units,
            "skipped": [],
        },
    }


def manifest(rows):
    return {
        "schema": "veriput-unit-manifest/v1",
        "summary": {
            "subjects": len(rows),
        },
        "subjects": rows,
    }


def test_gate_blocks_missing_ast_and_pending_hints():
    doc = unit_manifest_gate.build_gate(
        manifest([
            {
                "subject": subject("peer182", "missing__C"),
                "status": "missing-ast",
                "reason": "flat.sol.solast does not exist",
                "unit_hints": {
                    "hinted_units": [],
                    "missing_unit_hints": [],
                    "pending_unit_hints": ["changed"],
                },
            },
            ok_row("peer182", "ok__C", ["f"]),
        ]))
    bad = 0
    bad += check(doc["gate_status"] == "blocked", f"missing AST blocks the gate: {doc}")
    bad += check("missing compact AST rows remain" in doc["blockers"],
                 f"missing AST blocker is explicit: {doc['blockers']}")
    bad += check("changed-function hints are still pending AST enumeration" in doc["blockers"],
                 f"pending hint blocker is explicit: {doc['blockers']}")
    bad += check(doc["summary"]["unique_unit_jobs"] == 1,
                 f"ok rows still contribute unit jobs: {doc['summary']}")
    return bad


def test_gate_degrades_on_errors_missing_hints_and_duplicates():
    rows = [
        ok_row("bugfix124",
               "repo__C", ["setX", "getX"],
               hints={
                   "hinted_units": ["setX"],
                   "missing_unit_hints": ["changedMissing"],
                   "pending_unit_hints": [],
               }),
        ok_row("bugfix124", "repo__C", ["setX", "getX"]),
        {
            "subject": subject("stress243", "bad__C"),
            "status": "error",
            "reason": "/tmp/bad__C is not a usable subject: status='compile-failed'",
        },
    ]
    doc = unit_manifest_gate.build_gate(manifest(rows))
    bad = 0
    bad += check(doc["gate_status"] == "degraded",
                 f"errors and missing hints degrade the gate: {doc}")
    bad += check(doc["summary"]["ready_for_unit_schedule"] is True,
                 f"degraded manifest can still schedule ok units: {doc['summary']}")
    bad += check(doc["summary"]["ready_for_full_denominator"] is False,
                 f"degraded manifest is not a full denominator: {doc['summary']}")
    bad += check(doc["summary"]["duplicate_unit_jobs"] == 2,
                 f"duplicate unit jobs are detected: {doc['summary']}")
    bad += check(doc["summary"]["duplicate_subject_rows"] == 1,
                 f"duplicate subject row is detected: {doc['summary']}")
    bad += check(doc["summary"]["errors"]["stress243"] == {"prepared-status:compile-failed": 1},
                 f"prepared error bucket is normalized: {doc['summary']['errors']}")
    return bad


def test_gate_cli_reads_stdin():
    with tempfile.TemporaryDirectory():
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_manifest_gate.py"),
            "-",
        ],
                            input=json.dumps(manifest([ok_row("peer182", "ok__C", ["f"])])),
                            capture_output=True,
                            text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    bad = 0
    bad += check(doc["gate_status"] == "ready", f"single ok row is ready: {doc}")
    bad += check(doc["summary"]["unique_unit_jobs"] == 1,
                 f"CLI reports one unit job: {doc['summary']}")
    return bad


TESTS = [
    test_gate_blocks_missing_ast_and_pending_hints,
    test_gate_degrades_on_errors_missing_hints_and_duplicates,
    test_gate_cli_reads_stdin,
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
