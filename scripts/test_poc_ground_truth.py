#!/usr/bin/env python3
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import poc_ground_truth  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def write_fixture(tmp):
    poc_dir = tmp / "poc"
    put_root = tmp / "put"
    cert = tmp / "cert.jsonl"
    poc_dir.mkdir()
    (poc_dir / "P01.sol").write_text("""// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: simple setter.
/// EXPECTED: x generalises to [1, 9].
/// The generated PUT must keep a post-state assertion.
///
/// WHAT WOULD BE A FAILURE: no oracle.
contract P01 {
    uint256 public y;
    function set(uint256 x) external {
        require(x > 0 && x < 10);
        y = x;
    }
}
""")
    cert.write_text(json.dumps({
        "unit": "set",
        "path_function": "sol:@C@P01@F@set#1",
        "bucket": "CERTIFIED",
        "witnessed": 1,
        "coords": ["x"],
        "pins": "{'msg.value': 0}",
        "certified": {
            "6": "x in [1, 9], msg.value == 0",
        },
        "not_certified": {},
        "unit_timeout_s": 60,
        "memlimit_gib": 8,
    }) + "\n")
    put_dir = put_root / "P01__set__6"
    put_dir.mkdir(parents=True)
    (put_dir / "put.json").write_text(json.dumps({
        "contract": "P01",
        "unit": "set",
        "path_function": "sol:@C@P01@F@set#1",
        "enc": 6,
        "depth": 1,
        "test": "test_put_P01_set_path6",
        "file": "/tmp/P01.t.sol",
        "region": {
            "x": ["1", "9"],
        },
        "holes": {},
        "pins": {
            "msg.value": "0",
        },
        "stats": {
            "fuzz_params": 1,
            "lifted": ["x"],
            "asserts": 1,
        },
    }) + "\n")
    return poc_dir, put_root, cert


def test_inventory_reads_sources_cert_and_puts_without_execution():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        poc_dir, put_root, cert = write_fixture(tmp)
        args = argparse.Namespace(poc_dir=str(poc_dir),
                                  put_root=str(put_root),
                                  cert_jsonl=[str(cert)],
                                  contract=[],
                                  unit=[],
                                  poc=[],
                                  only=[],
                                  max_expected_lines=8,
                                  limit=20,
                                  format="json",
                                  out="")
        doc = poc_ground_truth.build_inventory(args)
        bad = 0
        bad += check(doc["read_only"] is True and not doc["execution"]["runs_esbmc"],
                     f"inventory is explicitly read-only: {doc['execution']}")
        bad += check(doc["summary"]["poc_sources"] == 1
                     and doc["summary"]["sources_with_expected"] == 1,
                     f"source expectations are counted: {doc['summary']}")
        bad += check(doc["summary"]["cert_rows"] == 1
                     and doc["summary"]["put_rows"] == 1
                     and doc["summary"]["strong_shape_puts"] == 1,
                     f"artefact rows are counted: {doc['summary']}")
        bad += check(doc["summary"]["unit_status"] == {"ready-strong": 1},
                     f"unit status bucket is counted: {doc['summary']}")
        unit = doc["units"][0]
        bad += check(unit["contract"] == "P01"
                     and unit["unit"] == "set"
                     and unit["certifications"][0]["certified_paths"] == ["6"],
                     f"certification row is attached to source contract: {unit}")
        bad += check(unit["ground_truth_status"] == "ready-strong",
                     f"unit is classified as ready-strong: {unit}")
        bad += check(unit["source"]["expected_blocks"][0]["text"][0]
                     == "EXPECTED: x generalises to [1, 9].",
                     f"EXPECTED block is extracted: {unit['source']['expected_blocks']}")
        bad += check(unit["put_summary"]["strong_shape"] == 1
                     and unit["put_summary"]["weak_reasons"] == {}
                     and unit["puts"][0]["wide_region"] is True,
                     f"PUT strength shape is summarized: {unit['put_summary']}")
        bad += check(unit["puts"][0]["wide_fuzz_coords"] == ["x"]
                     and unit["puts"][0]["wide_fuzz"] is True,
                     f"wide rendered fuzz coordinates are recorded: "
                     f"{unit['puts'][0]}")
        bad += check(not (tmp / "out.json").exists(),
                     "no output file is written without --out")
        return bad


