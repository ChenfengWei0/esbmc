#!/usr/bin/env python3
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

from veriput_readiness import build_readiness  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def fixture_manifest():
    return {
        "schema": "veriput-unit-manifest/v1",
        "generated_at": "2026-08-06T00:00:00+00:00",
        "target_manifest": "targets.json",
        "subjects": [
            {
                "status": "missing-ast",
                "subject": {
                    "benchmark": "bugfix124",
                    "subject_id": "case_a",
                    "contract": "C",
                    "solc_bin": "/opt/solc-0.8.29",
                    "solc_extra": ["--via-ir"],
                },
                "unit_hints": {
                    "hinted_units": [],
                    "missing_unit_hints": [],
                    "pending_unit_hints": ["changed"],
                },
            },
            {
                "status": "error",
                "reason": "/tmp/s is not a usable subject: status='compile-failed'",
                "subject": {
                    "benchmark": "stress243",
                    "subject_id": "stress_a",
                    "contract": "S",
                },
            },
            {
                "status": "ok",
                "subject": {
                    "benchmark": "peer182",
                    "subject_id": "peer_a",
                    "contract": "P",
                },
                "units": {
                    "units": ["f"],
                    "skipped": [],
                },
                "unit_hints": {
                    "hinted_units": ["f"],
                    "missing_unit_hints": ["g"],
                    "pending_unit_hints": [],
                },
            },
        ],
    }


def test_readiness_groups_status_errors_and_hints():
    report = build_readiness(fixture_manifest(), sample_limit=5)
    s = report["summary"]
    bad = 0
    bad += check(report["schema"] == "veriput-readiness/v1",
                 f"schema is stable: {report['schema']}")
    bad += check(s["status"] == {"error": 1, "missing-ast": 1, "ok": 1},
                 f"status totals are grouped: {s['status']}")
    bad += check(s["benchmarks"]["bugfix124"]["missing-ast"] == 1,
                 f"benchmark status is counted: {s['benchmarks']}")
    bad += check(s["prepared_errors"]["stress243"]
                 ["prepared-status:compile-failed"] == 1,
                 f"prepared error bucket is normalized: {s['prepared_errors']}")
    bad += check(s["hints"]["bugfix124"]["pending_unit_hints"] == 1,
                 f"pending hint is counted: {s['hints']}")
    bad += check(s["hints"]["peer182"]["missing_unit_hints"] == 1,
                 f"missing hint is counted after enumeration: {s['hints']}")
    bad += check(s["missing_ast_by_solc"]["bugfix124"]
                 ["solc-0.8.29 --via-ir"] == 1,
                 f"AST preheat solc bucket is recorded: {s['missing_ast_by_solc']}")
    bad += check(s["preheat"]["bugfix124"]["preheatable_missing_ast"] == 1,
                 f"preheatable missing AST is counted: {s['preheat']}")
    return bad


def main():
    tests = [test_readiness_groups_status_errors_and_hints]
    bad = 0
    for test in tests:
        print("---", test.__name__)
        bad += test()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
