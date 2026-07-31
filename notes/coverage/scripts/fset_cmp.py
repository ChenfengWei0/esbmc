#!/usr/bin/env python3
"""Compare the F (witnessed-path) SETS of two path-coverage collections.

Why a script and not an eyeball: the question "does whole-contract reach paths
per-method focus cannot?" is a SET question, and the only number the summaries
give is a COUNT. A count comparison cannot tell `15 = 13 + 2 new` from
`15 = 11 shared + 4 new, 2 lost`, and the second is the answer that matters --
if the whole-contract run LOSES a path the per-method union had, then whole is
not a replacement for per-method, it is a different measurement.

Identity used: (path_function, path_id) -- i.e. the unit's goto id plus enc(pi).
Both are published per claim by bmc.cpp's report writer (`path_function` /
`path_id`, set at bmc.cpp:1541-1542), and both sides of this comparison were
produced by the SAME binary against the SAME flat source at the same
--path-cov-max-goals, so the enumeration -- and therefore enc -- is identical.
That premise is CHECKED rather than assumed: `paths_total` must agree across
every report, and the script refuses to compare if it does not. (The stable
content-addressed id in goto_coverage.h:131-158 exists precisely because enc is
NOT safe across differing enumerations; it is not published in the report, so
the equality of paths_total is what licenses using enc here.)

Usage:
    python3 fset_cmp.py --a <report.json> --b <report.json> [<report.json> ...]

`--a` is one report (the whole-contract run); everything after it is the other
side, unioned (the per-method collection).
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load(path):
    d = json.loads(Path(path).read_text())
    total = d.get("summary", {}).get("paths_total")
    fset = set()
    for c in d.get("claims", []):
        if c.get("status") != "F":
            continue
        fn = c.get("path_function")
        pid = c.get("path_id")
        if fn is None or pid is None:
            # An F with no identity cannot be compared. Reported, never skipped
            # silently: it would shrink one side of the difference and read as
            # "that path was not reached".
            fset.add(("<UNIDENTIFIED>", json.dumps(c.get("condition"))))
            continue
        fset.add((fn, str(pid)))
    return d, total, fset


def unit(fn):
    """`sol:@C@Aqua@F@ship#3022` -> `Aqua.ship`, for a readable table."""
    s = fn
    if "@F@" in s:
        c = s.split("@C@", 1)[-1].split("@F@", 1)[0]
        f = s.split("@F@", 1)[1].split("#", 1)[0]
        return f"{c}.{f}"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="side A (one report)")
    ap.add_argument("b", nargs="+", help="side B (unioned)")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B (union)")
    args = ap.parse_args()

    _da, ta, fa = load(args.a)
    totals = {args.a: ta}
    fb = set()
    b_origin = {}
    for p in args.b:
        _d, t, s = load(p)
        totals[p] = t
        for k in s:
            b_origin.setdefault(k, []).append(Path(p).name)
        fb |= s

    # THE PREMISE CHECK. enc is an ordinal in one enumeration; comparing two
    # enumerations by enc is only meaningful if they enumerated the same set.
    # A report with paths_total 0 is a run that instrumented nothing (a library
    # unit) and carries no enc at all, so it is excluded from the check rather
    # than making it fail.
    nz = {p: t for p, t in totals.items() if t}
    if len(set(nz.values())) > 1:
        print("REFUSING TO COMPARE: paths_total disagrees across reports, so "
              "enc(pi) does not denote the same path on both sides.")
        for p, t in totals.items():
            print(f"  {t!s:>8}  {p}")
        return 2
    print(f"paths_total agrees at {sorted(set(nz.values()))[0]} across "
          f"{len(nz)} non-empty report(s); "
          f"{len(totals) - len(nz)} report(s) enumerated 0 paths")

    only_a, only_b, both = fa - fb, fb - fa, fa & fb
    print(f"\n{args.label_a:<24} |F| = {len(fa)}")
    print(f"{args.label_b:<24} |F| = {len(fb)}")
    print(f"{'both':<24}     = {len(both)}")
    print(f"{'ONLY ' + args.label_a:<24}     = {len(only_a)}")
    print(f"{'ONLY ' + args.label_b:<24}     = {len(only_b)}")

    per = defaultdict(lambda: [0, 0, 0])
    for k in both:
        per[unit(k[0])][0] += 1
    for k in only_a:
        per[unit(k[0])][1] += 1
    for k in only_b:
        per[unit(k[0])][2] += 1
    print(f"\n{'unit':<28} {'both':>5} {'only-A':>7} {'only-B':>7}")
    for u in sorted(per):
        b, oa, ob = per[u]
        print(f"{u:<28} {b:>5} {oa:>7} {ob:>7}")

    if only_a:
        print(f"\nONLY {args.label_a} -- paths this side reached and the other did not:")
        for fn, pid in sorted(only_a):
            print(f"  {unit(fn):<28} path:{pid}")
    if only_b:
        print(f"\nONLY {args.label_b} -- paths the other side reached and this one did NOT:")
        for fn, pid in sorted(only_b):
            src = ",".join(b_origin.get((fn, pid), []))
            print(f"  {unit(fn):<28} path:{pid}   (from {src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
