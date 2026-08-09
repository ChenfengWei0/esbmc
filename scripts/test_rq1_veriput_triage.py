#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import rq1_veriput_triage  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def put_json(path, *, stats=None, notes=None):
    write_json(path, {
        "kind": "put",
        "stats": stats or {},
        "notes": notes or [],
    })
    return str(path)


def result_doc(*, valid=0, put_valid=0, concrete_valid=0, tests=None,
               cert=None, status="ok", reason=None):
    return {
        "schema": "veriput-rq1-case-result/v1",
        "row": {
            "contract": "C",
            "completion_status": status,
            "early_stop_reason": reason,
        },
        "certification": {
            "bucket_counts": cert or {},
            "exit_counts": {},
        },
        "put": {
            "raw": valid,
            "valid": valid,
            "put_raw": put_valid,
            "put_valid": put_valid,
            "concrete_raw": concrete_valid,
            "concrete_valid": concrete_valid,
            "valid_tests": tests or [],
        },
    }


def test_canonical_result_wins_over_redo_archive():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        canonical = root / "real203" / "subjects" / "S" / "result.json"
        redo = root / "real203" / "subjects" / "S.redo.200.1" / "result.json"
        write_json(canonical,
                   result_doc(valid=0, cert={"NO-WITNESS-UNKNOWN": 1}))
        write_json(redo, result_doc(
            valid=1,
            put_valid=1,
            tests=[{
                "kind": "put",
                "oracle_classes": ["R0"],
                "put_json": put_json(root / "p.json", stats={
                    "oracle_classes": ["R0"],
                    "exit_kind": "normal",
                    "oracle_skipped": [],
                }),
            }]))
        rows = rq1_veriput_triage.triage_rows(root, ["real203"])
        bad = 0
        bad += check(len(rows) == 1 and rows[0]["subject_id"] == "S",
                     f"redo archive collapses onto the base subject id: {rows}")
        bad += check(rows[0]["quality_bucket"] == "no-valid",
                     f"canonical result wins over redo archive: {rows}")
        bad += check(rows[0]["triage_cause"] == "cert-no-witness-unknown",
                     f"canonical no-valid cause is retained: {rows}")
        return bad


def test_redo_archive_used_when_no_canonical_exists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        redo = root / "real203" / "subjects" / "S.redo.200.1" / "result.json"
        write_json(redo, result_doc(
            valid=1,
            put_valid=1,
            tests=[{
                "kind": "put",
                "oracle_classes": ["R0"],
                "put_json": put_json(root / "p.json", stats={
                    "oracle_classes": ["R0"],
                    "exit_kind": "normal",
                    "oracle_skipped": [],
                }),
            }]))
        rows = rq1_veriput_triage.triage_rows(root, ["real203"])
        bad = 0
        bad += check(len(rows) == 1 and rows[0]["subject_id"] == "S",
                     f"orphan redo collapses onto the base subject id: {rows}")
        bad += check(rows[0]["quality_bucket"] == "valid-PUT-no-R1R2",
                     f"orphan redo remains usable evidence: {rows}")
        bad += check(rows[0]["triage_cause"] == "normal-r0-only-other",
                     f"orphan redo cause is actionable: {rows}")
        return bad


def test_adopted_artifacts_collapse_to_base_subject_id():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        adopted = (root / "real203" / "subjects"
                   / "S.adopted_from_artifacts" / "result.json")
        base = root / "real203" / "subjects" / "S" / "result.json"
        write_json(adopted, result_doc(valid=0, cert={"NO-WITNESS-UNKNOWN": 1}))
        write_json(base, result_doc(
            valid=1,
            put_valid=1,
            tests=[{
                "kind": "put",
                "oracle_classes": ["R1"],
                "put_json": put_json(root / "p.json", stats={
                    "oracle_classes": ["R1"],
                }),
            }]))
        rows = rq1_veriput_triage.triage_rows(root, ["real203"])
        bad = 0
        bad += check(len(rows) == 1 and rows[0]["subject_id"] == "S",
                     f"adopted artifact directory collapses to base id: {rows}")
        bad += check(rows[0]["quality_bucket"] == "valid-PUT-with-R1R2",
                     f"base result wins over older adopted artifact: {rows}")
        return bad


