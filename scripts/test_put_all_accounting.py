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
                    "ce": {"x": "7", "msg.sender": "5"},
                },
                "3": {"enc": 3, "concrete_fallback": False},
                "4": {
                    "enc": 4,
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "ce": {"msg.sender": "5"},
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
    ]
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        path = fh.name
        for record in records:
            fh.write(json.dumps(record) + "\n")
    try:
        bad = 0
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
                     [(r["enc"], r["region"], r["pins"])
                      for r in fallback_rows],
                     [("2", {"x": [7, 7]}, {"msg.sender": 5}),
                      ("4", {}, {"msg.sender": 5})])
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
            "driver_diagnostic": {"tag": "esbmc-no-cov-report"},
        })
        inferred_rows = put_all.timeout_concrete_fallback_rows(inferred_timeout)
        bad += check("timeout-fallback-matches-runner-inferred-timeout",
                     [(r["enc"], r["path_function"]) for r in inferred_rows],
                     [("15", "sol:@C@Token@F@approve#972")])
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
                     args.auto_unwind, 1)
        bad += check("stage4-strong-recipe-auto-partial-loops",
                     args.auto_partial_loops, True)
        bad += check("stage4-strong-recipe-lift-unconstrained-calldata",
                     args.lift_unconstrained_calldata, True)
        bad += check("stage4-strong-recipe-r2",
                     (args.propose_r2, args.r2_depth, args.r2_term_budget,
                      args.r2_candidate_budget),
                     (True, 1, 96, 128))
        bad += check("stage4-strong-recipe-fuzz-refute",
                     (args.fuzz_r2_prefilter, args.fuzz_runs,
                      args.fuzz_r2_candidate_budget),
                     (True, 256, 128))
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
                                r2_candidate_budget=128,
                                fuzz_r2_prefilter=True,
                                fuzz_runs=256,
                                fuzz_r2_candidate_budget=128,
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
                    "binary": {"binaryMtime": 123},
                    "stats": {
                        "fuzz_params": 1,
                        "asserts": 1,
                        "guarded_asserts": 0,
                        "rendered_width": {"x": 2},
                    },
                }, "/tmp/forge-project", {"x": [0, 2]}, True, "C"),
                ("bench", "target", 2, None, 0, {
                    "kind": "concrete",
                    "test": "test_cov_0",
                    "file": concrete_ok,
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
                         (2, 2))
            bad += check("stage4-b-summary-row-gates",
                         summary["rows"][0]["gates"],
                         {"fuzz": True, "width": True, "assert": True,
                          "green": True, "corpus": True})
            bad += check("stage4-concrete-row-is-not-b",
                         (summary["rows"][1]["kind"],
                          summary["rows"][1]["b"],
                          summary["rows"][1]["valid_reference_test"]),
                         ("concrete", False, True))
            bad += check("stage4-valid-reference-test-split",
                         summary["valid_reference_tests"],
                         {"total": 2, "put": 1, "concrete": 1})
            bad += check("stage4-source-counts",
                         summary["stage2_source_counts"],
                         {"certified_region": 4})
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