def test_inventory_filters_and_reports_weak_reasons():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        poc_dir, put_root, cert = write_fixture(tmp)
        weak_dir = put_root / "P01__set__7"
        weak_dir.mkdir()
        (weak_dir / "put.json").write_text(json.dumps({
            "contract": "P01",
            "unit": "set",
            "path_function": "sol:@C@P01@F@set#1",
            "enc": 7,
            "depth": 1,
            "ladder_refusal": "region coordinate refused",
            "notes": [
                "coordinate `b` has type `bool`, which this emitter cannot bound",
            ],
            "region": {
                "x": ["5", "5"],
            },
            "stats": {
                "fuzz_params": 0,
                "asserts": 0,
                "oracle_skipped": [
                    "state.x (no storage slot)",
                ],
                "state_skipped": [
                    "state.y in [0, 9] (width > 1, DROPPED)",
                ],
            },
        }) + "\n")
        args = argparse.Namespace(poc_dir=str(poc_dir),
                                  put_root=str(put_root),
                                  cert_jsonl=[str(cert)],
                                  contract=[],
                                  unit=[],
                                  poc=[],
                                  only=["P01.set"],
                                  max_expected_lines=8,
                                  limit=20,
                                  format="json",
                                  out="")
        doc = poc_ground_truth.build_inventory(args)
        unit = doc["units"][0]
        bad = 0
        bad += check(doc["summary"]["unit_rows"] == 1
                     and doc["summary"]["filtered_put_rows"] == 2,
                     f"Contract.unit filter keeps the requested unit: {doc['summary']}")
        bad += check(unit["put_summary"]["weak_reasons"] == {
            "no-fuzz-params": 1,
            "no-oracle": 1,
            "no-wide-region": 1,
        }, f"weak PUT reasons are bucketed: {unit['put_summary']}")
        bad += check(unit["put_summary"]["weak_details"] == {
            "no-fuzz-params": 1,
            "no-fuzz:coordinate `b` has type `bool`, which this emitter cannot bound": 1,
            "no-fuzz:state-skipped:state.y in [0, 9] (width > 1, DROPPED)": 1,
            "no-oracle:ladder-refusal:region coordinate refused": 1,
            "no-oracle:state.x (no storage slot)": 1,
            "no-wide-region": 1,
        }, f"weak PUT details preserve oracle causes: {unit['put_summary']}")
        bad += check(unit["put_summary"]["weak_detail_tags"] == {
            "no-fuzz-params": 1,
            "no-fuzz:stale-bool-unliftable-note": 1,
            "no-fuzz:state-coordinate-dropped": 1,
            "no-oracle:ladder-refusal": 1,
            "no-oracle:no-storage-slot": 1,
            "no-wide-region": 1,
        }, f"weak PUT detail tags are bucketed: {unit['put_summary']}")
        bad += check(unit["put_summary"]["strong_shape"] == 1,
                     f"strong PUTs stay counted separately: {unit['put_summary']}")
        bad += check(doc["summary"]["unit_status"] == {"ready-strong": 1}
                     and unit["ground_truth_status"] == "ready-strong",
                     f"weak extra PUT does not hide ready strong coverage: {unit}")
        args.only = ["P01.missing"]
        empty = poc_ground_truth.build_inventory(args)
        bad += check(empty["summary"]["unit_rows"] == 0
                     and empty["summary"]["filtered_out_unit_rows"] == 1,
                     f"non-matching filters produce an empty unit set: {empty['summary']}")
        return bad


