#!/usr/bin/env python3
from argparse import Namespace
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import certify_all  # noqa: E402
import veriput_recipe  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def blank_args():
    return Namespace(
        strong_recipe=True,
        recipe_version="manual-label",
        jobs=8,
        probes=1,
        refine_rounds=1,
        shrink_rounds=1,
        safety_retreat_after_tiny_cuts=0,
        claim_budget=5,
        level0=False,
        level0_perturb=False,
        probe_witnesses=0,
        probe_ladder=False,
        probe_ladder_budget=0,
        skip_bracket=False,
        env_coord_disagreed=False,
        pin_agreed_establishable_env=False,
        pin_agreed_state=False,
        max_holes=0,
        max_region_pieces=9,
        cut_policy="tool",
        state_struct_fields=False,
        slot_coords=0,
        static_uncontrolled_inseparable=False,
        esbmc_arg=["--overflow-check"],
        no_region_refinement=False,
    )


def main():
    args = blank_args()
    got = certify_all.apply_strong_certify_recipe(args)
    bad = 0
    bad += check(got == veriput_recipe.STRONG_RECIPE_VERSION,
                 f"strong recipe returns canonical version: {got}")
    bad += check(args.recipe_version == veriput_recipe.STRONG_RECIPE_VERSION,
                 f"recipe label is canonical: {args.recipe_version}")
    bad += check(args.jobs == 1 and args.shrink_rounds == 4
                 and args.probe_witnesses == veriput_recipe.STRONG_PROBE_WITNESSES,
                 f"strong numeric controls are applied: {args}")
    bad += check(args.skip_bracket and args.env_coord_disagreed
                 and args.pin_agreed_state and args.state_struct_fields,
                 f"strong structural controls are applied: {args}")
    bad += check(args.slot_coords == veriput_recipe.STRONG_SLOT_COORDS
                 and args.max_holes == veriput_recipe.STRONG_MAX_HOLES
                 and args.max_region_pieces == veriput_recipe.STRONG_MAX_REGION_PIECES,
                 f"slot and hole controls are applied: {args}")
    bad += check(args.esbmc_arg.count("--overflow-check") == 1
                 and "--div-by-zero-check" in args.esbmc_arg
                 and "--path-cov-arith-resolve" in args.esbmc_arg
                 and "--unwindsetname" in args.esbmc_arg
                 and "_ESBMC_alloc_nested_2d:0:16,nondet_string:0:33"
                 in args.esbmc_arg,
                 f"ESBMC args are applied without duplicates: {args.esbmc_arg}")

    plain = blank_args()
    plain.strong_recipe = False
    before = vars(plain).copy()
    got_plain = certify_all.apply_strong_certify_recipe(plain)
    bad += check(got_plain == "manual-label" and vars(plain) == before,
                 "plain recipe leaves arguments untouched")

    no_region = blank_args()
    no_region.no_region_refinement = True
    bad += check(certify_all.stage2_region_refinement_controls(no_region) == (0, 0, 0),
                 "no-region-refinement zeros Stage-2 region refinement controls")
    bad += check(not certify_all.classification_retries_enabled(no_region),
                 "no-region-refinement disables certify_all classification retries")
    fallback_reason = certify_all.concrete_fallback_reason_for_args(no_region)
    bad += check(fallback_reason and "no-region-refinement ablation" in fallback_reason,
                 f"no-region-refinement concrete fallback reason is explicit: {fallback_reason}")
    not_certified = {
        "3": {
            "bucket": "NOT-CERTIFIED",
            "ce": {
                "msg.value": "0",
            },
        },
    }
    added = certify_all.mark_not_certified_ce_concrete_fallbacks(
        not_certified, reason=fallback_reason)
    bad += check(added.get("3", {}).get("concrete_fallback") is True
                 and added["3"].get("reason") == fallback_reason,
                 f"no-region-refinement reason is written to concrete fallback rows: {added}")

    matched_none = """
[enumerate] ESBMC produced no cov-report.json. Its output was:
ERROR: --solidity-path-coverage: --focus-function 'decimals' matched NONE of the 2 unit(s) in scope, so NOT ONE path was enumerated and this run measures nothing. The unit(s) that were available: sol:@C@ReverseMultiplicativePriceFeed@F@latestRoundData#205; sol:@C@ReverseMultiplicativePriceFeed@F@version#240
[run] EXIT 1
"""
    diag = certify_all.result_driver_diagnostic(matched_none)
    rec = {
        "driver_diagnostic": diag,
        "witnessed": None,
        "certified": {},
        "no_coordinate_reason": None,
        "driver_refusal": None,
        "empty_witness_verdict": None,
    }
    bad += check(diag["tag"] == "focus-function-matched-none",
                 f"focus miss is diagnosed before generic no-report: {diag}")
    bad += check(certify_all.bucket(rec, 1, matched_none) == "DRIVER-REFUSED",
                 "focus miss is non-retryable driver refusal")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
