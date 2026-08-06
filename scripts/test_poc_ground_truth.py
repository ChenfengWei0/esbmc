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
            "enc": 7,
            "depth": 1,
            "ladder_refusal": "region coordinate refused",
            "region": {
                "x": ["5", "5"],
            },
            "stats": {
                "fuzz_params": 0,
                "asserts": 0,
                "oracle_skipped": [
                    "state.x (no storage slot)",
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
            "no-oracle:ladder-refusal:region coordinate refused": 1,
            "no-oracle:state.x (no storage slot)": 1,
            "no-wide-region": 1,
        }, f"weak PUT details preserve oracle causes: {unit['put_summary']}")
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


TESTS = [
    test_inventory_reads_sources_cert_and_puts_without_execution,
    test_inventory_filters_and_reports_weak_reasons,
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