def test_inventory_default_roots_include_poc_local_puts():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        poc_dir, old_put_root, cert = write_fixture(tmp)
        poc_units = tmp / "poc_units"
        local_dir = poc_units / "P01__P01__set" / "put_gate" / "_wd" / "P01__set__9"
        local_dir.mkdir(parents=True)
        (local_dir / "put.json").write_text(json.dumps({
            "contract": "P01",
            "unit": "set",
            "path_function": "sol:@C@P01@F@set#1",
            "enc": 9,
            "depth": 1,
            "test": "test_put_P01_set_path9",
            "file": "/tmp/P01_9.t.sol",
            "region": {
                "x": ["2", "8"],
            },
            "stats": {
                "fuzz_params": 1,
                "lifted": ["x"],
                "asserts": 1,
            },
        }) + "\n")
        old_default = poc_ground_truth.DEFAULT_PUT_ROOT
        old_poc_units = poc_ground_truth.DEFAULT_POC_UNITS_DIR
        poc_ground_truth.DEFAULT_PUT_ROOT = old_put_root
        poc_ground_truth.DEFAULT_POC_UNITS_DIR = poc_units
        try:
            args = argparse.Namespace(poc_dir=str(poc_dir),
                                      put_root="",
                                      cert_jsonl=[str(cert)],
                                      contract=[],
                                      unit=[],
                                      poc=[],
                                      only=["P01.set"],
                                      max_expected_lines=8,
                                      limit=20,
                                      format="json",
                                      out="")
            doc = poc_ground_truth.build_inventory(args)
        finally:
            poc_ground_truth.DEFAULT_PUT_ROOT = old_default
            poc_ground_truth.DEFAULT_POC_UNITS_DIR = old_poc_units

        unit = doc["units"][0]
        bad = 0
        bad += check(doc["summary"]["put_rows"] == 2
                     and doc["summary"]["filtered_put_rows"] == 2,
                     f"default roots include old and POC-local PUTs: "
                     f"{doc['summary']}")
        bad += check(len(doc["inputs"]["put_roots"]) == 2
                     and doc["inputs"]["put_root"] is None,
                     f"multiple default roots are recorded: {doc['inputs']}")
        bad += check(unit["put_summary"]["strong_shape"] == 2,
                     f"both strong PUTs attach to the same unit: "
                     f"{unit['put_summary']}")
        return bad


def test_inventory_joins_cert_and_put_with_path_derived_contract():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        poc_dir = tmp / "poc"
        put_root = tmp / "put"
        cert = tmp / "cert.jsonl"
        poc_dir.mkdir()
        (poc_dir / "Harness.sol").write_text("""// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// EXPECTED: PUT and certification rows join through path-derived contract.
contract Harness {
    uint256 public y;
    function set(uint256 x) external {
        y = x;
    }
}
""")
        cert.write_text(json.dumps({
            "contract": None,
            "unit": "set",
            "path_function": "sol:@C@Harness@F@set#9",
            "bucket": "CERTIFIED",
            "certified": {"9": "x in [1, 3]"},
        }) + "\n")
        put_dir = put_root / "Harness__set__9"
        put_dir.mkdir(parents=True)
        (put_dir / "put.json").write_text(json.dumps({
            "contract": "Harness",
            "unit": "set",
            "path_function": "sol:@C@Harness@F@set#9",
            "enc": 9,
            "region": {
                "x": ["1", "3"],
            },
            "stats": {
                "fuzz_params": 1,
                "lifted": ["x"],
                "asserts": 1,
            },
        }) + "\n")
        args = argparse.Namespace(poc_dir=str(poc_dir),
                                  put_root=str(put_root),
                                  cert_jsonl=[str(cert)],
                                  contract=[],
                                  unit=[],
                                  poc=[],
                                  only=[],
                                  max_expected_lines=8,
                                  limit=20,
                                  format="json",
                                  out="")
        doc = poc_ground_truth.build_inventory(args)
        bad = 0
        bad += check(doc["summary"]["unit_rows"] == 1,
                     f"path-derived contract avoids split rows: {doc['summary']}")
        unit = doc["units"][0]
        bad += check(unit["contract"] == "Harness"
                     and unit["unit"] == "set"
                     and len(unit["certifications"]) == 1
                     and len(unit["puts"]) == 1,
                     f"certification and PUT attach to one unit: {unit}")
        bad += check(unit["certifications"][0]["path_function"]
                     == "sol:@C@Harness@F@set#9"
                     and unit["puts"][0]["path_function"]
                     == "sol:@C@Harness@F@set#9",
                     f"path_function is preserved in artifacts: {unit}")
        bad += check(unit["ground_truth_status"] == "ready-strong",
                     f"joined row is classified correctly: {unit}")
        return bad


