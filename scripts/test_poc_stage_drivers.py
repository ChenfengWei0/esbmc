#!/usr/bin/env python3
import json
import subprocess
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


def compact_ast(include_receive=False):
    nodes = [{
        "nodeType": "FunctionDefinition",
        "kind": "function",
        "name": "set",
        "visibility": "external",
    }]
    if include_receive:
        nodes.append({
            "nodeType": "FunctionDefinition",
            "kind": "receive",
            "name": "",
            "visibility": "external",
        })
    return {
        "nodeType": "SourceUnit",
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 1,
            "name": "C",
            "contractKind": "contract",
            "linearizedBaseContracts": [1],
            "nodes": nodes,
        }],
    }


def write_subject(tmp):
    d = Path(tmp) / "repo__C"
    d.mkdir()
    (d / "flat.sol").write_text("contract C { function set() external {} }\n")
    (d / "meta.json").write_text(json.dumps({
        "subject_id": "repo__C",
        "benchmark": "stress243",
        "contract": "C",
        "status": "ok",
        "solc_bin": "/bin/false",
    }) + "\n")
    return d


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


def test_certify_all_lists_subject_units_from_ast_cache():
    with tempfile.TemporaryDirectory() as td:
        subject = write_subject(td)
        prepared_ast = subject / "flat.sol.solast"
        cache = Path(td) / "cache"
        cached_ast = cache / "stress243" / "stress243__repo__C" \
            / "flat.sol.solast"
        cached_ast.parent.mkdir(parents=True)
        cached_ast.write_text(json.dumps(compact_ast()) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "certify_all.py"),
            "--subject-dir", str(subject),
            "--subject-benchmark", "stress243",
            "--unit", "set",
            "--ast-cache-root", str(cache),
            "--list-subject-units",
            "--dry-run",
        ], capture_output=True, text=True)
        prepared_exists = prepared_ast.exists()
    bad = 0
    bad += check(cp.returncode == 0,
                 f"certify_all cache list-units exits cleanly: {cp.stderr}")
    bad += check("subject units: set" in cp.stdout,
                 f"cache AST supplies unit list: {cp.stdout}")
    bad += check(str(cached_ast.resolve()) in cp.stdout,
                 f"dry-run names the cache AST path: {cp.stdout}")
    bad += check(not prepared_exists,
                 "prepared subject AST was not written")
    return bad


def test_certify_all_preflights_bad_prepared_focus_without_esbmc():
    with tempfile.TemporaryDirectory() as td:
        subject = write_subject(td)
        (subject / "flat.sol.solast").write_text(
            json.dumps(compact_ast(include_receive=True)) + "\n")
        out = Path(td) / "certify-results.jsonl"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "certify_all.py"),
            "--subject-dir", str(subject),
            "--subject-benchmark", "stress243",
            "--unit", "receive",
            "--out", str(out),
            "--workdir", str(Path(td) / "work"),
            "--memlimit-gib", "4",
            "--timeout", "1",
            "--run-timeout", "1",
        ], capture_output=True, text=True)
        rows = [json.loads(line) for line in out.read_text().splitlines()
                if line.strip()] if out.exists() else []
    row = rows[0] if rows else {}
    diag = row.get("driver_diagnostic") or {}
    bad = 0
    bad += check(cp.returncode == 0,
                 f"bad focus is recorded, not treated as a CLI error: {cp.stderr}")
    bad += check(row.get("bucket") == "DRIVER-REFUSED",
                 f"bad focus is a driver-refused row: {row}")
    bad += check(diag.get("preflight") == "prepared-subject-ast"
                 and diag.get("focus_function") == "receive",
                 f"preflight diagnostic names the skipped unit: {diag}")
    bad += check(diag.get("available_units") == ["set"],
                 f"diagnostic keeps available focus names: {diag}")
    bad += check("DRIVER-REFUSED" in cp.stdout,
                 f"stdout reports the cheap refusal: {cp.stdout}")
    return bad


def test_poc_one_materializes_declared_fixture():
    poc = {
        "id": "bench__C__set",
        "harness_contract": "C",
        "fixtures": {
            "gate": {
                "why": "owner setter",
                "skip_constructor": True,
                "foundry": {"constructor_args": ["address(uint160(7))"]},
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
        "foundry": {"constructor_args": ["address(uint160(7))"]},
        "skip_constructor": True,
        "state": {"_owner": "1"},
    }, f"fixture JSON carries verifier and replay metadata: {data}")
    bad += check(args == ["--path-cov-fixture", str(path)],
                 f"fixture is passed as two ESBMC argv tokens: {args}")
    bad += check(why == "owner setter", f"fixture rationale is returned: {why}")
    return bad


def test_poc_one_can_disable_stage1_probe_witnesses_for_a_cell():
    poc = {
        "id": "bench__C__set",
        "cells": {"gate": {"probe_witnesses": 0}},
    }
    bad = 0
    bad += check(poc_one.cell_probe_witnesses(poc, "gate") == 0,
                 "cell-level probe_witnesses=0 is accepted")
    args = poc_one.strong_certify_args(0)
    bad += check(args[args.index("--probe-witnesses") + 1] == "0",
                 f"stage2 sees the same probe witness count: {args}")
    bad += check("--probe-ladder" not in args,
                 "per-path ladder is disabled without probe witnesses")
    bad += check("--probe-ladder-budget" not in args,
                 "probe ladder budget is omitted with the ladder")
    poc["cells"]["gate"]["probe_witnesses"] = "3"
    bad += check(poc_one.cell_probe_witnesses(poc, "gate") == 3,
                 "string values from JSON are parsed")
    return bad


def main():
    tests = [
        test_poc_enumeration_index_supplies_one_unit,
        test_poc_enumeration_index_is_fail_closed,
        test_certify_all_lists_subject_units_from_ast_cache,
        test_certify_all_preflights_bad_prepared_focus_without_esbmc,
        test_poc_one_materializes_declared_fixture,
        test_poc_one_can_disable_stage1_probe_witnesses_for_a_cell,
    ]
    bad = 0
    for t in tests:
        print("---", t.__name__)
        bad += t()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
