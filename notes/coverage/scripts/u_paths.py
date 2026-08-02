#!/usr/bin/env python3
"""Turn the funnel's summed `U_reasons` back into NAMED paths.

The funnel prints, for the whole PoC set:

    ### paths -> F  (per U reason, summed)
        26  bounded-holds
         5  unit-not-entered
         2  run-died-before-solving

and 26 is not an answer to any question anyone has. `bounded-holds` does NOT
mean unreachable -- `path_cov_can_prove_unreachable()` returns false, so `I` is
never emitted and every path that merely held at this exploration lands here.
The question that matters is per path: is it unreachable AT THIS BOUND (and
would a larger one witness it), unreachable FULL STOP, or reachable and the
solver failed?

MEASURED on Tiny, which is where the distinction was first forced: 8 paths, 6 F,
and the two U are `withdraw`'s paths behind `require(bal >= amt)`. `bal` is 0
after the constructor and only `deposit` writes it, and one transaction is
exactly one entry call (`if (nondet) { f(...); return; }`,
solidity_convert_constructor.cpp:445). So at --solidity-max-tx 1 those two are
GENUINELY unreachable, `bounded-holds` is the correct verdict, and at
--solidity-max-tx 2 the same contract gives 8 of 8. Counting them as funnel LOSS
is counting a configuration choice as a defect.

This script prints every U path with its unit, its reason token, and its
decisions where the report carries them, so each one can be judged instead of
summed. EVERY U path is printed -- not a sample and not only the interesting
ones -- because the number worth having is the SPLIT, and a listing that showed
only some of them would have a complement nobody can check.

⚠ IT DOES NOT DECIDE REACHABILITY. It reports what the tool recorded. Deciding
whether a path needs a second transaction, a wider unwind, or is dead code is a
reading of the contract, and this script's job is to make that reading cheap by
saying exactly which paths to read.
"""

import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen_dir",
                    help="the funnel's _gen directory, one subdir per contract, "
                         "each holding that run's cov-report.json")
    a = ap.parse_args()

    if not os.path.isdir(a.gen_dir):
        sys.exit(f"{a.gen_dir}: not a directory -- run poc_funnel.py first")

    totals = {}
    n_u = n_paths = n_f = 0
    for stem in sorted(os.listdir(a.gen_dir)):
        rep = os.path.join(a.gen_dir, stem, "cov-report.json")
        if not os.path.exists(rep):
            # NAMED. A contract whose run produced no report contributed no
            # paths to the denominator either, and silently skipping it would
            # make this listing disagree with the funnel's own table.
            print(f"## {stem}: NO cov-report.json (the run produced none)")
            continue
        d = json.load(open(rep))
        s = d.get("summary") or {}
        claims = d.get("claims") or []
        n_paths += s.get("paths_total") or 0
        n_f += s.get("F_feasible_with_ce") or 0
        us = [c for c in claims if c.get("status") != "F"]
        if not us:
            continue
        print(f"## {stem}   paths={s.get('paths_total')} "
              f"F={s.get('F_feasible_with_ce')} U={len(us)}"
              + ("   **PARTIAL REPORT**" if d.get("partial") else ""))
        for c in us:
            n_u += 1
            reason = c.get("u_reason") or c.get("status_reason") or "(no token)"
            totals[reason] = totals.get(reason, 0) + 1
            fn = (c.get("path_function") or "").split("@F@")[-1]
            decs = c.get("decisions") or []
            print(f"   {c.get('condition')}  unit={fn}  status={c.get('status')}"
                  f"  reason={reason}  depth={c.get('path_depth')}")
            for step in decs:
                print(f"       {step.get('arm'):<13}"
                      f"{step.get('branch_claim')}"
                      f"   ({step.get('file')}:{step.get('line')}"
                      f" in {step.get('function')})")
            if not decs:
                print("       (no decision sequence published for this path -- "
                      "it cannot be projected onto source guards, so judging it "
                      "needs the goto, not this listing)")
        print()

    print("=" * 72)
    print(f"  paths {n_paths}   F {n_f}   U {n_u}")
    for r, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {r}")
    print()
    print("  `bounded-holds` is NOT `unreachable`. It means the claim held at "
          "THIS exploration (tx bound, unwind bound, post-constructor entry "
          "state). Whether a larger bound would witness the path is a question "
          "about the CONTRACT, answered by reading the guards printed above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
