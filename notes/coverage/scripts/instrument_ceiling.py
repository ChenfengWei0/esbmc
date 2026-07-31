#!/usr/bin/env python3
"""How much of each benchmark's denominator can the FORGE-measured columns reach?

THE PROBLEM THIS EXISTS FOR. The three-column table compares

    bar     ESBMC branch coverage        instrument: ESBMC
    native  the project's own suite      instrument: forge lcov
    ours    the generated suite          instrument: forge lcov

against ONE denominator: the AST-derived canonical decision count. But the two
instruments do not agree about what a decision IS. MEASURED on aqua: forge does
not instrument a `for` LOOP HEADER as a branch, so lines 2249 and 2258 are in
the denominator and outside forge's universe entirely -- no test whatsoever can
credit them. `native` reads 6/8 and is really 6 of 6.

Comparing `ours/astDecisions` against `bar/astDecisions` therefore mixes two
universes and inflates the apparent gap by exactly the lines forge cannot see.

The number needed to say so has been in every locked JSON all along, unread:
`perFile[].native.instrumented`. This reads it, and reports per benchmark how
much of the denominator is out of reach of the deliverable's own measuring
instrument.

Reads only. No esbmc, no forge, no solver.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect as base  # noqa: E402

DATA = Path("/home/samson/workspace/esbmc/notes/coverage/data")


def main():
    print("=" * 78)
    print("How much of the denominator can forge's branch instrument reach?")
    print("=" * 78)
    # A COUNT DIFFERENCE IS NOT A MEMBERSHIP DIFFERENCE, and this column used to
    # be printed as "unreach = AST - forge", which is wrong in both directions.
    # `forge >= AST` does NOT imply every AST line is instrumented -- the two
    # instruments disagree about what a decision is, so each set can contain
    # lines the other lacks while the counts happen to favour one. The only way
    # to know is to intersect the SETS, which needs an lcov for that benchmark.
    # So this prints the two counts and says which benchmarks have been checked
    # by membership; it does not compute a difference that would read as one.
    print(f"  {'benchmark':<28} {'AST':>5} {'forge':>6} {'bar':>5} "
          f"{'native':>7}  membership checked?")
    tot_ast = tot_forge = 0
    for bench in sorted(base.BENCHES):
        p = DATA / f"esbmc_{bench}.json"
        if not p.exists():
            print(f"  {bench:<28}  (no locked data)")
            continue
        d = json.loads(p.read_text())
        pf = d.get("per_function", {})
        # `instrumented` is recorded ONLY in the whole-contract (`no_function`)
        # entry; the per-method entry carries `reached` alone. Reading the
        # per-method one returns None for every benchmark, which reads as "no
        # ceiling known" rather than as "wrong section" -- so the lookup is
        # written against the section that actually has the field, and the
        # denominator is taken from the same place so the two cannot come from
        # different scopes.
        nf = d.get("no_function", {})
        ast = forge = None
        for e in nf.get("perFile", []):
            a = e.get("astDecisions")
            n = (e.get("native") or {}).get("instrumented")
            if isinstance(a, int):
                ast = (ast or 0) + a
            if isinstance(n, int):
                forge = (forge or 0) + n
        tot = pf.get("total", {})
        bar = tot.get("esbmcReached")
        nat = tot.get("nativeReached")
        if ast is None:
            print(f"  {bench:<28}  (no per-file denominator)")
            continue
        # A benchmark with no native lcov record leaves `instrumented` absent.
        # Printed as "?" rather than folded to the AST count: "we do not know
        # the ceiling" and "the ceiling is the whole denominator" are different
        # statements and only one of them is safe to quote.
        # Only aqua has had its two SETS intersected, by reading every BRDA
        # record of a real lcov and asking which canonical lines forge never
        # mentions at any count. That measurement found 2249 and 2258 -- both
        # `for` loop headers -- absent, and the emitted, the project's own and
        # four hand-written tests all top out without them.
        checked = ("YES: 2 AST line(s) forge never mentions (2249, 2258 -- "
                   "`for` headers)" if bench == "aqua_Aqua" else
                   "no -- counts only, ceiling UNKNOWN")
        if forge is None:
            print(f"  {bench:<28} {ast:>5} {'?':>6} {str(bar):>5} "
                  f"{str(nat):>7}  no native lcov record")
            continue
        tot_ast += ast
        tot_forge += forge
        print(f"  {bench:<28} {ast:>5} {forge:>6} {str(bar):>5} "
              f"{str(nat):>7}  {checked}")
    if tot_ast:
        print()
        print(f"  totals over benchmarks with a native record: AST {tot_ast}, "
              f"forge branch records {tot_forge}")
        print()
        print("  READ THIS BEFORE SUBTRACTING THE TWO COLUMNS. On farming and")
        print("  st1inch forge instruments MORE branches than the AST decision")
        print("  count, so the disagreement runs in BOTH directions and a count")
        print("  difference says nothing about which lines are missing. Only")
        print("  aqua has been checked by membership; there the AST denominator")
        print("  does contain 2 lines forge cannot report, so `native 6/8` is")
        print("  really 6 of 6 and `ours N/8` is N of 6.")
        print()
        print("  `bar` is ESBMC's column, universe = the AST count. `native` and")
        print("  `ours` are forge's. Quoting them against one denominator mixes")
        print("  two universes -- by a margin that is MEASURED only on aqua and")
        print("  UNKNOWN elsewhere until each benchmark's lcov is intersected.")


if __name__ == "__main__":
    main()
