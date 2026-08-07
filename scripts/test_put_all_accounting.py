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
            },
            "not_certified_details": {
                "2": {
                    "enc": 2,
                    "concrete_fallback": True,
                    "witness_check": "SUCCESSFUL",
                    "ce": {"x": "7", "msg.sender": "5"},
                },
                "3": {"enc": 3, "concrete_fallback": False},
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
                     target["not_certified"], 2)
        bad += check("structured-concrete-fallback",
                     target["concrete_fallback"], 1)
        fallback_rows = put_all.cleared_concrete_fallback_rows(records[0])
        bad += check("cleared-fallback-point-region",
                     [(r["enc"], r["region"], r["pins"])
                      for r in fallback_rows],
                     [("2", {"x": [7, 7]}, {"msg.sender": 5})])
        bad += check("structured-method-unsupported",
                     target["method_unsupported"], 1)
        bad += check("selected-no-verdict", target["no_verdict"], 1)

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
        bad += check("stage4-claim-path-id-suffix",
                     put_all.claim_path_id_int("7#nonvacuous"), 7)
        bad += check("stage4-claim-path-id-nonnumeric",
                     put_all.claim_path_id_int("path:7#nonvacuous"), None)
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
                            "test_put_C_target_path3()": {
                                "status": "Success"
                            },
                        }
                    }
                }),
                "",
                False)
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
                    "file": "/tmp/concrete.t.sol",
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
            bad += check("stage4-b-summary-counts-b",
                         (summary["b"], summary["certified_region_rows"]),
                         (1, 3))
            bad += check("stage4-b-summary-forge-seen",
                         (summary["forge_seen"]["put"]["Success"],
                          summary["forge_seen"]["concrete"]["Success"]),
                         (2, 1))
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
                         {"certified_region": 3})
            bad += check("stage4-zero-assert-put-refused",
                         (summary["rows"][2]["refused"],
                          summary["rows"][2]["valid_reference_test"],
                          summary["rows"][2]["gates"]),
                         (True, False,
                          {"fuzz": False, "width": None, "assert": None,
                           "green": None, "corpus": None}))
        finally:
            put_all.run_forge = old_run_forge
            put_all.current_binary_identity = old_binary
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
