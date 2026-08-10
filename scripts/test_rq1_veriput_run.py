#!/usr/bin/env python3
import json
import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import rq1_veriput_run  # noqa: E402
import veriput_path_guard  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def test_path_guard_allows_only_veriput_rq1_result_tree():
    bad = 0
    allowed = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/results.jsonl")
    blocked = Path("/home/samson/workspace/VeriPUT/Results/Peer182/logs/gen_veriput.jsonl")
    try:
        veriput_path_guard.ensure_path_not_protected("--out", allowed)
        allowed_ok = True
    except ValueError:
        allowed_ok = False
    try:
        veriput_path_guard.ensure_path_not_protected("--out", blocked)
        blocked_ok = False
    except ValueError:
        blocked_ok = True
    bad += check(allowed_ok, "RQ1 VeriPUT result tree is an allowed generated artifact root")
    bad += check(blocked_ok, "prepared Results trees remain protected")
    return bad


def test_put_artifact_summary_counts_raw_valid_and_oracle_classes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unit = root / "put" / "approve"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest_Token_approve_put7.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T {}\n")
        disabled_file = unit / "Project" / "test" / "TokenCovTest_disabled.t.sol"
        disabled_file.write_text(
            "contract T { function disabled_test_cov_disabled() public {} }\n")
        unsupported_file = (
            unit / "Project" / "test" / "TokenCovTest_unsupported.t.sol")
        unsupported_file.write_text("""\
contract T {
  function test_cov_unsupported() public {
    // UNSUPPORTED: Token.approve has an argument type ESBMC cannot yet render
  }
}
""")
        setup_warning_file = (
            unit / "Project" / "test" / "TokenCovTest_setup_warning.t.sol")
        setup_warning_file.write_text("""\
contract T {
  function setUp() public {
    // UNSUPPORTED: helper deployment is abstract
  }
  function test_cov_setup_warning() public {
    assertTrue(true);
  }
}
""")
        (wd / "put.json").write_text(json.dumps({
            "kind": "put",
            "test": "test_put_Token_approve_path7",
            "file": str(put_file),
            "stats": {
                "oracle_classes": ["R1", "R2"],
                "assertion_oracles": [
                    {
                        "layer": "post",
                        "var": "balance",
                        "text": "post >= pre",
                        "classes": ["R1"],
                        "verdict": "HOLDS",
                        "emitted_in_test": True,
                        "guarded": False,
                    },
                    {
                        "layer": "delta",
                        "var": "allowance",
                        "text": "post - pre in [0, amount]",
                        "classes": ["R1", "R2"],
                        "verdict": "HOLDS",
                        "emitted_in_test": True,
                        "guarded": False,
                    },
                ],
            },
        }))
        zero_wd = unit / "_wd" / "zero"
        zero_wd.mkdir(parents=True)
        (zero_wd / "put.json").write_text(json.dumps({
            "kind": "put",
            "test": "test_put_Token_approve_path9",
            "file": "zero.t.sol",
            "stats": {
                "oracle_classes": [],
                "asserts": 0,
                "guarded_asserts": 0,
                "assertion_oracles": [],
            },
        }))
        colliding_concrete_wd = unit / "_wd" / "colliding-concrete"
        colliding_concrete_wd.mkdir(parents=True)
        (colliding_concrete_wd / "put.json").write_text(json.dumps({
            "kind": "concrete",
            "test": "test_cov_Token_approve_path8",
            "file": "different-concrete.t.sol",
            "stats": {
                "oracle_classes": ["BAD-MATCH"],
                "assertion_oracles": [],
            },
        }))
        recovered_concrete_file = (
            unit / "Project" / "test" / "TokenCovTest_recovered.t.sol")
        recovered_concrete_file.write_text("contract T {}\n")
        recovered_concrete_wd = unit / "_wd" / "recovered-concrete"
        recovered_concrete_wd.mkdir(parents=True)
        (recovered_concrete_wd / "put.json").write_text(json.dumps({
            "kind": "concrete",
            "test": "test_cov_recovered",
            "file": str(recovered_concrete_file),
            "stage2_source": "certified-region-concrete-fallback",
            "stage2_witness_check":
                "CERTIFIED-REGION-PUT-REFUSED:build-put-refused",
            "concrete_reason":
                "certified-region PUT refused as build-put-refused; "
                "emitted concrete replay only",
            "stats": {
                "oracle_classes": [],
                "assertion_oracles": [],
            },
        }))
        (unit / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 2,
                "concrete_replays_emitted": 4,
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "total": 3,
                    "put": 1,
                    "concrete": 2,
                },
                "rows": [
                    {
                        "kind": "put",
                        "unit": "approve",
                        "enc": 7,
                        "piece": None,
                        "test": "test_put_Token_approve_path7",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": True,
                    },
                    {
                        "kind": "concrete",
                        "stage2_source": "no-coordinate-concrete-fallback",
                        "stage2_witness_check":
                            "COMPLETE-WITNESS-NO-COORDINATE",
                        "concrete_reason":
                            "Stage-2 complete witness has no coordinate",
                        "unit": "approve",
                        "enc": 8,
                        "piece": None,
                        "test": "test_cov_Token_approve_path8",
                        "file": "concrete.t.sol",
                        "forge_status": "Failure",
                        "valid_reference_test": False,
                        "b": False,
                    },
                    {
                        "kind": "put",
                        "unit": "approve",
                        "enc": 9,
                        "piece": None,
                        "test": "test_put_Token_approve_path9",
                        "file": "zero.t.sol",
                        "forge_status": "Success",
                        "valid_reference_test": False,
                        "b": False,
                    },
                    {
                        "kind": "concrete",
                        "unit": "approve",
                        "enc": 13,
                        "piece": None,
                        "test": "test_cov_recovered",
                        "file": str(recovered_concrete_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": False,
                    },
                    {
                        "kind": "concrete",
                        "unit": "approve",
                        "enc": 14,
                        "piece": None,
                        "test": "test_cov_setup_warning",
                        "file": str(setup_warning_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": False,
                    },
                    {
                        "kind": "concrete",
                        "unit": "approve",
                        "enc": 10,
                        "piece": None,
                        "test": "test_cov_disabled",
                        "file": str(disabled_file),
                        "forge_status": None,
                        "valid_reference_test": False,
                        "b": False,
                    },
                    {
                        "kind": "concrete",
                        "unit": "approve",
                        "enc": 11,
                        "piece": None,
                        "test": "test_cov_unsupported",
                        "file": str(unsupported_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": False,
                    },
                    {
                        "kind": "refusal",
                        "unit": "approve",
                        "enc": 12,
                        "test": "test_refused_kind",
                        "file": "refused.t.sol",
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": False,
                    },
                ],
            },
        }))
        summary = rq1_veriput_run.summarize_put_artifacts(root / "put")
        bad = 0
        bad += check(summary["raw"] == 4 and summary["valid"] == 3,
                     f"raw/valid split is retained: {summary}")
        bad += check(summary["put_raw"] == 1 and summary["put_valid"] == 1
                     and summary["concrete_raw"] == 3
                     and summary["concrete_valid"] == 2,
                     f"PUT/concrete split is retained: {summary}")
        bad += check(summary["oracle_class_counts"] == {"R1": 2, "R2": 1},
                     f"oracle labels counted: {summary['oracle_class_counts']}")
        bad += check(summary["oracle_class_combo_counts"] == {
            "R1": 1,
            "R1+R2": 1,
        }, f"oracle combinations counted: {summary['oracle_class_combo_counts']}")
        bad += check(len(summary["assertion_oracles"]) == 2
                     and summary["raw_tests"][0]["oracle_classes"] == ["R1", "R2"],
                     f"assertion metadata remains tied to artifacts: {summary}")
        concrete = [row for row in summary["raw_tests"]
                    if row["kind"] == "concrete"][0]
        bad += check(concrete["oracle_classes"] == []
                     and concrete["put_json"] is None,
                     f"duplicate concrete test names do not cross-link put.json: {summary}")
        bad += check(
            concrete["stage2_source"] == "no-coordinate-concrete-fallback"
            and concrete["stage2_witness_check"] ==
            "COMPLETE-WITNESS-NO-COORDINATE"
            and "complete witness" in concrete["concrete_reason"],
            f"concrete fallback provenance is retained: {summary}")
        recovered = [row for row in summary["raw_tests"]
                     if row["enc"] == 13][0]
        bad += check(
            recovered["stage2_source"] ==
            "certified-region-concrete-fallback"
            and recovered["stage2_witness_check"] ==
            "CERTIFIED-REGION-PUT-REFUSED:build-put-refused"
            and "certified-region PUT refused" in
            recovered["concrete_reason"],
            f"put.json provenance fills sparse B rows: {summary}")
        bad += check(len(summary["raw_tests"]) == 4
                     and all(t["enc"] != 9 for t in summary["raw_tests"]),
                     f"refused PUT rows are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 12 for t in summary["raw_tests"]),
                     f"non-deliverable kind rows are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 10 for t in summary["raw_tests"]),
                     f"disabled concrete replays are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 11 for t in summary["raw_tests"]),
                     f"unsupported concrete bodies are not raw deliverables: {summary}")
        bad += check(any(t["enc"] == 14 for t in summary["valid_tests"]),
                     f"green concrete replays with setup warnings are retained: {summary}")
        bad += check(summary["quality_bucket"] == "valid-PUT-with-R1R2"
                     and summary["valid_put_with_R1"] == 1
                     and summary["valid_put_with_R2"] == 1
                     and summary["valid_put_with_R1_or_R2"] == 1
                     and summary["valid_put_without_R1R2"] == 0,
                     f"methodology strength fields are counted: {summary}")
        return bad


def test_strength_quality_bucket_keeps_no_put_and_no_r1r2_visible():
    cases = [
        ({
            "valid_tests": [],
        }, "no-valid"),
        ({
            "valid_tests": [{
                "kind": "concrete",
                "valid_reference_test": True,
            }],
        }, "valid-no-PUT"),
        ({
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            }],
        }, "valid-PUT-no-R1R2"),
        ({
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R1"],
            }],
        }, "valid-PUT-with-R1R2"),
    ]
    bad = 0
    for summary, bucket in cases:
        got = rq1_veriput_run._strength_quality(summary)
        bad += check(got["quality_bucket"] == bucket,
                     f"{bucket} is reported distinctly: {got}")
    return bad


