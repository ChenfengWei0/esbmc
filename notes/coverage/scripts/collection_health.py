#!/usr/bin/env python3
"""Why did a path-coverage run produce no report?

`branch_gate.py` already refuses to read a run's absent report as a measured
zero, and `gap_attribution.py` already says the buckets are an upper bound when
reports are missing. Neither says WHY they are missing, and the answer decides
what to do next:

  * killed by the outer timeout        -> raise the budget, re-collect
  * non-zero exit with a message       -> a defect; the message names it
  * exit 0 and still no report         -> the run decided there was nothing to
                                          report (0 units), which is a SCOPE
                                          fact, not a failure

Those three are indistinguishable in every table produced so far -- all three
appear as a blank row -- and the third one is the one that must never be
counted against the method.

Reads `index.json` + `runs.jsonl` only. No esbmc invocation.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATHCOV = HERE.parent / "pathcov"

BENCHES = ["aqua_Aqua", "cross_chain_swap_EscrowDst",
           "cross_chain_swap_EscrowSrc", "farming",
           "limit_order_protocol", "st1inch_St1inch"]


def load_runs(bench):
    d = PATHCOV / bench
    idx = d / "index.json"
    if not idx.exists():
        return None, [], "no index.json"
    meta = json.loads(idx.read_text())
    runs = meta.get("runs", [])
    jl = d / "runs.jsonl"
    extra = {}
    if jl.exists():
        for ln in jl.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            k = r.get("unit") or r.get("name") or r.get("focus")
            if k:
                extra[k] = r
    return meta, runs, extra


def main():
    print("# Collection health -- the 'no report' rows, named\n")
    grand = defaultdict(int)
    aborts = []
    for bench in BENCHES:
        meta, runs, extra = load_runs(bench)
        if meta is None:
            print(f"\n## `{bench}`\n\n{runs}")
            continue
        print(f"\n## `{bench}`  ({len(runs)} run(s))\n")

        keys = set()
        for r in runs:
            keys.update(r.keys())
        print(f"index run keys: {', '.join(sorted(keys))}\n")

        bad = [r for r in runs if not r.get("reportPresent")]
        print(f"runs without a report: {len(bad)} / {len(runs)}\n")
        if not bad:
            continue
        # THE UNIT KEY IS `function`, NOT `unit`. The first version of this
        # script guessed `unit`/`focus`/`name` and printed a column of `?` for
        # every row -- a table that looked populated and identified nothing.
        # The key inventory above is printed first for exactly this reason, and
        # an unresolved name is now a hard failure rather than a `?`.
        print("| contract | function | exit | killed | wall s | units enum |")
        print("|---|---|---|---|---|---|")
        for r in bad:
            fn = r.get("function")
            if fn is None:
                sys.exit(
                    f"{bench}: a run row has no `function` key; keys are "
                    f"{sorted(r.keys())}. Refusing to print an unidentified "
                    f"row -- naming the failing unit is the whole point.")
            ex = r.get("exitCode", "?")
            killed = r.get("killedByOuterTimeout")
            wall = r.get("wallSeconds", "?")
            ue = r.get("unitsEnumerated", "?")
            if killed:
                grand["killed"] += 1
            elif ex == 0:
                grand["exit0_no_report"] += 1
            else:
                grand["nonzero_exit"] += 1
                aborts.append((bench, r.get("contract"), fn, ex,
                               r.get("cmd")))
            print(f"| {r.get('contract')} | `{fn}` | {ex} | {killed} | "
                  f"{wall} | {ue} |")

    print("\n## Totals across the corpus\n")
    for k in sorted(grand):
        print(f"- {k}: {grand[k]}")

    if aborts:
        print("\n## Non-zero exits, with the exact command to reproduce\n")
        print("A run that aborts in seconds is not a budget problem and must "
              "not be re-collected with a larger timeout until it is "
              "diagnosed -- the same abort would simply happen again.\n")
        for bench, contract, fn, ex, cmd in aborts:
            print(f"- `{bench}` {contract}.{fn} -> exit {ex}")
            print(f"  ```\n  {cmd}\n  ```")
    return 0


if __name__ == "__main__":
    sys.exit(main())
