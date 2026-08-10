#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from argparse import Namespace


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PUT_ALL = os.path.join(REPO, "notes", "coverage", "scripts", "put_all.py")
COLLECT = os.path.join(REPO, "notes", "coverage", "scripts", "collect.py")

spec = importlib.util.spec_from_file_location("put_all", PUT_ALL)
put_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(put_all)

collect_spec = importlib.util.spec_from_file_location("collect", COLLECT)
collect = importlib.util.module_from_spec(collect_spec)
collect_spec.loader.exec_module(collect)


def check(name, got, want):
    if got == want:
        print(f"ok - {name}")
        return 0
    print(f"not ok - {name}: got {got!r}, want {want!r}")
    return 1


def main():
    records = [
        {
            "benchmark": "bench",
            "unit": "target",
            "coords": ["x"],
            "pins": "{'msg.sender': 5}",
            "witnessed": 4,
            "certified": {"1": "x in [0, 9]"},
            "not_certified": {
                "2": "refuted with concrete witness",
                "3": "STATICALLY INSEPARABLE: differs only on external-call behavior",
                "4": "no generalisable coordinate",
            },
            "not_certified_details": {
                "2": {
                    "enc": 2,
                    "concrete_fallback": True,
                    "witness_check": "SUCCESSFUL",
                    "ce": {
                        "x": "7",
                        "msg.sender": "5",
                        "amount": "11",
                        "return": "99",
                    },
                },
                "3": {"enc": 3, "concrete_fallback": False},
                "4": {
                    "enc": 4,
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "path_function": "sol:@C@Target@F@target#100",
                    "ce": {"msg.sender": "5", "block.timestamp": "1234"},
                },
            },
        },
        {
            "benchmark": "bench",
            "unit": "other",
            "witnessed": 9,
            "certified": {},
            "not_certified": {"9": "not selected"},
        },
        {
            "benchmark": "bench",
            "unit": "legacy",
            "witnessed": 2,
            "certified": {},
            "not_certified": {
                "5": "STATICALLY INSEPARABLE: differs only on external-call behavior",
            },
            "static_extcall_inseparable": True,
        },
        {
            "benchmark": "bench",
            "unit": "timeout",
            "bucket": "KILLED",
            "exit": 124,
            "witnessed": 1,
            "pins": {"msg.sender": 1},
            "certified": {},
            "not_certified": {},
            "partial_witness_journal": {
                "source_stage": "certify-query-started",
                "partial": True,
                "claims_decided": 1,
                "claims_total": 9,
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
            "unit": "no_coord_journal",
            "bucket": "NO-COORDINATE",
            "witnessed": 1,
            "pins": {"msg.value": 0},
            "certified": {},
            "not_certified": {},
            "no_coordinate_reason": "every coordinate was pinned by request",
            "partial_witness_journal": {
                "source_stage": "no-generalizable-coordinate",
                "source_context": "path-enumeration-or-probe",
                "partial": False,
                "complete": True,
                "claims_decided": 12,
                "claims_total": 12,
                "witness_count": 8,
                "paths": [{
                    "path_id": "7",
                    "path_function": "sol:@C@BadAuction@F@bid#42",
                    "witness_count": 8,
                }],
            },
        },
        {
            "benchmark": "bench",
            "unit": "partial_journal",
            "bucket": "KILLED",
            "exit": 1,
            "witnessed": 2,
            "pins": {"msg.sender": 9},
            "certified": {"41": "already certified"},
            "not_certified": {},
            "driver_diagnostic": {
                "tag": "path-coverage-partial-journal-no-report",
                "category": "no-cov-report",
            },
            "partial_witness_journal": {
                "source_stage": "partial-witness-journal",
                "partial": True,
                "claims_decided": 1,
                "claims_total": 4,
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
            "unit": "mixed_timeout",
            "bucket": "KILLED",
            "exit": 124,
            "witnessed": 3,
            "pins": {"msg.sender": 2},
            "certified": {"1": "x in [0, 1]"},
            "not_certified": {"2": "refuted before timeout"},
            "certified_details": {
                "1": {
                    "enc": 1,
                    "piece": 1,
                    "box": [{"name": "x", "lo": "0", "hi": "1"}],
                },
            },
            "not_certified_details": {
                "2": {"enc": 2, "concrete_fallback": False},
            },
            "partial_witness_journal": {
                "source_stage": "certify-query-started",
                "partial": True,
                "claims_decided": 2,
                "claims_total": 5,
                "witness_count": 3,
                "paths": [
                    {
                        "path_id": "1",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "2",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "3",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "mixed_no_coord",
            "bucket": "NO-COORDINATE",
            "witnessed": 2,
            "pins": {"msg.value": 0},
            "certified": {},
            "not_certified": {
                "7": "structured no-coordinate detail already emitted",
            },
            "not_certified_details": {
                "7": {
                    "enc": 7,
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "ce": {"msg.value": "0"},
                },
            },
            "partial_witness_journal": {
                "source_stage": "no-generalizable-coordinate",
                "source_context": "path-enumeration-or-probe",
                "partial": False,
                "complete": True,
                "claims_decided": 12,
                "claims_total": 12,
                "witness_count": 2,
                "paths": [
                    {
                        "path_id": "7",
                        "path_function": "sol:@C@BadAuction@F@bid#42",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "8",
                        "path_function": "sol:@C@BadAuction@F@bid#42",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "certified_no_coord",
            "bucket": "CERTIFIED",
            "witnessed": 2,
            "pins": {"msg.value": 0},
            "certified": {},
            "not_certified": {},
            "partial_witness_journal": {
                "source_stage": "certified-no-coordinate",
                "source_context": "path-enumeration-or-probe",
                "partial": False,
                "complete": True,
                "claims_decided": 6,
                "claims_total": 11,
                "witness_count": 2,
                "paths": [
                    {
                        "path_id": "2",
                        "path_function": "sol:@C@Registry@F@getVault#442",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "3",
                        "path_function": "sol:@C@Registry@F@getVault#442",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "pin_conflict",
            "coords": ["x"],
            "pins": {"msg.sender": 5},
            "witnessed": 1,
            "certified": {},
            "not_certified": {"1": "conflicting stale witness detail"},
            "not_certified_details": {
                "1": {
                    "enc": 1,
                    "concrete_fallback": True,
                    "witness_check": "SUCCESSFUL",
                    "ce": {"x": "3", "msg.sender": "6"},
                },
            },
        },
    ]
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        path = fh.name
        for record in records:
            fh.write(json.dumps(record) + "\n")
    try:
        bad = 0
        with tempfile.TemporaryDirectory() as td:
            old_out = put_all.OUT
            old_forge_std = put_all.FORGE_STD
            try:
                put_all.OUT = os.path.join(td, "out")
                stale_forge_std = os.path.join(td, "missing-forge-std")
                good_forge_std = os.path.join(td, "repo-forge-std")
                os.makedirs(good_forge_std)
                put_all.FORGE_STD = good_forge_std
                flat = os.path.join(td, "Flat.sol")
                with open(flat, "w") as fh:
                    fh.write("contract Flat {}\n")
                project = os.path.join(put_all.OUT, "bench")
                os.makedirs(os.path.join(project, "lib"), exist_ok=True)
                os.symlink(stale_forge_std,
                           os.path.join(project, "lib", "forge-std"))
                bad += check("stage4-existing-broken-forge-std-symlink",
                             put_all.ensure_project("bench", flat), project)
                bad += check("stage4-broken-forge-std-symlink-repaired",
                             os.path.realpath(os.path.join(
                                 project, "lib", "forge-std")),
                             good_forge_std)
            finally:
                put_all.OUT = old_out
                put_all.FORGE_STD = old_forge_std
        target = put_all.stage2_path_accounting(path, "bench.target")
        bad += check("selected-record-count", target["records"], 1)
        bad += check("selected-witnessed-count", target["witnessed"], 4)
        bad += check("selected-certified-count", target["certified"], 1)
        bad += check("selected-not-certified-count",
                     target["not_certified"], 3)
        bad += check("structured-concrete-fallback",
                     target["concrete_fallback"], 2)
        fallback_rows = put_all.cleared_concrete_fallback_rows(records[0])
        bad += check("cleared-fallback-point-region",
                     [(r["enc"], r["path_function"], r["region"], r["pins"])
                      for r in fallback_rows],
                     [("2", None, {"x": [7, 7]},
                       {"amount": 11, "msg.sender": 5}),
                      ("4", "sol:@C@Target@F@target#100", {},
                       {"block.timestamp": 1234, "msg.sender": 5})])
        conflict_rows = put_all.cleared_concrete_fallback_rows(records[9])
        bad += check("cleared-fallback-conflicting-pin-is-refused",
                     conflict_rows, [])
        timeout_rows = put_all.timeout_concrete_fallback_rows(records[3])
        bad += check("timeout-fallback-uses-partial-witness-path",
                     [(r["enc"], r["path_function"], r["region"], r["pins"],
                       r["detail"]["witness_check"]) for r in timeout_rows],
                     [("15", "sol:@C@Token@F@approve#972", {},
                       {"msg.sender": 1}, "TIMEOUT-WITNESSED")])
        inferred_timeout = dict(records[3])
        inferred_timeout.update({
            "exit": 1,
            "witnessed": None,
            "wall_s": 119.5,
            "run_timeout_s": 120,
            "driver_diagnostic": {
                "tag": "goto-inline-call-type-mismatch",
                "category": "no-cov-report",
            },
        })
        inferred_rows = put_all.timeout_concrete_fallback_rows(inferred_timeout)
        bad += check("timeout-fallback-matches-runner-inferred-timeout",
                     [(r["enc"], r["path_function"]) for r in inferred_rows],
                     [("15", "sol:@C@Token@F@approve#972")])
        timeout_accounting = put_all.stage2_path_accounting(
            path, "bench.timeout")
        bad += check("timeout-fallback-counts-as-concrete-fallback",
                     timeout_accounting["concrete_fallback"], 1)
        bad += check("timeout-fallback-not-no-verdict",
                     timeout_accounting["no_verdict"], 0)
        no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(
            records[4])
        bad += check("no-coordinate-complete-journal-fallback",
                     [(r["enc"], r["path_function"], r["region"], r["pins"],
                       r["detail"]["witness_check"]) for r in no_coord_rows],
                     [("7", "sol:@C@BadAuction@F@bid#42", {},
                       {"msg.value": 0}, "COMPLETE-WITNESS-NO-COORDINATE")])
        no_coord_accounting = put_all.stage2_path_accounting(
            path, "bench.no_coord_journal")
        bad += check("no-coordinate-journal-counts-as-fallback",
                     no_coord_accounting["concrete_fallback"], 1)
        bad += check("no-coordinate-journal-not-no-verdict",
                     no_coord_accounting["no_verdict"], 0)
        partial_journal_rows = put_all.partial_journal_concrete_fallback_rows(
            records[5])
        bad += check("partial-journal-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"], r["pins"],
                       r["detail"]["witness_check"])
                      for r in partial_journal_rows],
                     [("42", "sol:@C@Token@F@approve#972",
                       {"msg.sender": 9}, "PARTIAL-JOURNAL-WITNESSED")])
        partial_journal_accounting = put_all.stage2_path_accounting(
            path, "bench.partial_journal")
        bad += check("partial-journal-counts-as-fallback",
                     (partial_journal_accounting["certified"],
                      partial_journal_accounting["concrete_fallback"],
                      partial_journal_accounting["no_verdict"]),
                     (1, 1, 0))
        mixed_timeout_rows = put_all.timeout_concrete_fallback_rows(
            records[6])
        bad += check("mixed-timeout-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"]) for r in mixed_timeout_rows],
                     [("3", "sol:@C@Token@F@approve#972")])
        mixed_timeout_accounting = put_all.stage2_path_accounting(
            path, "bench.mixed_timeout")
        bad += check("mixed-timeout-fallback-fills-gap",
                     (mixed_timeout_accounting["certified"],
                      mixed_timeout_accounting["not_certified"],
                      mixed_timeout_accounting["concrete_fallback"],
                      mixed_timeout_accounting["no_verdict"]),
                     (1, 1, 1, 0))
        mixed_no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(
            records[7])
        bad += check("mixed-no-coordinate-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"]) for r in mixed_no_coord_rows],
                     [("8", "sol:@C@BadAuction@F@bid#42")])
        mixed_no_coord_accounting = put_all.stage2_path_accounting(
            path, "bench.mixed_no_coord")
        bad += check("mixed-no-coordinate-fallback-fills-gap",
                     (mixed_no_coord_accounting["not_certified"],
                      mixed_no_coord_accounting["concrete_fallback"],
                      mixed_no_coord_accounting["no_verdict"]),
                     (1, 2, 0))
        certified_no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(
            records[8])
        bad += check("certified-no-coordinate-fallback-rows",
                     [(r["enc"], r["path_function"]) for r in certified_no_coord_rows],
                     [("2", "sol:@C@Registry@F@getVault#442"),
                      ("3", "sol:@C@Registry@F@getVault#442")])
        certified_no_coord_accounting = put_all.stage2_path_accounting(
            path, "bench.certified_no_coord")
        bad += check("certified-no-coordinate-counts-as-fallback",
                     (certified_no_coord_accounting["certified"],
                      certified_no_coord_accounting["concrete_fallback"],
                      certified_no_coord_accounting["no_verdict"]),
                     (0, 2, 0))
        bad += check("structured-method-unsupported",
                     target["method_unsupported"], 1)
        bad += check("selected-no-verdict", target["no_verdict"], 0)

        legacy = put_all.stage2_path_accounting(path, "bench.legacy")
        bad += check("legacy-extcall-attribution",
                     legacy["method_unsupported"], 1)
        bad += check("legacy-detail-not-unknown",
                     legacy["detail_unknown"], 0)
        bad += check("stage4-bench-table-covers-collector",
                     sorted(put_all.BENCHES),
                     sorted(collect.BENCHES))
        args = Namespace(strong_recipe=True,
                         auto_unwind=0,
                         auto_partial_loops=False,
                         lift_unconstrained_calldata=False,
                         propose_r2=False,
                         r2_depth=0,
                         r2_term_budget=1,
                         r2_candidate_budget=1,
                         fuzz_r2_prefilter=False,
                         fuzz_runs=1,
                         fuzz_r2_candidate_budget=1)
        bad += check("stage4-strong-recipe-version",
                     put_all.apply_strong_put_recipe(args),
                     put_all.STRONG_RECIPE_VERSION)
        bad += check("stage4-strong-recipe-auto-unwind",
                     args.auto_unwind, 2)
        bad += check("stage4-strong-recipe-auto-partial-loops",
                     args.auto_partial_loops, True)
        bad += check("stage4-strong-recipe-lift-unconstrained-calldata",
                     args.lift_unconstrained_calldata, True)
        bad += check("stage4-strong-recipe-r2",
                     (args.propose_r2, args.r2_depth, args.r2_term_budget,
                      args.r2_candidate_budget),
                     (True, 1, 96, 192))
        bad += check("stage4-strong-recipe-fuzz-refute",
                     (args.fuzz_r2_prefilter, args.fuzz_runs,
                      args.fuzz_r2_candidate_budget),
                     (True, 256, 192))
        bad += check("stage4-v14-does-not-require-certified-details",
                     put_all.recipe_requires_certified_details(
                         "veriput-strong/14"),
                     False)
        bad += check("stage4-v15-requires-certified-details",
                     put_all.recipe_requires_certified_details(
                         "veriput-strong/15-relation-establish"),
                     True)
        bad += check("stage4-v16-requires-certified-details",
                     put_all.recipe_requires_certified_details(
                         "veriput-strong/16-zero-interface-sender-arm"),
                     True)
        bad += check("stage4-v17-requires-certified-details",
                     put_all.recipe_requires_certified_details(
                         "veriput-strong/17-split-r2-repair"),
                     True)
        bad += check("stage4-claim-path-id-suffix",
                     put_all.claim_path_id_int("7#nonvacuous"), 7)
        bad += check("stage4-claim-path-id-nonnumeric",
                     put_all.claim_path_id_int("path:7#nonvacuous"), None)
        stage4_args = Namespace(foundry_fixture="/tmp/foundry.json",
                                auto_partial_loops=True,
                                lift_unconstrained_calldata=True,
                                propose_r2=True,
                                r2_depth=1,
                                r2_term_budget=96,
                                r2_candidate_budget=192,
                                fuzz_r2_prefilter=True,
                                fuzz_runs=256,
                                fuzz_r2_candidate_budget=192,
                                forge_timeout=660,
                                esbmc_arg=[
                                    "--path-cov-fixture",
                                    "/tmp/esbmc.json",
                                ])
        cmd = ["driver"]
        put_all.append_stage4_driver_options(
            cmd, stage4_args, "sol:@C@DCF@F@setDistributeAddress#1",
            "normal", "certified_region", None, None, {"state.owner": 7})
        bad += check("stage4-foundry-fixture-is-driver-option",
                     cmd[:3],
                     ["driver", "--foundry-fixture", "/tmp/foundry.json"])
        bad += check("stage4-esbmc-fixture-stays-esbmc-arg",
                     ("--esbmc-arg=--path-cov-fixture" in cmd
                      and "--esbmc-arg=/tmp/esbmc.json" in cmd),
                     True)
        bad += check("stage4-foundry-fixture-not-esbmc-arg",
                     "--esbmc-arg=/tmp/foundry.json" in cmd,
                     False)
        bad += check("stage4-driver-options-preserve-proof-switches",
                     ("--propose-r2" in cmd
                      and "--fuzz-r2-prefilter" in cmd
                      and "--pin" in cmd),
                     True)
        fb_cmd = ["driver"]
        put_all.append_stage4_driver_options(
            fb_cmd, stage4_args, "sol:@C@DCF@F@setDistributeAddress#1",
            "normal", "certified-region-concrete-fallback",
            "CERTIFIED-REGION-PUT-REFUSED:build-put-refused", None,
            {"state.owner": 7})
        bad += check("stage4-certified-region-fallback-is-concrete-only",
                     ("--concrete-only" in fb_cmd
                      and "--propose-r2" not in fb_cmd
                      and "--fuzz-r2-prefilter" not in fb_cmd),
                     True)
        bad += check("stage4-certified-region-fallback-driver-source",
                     fb_cmd[fb_cmd.index("--concrete-stage2-source") + 1],
                     "certified-region-concrete-fallback")
        no_coord_cmd = ["driver"]
        put_all.append_stage4_driver_options(
            no_coord_cmd, stage4_args, "sol:@C@DCF@F@setDistributeAddress#1",
            "normal", "no-coordinate-concrete-fallback",
            "COMPLETE-WITNESS-NO-COORDINATE", None, {"state.owner": 7})
        bad += check("stage4-no-coordinate-fallback-driver-source",
                     no_coord_cmd[
                         no_coord_cmd.index("--concrete-stage2-source") + 1],
                     "no-coordinate-concrete-fallback")
        partial_cmd = ["driver"]
        put_all.append_stage4_driver_options(
            partial_cmd, stage4_args, "sol:@C@DCF@F@setDistributeAddress#1",
            "normal", "partial-journal-concrete-fallback",
            "PARTIAL-JOURNAL-WITNESSED", None, {"state.owner": 7})
        bad += check("stage4-partial-journal-fallback-driver-source",
                     partial_cmd[
                         partial_cmd.index("--concrete-stage2-source") + 1],
                     "partial_journal_concrete_fallback")
        normalized_fb = put_all.normalize_stage2_concrete_fallback_record(
            {"kind": "put", "stage2_source": "stale"},
            "cleared-concrete-fallback", "SUCCESSFUL")
        bad += check("stage4-cleared-fallback-normalized-as-concrete",
                     (normalized_fb["kind"],
                      normalized_fb["stage2_source"],
                      normalized_fb["stage2_witness_check"]),
                     ("concrete", "cleared_not_certified_fallback",
                      "SUCCESSFUL"))
        missing_cleared = put_all.stage4_missing_record(
            "cleared-concrete-fallback", "SUCCESSFUL")
        bad += check("stage4-missing-cleared-source-normalized",
                     (missing_cleared["kind"],
                      missing_cleared["stage2_source"],
                      missing_cleared["stage2_witness_check"]),
                     ("concrete", "cleared_not_certified_fallback",
                      "SUCCESSFUL"))
        put_all.append_row_esbmc_args(
            cmd,
            ["--overflow-check", "--path-cov-fixture", "--path-cov-arith-resolve"],
            stage4_args.esbmc_arg)
        bad += check("stage4-row-esbmc-args-are-carried",
                     ("--esbmc-arg=--overflow-check" in cmd
                      and "--esbmc-arg=--path-cov-arith-resolve" in cmd),
                     True)
        bad += check("stage4-row-esbmc-args-are-deduplicated",
                     cmd.count("--esbmc-arg=--path-cov-fixture"), 1)
        with tempfile.NamedTemporaryFile("w", delete=False) as report_fh:
            report_path = report_fh.name
            json.dump({
                "claims": [
                    {
                        "path_id": "7#nonvacuous",
                        "path_function": "sol:@C@Cb7@F@f#31",
                        "exit_kind": "normal",
                    },
                    {
                        "path_id": "8",
                        "path_function": "sol:@C@Cb7@F@f#31",
                        "exit_kind": "revert",
                    },
                    {
                        "path_id": "9",
                        "path_function": "sol:@C@Cb7@F@f#31",
                        "exit_kind": "undetermined",
                    },
                ],
            }, report_fh)
        try:
            put_all.EXIT_KIND_CACHE.clear()
            bad += check("stage4-report-exit-kind-suffixed-path-id",
                         put_all.report_exit_kind(
                             report_path, "sol:@C@Cb7@F@f#31", 7),
                         "normal")
            bad += check("stage4-report-exit-kind-plain-path-id",
                         put_all.report_exit_kind(
                             report_path, "sol:@C@Cb7@F@f#31", 8),
                         "revert")
            bad += check("stage4-report-exit-kind-undetermined-normalized",
                         put_all.report_exit_kind(
                             report_path, "sol:@C@Cb7@F@f#31", 9),
                         "unknown")
        finally:
            os.unlink(report_path)
        old_run_forge = put_all.run_forge
        old_binary = put_all.current_binary_identity
        try:
            put_all.current_binary_identity = lambda: {
                "head": "test",
                "srcDirty": False,
                "binaryMtime": 123,
            }
            put_all.run_forge = lambda _proj, _timeout: (
                0,
                json.dumps({
                    "Suite": {
                        "test_results": {
                            "test_put_C_target_path1()": {
                                "status": "Success"
                            },
                            "test_cov_0()": {
                                "status": "Success"
                            },
                            "test_cov_1()": {
                                "status": "Success"
                            },
                            "test_put_C_target_path3()": {
                                "status": "Success"
                            },
                        }
                    }
                }),
                "",
                False,
                0.01)
            tmpdir = tempfile.TemporaryDirectory()
            selfcheck_files = tmpdir.name
            concrete_ok = os.path.join(selfcheck_files, "concrete.t.sol")
            concrete_unsupported = os.path.join(selfcheck_files,
                                                "unsupported.t.sol")
            with open(concrete_ok, "w") as fh:
                fh.write("contract T { function test_cov_0() public {} }\n")
            with open(concrete_unsupported, "w") as fh:
                fh.write("""\
contract T {
  function test_cov_1() public {
    // UNSUPPORTED: C.target has an argument type ESBMC cannot yet render
  }
}
""")
            summary = put_all.b_report([
                ("bench", "target", 1, None, 0, {
                    "test": "test_put_C_target_path1",
                    "file": "/tmp/test.t.sol",
                    "storage_layout_available": True,
                    "binary": {"binaryMtime": 123},
                    "stats": {
                        "fuzz_params": 1,
                        "asserts": 1,
                        "verifier_asserts": 1,
                        "exit_kind_asserts": 0,
                        "oracle_classes": ["R1"],
                        "guarded_asserts": 0,
                        "rendered_width": {"x": 2},
                    },
                }, "/tmp/forge-project", {"x": [0, 2]}, True, "C"),
                ("bench", "target", 2, None, 0, {
                    "kind": "concrete",
                    "test": "test_cov_0",
                    "file": concrete_ok,
                    "storage_layout_available": False,
                    "storage_layout_error": "forge inspect failed",
                    "binary": {"binaryMtime": 123},
                    "stats": {
                        "fuzz_params": 0,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {},
                    },
                }, "/tmp/forge-project", {}, True, "C"),
                ("bench", "target", 4, None, 0, {
                    "kind": "concrete",
                    "test": "test_cov_1",
                    "file": concrete_unsupported,
                    "storage_layout_available": False,
                    "storage_layout_error": "forge inspect failed",
                    "binary": {"binaryMtime": 123},
                    "stats": {
                        "fuzz_params": 0,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {},
                    },
                }, "/tmp/forge-project", {}, True, "C"),
                ("bench", "target", 3, None, 0, {
                    "test": "test_put_C_target_path3",
                    "file": "/tmp/zero.t.sol",
                    "binary": {"binaryMtime": 123},
                    "stats": {
                        "fuzz_params": 1,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {"x": 2},
                    },
                }, "/tmp/forge-project", {"x": [0, 2]}, True, "C"),
            ], 10)
            tmpdir.cleanup()
            bad += check("stage4-b-summary-counts-b",
                         (summary["b"], summary["certified_region_rows"]),
                         (1, 4))
            bad += check("stage4-b-summary-forge-seen",
                         (summary["forge_seen"]["put"]["Success"],
                          summary["forge_seen"]["concrete"]["Success"]),
                         (1, 0))
            bad += check("stage4-b-summary-row-gates",
                         summary["rows"][0]["gates"],
                         {"fuzz": True, "width": True, "assert": True,
                          "green": True, "corpus": True})
            bad += check("stage4-concrete-row-is-not-b",
                         (summary["rows"][1]["kind"],
                          summary["rows"][1]["b"],
                          summary["rows"][1]["valid_reference_test"]),
                         ("concrete", False, False))
            bad += check("stage4-valid-reference-test-split",
                         summary["valid_reference_tests"],
                         {"total": 1, "put": 1, "concrete": 0})
            bad += check("stage4-source-counts",
                         summary["stage2_source_counts"],
                         {"certified_region": 4})
            bad += check("stage4-storage-layout-counts",
                         summary["storage_layout_counts"],
                         {"available": 1, "unavailable": 2,
                          "unavailable_with_artifact": 2})
            bad += check("stage4-storage-layout-row-field",
                         (summary["rows"][1]["storage_layout_available"],
                          summary["rows"][1]["storage_layout_error"]),
                         (False, "forge inspect failed"))
            bad += check("stage4-row-oracle-class-fields",
                         (summary["rows"][0]["oracle_classes"],
                          summary["rows"][0]["verifier_asserts"],
                          summary["rows"][0]["exit_kind_asserts"]),
                         (["R1"], 1, 0))
            bad += check("stage4-unsupported-concrete-refused",
                         (summary["rows"][2]["refused"],
                          summary["rows"][2]["valid_reference_test"],
                          bool(summary["rows"][2]["refusal_reason"])),
                         (True, False, True))
            bad += check("stage4-zero-assert-put-refused",
                         (summary["rows"][3]["refused"],
                          summary["rows"][3]["valid_reference_test"],
                          summary["rows"][3]["gates"]),
                         (True, False,
                          {"fuzz": False, "width": None, "assert": None,
                           "green": None, "corpus": None}))
            bad += check("stage4-certified-region-build-refusal-retries",
                         put_all.certified_region_concrete_fallback_reason(
                             "certified-region", 2,
                             {"kind": "refusal",
                              "refused": "build-put-refused"}),
                         "build-put-refused")
            bad += check("stage4-certified-region-underscore-source-retries",
                         put_all.certified_region_concrete_fallback_reason(
                             "certified_region", 2,
                             {"kind": "refusal",
                              "refused": "build-put-refused"}),
                         "build-put-refused")
            bad += check("stage4-certified-region-zero-assert-retries",
                         put_all.certified_region_concrete_fallback_reason(
                             "certified-region", 0,
                             {"kind": "put",
                              "stats": {"asserts": 1,
                                        "guarded_asserts": 1}}),
                         "zero-unconditional-assertions")
            bad += check("stage4-certified-region-vacuous-not-retried",
                         put_all.certified_region_concrete_fallback_reason(
                             "certified-region", 2,
                             {"kind": "refusal",
                              "refused": "ladder-vacuous"}),
                         None)
            normalized = put_all.normalize_certified_region_concrete_fallback_record(
                {
                    "kind": "concrete",
                    "stage2_source": "cleared_not_certified_fallback",
                    "concrete_reason": "Stage-2 fallback",
                    "notes": [],
                },
                "build-put-refused")
            bad += check("stage4-certified-region-fallback-normalized",
                         (normalized["kind"], normalized["stage2_source"],
                          normalized["certified_region_fallback_reason"],
                          "Stage-2 fallback" in normalized["concrete_reason"]),
                         ("concrete",
                          "certified-region-concrete-fallback",
                          "build-put-refused", True))
            missing = put_all.stage4_missing_record(
                "no-coordinate-concrete-fallback")
            missing_summary = put_all.b_report([
                ("bench", "target", 6, None, 1, missing,
                 "/tmp/forge-project", {}, True, "C"),
            ], 10)
            bad += check("stage4-missing-json-kind",
                         missing_summary["rows"][0]["kind"], "concrete")
            bad += check("stage4-missing-json-stage2-source",
                         missing_summary["rows"][0]["stage2_source"],
                         "no-coordinate-concrete-fallback")
            bad += check("stage4-missing-json-source-counts",
                         missing_summary["stage2_source_counts"],
                         {"no-coordinate-concrete-fallback": 1})
            bad += check("stage4-missing-json-not-valid",
                         missing_summary["valid_reference_tests"],
                         {"total": 0, "put": 0, "concrete": 0})
        finally:
            put_all.run_forge = old_run_forge
            put_all.current_binary_identity = old_binary
        with tempfile.TemporaryDirectory() as proj:
            os.makedirs(os.path.join(proj, "test"))
            test_path = os.path.join(proj, "test", "Probe.t.sol")
            with open(test_path, "w") as fh:
                fh.write("""\
contract Probe {
  function setUp() public {
  }
  function helper() public {
  }
  function test_cov_0() public {
  }
  function test_put_Probe_target_path1() public {
  }
}
""")
            old_run_forge = put_all.run_forge
            try:
                put_all.run_forge = lambda _proj, _timeout: (
                    0,
                    json.dumps({
                        "test/Probe.t.sol:Probe": {
                            "test_results": {
                                "setUp()": {"status": "Failure"},
                                "helper()": {"status": "Failure"},
                                "test_cov_0()": {"status": "Failure"},
                                "test_put_Probe_target_path1()": {
                                    "status": "Failure"
                                },
                            }
                        }
                    }),
                    "",
                    False,
                    0.01)
                put_all.disable_red_replays([proj], 10)
            finally:
                put_all.run_forge = old_run_forge
            with open(test_path) as fh:
                disabled = fh.read()
            bad += check("stage4-red-selfcheck-keeps-setup",
                         "function setUp() public" in disabled,
                         True)
            bad += check("stage4-red-selfcheck-keeps-helper",
                         "function helper() public" in disabled,
                         True)
            bad += check("stage4-red-selfcheck-disables-only-concrete",
                         "function disabled_test_cov_0() public" in disabled,
                         True)
            bad += check("stage4-red-selfcheck-keeps-put-red",
                         "function test_put_Probe_target_path1() public"
                         in disabled,
                         True)
        plain = Namespace(strong_recipe=False, auto_unwind=0,
                          auto_partial_loops=False,
                          lift_unconstrained_calldata=False)
        bad += check("stage4-plain-recipe-unchanged",
                     (put_all.apply_strong_put_recipe(plain),
                      plain.auto_unwind, plain.auto_partial_loops,
                      plain.lift_unconstrained_calldata),
                     (None, 0, False, False))
        cp = subprocess.run([
            sys.executable,
            PUT_ALL,
            "--out-root",
            "/home/samson/workspace/VeriPUT/Results/put-stage4",
            "--cert",
            path,
        ],
                            capture_output=True,
                            text=True)
        bad += check("stage4-refuses-protected-out-root",
                     (cp.returncode != 0
                      and "--out-root must not be under" in cp.stderr),
                     True)
        return bad
    finally:
        os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