def test_normalize_result_row_trusts_valid_test_oracle_classes():
    row = rq1_veriput_run._normalize_result_row({
        "status": "timeout",
        "reason": "case timed out after producing artifacts",
        "valid": 1,
        "put_valid": 1,
        "valid_put_with_R1": 0,
        "valid_put_with_R2": 0,
        "valid_put_with_R1_or_R2": 0,
        "valid_put_without_R1R2": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0", "R1"],
        }],
    })
    bad = 0
    bad += check(row["valid_put_with_R1"] == 1,
                 f"R1 count is recomputed from valid_tests: {row}")
    bad += check(row["valid_put_with_R1_or_R2"] == 1,
                 f"R1/R2 count is recomputed from valid_tests: {row}")
    bad += check(row["valid_put_without_R1R2"] == 0,
                 f"stale no-R1/R2 aggregate is not retained: {row}")
    bad += check(row["status"] == "ok"
                 and row["reason"] is None
                 and row["partial_failure_reason"] ==
                 "case timed out after producing artifacts",
                 f"valid normalized row is successful but keeps old reason: {row}")
    return bad


def test_normalize_result_row_requires_explicit_double_oracle_validity():
    row = rq1_veriput_run._normalize_result_row({
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_put_with_R1": 1,
        "valid_put_with_R2": 1,
        "valid_put_with_R1_or_R2": 1,
        "valid_tests": [{
            "kind": "put",
            "oracle_classes": ["R1", "R2"],
        }],
    })
    bad = 0
    bad += check(row["valid"] == 0
                 and row["put_valid"] == 0
                 and row["valid_put_with_R1_or_R2"] == 0,
                 f"missing replay validity is not counted: {row}")
    bad += check(row["quality_bucket"] == "no-valid",
                 f"unknown replay validity keeps row weak: {row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(row),
                 f"old empty ok rows are retryable: {row}")
    return bad


def test_row_strength_prioritizes_methodology_quality():
    r1_put = {
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
        }],
        "raw": 1,
    }
    many_r0_puts = {
        "valid_tests": [
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            },
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            },
        ],
        "raw": 2,
    }
    one_put = {
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0"],
        }],
        "raw": 1,
    }
    many_concrete = {
        "valid_tests": [
            {"kind": "concrete", "valid_reference_test": True},
            {"kind": "concrete", "valid_reference_test": True},
        ],
        "raw": 2,
    }
    stale_empty = {
        "valid": 2,
        "put_valid": 1,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
        "valid_tests": [],
        "raw": 2,
    }
    stale_false = {
        "valid": 2,
        "put_valid": 1,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": False,
            "oracle_classes": ["R1", "R2"],
        }],
        "raw": 2,
    }
    bad = 0
    bad += check(rq1_veriput_run._row_strength(r1_put)
                 > rq1_veriput_run._row_strength(many_r0_puts),
                 "R1/R2 PUT quality outranks more R0-only PUTs")
    bad += check(rq1_veriput_run._row_strength(one_put)
                 > rq1_veriput_run._row_strength(many_concrete),
                 "PUT quality outranks more concrete-only replays")
    bad += check(rq1_veriput_run._row_strength(stale_empty)[0] == 0
                 and rq1_veriput_run._row_strength(stale_false)[0] == 0,
                 "explicit valid_tests evidence overrides stale aggregates")
    normalized = rq1_veriput_run._normalize_result_row(stale_empty)
    bad += check(normalized["valid"] == 0
                 and normalized["put_valid"] == 0
                 and normalized["quality_bucket"] == "no-valid",
                 f"normalization also trusts empty valid_tests: {normalized}")
    return bad


def test_resume_retries_empty_no_valid_rows_only():
    retryable = {
        "status": "no-output",
        "completion_status": "early-stop-no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no Stage-2 candidate after 4 consecutive units; "
                  "stopped before remaining units",
        "stage4_candidate_units_attempted": 0,
    }
    timed_out_empty = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "certification timed out before PUT artifacts: approve",
    }
    valid = {
        "status": "ok",
        "quality_bucket": "valid-PUT-with-R1R2",
        "raw": 1,
        "valid": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
        }],
    }
    raw_invalid = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 1,
        "valid": 0,
        "reason": "no valid reference tests",
    }
    runner_error = {
        "status": "error",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "runner exception: NameError('old bug')",
    }
    missing_ast_schedule = {
        "status": "no-units",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "unit schedule preparation failed: missing-ast=1",
        "schedule_summary": {
            "jobs": 0,
            "skipped_by_status": {"missing-ast": 1},
        },
    }
    diagnostic_empty = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no certified regions: diagnostics esbmc-no-cov-report=1",
        "driver_diagnostic_tags": {
            "esbmc-no-cov-report": 1,
            "path-coverage-probe-goal-cap": 1,
        },
    }
    true_no_units = {
        "status": "no-units",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "target contract has no public/external FunctionDefinition units",
        "schedule_summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
    }
    done = {
        "retry": retryable,
        "timeout": timed_out_empty,
        "error": runner_error,
        "missing-ast": missing_ast_schedule,
        "diagnostic": diagnostic_empty,
        "true-no-units": true_no_units,
        "valid": valid,
        "raw-invalid": raw_invalid,
    }
    got = rq1_veriput_run.retryable_resume_rows(done, "no-valid")
    default = rq1_veriput_run.retryable_resume_rows(done)
    bad = 0
    bad += check(sorted(got) == [
        "diagnostic", "error", "missing-ast", "retry", "timeout",
        "true-no-units"
    ],
                 f"legacy no-valid resume retries only empty no-valid rows: {got}")
    bad += check("raw-invalid" in default,
                 f"default resume retries rows with raw but no valid: {default}")
    bad += check(not rq1_veriput_run._row_needs_resume_retry(valid),
                 "resume keeps valid rows terminal")
    bad += check(not rq1_veriput_run._row_needs_resume_retry(raw_invalid),
                 "resume keeps rows with retained raw artifacts terminal")
    bad += check(rq1_veriput_run._row_needs_resume_retry(true_no_units),
                 "resume retries constructor-only rows for deploy fallback")
    return bad


def test_resume_retries_certified_or_partial_zero_output_rows():
    certified_no_output = {
        "status": "no-output",
        "completion_status": "early-stop-no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no output after 44.5s Stage 4; stopped before remaining units",
        "cert_bucket_counts": {"CERTIFIED": 1, "KILLED": 1},
        "put_summary_paths": ["put/cancelOrders/put-summary.json"],
    }
    partial_stage4_no_output = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no output after 91.0s Stage 4; stopped before remaining units",
        "put_summary_paths": ["put/getVault/put-summary.json"],
    }
    done = {
        "certified": certified_no_output,
        "partial": partial_stage4_no_output,
    }
    got = rq1_veriput_run.retryable_resume_rows(done)
    bad = 0
    bad += check(sorted(got) == ["certified", "partial"],
                 f"certified/partial zero-output rows are retryable: {got}")
    return bad


def test_load_subject_result_row_recovers_certification_diagnostics():
    with tempfile.TemporaryDirectory() as td:
        case_dir = Path(td)
        (case_dir / "result.json").write_text(json.dumps({
            "row": {
                "status": "no-output",
                "quality_bucket": "no-valid",
                "raw": 0,
                "valid": 0,
                "reason": "no certified regions: diagnostics esbmc-no-cov-report=1",
            },
            "certification": {
                "bucket_counts": {"NO-WITNESS-UNKNOWN": 1},
                "driver_diagnostic_tags": {
                    "esbmc-no-cov-report": 1,
                },
                "driver_refusal_tags": {
                    "path-coverage-probe-goal-cap": 1,
                },
            },
        }))
        row = rq1_veriput_run._load_subject_result_row(case_dir)
        sidecar_dir = case_dir / "sidecar"
        (sidecar_dir / "cert").mkdir(parents=True)
        (sidecar_dir / "result.json").write_text(json.dumps({
            "row": {
                "status": "no-output",
                "quality_bucket": "no-valid",
                "raw": 0,
                "valid": 0,
                "reason": "no certified regions",
            },
        }))
        (sidecar_dir / "cert" / "certify-results.jsonl").write_text(
            json.dumps({
                "benchmark": "bench",
                "unit": "f",
                "bucket": "NO-WITNESS-UNKNOWN",
                "driver_diagnostic": {
                    "tag": "path-coverage-probe-goal-cap",
                },
            }) + "\n")
        sidecar_row = rq1_veriput_run._load_subject_result_row(sidecar_dir)
    bad = 0
    bad += check(row["driver_diagnostic_tags"] == {
        "esbmc-no-cov-report": 1,
    }, f"certification diagnostic tags are recovered: {row}")
    bad += check(row["driver_refusal_tags"] == {
        "path-coverage-probe-goal-cap": 1,
    }, f"certification refusal tags are recovered: {row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(row),
                 f"recovered diagnostic row is retryable: {row}")
    bad += check(sidecar_row["driver_diagnostic_tags"] == {
        "path-coverage-probe-goal-cap": 1,
    }, f"cert sidecar diagnostic tags are recovered: {sidecar_row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(sidecar_row),
                 f"sidecar diagnostic row is retryable: {sidecar_row}")
    return bad


def test_adoption_updates_equal_strength_rows_with_cert_evidence():
    current = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
    }
    candidate = dict(current)
    candidate.update({
        "cert_bucket_counts": {"NO-WITNESS-UNKNOWN": 1},
        "driver_diagnostic_tags": {"path-coverage-probe-goal-cap": 1},
    })
    return check(
        rq1_veriput_run._row_needs_normalized_adoption(current, candidate),
        "equal-strength rows adopt newly recovered certification evidence")


def test_normalize_result_row_recomputes_stale_aggregate_quality():
    put_row = rq1_veriput_run._normalize_result_row({
        "status": "no-output",
        "reason": "old stale no-valid row",
        "valid": 0,
        "put_valid": 1,
        "concrete_valid": 0,
        "valid_put_with_R1": 1,
        "valid_put_with_R2": 0,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "no-valid",
    })
    concrete_row = rq1_veriput_run._normalize_result_row({
        "status": "no-output",
        "reason": "old stale no-valid concrete row",
        "valid": 0,
        "put_valid": 0,
        "concrete_valid": 1,
        "quality_bucket": "no-valid",
    })
    bad = 0
    bad += check(put_row["valid"] == 1
                 and put_row["quality_bucket"] == "valid-PUT-with-R1R2",
                 f"aggregate PUT counts repair stale no-valid bucket: {put_row}")
    bad += check(put_row["status"] == "ok"
                 and put_row["reason"] is None
                 and put_row["partial_failure_reason"] ==
                 "old stale no-valid row",
                 f"aggregate valid counts promote stale status: {put_row}")
    bad += check(concrete_row["valid"] == 1
                 and concrete_row["valid_concrete"] == 1
                 and concrete_row["quality_bucket"] == "valid-no-PUT",
                 f"aggregate concrete counts repair stale bucket: {concrete_row}")
    return bad


def test_adoption_updates_equal_strength_rows_with_artifact_evidence():
    current = {
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_put_with_R1": 1,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
    }
    candidate = dict(current)
    candidate.update({
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
            "forge_status": "Success",
        }],
        "raw_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
            "forge_status": "Success",
        }],
        "oracle_class_counts": {"R1": 1},
        "oracle_class_combo_counts": {"R1": 1},
        "assertion_oracles": [{
            "classes": ["R1"],
            "text": "post >= pre",
        }],
        "put_summary_paths": ["put/transfer/put-summary.json"],
        "foundry_replay_wall_s": 0.25,
        "valid_artifacts_retained": True,
    })
    return check(
        rq1_veriput_run._row_needs_normalized_adoption(current, candidate),
        "equal-strength rows adopt double-oracle artifact evidence")


