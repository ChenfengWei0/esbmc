#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import certify_all  # noqa: E402
import poc_one  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def write_index(tmp, **overrides):
    data = {
        "schema": "veriput-pathcov-collection/2",
        "benchmark": "st1inch_St1inch",
        "primary": {"name": "St1inch", "kind": "contract"},
        "config": {"onlyUnits": ["setEmergencyExit"]},
    }
    data.update(overrides)
    p = Path(tmp) / "index.json"
    p.write_text(json.dumps(data) + "\n")
    return p


def test_poc_enumeration_index_supplies_one_unit():
    with tempfile.TemporaryDirectory() as td:
        idx = write_index(td)
        got, why = certify_all.units_from_enumeration_index(
            str(idx), "st1inch_St1inch", {"setEmergencyExit"})
    bad = 0
    bad += check(got == ([("St1inch", "setEmergencyExit")], []),
                 f"POC index supplies the requested unit: {got}")
    bad += check("forge_roundtrip/emit.jsonl is not required" in why,
                 f"reason explains the POC-only authority: {why}")
    return bad


def test_poc_enumeration_index_is_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        idx = write_index(td, benchmark="farming")
        got, why = certify_all.units_from_enumeration_index(
            str(idx), "st1inch_St1inch", {"setEmergencyExit"})
    bad = 0
    bad += check(got is None, f"mismatched benchmark is refused: {got}")
    bad += check("not 'st1inch_St1inch'" in why,
                 f"mismatch reason names the target: {why}")
    with tempfile.TemporaryDirectory() as td:
        idx = write_index(td)
        got, why = certify_all.units_from_enumeration_index(
            str(idx), "st1inch_St1inch", {"setEmergencyExit", "setFeeReceiver"})
    bad += check(got is None, f"multi-unit fallback is refused: {got}")
    bad += check("exactly one --unit" in why,
                 f"multi-unit reason is explicit: {why}")
    return bad


def test_poc_one_materializes_declared_fixture():
    poc = {
        "id": "bench__C__set",
        "harness_contract": "C",
        "fixtures": {
            "gate": {
                "why": "owner setter",
                "skip_constructor": True,
                "state": {"_owner": "1"},
            }
        },
    }
    with tempfile.TemporaryDirectory() as td:
        path, args, why = poc_one.materialize_fixture(poc, "gate", Path(td))
        data = json.loads(path.read_text())
    bad = 0
    bad += check(data == {
        "contract": "C",
        "skip_constructor": True,
        "state": {"_owner": "1"},
    }, f"fixture JSON is minimal and explicit: {data}")
    bad += check(args == ["--path-cov-fixture", str(path)],
                 f"fixture is passed as two ESBMC argv tokens: {args}")
    bad += check(why == "owner setter", f"fixture rationale is returned: {why}")
    return bad


def main():
    tests = [
        test_poc_enumeration_index_supplies_one_unit,
        test_poc_enumeration_index_is_fail_closed,
        test_poc_one_materializes_declared_fixture,
    ]
    bad = 0
    for t in tests:
        print("---", t.__name__)
        bad += t()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
