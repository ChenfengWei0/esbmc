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
    bad += check(args.slot_coords == 8 and args.max_holes == 1,
                 f"slot and hole controls are applied: {args}")
    bad += check(args.esbmc_arg.count("--overflow-check") == 1
                 and "--div-by-zero-check" in args.esbmc_arg
                 and "--path-cov-arith-resolve" in args.esbmc_arg,
                 f"ESBMC args are applied without duplicates: {args.esbmc_arg}")

    plain = blank_args()
    plain.strong_recipe = False
    before = vars(plain).copy()
    got_plain = certify_all.apply_strong_certify_recipe(plain)
    bad += check(got_plain == "manual-label" and vars(plain) == before,
                 "plain recipe leaves arguments untouched")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
