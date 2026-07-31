#!/usr/bin/env python3
"""Does per-method focus actually SEGMENT the work?

The collection is per-method precisely so each run is small enough to finish.
83 of 110 runs did finish. This asks what the other 27 have in common, and the
answer decides whether the fix is "more budget" or "a different mechanism".

Two numbers per run, both already recorded by the collector:

  pathsInstrumented -- what the run had to carry, which is a CONTRACT-level
                       number: `--focus-function` narrows which unit the
                       dispatcher may enter, NOT what gets instrumented, so
                       every focused run instruments the whole contract's path
                       set and most claims come back `unit-not-entered`.
  the unit's OWN paths -- F + bounded-holds from its report, i.e. the part that
                       actually has to be solved.

If the killed runs are the ones whose own share is a large fraction of the
contract's total, then focusing cannot help them: the unit IS the contract.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATHCOV = HERE.parent / "pathcov"

BENCHES = ["aqua_Aqua", "cross_chain_swap_EscrowDst",
           "cross_chain_swap_EscrowSrc", "farming",
           "limit_order_protocol", "st1inch_St1inch"]


def main():
    print("# Does focus segment the work?\n")
    for bench in BENCHES:
        idx = PATHCOV / bench / "index.json"
        if not idx.exists():
            print(f"\n## `{bench}` -- no index\n")
            continue
        runs = json.loads(idx.read_text()).get("runs", [])
        print(f"\n## `{bench}`  ({len(runs)} run(s))\n")
        print("| contract.function | instrumented | unit's own | own share | "
              "outcome |")
        print("|---|---|---|---|---|")
        for r in runs:
            inst = r.get("pathsInstrumented")
            own = None
            f, u = r.get("F"), r.get("uReasons") or {}
            if f is not None:
                own = f + u.get("bounded-holds", 0)
            share = (f"{100.0 * own / inst:.1f}%"
                     if (own is not None and inst) else "-")
            if r.get("skipped"):
                out = "REFUSED (" + r["skipped"] + ")"
            elif r.get("killedByOuterTimeout"):
                out = "KILLED at the outer timeout"
            elif not r.get("reportPresent"):
                out = f"no report (exit {r.get('exitCode')})"
            else:
                out = f"ok, {r.get('wallSeconds')}s"
            print(f"| {r.get('contract')}.{r.get('function')} | "
                  f"{inst if inst is not None else '-'} | "
                  f"{own if own is not None else '-'} | {share} | {out} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