def test_merge_put_summary_marks_valid_partial_artifacts_ok():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unit = root / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (wd / "put.json").write_text(json.dumps({
            "kind": "put",
            "test": "test_put",
            "file": str(put_file),
            "stats": {
                "oracle_classes": ["R1"],
                "assertion_oracles": [{
                    "classes": ["R1"],
                    "text": "post >= pre",
                }],
            },
        }))
        (unit / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 1,
                "concrete_replays_emitted": 0,
            },
            "deliverable_b": {
                "rows": [{
                    "kind": "put",
                    "unit": "transfer",
                    "enc": 3,
                    "test": "test_put",
                    "file": str(put_file),
                    "forge_status": "Success",
                    "valid_reference_test": True,
                    "b": True,
                }],
            },
        }))
        row = rq1_veriput_run._merge_put_summary_into_row({
            "status": "budget-exhausted",
            "completion_status": "budget-exhausted",
            "reason": "subject budget exhausted",
            "raw": 0,
            "valid": 0,
            "quality_bucket": "no-valid",
        }, root)
        bad = 0
        bad += check(row["status"] == "ok"
                     and row["completion_status"] == "budget-exhausted",
                     f"valid partial artifacts promote status only: {row}")
        bad += check(row["reason"] is None
                     and row["partial_failure_reason"] == "subject budget exhausted",
                     f"old failure reason is retained as partial: {row}")
        bad += check(row["valid"] == 1 and row["put_valid"] == 1
                     and row["quality_bucket"] == "valid-PUT-with-R1R2",
                     f"valid artifact strength is adopted: {row}")
        bad += check(row["raw_artifacts_retained"]
                     and row["valid_artifacts_retained"],
                     f"artifact retention flags are adopted: {row}")
        return bad


def test_load_subject_result_row_adopts_put_artifacts():
    with tempfile.TemporaryDirectory() as td:
        case_dir = Path(td)
        unit = case_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (case_dir / "result.json").write_text(json.dumps({
            "row": {
                "status": "budget-exhausted",
                "reason": "case budget exhausted before Stage 4",
                "raw": 0,
                "valid": 0,
                "put_valid": 0,
                "quality_bucket": "no-valid",
            },
        }))
        (wd / "put.json").write_text(json.dumps({
            "kind": "put",
            "test": "test_put",
            "file": str(put_file),
            "stats": {
                "oracle_classes": ["R1", "R2"],
                "assertion_oracles": [{
                    "classes": ["R1", "R2"],
                    "text": "post >= pre",
                }],
            },
        }))
        (unit / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 1,
                "concrete_replays_emitted": 0,
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "total": 1,
                    "put": 1,
                    "concrete": 0,
                },
                "rows": [{
                    "kind": "put",
                    "unit": "transfer",
                    "enc": 8,
                    "test": "test_put",
                    "file": str(put_file),
                    "forge_status": "Success",
                    "valid_reference_test": True,
                    "b": True,
                }],
            },
        }))
        row = rq1_veriput_run._load_subject_result_row(case_dir)
    bad = 0
    bad += check(row["status"] == "ok"
                 and row["partial_failure_reason"] ==
                 "case budget exhausted before Stage 4",
                 f"stale result row is promoted from put artifacts: {row}")
    bad += check(row["valid"] == 1 and row["put_valid"] == 1
                 and row["valid_put_with_R1_or_R2"] == 1
                 and row["quality_bucket"] == "valid-PUT-with-R1R2",
                 f"artifact counters replace stale no-valid result: {row}")
    bad += check(row["adopted_put_summary_artifacts"]
                 and row["valid_artifacts_retained"],
                 f"artifact adoption is marked: {row}")
    return bad


def test_results_all_requires_double_oracle_validity():
    path = ROOT.parent / "VeriPUT" / "Results" / "results_all.py"
    spec = importlib.util.spec_from_file_location("results_all_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = mod.normalize_veriput_row({
        "valid_tests": [
            {
                "kind": "put",
                "oracle_classes": ["R1", "R2"],
            },
            {
                "kind": "concrete",
                "valid_reference_test": False,
            },
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R1"],
            },
        ],
    })
    bad = 0
    bad += check(row["valid"] == 1 and row["put_valid"] == 1
                 and row["concrete_valid"] == 0,
                 f"only explicit double-oracle tests count as valid: {row}")
    bad += check(row["valid_put_with_R1_or_R2"] == 1
                 and row["quality_bucket"] == "valid-PUT-with-R1R2",
                 f"R-class quality is computed from explicit valid tests: {row}")
    stale = mod.normalize_veriput_row({
        "valid": 3,
        "put_valid": 2,
        "concrete_valid": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": False,
            "oracle_classes": ["R1", "R2"],
        }],
    })
    bad += check(stale["valid"] == 0 and stale["put_valid"] == 0
                 and stale["concrete_valid"] == 0
                 and stale["quality_bucket"] == "no-valid",
                 f"explicit non-valid test evidence overrides stale "
                 f"aggregate counters: {stale}")
    empty = mod.normalize_veriput_row({
        "valid": 1,
        "put_valid": 1,
        "quality_bucket": "valid-PUT-no-R1R2",
        "valid_tests": [],
    })
    bad += check(empty["valid"] == 0 and empty["put_valid"] == 0
                 and empty["quality_bucket"] == "no-valid",
                 f"empty valid_tests overrides stale aggregate counters: {empty}")
    return bad


def test_adopt_existing_subject_results_promotes_stale_sidecar_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "stale"
        unit = case_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (case_dir / "result.json").write_text(json.dumps({
            "row": {
                "status": "no-output",
                "completion_status": "early-stop-no-output",
                "reason": "old stale no-valid row",
                "raw": 0,
                "valid": 0,
                "put_valid": 0,
                "concrete_valid": 0,
                "quality_bucket": "no-valid",
                "stage4_generation_wall_s": 0.01,
                "foundry_replay_wall_s": 0.01,
            },
        }))
        (wd / "put.json").write_text(json.dumps({
            "kind": "put",
            "test": "test_put",
            "file": str(put_file),
            "stats": {
                "oracle_classes": ["R1", "R2"],
                "assertion_oracles": [{
                    "classes": ["R1", "R2"],
                    "text": "post == pre + amount",
                }],
            },
        }))
        (unit / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 1,
                "concrete_replays_emitted": 0,
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "total": 1,
                    "put": 1,
                    "concrete": 0,
                },
                "rows": [{
                    "kind": "put",
                    "unit": "transfer",
                    "enc": 8,
                    "test": "test_put",
                    "file": str(put_file),
                    "forge_status": "Success",
                    "valid_reference_test": True,
                    "b": True,
                }],
            },
            "timing": {
                "generation_wall_s": 0.2,
                "emission_wall_s": 0.3,
                "foundry_replay_wall_s": 0.4,
                "total_wall_s": 0.9,
            },
        }))
        journal = root / "peer182" / "results.jsonl"
        done = {
            "gen:veriput:stale": {
                "key": "gen:veriput:stale",
                "subject_id": "stale",
                "status": "no-output",
                "reason": "old stale no-valid row",
                "raw": 0,
                "valid": 0,
                "quality_bucket": "no-valid",
            },
        }
        updated = rq1_veriput_run.adopt_existing_subject_results(
            root, "peer182", [{
                "subject_id": "stale",
                "benchmark": "peer182",
                "contract": "Token",
            }], journal, done)
        row = updated["gen:veriput:stale"]
        journal_rows = [
            json.loads(line) for line in journal.read_text().splitlines()
            if line.strip()
        ]
    bad = 0
    bad += check(row["status"] == "ok"
                 and row["partial_failure_reason"] ==
                 "old stale no-valid row",
                 f"stale no-valid row is promoted from sidecar: {row}")
    bad += check(row["valid"] == 1 and row["put_valid"] == 1
                 and row["quality_bucket"] == "valid-PUT-with-R1R2",
                 f"sidecar strength counters are authoritative: {row}")
    bad += check(row["valid_tests"][0]["valid_reference_test"] is True
                 and row["valid_tests"][0]["forge_status"] == "Success",
                 f"double-oracle fields are retained: {row}")
    bad += check(row["oracle_class_counts"] == {"R1": 1, "R2": 1}
                 and row["oracle_class_combo_counts"] == {"R1+R2": 1},
                 f"R-class metadata is retained: {row}")
    bad += check(row["stage4_generation_wall_s"] == 0.2
                 and row["stage4_emission_wall_s"] == 0.3
                 and row["foundry_replay_wall_s"] == 0.4
                 and row["put_all_wall_s"] == 0.9,
                 f"Stage4 timing is retained: {row}")
    bad += check(row["raw_artifacts_retained"]
                 and row["valid_artifacts_retained"]
                 and row["adopted_put_summary_artifacts"],
                 f"artifact retention/adoption flags are retained: {row}")
    bad += check(len(journal_rows) == 1
                 and journal_rows[0]["quality_bucket"] ==
                 "valid-PUT-with-R1R2",
                 f"journal is rewritten with adopted row: {journal_rows}")
    return bad


def test_resume_quality_floor_can_focus_no_put_and_no_r1r2():
    no_put = {
        "subject_id": "concrete",
        "status": "ok",
        "valid": 1,
        "concrete_valid": 1,
        "put_valid": 0,
        "quality_bucket": "valid-no-PUT",
    }
    no_r1r2 = {
        "subject_id": "r0",
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0"],
        }],
    }
    strong = {
        "subject_id": "strong",
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1", "R2"],
        }],
    }
    done = {
        "gen:veriput:concrete": no_put,
        "gen:veriput:r0": no_r1r2,
        "gen:veriput:strong": strong,
    }
    default = rq1_veriput_run.retryable_resume_rows(done)
    focused = rq1_veriput_run.retryable_resume_rows(
        done, "valid-PUT-with-R1R2")
    bad = 0
    bad += check(set(default) == {"gen:veriput:concrete", "gen:veriput:r0"},
                 f"default resume improves weak valid rows: {default}")
    bad += check(set(focused) == {"gen:veriput:concrete", "gen:veriput:r0"},
                 f"quality floor focuses no-PUT/no-R1R2 rows: {focused}")
    legacy = rq1_veriput_run.retryable_resume_rows(done, "no-valid")
    bad += check(legacy == {},
                 f"legacy no-valid floor still preserves valid rows: {legacy}")
    return bad