def test_default_cert_paths_include_poc_local_gate_files():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = tmp / "certs"
        top = root / "top.jsonl"
        local = root / "poc_a" / "certify_gate.jsonl"
        nested = root / "poc_b" / "nested" / "certify_gate.jsonl"
        local.parent.mkdir(parents=True)
        nested.parent.mkdir(parents=True)
        root.mkdir(exist_ok=True)
        top.write_text("{}\n")
        local.write_text("{}\n")
        nested.write_text("{}\n")
        paths = [p.relative_to(root) for p in poc_ground_truth.default_cert_paths((root,))]
        bad = 0
        bad += check(Path("top.jsonl") in paths
                     and Path("poc_a/certify_gate.jsonl") in paths
                     and Path("poc_b/nested/certify_gate.jsonl") in paths,
                     f"default cert scan includes top-level and POC-local gates: "
                     f"{paths}")
        return bad


def test_implicit_full_domain_lift_counts_as_wide_fuzz():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        poc_dir, put_root, cert = write_fixture(tmp)
        put_dir = put_root / "P01__set__implicit"
        put_dir.mkdir()
        (put_dir / "put.json").write_text(json.dumps({
            "contract": "P01",
            "unit": "set",
            "path_function": "sol:@C@P01@F@set#1",
            "enc": 8,
            "region": {
                "msg.value": ["0", "0"],
            },
            "stats": {
                "fuzz_params": 1,
                "lifted": ["x"],
                "asserts": 1,
            },
            "notes": [
                "emitted replay omitted x; lifting it as a full-domain calldata fuzz input",
            ],
        }) + "\n")
        args = argparse.Namespace(poc_dir=str(poc_dir),
                                  put_root=str(put_root),
                                  cert_jsonl=[str(cert)],
                                  contract=[],
                                  unit=[],
                                  poc=[],
                                  only=["P01.set"],
                                  max_expected_lines=8,
                                  limit=20,
                                  format="json",
                                  out="")
        doc = poc_ground_truth.build_inventory(args)
        unit = doc["units"][0]
        implicit = next(p for p in unit["puts"] if p["enc"] == 8)
        bad = 0
        bad += check(implicit["wide_region"] is False
                     and implicit["wide_fuzz"] is True
                     and implicit["wide_fuzz_coords"] == ["x"],
                     f"implicit full-domain lifted arg supplies PUT width: "
                     f"{implicit}")
        bad += check(implicit["strong_shape"] is True,
                     f"implicit full-domain PUT is strong-shaped: {implicit}")
        bad += check(unit["put_summary"]["strong_shape"] == 2,
                     f"implicit full-domain PUT contributes to summary: "
                     f"{unit['put_summary']}")
        return bad


TESTS = [
    test_inventory_reads_sources_cert_and_puts_without_execution,
    test_inventory_filters_and_reports_weak_reasons,
    test_inventory_default_roots_include_poc_local_puts,
    test_inventory_joins_cert_and_put_with_path_derived_contract,
    test_default_cert_paths_include_poc_local_gate_files,
    test_implicit_full_domain_lift_counts_as_wide_fuzz,
]


if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            failures += test()
        except Exception as exc:  # pragma: no cover
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    raise SystemExit(1 if failures else 0)
