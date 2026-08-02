#!/usr/bin/env python3
"""Print the per-path status of one or two cov-report.json files, side by side.

Two arms of an experiment differ in ONE flag and the question is always which
PATHS moved, never how many. A summary line that goes "F 6 -> 12" is compatible
with six different paths being witnessed and six others being lost, so the
comparison has to be per claim id.

EVERY path is printed, in both arms, including the ones that did not move --
otherwise the listing is a filter whose complement nobody can check, and the
number that matters is the split.
"""

import argparse
import json
import sys


def load(p):
    d = json.load(open(p))
    s = d.get("summary") or {}
    byid = {}
    for c in d.get("claims") or []:
        byid[c.get("condition")] = c
    return d, s, byid


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    args = ap.parse_args()

    da, sa, ba = load(args.a)
    print(f"## {args.a}")
    print(f"   paths_total={sa.get('paths_total')} "
          f"F={sa.get('F_feasible_with_ce')} "
          f"U={sa.get('U_undecided')} "
          f"bounded_holds={sa.get('U_of_which_bounded_holds')} "
          f"partial={da.get('partial')}")
    print(f"   bound={sa.get('bound')}")
    print(f"   U_reasons={ {k: v for k, v in (sa.get('U_reasons') or {}).items() if v} }")

    if not args.b:
        for cid in sorted(ba):
            c = ba[cid]
            print(f"   {cid:<28}{c.get('status')}  "
                  f"inputs={c.get('inputs')}  final={c.get('final_state')}")
        return 0

    db, sb, bb = load(args.b)
    print()
    print(f"## {args.b}")
    print(f"   paths_total={sb.get('paths_total')} "
          f"F={sb.get('F_feasible_with_ce')} "
          f"U={sb.get('U_undecided')} "
          f"bounded_holds={sb.get('U_of_which_bounded_holds')} "
          f"partial={db.get('partial')}")
    print(f"   bound={sb.get('bound')}")
    print(f"   U_reasons={ {k: v for k, v in (sb.get('U_reasons') or {}).items() if v} }")

    print()
    print("## per path")
    print(f"{'claim':<28}{'A':<6}{'B':<6}  moved?   B inputs / final")
    moved = 0
    for cid in sorted(set(ba) | set(bb)):
        ca, cb = ba.get(cid), bb.get(cid)
        sA = ca.get("status") if ca else "-absent-"
        sB = cb.get("status") if cb else "-absent-"
        tag = ""
        if sA != sB:
            moved += 1
            tag = "MOVED"
        extra = ""
        if cb and cb.get("status") == "F":
            extra = f"{cb.get('inputs')} -> {cb.get('final_state')}"
        print(f"{cid:<28}{sA:<6}{sB:<6}  {tag:<8} {extra}")
    print()
    print(f"  {moved} path(s) changed status between the two arms.")
    print("  A path present in one arm and '-absent-' in the other means the "
          "two arms do not have the same PATH SET, so their F counts are not "
          "a like-for-like comparison and must not be quoted as a rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
