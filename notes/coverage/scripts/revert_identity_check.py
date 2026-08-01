#!/usr/bin/env python3
"""Does a path that REVERTS still carry the identity it accumulated first?

The enumeration identifies a complete path by `tr == enc && cnt == depth`, and
`tr` is built one bit per decision. Solidity's `revert` undoes every state
modification the transaction made. So the question is whether `tr` is inside the
state that gets undone: if it were, a path that records three decisions and then
reverts would reach its exit assert with `tr` back at its entry value, the claim
could never match, and the path would come back U.

That failure mode is invisible in aggregate -- reverting paths are common, U is
common, and nothing in a report distinguishes "the solver did not decide it"
from "the identity was erased before the assert could see it".

Reads the report of a run over `poc/D26_RevertKeepsIdentity.sol`, whose `f` has
three decisions before a reverting `require` and whose `g` is a revert-free
CONTROL. Prints the per-unit status split and the exit kinds, and applies the
three readings the fixture pre-registers.

Usage: python3 revert_identity_check.py <cov-report.json>
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def unit_of(c):
    cond = c.get("condition") or ""
    return cond.split(":", 1)[0] if ":" in cond else "?"


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    rep = json.loads(Path(argv[1]).read_text())

    by_unit = defaultdict(list)
    for c in rep.get("claims", []):
        if "path_id" in c:
            by_unit[unit_of(c)].append(c)

    print("## Does a reverting path keep the identity it accumulated?\n")
    print(f"report : {argv[1]}")
    s = rep.get("summary", {})
    print(f"summary: paths_total={s.get('paths_total')} "
          f"F={s.get('F_feasible_with_ce')} U={s.get('U_undecided')} "
          f"partial={rep.get('partial', s.get('partial'))}\n")

    for u in sorted(by_unit):
        cs = by_unit[u]
        st = Counter(c.get("status") for c in cs)
        print(f"--- unit `{u}`: {len(cs)} path claim(s)   "
              + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
        for c in sorted(cs, key=lambda x: (x.get("path_depth") or 0,
                                           x.get("path_id") or 0)):
            print(f"      enc={c.get('path_id'):<4} depth={c.get('path_depth')} "
                  f" status={c.get('status')}  exit_kind="
                  f"{c.get('exit_kind')}")
        print()

    f_cs = by_unit.get("f", [])
    g_cs = by_unit.get("g", [])
    rev = [c for c in f_cs if c.get("exit_kind") == "revert"]
    rev_f = [c for c in rev if c.get("status") == "F"]
    norm_f = [c for c in f_cs
              if c.get("exit_kind") != "revert" and c.get("status") == "F"]
    g_f = [c for c in g_cs if c.get("status") == "F"]

    print("=" * 74)
    if not g_f:
        print("  VOID -- THE CONTROL DID NOT FIRE. `g` has no revert on any "
              "path and was not\n  witnessed, so this run measured nothing "
              "about reverts and the status of the\n  reverting paths is not "
              "evidence of anything.")
        return 1
    if not rev:
        print("  READING C: no path of `f` is recorded with a revert exit at "
              "all. That\n  contradicts the three-state exit census and is a "
              "different defect from the one\n  this fixture was built to ask "
              "about. Read the exit_kind values above.")
        return 1
    print(f"  control `g`: {len(g_f)} witnessed path(s) -- the run measured "
          f"something")
    print(f"  `f` revert-exit paths: {len(rev)}, of which WITNESSED (F): "
          f"{len(rev_f)}")
    print(f"  `f` non-revert paths WITNESSED (F): {len(norm_f)}")
    depths = sorted({c.get("path_depth") for c in rev_f})
    if rev_f:
        print(f"\n  ✅ READING A: reverting paths ARE witnessed, at depth(s) "
              f"{depths}.\n     A witness exists only if the exit assert saw "
              f"`tr == enc && cnt == depth`, so\n     the accumulator was NOT "
              f"rolled back with the contract state. Identity survives\n     "
              f"the revert, and no rollback modelling is needed FOR THE "
              f"ACCUMULATOR.\n     (Rollback modelling is still needed for the "
              f"contract state and for R0's exit\n     kind -- that is a "
              f"different requirement and this says nothing about it.)")
        return 0
    print("\n  ⛔ READING B: every reverting path came back UNWITNESSED while "
          "the control and\n     the normal-exit paths were witnessed. That is "
          "CONSISTENT with the identity\n     being erased at the revert -- and "
          "it is also what a solver limit looks like, so\n     it is not proof. "
          "Next step is to read the claim and the accumulator, not to\n     "
          "conclude from this table.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