def test_triage_causes_distinguish_concrete_and_unobservable_puts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_json(root / "bugfix124" / "subjects" / "Concrete" / "result.json",
                   result_doc(
                       valid=1,
                       concrete_valid=1,
                       tests=[{
                           "kind": "concrete",
                           "stage2_source": "timeout_concrete_fallback",
                           "put_json": put_json(root / "concrete-put.json",
                                                notes=["concrete-only fallback"]),
                       }],
                       cert={"KILLED": 1}))
        write_json(root / "bugfix124" / "subjects" / "Rollback" / "result.json",
                   result_doc(
                       valid=1,
                       put_valid=1,
                       tests=[{
                           "kind": "put",
                           "oracle_classes": ["R0"],
                           "put_json": put_json(root / "rollback-put.json",
                                                stats={
                                                    "oracle_classes": ["R0"],
                                                    "rollback_exit": True,
                                                    "oracle_skipped": [
                                                        "ROLLBACK revert"],
                                                }),
                       }],
                       cert={"CERTIFIED": 1}))
        write_json(root / "bugfix124" / "subjects" / "NoValid" / "result.json",
                   result_doc(valid=0, cert={"NO-COORDINATE": 2}))
        rows = {row["subject_id"]: row
                for row in rq1_veriput_triage.triage_rows(root, ["bugfix124"])}
        bad = 0
        bad += check(rows["Concrete"]["quality_bucket"] == "valid-no-PUT",
                     f"concrete-only valid remains no-PUT: {rows}")
        bad += check(rows["Concrete"]["triage_cause"] == "timeout_concrete_fallback",
                     f"concrete fallback cause is retained: {rows}")
        bad += check(rows["Rollback"]["triage_cause"] == "rollback-unobservable",
                     f"rollback R0-only PUT is not mistaken for render bug: {rows}")
        bad += check(rows["NoValid"]["triage_cause"] == "cert-no-coordinate",
                     f"no-valid certificate blocker is exposed: {rows}")
        return bad


def test_unsupported_calldata_beats_generic_not_parameterized_note():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_json(root / "bugfix124" / "subjects" / "StringArg" / "result.json",
                   result_doc(
                       valid=1,
                       concrete_valid=1,
                       tests=[{
                           "kind": "concrete",
                           "stage2_source": "certified_region",
                           "put_json": put_json(root / "string-put.json", notes=[
                               "declared parameter `description` is absent from "
                               "the certified region, but type `string` cannot "
                               "be synthesized as a full-domain fuzz input",
                               "NOT PARAMETERIZED, per From a Region to a Test",
                           ]),
                       }],
                       cert={"CERTIFIED": 1}))
        rows = rq1_veriput_triage.triage_rows(root, ["bugfix124"])
        bad = 0
        bad += check(rows[0]["triage_cause"] == "unsupported-calldata-type",
                     f"the specific unsupported calldata blocker wins: {rows}")
        return bad


def test_action_queue_demotes_hard_dynamic_mapping_no_r1r2():
    normal = {
        "dataset": "real203",
        "subject_id": "Normal",
        "quality_bucket": "valid-PUT-no-R1R2",
        "triage_cause": "normal-r0-only-other",
    }
    no_put = {
        "dataset": "real203",
        "subject_id": "NoPut",
        "quality_bucket": "valid-no-PUT",
        "triage_cause": "timeout_concrete_fallback",
    }
    dynamic_mapping = {
        "dataset": "real203",
        "subject_id": "DynamicMapping",
        "quality_bucket": "valid-PUT-no-R1R2",
        "triage_cause": "mapping-dynarray-unrendered",
    }
    ordered = sorted([dynamic_mapping, no_put, normal],
                     key=rq1_veriput_triage.queue_order)
    bad = 0
    bad += check([row["subject_id"] for row in ordered]
                 == ["Normal", "NoPut", "DynamicMapping"],
                 f"hard dynamic mapping no-R1/R2 is not the first queue item: "
                 f"{ordered}")
    return bad


def test_action_queue_demotes_no_wide_rendered_coordinate():
    timeout_fallback = {
        "dataset": "real203",
        "subject_id": "TimeoutFallback",
        "quality_bucket": "valid-no-PUT",
        "triage_cause": "timeout_concrete_fallback",
    }
    no_wide = {
        "dataset": "bugfix124",
        "subject_id": "NoWide",
        "quality_bucket": "valid-no-PUT",
        "triage_cause": "not-parameterized-no-wide-rendered-coordinate",
    }
    ordered = sorted([no_wide, timeout_fallback],
                     key=rq1_veriput_triage.queue_order)
    bad = 0
    bad += check([row["subject_id"] for row in ordered]
                 == ["TimeoutFallback", "NoWide"],
                 f"point-region no-PUT does not block fallback triage: "
                 f"{ordered}")
    return bad


def main():
    tests = [
        test_canonical_result_wins_over_redo_archive,
        test_redo_archive_used_when_no_canonical_exists,
        test_adopted_artifacts_collapse_to_base_subject_id,
        test_triage_causes_distinguish_concrete_and_unobservable_puts,
        test_unsupported_calldata_beats_generic_not_parameterized_note,
        test_action_queue_demotes_hard_dynamic_mapping_no_r1r2,
        test_action_queue_demotes_no_wide_rendered_coordinate,
    ]
    bad = 0
    for test in tests:
        print(f"--- {test.__name__}")
        bad += test()
    if bad:
        print(f"\n{bad} check(s) failed")
        return 1
    print(f"\n{len(tests)} test(s) ran")
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