def test_empty_schedule_status_preserves_preparation_failures():
    prep_failed = {
        "summary": {
            "jobs": 0,
            "skipped_by_status": {"missing-ast": 1},
        },
        "skipped_rows": [{
            "status": "missing-ast",
            "reason": "/tmp/cache/C.solast does not exist",
        }],
    }
    no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "reason": "target contract has no public/external FunctionDefinition units",
        }],
    }
    special_only = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
            "skipped_units": 2,
        },
        "no_unit_rows": [{
            "reason": (
                "target contract exposes only fallback/receive entries; "
                "use deploy-only concrete fallback"),
        }],
    }
    summary_only_no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [],
    }
    filtered_empty = {
        "summary": {
            "jobs": 0,
            "jobs_before_unit_filter": 3,
            "unit_filter": ["missingUnit"],
        },
        "jobs": [],
        "no_unit_rows": [],
    }
    bad = 0
    status, reason = rq1_veriput_run._empty_schedule_status_reason(prep_failed)
    bad += check(status == "error",
                 f"missing AST schedule is a preparation error: {status}")
    bad += check("missing-ast=1" in reason and "/tmp/cache/C.solast" in reason,
                 f"missing AST reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(no_units)
    bad += check(status == "no-units",
                 f"true no-unit schedule remains no-units: {status}")
    bad += check("no public/external" in reason,
                 f"true no-unit reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(special_only)
    bad += check(status == "no-units",
                 f"special-entry-only schedule remains deploy-fallback eligible: {status}")
    bad += check("fallback/receive" in reason,
                 f"special-entry-only reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(
        summary_only_no_units)
    bad += check(status == "no-units",
                 f"summary-only no-unit schedule remains eligible: {status}")
    bad += check(rq1_veriput_run._is_true_no_unit_schedule(summary_only_no_units),
                 "summary-only no-unit schedule triggers deploy fallback")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(filtered_empty)
    bad += check(status == "no-output" and "unit filter" in reason,
                 f"filtered-empty schedule is not mislabeled no-units: "
                 f"{status}, {reason}")
    bad += check(not rq1_veriput_run._is_true_no_unit_schedule(filtered_empty),
                 "filtered-empty schedule does not trigger deploy fallback")
    return bad


def test_no_unit_deploy_fallback_writes_valid_concrete_artifact():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subj_root = root / "subject"
        subj_root.mkdir()
        flat = subj_root / "flat.sol"
        flat.write_text("""\
pragma solidity ^0.8.20;
contract C {
  address public owner;
  string public name;
  int256 public delta;
  constructor(address owner_, string memory name_, int256 delta_) {
    require(owner_ != address(0), "owner");
    require(bytes(name_).length != 0, "name");
    owner = owner_;
    name = name_;
    delta = delta_;
  }
}
""")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id="no_unit_C",
            root=str(subj_root),
            flat_sol=str(flat),
            solast=str(subj_root / "flat.sol.solast"),
            contract="C",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        schedule = {
            "jobs": [],
            "summary": {"jobs": 0, "no_unit_rows": 1},
            "no_unit_rows": [{
                "reason": "target contract has no public/external FunctionDefinition units",
            }],
        }

        def fake_forge(_project, test_name, _timeout):
            out = json.dumps({
                "test/CDeployOnlyCovTest.t.sol:CDeployOnlyCovTest": {
                    "test_results": {
                        f"{test_name}()": {"status": "Success"},
                    },
                },
            })
            return "Success", False, 0.01, out

        stage = rq1_veriput_run.emit_no_unit_deploy_fallback(
            subject, root / "case", schedule, 1, forge_runner=fake_forge)
        summary = rq1_veriput_run.summarize_put_artifacts(
            root / "case" / "put")
        test_file = Path(stage["test_file"])
        text = test_file.read_text()
    bad = 0
    bad += check(stage["status"] == "ok",
                 f"deploy-only fallback stage is green: {stage}")
    bad += check(
        "new C(address(uint160(1000)), \"VeriPUT1001\", int256(1))" in text,
                 f"constructor args are synthesized safely: {text}")
    bad += check('from "../src/flat.sol"' in text,
                 f"deploy-only test imports the copied source: {text}")
    bad += check(summary["raw"] == 1 and summary["valid"] == 1,
                 f"deploy-only artifact is raw and valid: {summary}")
    bad += check(summary["concrete_raw"] == 1 and summary["concrete_valid"] == 1
                 and summary["put_raw"] == 0,
                 f"deploy-only artifact is concrete, not PUT: {summary}")
    bad += check(summary["quality_bucket"] == "valid-no-PUT",
                 f"methodology bucket remains concrete-only: {summary}")
    return bad


def test_no_unit_deploy_fallback_uses_prepared_source_fallback():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subject_id = "relocated_no_unit_subject"
        old_flat = root / "Results" / "Peer182" / "subjects" / subject_id / "flat.sol"
        fallback = (
            root / "scripts" / "Results" / "workdirs" / "Peer182"
            / "subjects" / subject_id / "flat.sol")
        fallback.parent.mkdir(parents=True)
        fallback.write_text("""\
pragma solidity ^0.8.20;
contract RelocatedNoUnit {
  constructor() {}
}
""")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id=subject_id,
            root=str(old_flat.parent),
            flat_sol=str(old_flat),
            solast=str(old_flat.with_name("flat.sol.solast")),
            contract="RelocatedNoUnit",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        legacy_schedule = {
            "jobs": [],
            "summary": {
                "jobs": 0,
                "subjects": 0,
                "skipped_by_status": {},
            },
        }

        def fake_forge(_project, test_name, _timeout):
            out = json.dumps({
                "test/RelocatedNoUnitDeployOnlyCovTest.t.sol:"
                "RelocatedNoUnitDeployOnlyCovTest": {
                    "test_results": {
                        f"{test_name}()": {"status": "Success"},
                    },
                },
            })
            return "Success", False, 0.01, out

        old_root = rq1_veriput_run.DEFAULT_VERIPUT_ROOT
        rq1_veriput_run.DEFAULT_VERIPUT_ROOT = root
        try:
            stage = rq1_veriput_run.emit_no_unit_deploy_fallback(
                subject,
                root / "case",
                legacy_schedule,
                1,
                forge_runner=fake_forge)
        finally:
            rq1_veriput_run.DEFAULT_VERIPUT_ROOT = old_root

        copied = (
            root / "case" / "put" / "deploy_only" / "Project"
            / "src" / "flat.sol")
        copied_text = copied.read_text()
        summary = rq1_veriput_run.summarize_put_artifacts(
            root / "case" / "put")

    bad = 0
    bad += check(stage["status"] == "ok",
                 f"relocated prepared source still emits fallback: {stage}")
    bad += check("RelocatedNoUnit" in copied_text,
                 f"fallback flat.sol was copied from workdir source: {copied}")
    bad += check(summary["valid"] == 1 and summary["concrete_valid"] == 1,
                 f"relocated fallback is counted as valid concrete: {summary}")
    return bad


def test_run_subject_records_no_unit_deploy_fallback_schema():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "jobs": [],
        "summary": {"jobs": 0, "no_unit_rows": 1},
        "no_unit_rows": [{
            "reason": "target contract has no public/external FunctionDefinition units",
        }],
    }

    def fake_emit(_subject, case_dir, _schedule, _forge_timeout):
        out_root = case_dir / "put" / "deploy_only"
        out_root.mkdir(parents=True, exist_ok=True)
        row = {
            "kind": "concrete",
            "stage2_source": "no_unit_deploy_fallback",
            "unit": "__deploy__",
            "enc": 0,
            "test": "test_cov_C_deploy_only",
            "file": str(out_root / "Project" / "test" / "CDeployOnlyCovTest.t.sol"),
            "forge_status": "Success",
            "valid_reference_test": True,
            "b": False,
        }
        (out_root / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 0,
                "concrete_replays_emitted": 1,
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "total": 1,
                    "put": 0,
                    "concrete": 1,
                },
                "rows": [row],
            },
            "timing": {
                "generation_wall_s": 0.0,
                "emission_wall_s": 0.02,
                "foundry_replay_wall_s": 0.03,
                "total_wall_s": 0.05,
            },
        }))
        return {
            "stage": "no-unit-deploy-fallback",
            "status": "ok",
            "put_out_root": str(out_root),
            "wall_s": 0.05,
            "forge_wall_s": 0.03,
        }

    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_emit = rq1_veriput_run.emit_no_unit_deploy_fallback
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (
        lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (
        lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.emit_no_unit_deploy_fallback = fake_emit
    try:
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            row, detail = rq1_veriput_run.run_subject({
                "subject_id": "s",
                "benchmark": "peer182",
                "contract": "C",
            }, "peer182", args)
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.emit_no_unit_deploy_fallback = old_emit

    bad = 0
    bad += check(row["status"] == "ok" and row["valid"] == 1,
                 f"deploy-only fallback promotes true no-unit to valid: {row}")
    bad += check(row["concrete_valid"] == 1 and row["put_valid"] == 0,
                 f"deploy-only fallback remains concrete-only: {row}")
    bad += check(row["no_unit_deploy_fallback_count"] == 1,
                 f"fallback count is retained in row: {row}")
    bad += check(row["no_unit_deploy_fallback_statuses"] == ["ok"],
                 f"fallback status is retained in row: {row}")
    bad += check(row["foundry_replay_wall_s"] == 0.03,
                 f"fallback replay timing is retained: {row}")
    bad += check((detail.get("stages") or [{}])[0].get("stage")
                 == "no-unit-deploy-fallback",
                 f"fallback stage is retained in detail: {detail}")
    return bad


def test_real203_cache_uses_prepared_benchmark_namespace():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="repo__C",
        root="/prepared/repo__C",
        flat_sol="/prepared/repo__C/flat.sol",
        solast="/prepared/repo__C/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin="/bin/solc",
        solc_extra=(),
        metadata={
            "status": "ok",
        },
    )
    cached = rq1_veriput_run.cached_subject(
        subject, Path("/tmp/cache"), "real203")
    return check(cached.solast == "/tmp/cache/stress243/stress243__repo__C/flat.sol.solast",
                 f"real203 output label does not change AST cache namespace: "
                 f"{cached.solast}")


def test_jobs_admission_refuses_oversubscription():
    old = rq1_veriput_run._mem_available_gib
    rq1_veriput_run._mem_available_gib = lambda: 20.0
    args = argparse.Namespace(jobs=2, memlimit_gib=8, mem_fraction=0.70)
    try:
        try:
            rq1_veriput_run.validate_jobs(args)
            refused = False
        except rq1_veriput_run.RQ1RunError:
            refused = True
    finally:
        rq1_veriput_run._mem_available_gib = old
    return check(refused, "subject concurrency refuses memory oversubscription")


def test_target_rows_fast_first_sorts_before_limit():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fast = root / "Results" / "BugFix124" / "subjects" / "fast" / "flat.sol"
            slow = root / "Results" / "BugFix124" / "subjects" / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, dataset_rows = rq1_veriput_run.target_rows(
                root, "bugfix124", [], 1, "dataset")
            _label, fast_rows = rq1_veriput_run.target_rows(
                root, "bugfix124", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    bad = 0
    bad += check(dataset_rows[0]["subject_id"] == "slow",
                 "dataset order is preserved before limit")
    bad += check(fast_rows[0]["subject_id"] == "fast",
                 "fast-first sorts by prepared flat.sol size before limit")
    return bad


def test_target_rows_fast_first_uses_bugfix_fallback_size():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "scripts" / "Results" / "workdirs" \
                / "BugFix124" / "subjects"
            fast = base / "fast" / "flat.sol"
            slow = base / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, fast_rows = rq1_veriput_run.target_rows(
                root, "bugfix124", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    return check(fast_rows[0]["subject_id"] == "fast",
                 "bugfix fast-first uses fallback prepared flat.sol size")


def test_target_rows_fast_first_uses_peer_fallback_size():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "peer182",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "peer182",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "scripts" / "Results" / "workdirs" \
                / "Peer182" / "subjects"
            fast = base / "fast" / "flat.sol"
            slow = base / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, fast_rows = rq1_veriput_run.target_rows(
                root, "peer182", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    return check(fast_rows[0]["subject_id"] == "fast",
                 "peer fast-first uses fallback prepared flat.sol size")


def test_certification_summary_identifies_inner_timeouts():
    with tempfile.TemporaryDirectory() as td:
        cert = Path(td) / "certify-results.jsonl"
        rows = [
            {
                "bucket": "KILLED",
                "exit": 124,
                "unit": "transfer",
                "witnessed": None,
                "certified": {},
                "not_certified": {},
            },
            {
                "bucket": "KILLED",
                "exit": 1,
                "unit": "fallback",
                "witnessed": None,
                "wall_s": 120.3,
                "run_timeout_s": 120,
                "driver_diagnostic": {
                    "tag": "goto-inline-call-type-mismatch",
                    "category": "no-cov-report",
                },
                "certified": {},
                "not_certified": {},
            },
        ]
        cert.write_text("".join(json.dumps(row) + "\n" for row in rows))
        summary = rq1_veriput_run.summarize_certification(cert)
    bad = 0
    bad += check(summary["bucket_counts"] == {"KILLED": 2},
                 f"certification buckets retained: {summary}")
    bad += check(summary["exit_counts"] == {"1": 1, "124": 1},
                 f"certification exits retained: {summary}")
    bad += check(summary["witness_counts"] == {"unknown": 2},
                 f"witness status retained: {summary}")
    bad += check(summary["timed_out_units"] == ["fallback", "transfer"],
                 f"inner timeout unit identified: {summary}")
    bad += check(summary["driver_diagnostic_tags"] == {
        "goto-inline-call-type-mismatch": 1,
    }, f"driver diagnostic tags retained: {summary}")
    bad += check(rq1_veriput_run._no_output_reason(summary) ==
                 "certification timed out before PUT artifacts: fallback, transfer",
                 "no-output reason distinguishes inner certification timeout")
    return bad


def test_certification_summary_uses_diagnostics_for_no_output_reason():
    summary = {
        "rows": 2,
        "certified_regions": 0,
        "driver_diagnostic_tags": {
            "frontend-tuple-rhs-symbol": 1,
            "path-coverage-no-claims-reached-solver": 1,
        },
        "bucket_counts": {"NO-WITNESS-UNKNOWN": 2},
    }
    return check(
        rq1_veriput_run._no_output_reason(summary) ==
        "no certified regions: diagnostics frontend-tuple-rhs-symbol=1, "
        "path-coverage-no-claims-reached-solver=1",
        "diagnostics outrank coarse bucket counts in no-output reason")


def test_cleared_concrete_fallbacks_trigger_stage4():
    with tempfile.TemporaryDirectory() as td:
        cert = Path(td) / "certify-results.jsonl"
        rows = [
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {"7": "single point cleared"},
                "not_certified_details": {
                    "7": {
                        "enc": 7,
                        "concrete_fallback": True,
                        "witness_check": "SUCCESSFUL",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {"9": "no generalisable coordinate"},
                "not_certified_details": {
                    "9": {
                        "enc": 9,
                        "concrete_fallback": True,
                        "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {"8": "unknown point"},
                "not_certified_details": {
                    "8": {
                        "enc": 8,
                        "concrete_fallback": True,
                        "witness_check": "UNKNOWN",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 124,
                "witnessed": 1,
                "certified": {},
                "not_certified": {},
                "partial_witness_journal": {
                    "partial": True,
                    "witness_count": 1,
                    "paths": [{
                        "path_id": "15",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    }],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 1,
                "witnessed": 2,
                "certified": {"41": "already certified"},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "path-coverage-partial-journal-no-report",
                    "category": "no-cov-report",
                },
                "partial_witness_journal": {
                    "source_stage": "partial-witness-journal",
                    "partial": True,
                    "witness_count": 2,
                    "paths": [
                        {
                            "path_id": "41",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "42",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 124,
                "witnessed": 1,
                "certified": {"15": "already measured"},
                "not_certified": {"17": "already rejected"},
                "partial_witness_journal": {
                    "partial": True,
                    "witness_count": 3,
                    "paths": [
                        {
                            "path_id": "15",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "16",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "17",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NO-COORDINATE",
                "certified": {"21": "already certified"},
                "not_certified": {},
                "partial_witness_journal": {
                    "complete": True,
                    "witness_count": 2,
                    "paths": [
                        {
                            "path_id": "21",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "22",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "CERTIFIED",
                "certified": {},
                "not_certified": {},
                "partial_witness_journal": {
                    "source_stage": "certified-no-coordinate",
                    "complete": True,
                    "witness_count": 2,
                    "paths": [
                        {
                            "path_id": "31",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "32",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
        ]
        cert.write_text("".join(json.dumps(row) + "\n" for row in rows))
        cleared_count = rq1_veriput_run._cleared_concrete_fallback_count(
            cert, "bench", "approve")
        timeout_count = rq1_veriput_run._timeout_concrete_fallback_count(
            cert, "bench", "approve")
        complete_count = rq1_veriput_run._complete_witness_concrete_fallback_count(
            cert, "bench", "approve")
        partial_journal_count = \
            rq1_veriput_run._partial_journal_concrete_fallback_count(
                cert, "bench", "approve")
    argv = rq1_veriput_run._put_argv(
        cert, "approve", "bench", Path("/tmp/out"), 600, 12, 300)
    bad = 0
    bad += check(cleared_count == 2,
                 "cleared and complete-witness concrete fallbacks trigger Stage 4")
    bad += check(timeout_count == 2,
                 "timed-out witnessed certification rows skip only occupied encs")
    bad += check(complete_count == 3,
                 "complete witnessed rows include certified no-coordinate fallbacks")
    bad += check(partial_journal_count == 1,
                 "partial witness journals without verdict trigger Stage 4")
    bad += check(rq1_veriput_run._is_concrete_only_stage4(0, 0, 0, 0, 1),
                 "partial-journal-only candidates are concrete-only Stage 4")
    bad += check("--emit-cleared-concrete-fallbacks" in argv,
                 f"RQ1 Stage 4 enables cleared concrete fallback emission: {argv}")
    return bad


def test_subject_schedule_uses_separate_esbmc_run_timeout():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="bugfix124",
        subject_id="s",
        root="/prepared/s",
        flat_sol="/prepared/s/flat.sol",
        solast="/prepared/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin="/bin/solc",
        solc_extra=(),
        metadata={
            "status": "ok",
        },
    )
    old_manifest = rq1_veriput_run.subject_unit_manifest.manifest_for_subject
    old_schedule = rq1_veriput_run.unit_schedule.build_schedule
    captured = {}
    rq1_veriput_run.subject_unit_manifest.manifest_for_subject = lambda *_a, **_kw: {
        "status": "ok",
        "units": {
            "units": ["f"],
        },
    }

    def fake_schedule(_manifest, **kwargs):
        captured.update(kwargs)
        return {
            "jobs": [],
            "summary": {},
        }

    rq1_veriput_run.unit_schedule.build_schedule = fake_schedule
    try:
        rq1_veriput_run.build_subject_schedule(
            subject,
            {
                "units_hint": ["f"],
            },
            Path("/tmp/cache"),
            Path("/tmp/case"),
            timeout_s=600,
            run_timeout_s=120,
            memlimit_gib=12)
    finally:
        rq1_veriput_run.subject_unit_manifest.manifest_for_subject = old_manifest
        rq1_veriput_run.unit_schedule.build_schedule = old_schedule
    bad = 0
    bad += check(captured.get("timeout_s") == 600,
                 f"subject budget is preserved: {captured}")
    bad += check(captured.get("run_timeout_s") == 120,
                 f"per-ESBMC run budget is separate: {captured}")
    return bad


def test_certify_argv_for_remaining_caps_only_run_timeout():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--timeout",
            "600",
            "--run-timeout",
            "600",
            "--memlimit-gib",
            "8",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    argv = rq1_veriput_run._certify_argv_for_remaining(
        job,
        remaining_s=599.8,
        run_timeout_s=120,
        memlimit_gib=12,
        stage_mem_fraction=0.70)
    pairs = dict(zip(argv, argv[1:]))
    bad = 0
    bad += check(pairs.get("--timeout") == "599",
                 f"whole certify budget follows remaining case time: {argv}")
    bad += check(pairs.get("--run-timeout") == "120",
                 f"per-ESBMC run budget is capped: {argv}")
    bad += check(pairs.get("--memlimit-gib") == "12",
                 f"memory budget is authoritative: {argv}")
    bad += check(pairs.get("--mem-fraction") == "0.7",
                 f"stage memory fraction is passed to certify_all: {argv}")
    return bad


def test_stage2_unit_timeout_cap_defaults_to_adaptive():
    bad = 0
    bad += check(rq1_veriput_run.DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S == 0,
                 "explicit Stage-2 unit timeout cap defaults to unset")
    bad += check(rq1_veriput_run.DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S > 0,
                 "adaptive Stage-2 unit timeout cap is enabled by default")
    return bad


def test_adaptive_stage2_unit_timeout_cap_policy():
    cheap_job = {
        "schedule_rank": {
            "cheap_first": [10, 0, 0],
        },
    }
    expensive_job = {
        "schedule_rank": {
            "cheap_first": [
                rq1_veriput_run.ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
                1,
                0,
            ],
        },
    }
    args = argparse.Namespace(
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=120,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
    )
    explicit = argparse.Namespace(stage2_unit_timeout_cap_s=90,
                                  adaptive_stage2_unit_timeout_cap_s=120)
    disabled = argparse.Namespace(stage2_unit_timeout_cap_s=0,
                                  adaptive_stage2_unit_timeout_cap_s=0)
    cap = rq1_veriput_run._effective_stage2_unit_timeout_cap_s
    bad = 0
    bad += check(cap(cheap_job, args, 1) == 0,
                 "single cheap unit remains uncapped")
    bad += check(cap(cheap_job, args,
                     rq1_veriput_run.ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD) == 120,
                 "multi-unit subject gets adaptive Stage-2 cap")
    bad += check(cap(expensive_job, args, 1) == 120,
                 "expensive-looking unit gets adaptive Stage-2 cap")
    bad += check(cap(cheap_job, args, 3, prior_no_candidate_units=1) == 120,
                 "later units are capped after a no-candidate prefix")
    bad += check(cap(expensive_job, explicit, 1) == 90,
                 "explicit Stage-2 cap overrides adaptive policy")
    bad += check(cap(expensive_job, disabled, 10) == 0,
                 "adaptive Stage-2 cap can be disabled")
    return bad


def test_stage2_wrapper_timeout_uses_effective_unit_cap():
    wrapper = rq1_veriput_run._stage2_wrapper_timeout_s
    bad = 0
    bad += check(wrapper(599.8, 60, 120) == 180.0,
                 "Stage-2 wrapper timeout follows effective unit cap plus grace")
    bad += check(wrapper(91.2, 60, 120) == 151.2,
                 "Stage-2 wrapper timeout never exceeds remaining budget plus grace")
    bad += check(wrapper(599.8, 60, 0) == 659.8,
                 "uncapped Stage-2 wrapper keeps subject remaining budget plus grace")
    return bad


def test_schedule_annotation_records_runtime_stage2_caps():
    schedule = {
        "jobs": [
            {
                "unit": "cheap",
                "schedule_rank": {
                    "cheap_first": [10, 0, 0],
                },
            },
            {
                "unit": "expensive",
                "schedule_rank": {
                    "cheap_first": [70, 0, 0],
                },
            },
        ],
    }
    args = argparse.Namespace(
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=120,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
    )
    rq1_veriput_run.annotate_stage2_runtime_policy(schedule, args)
    cheap = schedule["jobs"][0]["rq1_stage2_runtime_policy"]
    expensive = schedule["jobs"][1]["rq1_stage2_runtime_policy"]
    policy = schedule["rq1_stage2_runtime_policy"]
    bad = 0
    bad += check(policy["capped_timeout_advances_to_next_unit"] is True,
                 f"schedule records capped-timeout continuation policy: {policy}")
    bad += check(cheap["initial_effective_unit_timeout_cap_s"] == 0,
                 f"cheap first unit starts uncapped in a two-unit subject: {cheap}")
    bad += check(cheap["after_no_candidate_effective_unit_timeout_cap_s"] == 120,
                 f"cheap later unit is capped after a no-candidate prefix: {cheap}")
    bad += check(expensive["initial_effective_unit_timeout_cap_s"] == 120,
                 f"expensive unit is capped immediately: {expensive}")
    return bad


def test_certify_argv_for_remaining_honors_unit_timeout_cap():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--timeout",
            "600",
            "--run-timeout",
            "600",
            "--memlimit-gib",
            "8",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    argv = rq1_veriput_run._certify_argv_for_remaining(
        job,
        remaining_s=599.8,
        run_timeout_s=120,
        memlimit_gib=12,
        unit_timeout_cap_s=90)
    pairs = dict(zip(argv, argv[1:]))
    bad = 0
    bad += check(pairs.get("--timeout") == "90",
                 f"whole certify budget follows unit cap: {argv}")
    bad += check(pairs.get("--run-timeout") == "90",
                 f"per-ESBMC run budget cannot exceed capped unit budget: {argv}")
    return bad


def test_stage2_cert_shard_argv_and_merge():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--out",
            "/tmp/canonical.jsonl",
            "--timeout",
            "600",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shard = root / "shard.jsonl"
        canonical = root / "canonical.jsonl"
        argv = rq1_veriput_run._certify_argv_for_remaining(
            job,
            remaining_s=100,
            run_timeout_s=100,
            memlimit_gib=8,
            unit_timeout_cap_s=20,
            out_path=shard)
        pairs = dict(zip(argv, argv[1:]))
        shard.write_text(
            json.dumps({"unit": "a", "bucket": "KILLED"}) + "\n"
            + "{not json}\n"
            + json.dumps({"unit": "b", "bucket": "CERTIFIED"}) + "\n")
        merge = rq1_veriput_run._merge_jsonl_records(canonical, shard)
        rows = [
            json.loads(line)
            for line in canonical.read_text().splitlines()
            if line.strip()
        ]
    bad = 0
    bad += check(pairs.get("--out") == str(shard),
                 f"Stage-2 certify argv writes to per-unit shard: {argv}")
    bad += check(merge["merged"] == 2 and merge["invalid"] == 1,
                 f"cert shard merge counts valid and invalid rows: {merge}")
    bad += check([row["unit"] for row in rows] == ["a", "b"],
                 f"canonical cert JSONL receives shard rows: {rows}")
    return bad


def test_capped_stage2_timeout_advances_to_next_unit():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    jobs = []
    for unit in ("slowA", "slowB"):
        jobs.append({
            "unit": unit,
            "job_id": f"job-{unit}",
            "schedule_rank": {
                "cheap_first": [70, 0, 0],
            },
            "certification_budget": {
                "workdir": f"/tmp/work/{unit}",
            },
            "certify_argv": [
                "python3",
                "certify_all.py",
                "--unit",
                unit,
                "--timeout",
                "600",
                "--run-timeout",
                "600",
            ],
        })
    schedule = {
        "jobs": jobs,
        "summary": {
            "jobs": 2,
        },
    }
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = None
        if "--out" in argv:
            out_i = argv.index("--out") + 1
            if out_i < len(argv):
                out_path = argv[out_i]
        unit = "slowA" if len(calls) == 1 else "slowB"
        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(
                json.dumps({
                    "benchmark": subject.benchmark_key,
                    "unit": unit,
                    "bucket": "NO-WITNESS-UNKNOWN",
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "frontend-tuple-rhs-symbol",
                        "category": "no-cov-report",
                    },
                }) + "\n")
        return {
            "argv": argv,
            "rc": None,
            "status": "timeout",
            "timed_out": True,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_wait = rq1_veriput_run.wait_for_mem_budget
    old_run = rq1_veriput_run.run_command
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (
        lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (
        lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.wait_for_mem_budget = lambda *_args, **_kwargs: {
        "status": "ok",
        "waited": False,
    }
    rq1_veriput_run.run_command = fake_run_command
    try:
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(
                result_root=td,
                ast_cache_root=str(Path(td) / "ast"),
                redo=False,
                timeout=30,
                esbmc_run_timeout=30,
                memlimit_gib=1,
                stage2_unit_timeout_cap_s=0,
                adaptive_stage2_unit_timeout_cap_s=2,
                wrapper_grace=3,
                min_remaining_s=1,
                stage_mem_fraction=0.1,
                mem_wait_poll_s=0.1,
                no_candidate_stage2_unit_stop_n=1,
                min_no_candidate_stage2_unit_stop_n=1,
                no_output_stage2_stop_s=0,
                min_no_output_stage2_unit_stop_n=0,
                skip_concrete_only_after_put_valid=0,
                min_concrete_only_stage4_s=0,
                min_timeout_only_stage4_s=0,
                zero_output_stage4_stop_s=0,
                forge_timeout=1,
                jobs=1,
            )
            row, detail = rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                },
                "peer182",
                args)
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.wait_for_mem_budget = old_wait
        rq1_veriput_run.run_command = old_run

    bad = 0
    bad += check(len(calls) == 2,
                 f"capped Stage-2 tool timeout advances to the next unit: "
                 f"{calls}")
    bad += check([call[1] for call in calls] == [5.0, 5.0],
                 f"wrapper timeout is unit cap plus grace: {calls}")
    bad += check(row["units_attempted"] == ["slowA", "slowB"],
                 f"both capped units are recorded as attempted: {row}")
    bad += check(row["stage2_capped_timeout_units"] == ["slowA", "slowB"],
                 f"capped timeout units are retained: {row}")
    bad += check(row["stage2_no_candidate_stop_skipped_unit_count"] == 2,
                 f"capped no-cov-report rows do not count as no-candidate "
                 f"stop evidence: {row}")
    bad += check(row["status"] == "no-output",
                 f"all-capped no-output subject is not a runner error: {row}")
    stages = detail.get("stages") or []
    bad += check(all(stage.get("wrapper_timeout_s") == 5.0 for stage in stages),
                 f"stage records keep wrapper cap: {stages}")
    return bad


def test_stage2_no_output_stop_ignores_tool_failure_units():
    subject, schedule = _mocked_subject_and_schedule(["badA", "badB", "badC"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        out_path.write_text(json.dumps({
            "benchmark": subject.benchmark_key,
            "unit": unit,
            "bucket": "NO-WITNESS-UNKNOWN",
            "certified": {},
            "not_certified": {},
            "driver_diagnostic": {
                "tag": "path-coverage-no-claims-reached-solver",
                "category": "no-cov-report",
            },
        }) + "\n")
        return {
            "argv": argv,
            "rc": None,
            "status": "timeout",
            "timed_out": True,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            args.no_output_stage2_stop_s = 1
            args.min_no_output_stage2_unit_stop_n = 2
            return rq1_veriput_run.run_subject({
                "subject_id": "s",
                "benchmark": "peer182",
                "contract": "C",
            }, "peer182", args)

    row, _detail = _with_mocked_run_subject(
        subject, schedule, fake_run_command, body)
    bad = 0
    bad += check(len(calls) == 3,
                 f"tool/focus failures do not trip Stage-2 no-output stop: "
                 f"{calls}")
    bad += check(row["stage2_no_candidate_evidence_units"] == 0,
                 f"tool failures are not no-candidate evidence: {row}")
    bad += check(row["stage2_no_candidate_stop_skipped_unit_count"] == 3,
                 f"tool failures are audited as skipped stop evidence: {row}")
    return bad


def test_overload_refusal_appends_path_function_jobs():
    subject, schedule = _mocked_subject_and_schedule(["f"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        path_function = None
        if "--path-function" in argv:
            path_function = argv[argv.index("--path-function") + 1]
        if path_function is None:
            row = {
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "bucket": "DRIVER-REFUSED",
                "certified": {},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "overloaded-unit-path-function-required",
                    "reason": "explicit path function required",
                    "path_functions": [
                        "sol:@C@C@F@f#11",
                        "sol:@C@C@F@f#12",
                    ],
                },
            }
        else:
            row = {
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "path_function": path_function,
                "bucket": "NO-WITNESS-UNKNOWN",
                "certified": {},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "esbmc-no-cov-report",
                    "category": "no-cov-report",
                },
            }
        out_path.write_text(json.dumps(row) + "\n")
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            return rq1_veriput_run.run_subject({
                "subject_id": "s",
                "benchmark": "peer182",
                "contract": "C",
            }, "peer182", args)

    row, detail = _with_mocked_run_subject(
        subject, schedule, fake_run_command, body)
    path_function_calls = [
        argv[argv.index("--path-function") + 1]
        for argv, _timeout_s, _log_prefix in calls
        if "--path-function" in argv
    ]
    overload_stages = [
        stage for stage in (detail.get("stages") or [])
        if stage.get("stage") == "schedule-overload-path-functions"
    ]
    bad = 0
    bad += check(len(calls) == 3,
                 f"runner retries each overload path function: {calls}")
    bad += check(path_function_calls == [
        "sol:@C@C@F@f#11",
        "sol:@C@C@F@f#12",
    ], f"dynamic jobs pin the path functions: {path_function_calls}")
    bad += check(overload_stages and overload_stages[0]["added_jobs"] == 2,
                 f"overload expansion is audited: {overload_stages}")
    bad += check(row["units_scheduled"] == 3,
                 f"row sees appended overload jobs: {row}")
    bad += check(row["overload_path_function_retry_count"] == 2
                 and row["overload_path_function_retry_units"] == ["f"],
                 f"row summarizes overload expansion: {row}")
    return bad


def _minimal_run_subject_args(result_root: str) -> argparse.Namespace:
    return argparse.Namespace(
        result_root=result_root,
        ast_cache_root=str(Path(result_root) / "ast"),
        redo=False,
        timeout=30,
        esbmc_run_timeout=30,
        memlimit_gib=1,
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=2,
        wrapper_grace=3,
        min_remaining_s=1,
        stage_mem_fraction=0.1,
        mem_wait_poll_s=0.1,
        no_candidate_stage2_unit_stop_n=0,
        min_no_candidate_stage2_unit_stop_n=0,
        no_output_stage2_stop_s=0,
        min_no_output_stage2_unit_stop_n=0,
        skip_concrete_only_after_put_valid=0,
        min_concrete_only_stage4_s=0,
        min_timeout_only_stage4_s=0,
        zero_output_stage4_stop_s=0,
        concrete_only_stage4_timeout_cap_s=0,
        skip_concrete_only_after_any_valid=True,
        forge_timeout=1,
        jobs=1,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
        unit=[],
    )


def _mocked_subject_and_schedule(units: list[str]) -> tuple:
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    jobs = []
    for unit in units:
        jobs.append({
            "unit": unit,
            "job_id": f"job-{unit}",
            "schedule_rank": {
                "cheap_first": [70, 0, 0],
            },
            "certification_budget": {
                "workdir": f"/tmp/work/{unit}",
            },
            "certify_argv": [
                "python3",
                "certify_all.py",
                "--unit",
                unit,
                "--timeout",
                "600",
                "--run-timeout",
                "600",
            ],
        })
    return subject, {
        "jobs": jobs,
        "summary": {
            "jobs": len(jobs),
        },
    }


def _with_mocked_run_subject(subject, schedule, fake_run_command, body):
    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_wait = rq1_veriput_run.wait_for_mem_budget
    old_run = rq1_veriput_run.run_command
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (
        lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (
        lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.wait_for_mem_budget = lambda *_args, **_kwargs: {
        "status": "ok",
        "waited": False,
    }
    rq1_veriput_run.run_command = fake_run_command
    try:
        return body()
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.wait_for_mem_budget = old_wait
        rq1_veriput_run.run_command = old_run


def test_capped_stage2_timeout_with_candidates_enters_stage4():
    subject, schedule = _mocked_subject_and_schedule(["slowA"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        if "put_all.py" not in " ".join(str(arg) for arg in argv):
            out_path = Path(argv[argv.index("--out") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({
                "benchmark": subject.benchmark_key,
                "unit": "slowA",
                "bucket": "TIMEOUT",
                "partial_witness_journal": {
                    "witness_count": 1,
                    "paths": [{
                        "path_id": "7",
                        "path_function": "sol:@C@C@F@slowA#1",
                        "witness_count": 1,
                    }],
                },
                "timed_out": True,
            }) + "\n")
            return {
                "argv": argv,
                "rc": None,
                "status": "timeout",
                "timed_out": True,
                "wall_s": round(timeout_s, 3),
                "maxrss_proc_mb": 1.0,
            }
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": 0.1,
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            return rq1_veriput_run.run_subject({
                "subject_id": "s",
                "benchmark": "peer182",
                "contract": "C",
            }, "peer182", args)

    row, detail = _with_mocked_run_subject(
        subject, schedule, fake_run_command, body)
    stages = detail.get("stages") or []
    bad = 0
    bad += check(len(calls) == 2,
                 f"capped timeout with candidates still runs Stage 4: {calls}")
    bad += check(stages[0].get("capped_timeout_stage4_candidates_retained") is True,
                 f"Stage-2 timeout records retained candidates: {stages}")
    bad += check(len(stages) > 1 and stages[1].get("stage") == "put",
                 f"second stage is PUT generation: {stages}")
    bad += check(row["stage4_candidate_units_attempted"] == 1,
                 f"Stage-4 attempt is counted: {row}")
    return bad


def test_timeout_only_stage4_skip_can_trigger_no_candidate_stop():
    subject, schedule = _mocked_subject_and_schedule(["slowA", "slowB"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        if "--unit" not in argv:
            return {
                "argv": argv,
                "rc": 0,
                "status": "ok",
                "timed_out": False,
                "wall_s": 0.1,
                "maxrss_proc_mb": 1.0,
            }
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        out_path.write_text(json.dumps({
            "benchmark": subject.benchmark_key,
            "unit": unit,
            "bucket": "TIMEOUT",
            "partial_witness_journal": {
                "witness_count": 1,
                "paths": [{
                    "path_id": "7",
                    "path_function": f"sol:@C@C@F@{unit}#1",
                    "witness_count": 1,
                }],
            },
            "timed_out": True,
        }) + "\n")
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": 0.1,
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            args.min_timeout_only_stage4_s = 90
            args.no_candidate_stage2_unit_stop_n = 1
            args.min_no_candidate_stage2_unit_stop_n = 1
            args.timeout = 10
            return rq1_veriput_run.run_subject({
                "subject_id": "s",
                "benchmark": "peer182",
                "contract": "C",
            }, "peer182", args)

    row, detail = _with_mocked_run_subject(
        subject, schedule, fake_run_command, body)
    bad = 0
    bad += check(len(calls) == 2,
                 f"timeout-only skip advances to the next unit: {calls}")
    bad += check(row["status"] == "no-output"
                 and row["completion_status"] == "ok",
                 f"skip-driven no-candidate does not early-stop before "
                 f"remaining units: {row}")
    bad += check(row["max_consecutive_no_candidate_units"] == 1,
                 f"skip contributes to no-candidate evidence: {row}")
    bad += check(row["low_budget_timeout_only_stage4_skips"][0]["unit"] == "slowA",
                 f"skip detail is retained: {row}")
    return bad


def test_prepare_case_dir_preserves_complete_and_quarantines_partial():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        complete = root / "complete"
        complete.mkdir()
        (complete / "result.json").write_text("{}\n")
        partial = root / "partial"
        partial.mkdir()
        (partial / "unit-schedule.json").write_text("{}\n")
        redo = root / "redo"
        redo.mkdir()
        (redo / "result.json").write_text("{}\n")
        rq1_veriput_run.prepare_case_dir(complete)
        rq1_veriput_run.prepare_case_dir(partial)
        rq1_veriput_run.prepare_case_dir(redo, force_fresh=True)
        quarantined = list(root.glob("partial.incomplete.*"))
        redone = list(root.glob("redo.redo.*"))
        bad = 0
        bad += check(complete.exists() and complete.joinpath("result.json").exists(),
                     "complete case directory is preserved")
        bad += check(not partial.exists() and len(quarantined) == 1,
                     f"partial case directory is quarantined: {quarantined}")
        bad += check(not redo.exists() and len(redone) == 1,
                     f"redo case directory is archived fresh: {redone}")
        return bad


def test_stage2_no_output_stop_reason_is_audit_friendly():
    stages = [
        {
            "stage": "certify",
            "wall_s": 61.25,
        },
        {
            "stage": "put",
            "wall_s": 5.0,
        },
        {
            "stage": "certify",
            "wall_s": 43.75,
        },
    ]
    wall_s = rq1_veriput_run._stage_wall_s(stages, "certify")
    reason = rq1_veriput_run._format_stage2_no_output_stop(wall_s)
    bad = 0
    bad += check(wall_s == 105.0,
                 f"Stage-2 wall clock sums only certification stages: {wall_s}")
    bad += check(reason == "no output after 105.0s Stage 2; "
                 "stopped before remaining units",
                 f"early-stop reason is stable: {reason}")
    return bad


def test_stage2_no_output_stop_requires_multiple_no_candidate_units():
    stages = [
        {
            "stage": "certify",
            "wall_s": 180.0,
        },
    ]
    bad = 0
    bad += check(not rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 1, 4),
                 "one heavy no-candidate unit does not end a multi-unit subject")
    bad += check(not rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 2, 4),
                 "Stage-2 no-output stop does not skip remaining units")
    bad += check(rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 4, 4),
                 "Stage-2 no-output stop can fire after all scheduled units")
    bad += check(not rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 1}, 90, 4, 4),
                 "Stage-2 no-output stop keeps raw outputs")
    bad += check(not rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 1, 4, min_attempted_units=1),
                 "explicit one-unit policy still cannot skip remaining units")
    bad += check(rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 1, 1, min_attempted_units=1),
                 "one-unit subjects can still stop after their only miss")
    return bad


def test_tool_failures_do_not_count_as_no_candidate_stop_evidence():
    should_count = rq1_veriput_run._no_candidate_counts_against_stop
    bad = 0
    bad += check(not should_count({
        "bucket": "NO-WITNESS-UNKNOWN",
        "driver_diagnostic": {
            "tag": "path-coverage-no-claims-reached-solver",
        },
    }), "no-claims-reached is a tool/focus failure, not subject exhaustion")
    bad += check(not should_count({
        "bucket": "NO-WITNESS-UNKNOWN",
        "driver_diagnostic": {
            "tag": "frontend-tuple-rhs-symbol",
            "category": "no-cov-report",
        },
    }), "frontend no-report failure does not stop remaining cheap units")
    bad += check(should_count({
        "bucket": "NO-COORDINATE",
        "driver_diagnostic": None,
    }), "method-level no-coordinate still counts as no-candidate evidence")
    bad += check(should_count({
        "bucket": "NOT-CERTIFIED",
    }), "semantic refutation still counts as no-candidate evidence")
    return bad


def test_zero_output_stage4_stop_is_thresholded_and_raw_sensitive():
    stages = [
        {
            "stage": "put",
            "wall_s": 49.5,
        },
    ]
    bad = 0
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(
        stages, {"raw": 0}, 0), "Stage-4 zero-output stop defaults off")
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(
        stages, {"raw": 1}, 30), "Stage-4 zero-output stop keeps raw outputs")
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(
        stages, {"raw": 0}, 60), "Stage-4 zero-output stop waits for threshold")
    bad += check(rq1_veriput_run._should_stop_after_zero_output_stage4(
        stages, {"raw": 0}, 30), "Stage-4 zero-output stop fires after threshold")
    bad += check(rq1_veriput_run._format_stage4_no_output_stop(49.5) ==
                 "no output after 49.5s Stage 4; stopped before remaining units",
                 "Stage-4 early-stop reason is stable")
    return bad


def test_no_candidate_stage2_unit_stop_is_thresholded_and_raw_sensitive():
    bad = 0
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(
        4, {"raw": 0}, 0), "no-candidate unit stop defaults off")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(
        4, {"raw": 1}, 4), "no-candidate unit stop keeps raw outputs")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(
        3, {"raw": 0}, 4), "no-candidate unit stop waits for threshold")
    bad += check(rq1_veriput_run._should_stop_after_no_candidate_units(
        4, {"raw": 0}, 4), "no-candidate unit stop fires at threshold")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(
        4, {"raw": 0}, 4, units_scheduled=6),
                 "no-candidate unit stop does not skip remaining units")
    bad += check(rq1_veriput_run._should_stop_after_no_candidate_units(
        6, {"raw": 0}, 4, units_scheduled=6),
                 "no-candidate unit stop can fire after all scheduled units")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(
        4, {"raw": 0}, 4, pending_hinted_units=1),
                 "no-candidate unit stop does not skip pending target hints")
    jobs = [
        {"unit": "prefix", "unit_hints": {"hinted_units": ["target"]}},
        {"unit": "target", "unit_hints": {"hinted_units": ["target"]}},
    ]
    bad += check(rq1_veriput_run._pending_hinted_units(jobs, ["prefix"]) == 1,
                 "pending target hint is visible after a noisy prefix unit")
    bad += check(rq1_veriput_run._pending_hinted_units(
        jobs, ["prefix", "target"]) == 0,
                 "attempted target hint no longer blocks early stop")
    bad += check(rq1_veriput_run._format_no_candidate_unit_stop(4) ==
                 "no Stage-2 candidate after 4 consecutive units; "
                 "stopped before remaining units",
                 "no-candidate early-stop reason is stable")
    return bad


def test_low_budget_concrete_only_stage4_skip_is_valid_and_put_sensitive():
    should = rq1_veriput_run._should_skip_low_budget_concrete_only_stage4
    bad = 0
    bad += check(should({"raw": 4, "valid": 4}, 36.5, 90, 0, 0, 3),
                 "low-budget timeout-concrete-only Stage 4 skips after valid")
    bad += check(should({"raw": 4, "valid": 4}, 36.5, 90, 0, 1, 0),
                 "low-budget cleared-concrete-only Stage 4 skips after valid")
    bad += check(not should({"raw": 4, "valid": 4}, 120.0, 90, 0, 0, 3),
                 "concrete-only Stage 4 keeps enough generation budget")
    bad += check(not should({"raw": 4, "valid": 0}, 36.5, 90, 0, 0, 3),
                 "concrete-only Stage 4 is not skipped before a valid artifact")
    bad += check(not should({"raw": 4, "valid": 4}, 36.5, 90, 1, 0, 3),
                 "certified regions are never skipped by the concrete-only floor")
    bad += check(not should({"raw": 4, "valid": 4}, 36.5, 0, 0, 0, 3),
                 "concrete-only floor can be disabled")
    reason = rq1_veriput_run._format_low_budget_concrete_only_skip(36.456, 90)
    bad += check("36.5s remains below the 90s" in reason,
                 f"low-budget concrete-only reason is audit-friendly: {reason}")
    return bad


def test_low_budget_timeout_only_stage4_skip_is_candidate_sensitive():
    should = rq1_veriput_run._should_skip_low_budget_timeout_only_stage4
    bad = 0
    bad += check(should(36.5, 90, 0, 0, 1),
                 "low-budget timeout-only Stage 4 skips partial witnesses")
    bad += check(should(36.5, 90, 0, 0, 0, 1),
                 "low-budget complete-witness-only Stage 4 skips")
    bad += check(not should(36.5, 90, 1, 0, 1),
                 "certified regions are never skipped by timeout-only floor")
    bad += check(not should(36.5, 90, 0, 1, 1),
                 "cleared concrete fallback is not timeout-only")
    bad += check(not should(120.0, 90, 0, 0, 1),
                 "timeout-only Stage 4 keeps enough generation budget")
    bad += check(not should(36.5, 0, 0, 0, 1),
                 "timeout-only floor can be disabled")
    reason = rq1_veriput_run._format_low_budget_timeout_only_skip(36.456, 90)
    bad += check("36.5s remains below the 90s" in reason,
                 f"timeout-only reason is audit-friendly: {reason}")
    return bad


def test_put_saturated_concrete_only_stage4_skip_keeps_put_work():
    should = rq1_veriput_run._should_skip_concrete_only_after_puts
    bad = 0
    bad += check(should({"put_valid": 2}, 2, 0, 1, 0),
                 "PUT-saturated cleared fallback Stage 4 is skipped")
    bad += check(should({"put_valid": 3}, 2, 0, 0, 4),
                 "PUT-saturated timeout fallback Stage 4 is skipped")
    bad += check(not should({"put_valid": 1}, 2, 0, 1, 4),
                 "PUT-saturated skip waits for enough valid PUT artifacts")
    bad += check(not should({"put_valid": 2}, 2, 1, 1, 4),
                 "PUT-saturated skip never drops certified regions")
    bad += check(not should({"put_valid": 2}, 0, 0, 1, 4),
                 "PUT-saturated skip can be disabled")
    reason = rq1_veriput_run._format_put_saturated_concrete_only_skip(3, 2)
    bad += check("3 valid PUT artifact(s)" in reason
                 and "2-PUT floor" in reason,
                 f"PUT-saturated skip reason is audit-friendly: {reason}")
    return bad


def test_valid_saturated_concrete_only_stage4_skip_preserves_put_budget():
    should = rq1_veriput_run._should_skip_concrete_only_after_any_valid
    bad = 0
    bad += check(should({"valid": 1, "put_valid": 0}, True, 0, 1, 0),
                 "after any valid artifact, cleared concrete-only work is skipped")
    bad += check(should({"valid": 1, "put_valid": 1}, True, 0, 0, 1),
                 "after a PUT, timeout concrete-only work is skipped")
    bad += check(not should({"valid": 0, "put_valid": 0}, True, 0, 1, 0),
                 "before a valid artifact, concrete fallback can establish validity")
    bad += check(not should({"valid": 1, "put_valid": 0}, True, 1, 1, 0),
                 "certified regions are never skipped by the any-valid rule")
    bad += check(not should({"valid": 1, "put_valid": 0}, False, 0, 1, 0),
                 "any-valid concrete-only skip can be disabled")
    reason = rq1_veriput_run._format_valid_saturated_concrete_only_skip(2, 1)
    bad += check("2 valid artifact(s)" in reason and "1 PUT" in reason
                 and "PUT/R1/R2" in reason,
                 f"any-valid skip reason is audit-friendly: {reason}")
    return bad


def main():
    tests = [
        test_path_guard_allows_only_veriput_rq1_result_tree,
        test_put_artifact_summary_counts_raw_valid_and_oracle_classes,
        test_strength_quality_bucket_keeps_no_put_and_no_r1r2_visible,
        test_normalize_result_row_trusts_valid_test_oracle_classes,
        test_normalize_result_row_requires_explicit_double_oracle_validity,
        test_row_strength_prioritizes_methodology_quality,
        test_resume_retries_empty_no_valid_rows_only,
        test_resume_retries_certified_or_partial_zero_output_rows,
        test_load_subject_result_row_recovers_certification_diagnostics,
        test_adoption_updates_equal_strength_rows_with_cert_evidence,
        test_normalize_result_row_recomputes_stale_aggregate_quality,
        test_adoption_updates_equal_strength_rows_with_artifact_evidence,
        test_merge_put_summary_marks_valid_partial_artifacts_ok,
        test_load_subject_result_row_adopts_put_artifacts,
        test_results_all_requires_double_oracle_validity,
        test_adopt_existing_subject_results_promotes_stale_sidecar_artifacts,
        test_resume_quality_floor_can_focus_no_put_and_no_r1r2,
        test_empty_schedule_status_preserves_preparation_failures,
        test_no_unit_deploy_fallback_writes_valid_concrete_artifact,
        test_no_unit_deploy_fallback_uses_prepared_source_fallback,
        test_run_subject_records_no_unit_deploy_fallback_schema,
        test_real203_cache_uses_prepared_benchmark_namespace,
        test_jobs_admission_refuses_oversubscription,
        test_target_rows_fast_first_sorts_before_limit,
        test_target_rows_fast_first_uses_bugfix_fallback_size,
        test_target_rows_fast_first_uses_peer_fallback_size,
        test_certification_summary_identifies_inner_timeouts,
        test_certification_summary_uses_diagnostics_for_no_output_reason,
        test_cleared_concrete_fallbacks_trigger_stage4,
        test_subject_schedule_uses_separate_esbmc_run_timeout,
        test_certify_argv_for_remaining_caps_only_run_timeout,
        test_stage2_unit_timeout_cap_defaults_to_adaptive,
        test_adaptive_stage2_unit_timeout_cap_policy,
        test_stage2_wrapper_timeout_uses_effective_unit_cap,
        test_schedule_annotation_records_runtime_stage2_caps,
        test_certify_argv_for_remaining_honors_unit_timeout_cap,
        test_stage2_cert_shard_argv_and_merge,
        test_capped_stage2_timeout_advances_to_next_unit,
        test_stage2_no_output_stop_ignores_tool_failure_units,
        test_overload_refusal_appends_path_function_jobs,
        test_capped_stage2_timeout_with_candidates_enters_stage4,
        test_timeout_only_stage4_skip_can_trigger_no_candidate_stop,
        test_prepare_case_dir_preserves_complete_and_quarantines_partial,
        test_stage2_no_output_stop_reason_is_audit_friendly,
        test_stage2_no_output_stop_requires_multiple_no_candidate_units,
        test_tool_failures_do_not_count_as_no_candidate_stop_evidence,
        test_zero_output_stage4_stop_is_thresholded_and_raw_sensitive,
        test_no_candidate_stage2_unit_stop_is_thresholded_and_raw_sensitive,
        test_low_budget_concrete_only_stage4_skip_is_valid_and_put_sensitive,
        test_low_budget_timeout_only_stage4_skip_is_candidate_sensitive,
        test_put_saturated_concrete_only_stage4_skip_keeps_put_work,
        test_valid_saturated_concrete_only_stage4_skip_preserves_put_budget,
    ]
    bad = 0
    for test in tests:
        bad += test()
    if bad:
        print(f"{bad} failure(s)")
        return 1
    print(f"all {len(tests)} rq1 veriput tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
