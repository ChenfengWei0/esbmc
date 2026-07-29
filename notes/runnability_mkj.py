#!/usr/bin/env python3
"""M / K / J per project: units in the corpus, units measured, units timed out.

The plan requires this triple to be reported ("that project has M units, the
first K were measured in lexicographic order, J of them timed out"). M comes
from the locked collector's own entry list; K and J come from the rows actually
written. Neither is recalled -- a remembered M is exactly the kind of number
that ends up in a paper unverified.
"""
import json
import os

DATA = "/home/samson/workspace/esbmc/notes/coverage/data"
OUT = "/home/samson/workspace/esbmc/notes/runnability-distribution.md"

BENCHES = ["aqua_Aqua", "cross_chain_swap_EscrowDst", "cross_chain_swap_EscrowSrc",
           "farming", "limit_order_protocol", "st1inch_St1inch"]

rows = {}
with open(OUT) as f:
    for line in f:
        if not line.startswith("| `"):
            continue
        parts = [p.strip() for p in line.split("|")]
        bench = parts[1].strip("` ")
        d = rows.setdefault(bench, {"K": 0, "J": 0})
        d["K"] += 1
        if "TIMEOUT" in line:
            d["J"] += 1

for b in BENCHES:
    p = os.path.join(DATA, f"esbmc_{b}.json")
    with open(p) as f:
        rep = json.load(f)
    m = len(rep["per_function"]["functions"])
    r = rows.get(b, {"K": 0, "J": 0})
    print(f"{b}: M={m} K={r['K']} J={r['J']}"
          + ("   (not started)" if r["K"] == 0 else ""))
