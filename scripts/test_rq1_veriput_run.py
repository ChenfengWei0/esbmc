#!/usr/bin/env python3
import json
import argparse
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
        (unit / "put-summary.json").write_text(json.dumps({
            "schema": "veriput-put-summary/1",
            "emission": {
                "puts_emitted": 2,
                "concrete_replays_emitted": 2,
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "total": 1,
                    "put": 1,
                    "concrete": 0,
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
                ],
            },
        }))
        summary = rq1_veriput_run.summarize_put_artifacts(root / "put")
        bad = 0
        bad += check(summary["raw"] == 3 and summary["valid"] == 2,
                     f"raw/valid split is retained: {summary}")
        bad += check(summary["put_raw"] == 1 and summary["put_valid"] == 1
                     and summary["concrete_raw"] == 2
                     and summary["concrete_valid"] == 1,
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
        bad += check(len(summary["raw_tests"]) == 3
                     and all(t["enc"] != 9 for t in summary["raw_tests"]),
                     f"refused PUT rows are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 10 for t in summary["raw_tests"]),
                     f"disabled concrete replays are not raw deliverables: {summary}")
        bad += check(any(t["enc"] == 11 for t in summary["valid_tests"]),
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
                    "tag": "esbmc-no-cov-report",
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
    bad += check(rq1_veriput_run._no_output_reason(summary) ==
                 "certification timed out before PUT artifacts: fallback, transfer",
                 "no-output reason distinguishes inner certification timeout")
    return bad


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
        ]
        cert.write_text("".join(json.dumps(row) + "\n" for row in rows))
        cleared_count = rq1_veriput_run._cleared_concrete_fallback_count(
            cert, "bench", "approve")
        timeout_count = rq1_veriput_run._timeout_concrete_fallback_count(
            cert, "bench", "approve")
    argv = rq1_veriput_run._put_argv(
        cert, "approve", "bench", Path("/tmp/out"), 600, 12, 300)
    bad = 0
    bad += check(cleared_count == 2,
                 "cleared and complete-witness concrete fallbacks trigger Stage 4")
    bad += check(timeout_count == 1,
                 "timed-out witnessed certification rows trigger Stage 4")
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
        job, remaining_s=599.8, run_timeout_s=120, memlimit_gib=12)
    pairs = dict(zip(argv, argv[1:]))
    bad = 0
    bad += check(pairs.get("--timeout") == "599",
                 f"whole certify budget follows remaining case time: {argv}")
    bad += check(pairs.get("--run-timeout") == "120",
                 f"per-ESBMC run budget is capped: {argv}")
    bad += check(pairs.get("--memlimit-gib") == "12",
                 f"memory budget is authoritative: {argv}")
    return bad


def test_stage2_unit_timeout_cap_defaults_to_uncapped():
    bad = 0
    bad += check(rq1_veriput_run.DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S == 0,
                 "Stage-2 unit timeout cap defaults to uncapped")
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
        stages, {"raw": 0}, 90, 1),
                 "one heavy no-candidate unit does not end a multi-unit subject")
    bad += check(rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 2),
                 "Stage-2 no-output stop fires after repeated no-candidate units")
    bad += check(not rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 1}, 90, 2),
                 "Stage-2 no-output stop keeps raw outputs")
    bad += check(rq1_veriput_run._should_stop_after_no_output_stage2(
        stages, {"raw": 0}, 90, 1, min_consecutive_units=1),
                 "explicit one-unit policy can still stop after one candidate miss")
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


def main():
    tests = [
        test_path_guard_allows_only_veriput_rq1_result_tree,
        test_put_artifact_summary_counts_raw_valid_and_oracle_classes,
        test_strength_quality_bucket_keeps_no_put_and_no_r1r2_visible,
        test_real203_cache_uses_prepared_benchmark_namespace,
        test_jobs_admission_refuses_oversubscription,
        test_target_rows_fast_first_sorts_before_limit,
        test_target_rows_fast_first_uses_bugfix_fallback_size,
        test_certification_summary_identifies_inner_timeouts,
        test_cleared_concrete_fallbacks_trigger_stage4,
        test_subject_schedule_uses_separate_esbmc_run_timeout,
        test_certify_argv_for_remaining_caps_only_run_timeout,
        test_stage2_unit_timeout_cap_defaults_to_uncapped,
        test_certify_argv_for_remaining_honors_unit_timeout_cap,
        test_prepare_case_dir_preserves_complete_and_quarantines_partial,
        test_stage2_no_output_stop_reason_is_audit_friendly,
        test_stage2_no_output_stop_requires_multiple_no_candidate_units,
        test_zero_output_stage4_stop_is_thresholded_and_raw_sensitive,
        test_no_candidate_stage2_unit_stop_is_thresholded_and_raw_sensitive,
        test_low_budget_concrete_only_stage4_skip_is_valid_and_put_sensitive,
        test_put_saturated_concrete_only_stage4_skip_keeps_put_work,
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
