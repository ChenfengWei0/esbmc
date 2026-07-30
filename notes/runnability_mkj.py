#!/usr/bin/env python3
"""M / K / J per project: units in the corpus, units measured, units timed out.

The plan requires this triple to be reported ("that project has M units, the
first K were measured in lexicographic order, J of them timed out"). M comes
from the locked collector's own entry list; K and J come from the rows actually
written. Neither is recalled -- a remembered M is exactly the kind of number
that ends up in a paper unverified.

THE OUTCOME CELL IS READ BY COLUMN, NOT BY SUBSTRING, and that is not
fastidiousness. `"TIMEOUT" in line` also matches a contract or function whose
name contains it, and `"| no |" in line` depends on the exact spacing the writer
happens to use -- a row rendered `| No |` silently leaves N while still counting
toward K, so the unit reads as measured and its outcome is lost with nothing
saying so.

K IS ALSO CROSS-CHECKED AGAINST M. M is read from a collector JSON that is
re-collected from time to time; K is read from a markdown table appended to over
many sessions. Nothing used to verify that the K rows were even a subset of the
M entries, so the two halves of the printed triple could be computed against
different unit sets and still print side by side. A duplicated row -- which is
what happens if t2_runnability's resume parse ever breaks -- would likewise give
K > M without comment.
"""
import json
import os
import sys

DATA = "/home/samson/workspace/esbmc/notes/coverage/data"
OUT = "/home/samson/workspace/esbmc/notes/runnability-distribution.md"

BENCHES = ["aqua_Aqua", "cross_chain_swap_EscrowDst", "cross_chain_swap_EscrowSrc",
           "farming", "limit_order_protocol", "st1inch_St1inch"]

# | '' | bench | contract | function | paths | F | I | U | wall | cap |
#   completed | ctr | ''
COL_BENCH, COL_CONTRACT, COL_FUNCTION, COL_CAP, COL_COMPLETED = 1, 2, 3, 9, 10

# Must agree with t2_runnability.UNIT_CAP. A TIMEOUT recorded against a smaller
# cap is a slice artifact, not a budget result, and t2_runnability now refuses
# to continue while one is on disk. J must not count it either, or the two
# scripts disagree about the same table.
UNIT_CAP = 540

rows = {}
seen = {}
dupes = []
with open(OUT) as f:
    for line in f:
        if not line.startswith("| `"):
            continue
        parts = [p.strip() for p in line.split("|")]
        # This file carries more than one table, and every one of them starts a
        # row with a backticked cell. The old filter took any such line, so rows
        # of the focus-enumeration table were counted into a phantom bench keyed
        # on a FUNCTION name -- harmless only because the report loop iterates
        # BENCHES and never looked at it. The bench cell is the discriminator.
        bench = parts[COL_BENCH].strip("` ") if len(parts) > COL_BENCH else ""
        if bench not in BENCHES:
            continue
        if len(parts) < 12:
            sys.exit(f"malformed unit row in {OUT} ({len(parts)} cells): "
                     f"{line!r}")
        key = (bench, parts[COL_CONTRACT].strip("` "),
               parts[COL_FUNCTION].strip("` "))
        if key in seen:
            dupes.append(key)
        seen[key] = True
        d = rows.setdefault(bench, {"K": 0, "J": 0, "S": 0, "T": 0, "N": 0,
                                    "keys": set()})
        d["K"] += 1
        d["keys"].add(key)
        outcome = parts[COL_COMPLETED]
        # TOOL-FAILURE and "no" are NOT timeouts and must not be folded into J.
        # A run the tool itself calls an internal defect, and a run that could
        # not be started because the entry name is ambiguous, are two further
        # outcomes; collapsing any of them into "timed out" would report a
        # budget problem where there is a tool problem or a definition one.
        if outcome == "TIMEOUT":
            try:
                cap = int(parts[COL_CAP])
            except ValueError:
                cap = -1
            if cap == UNIT_CAP:
                d["J"] += 1
            else:
                d["S"] += 1
        elif outcome == "TOOL-FAILURE":
            d["T"] += 1
        elif outcome == "no":
            d["N"] += 1
        elif outcome != "yes":
            sys.exit(f"unrecognised outcome cell {outcome!r} in {OUT}: {line!r}")

if dupes:
    sys.exit(f"{OUT} has {len(dupes)} duplicated unit row(s): {sorted(dupes)}. "
             f"K would exceed M and the outcome counted twice.")

bad = []
for b in BENCHES:
    p = os.path.join(DATA, f"esbmc_{b}.json")
    with open(p) as f:
        rep = json.load(f)
    entries = {(b, fn["contract"], fn["function"])
               for fn in rep["per_function"]["functions"]}
    m = len(entries)
    r = rows.get(b, {"K": 0, "J": 0, "S": 0, "T": 0, "N": 0, "keys": set()})
    stray = sorted(r["keys"] - entries)
    if stray:
        bad.append((b, stray))
    print(f"{b}: M={m} K={r['K']} timeout={r['J']} "
          f"tool-failure={r['T']} not-a-unit-or-unstartable={r['N']}"
          + (f" slice-artifact-NOT-a-measurement={r['S']}" if r["S"] else "")
          + ("   (not started)" if r["K"] == 0 else ""))

if bad:
    print()
    for b, stray in bad:
        print(f"MISMATCH {b}: {len(stray)} measured row(s) name a unit that is "
              f"not in the collector's entry list: {stray}")
    sys.exit("K and M were computed against different unit sets; the triple "
             "above is not a triple.")
