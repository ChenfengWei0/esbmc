#!/usr/bin/env python3
"""
summarize.py -- print the LOCKED comparison from data/esbmc_*.json.

Both columns share the SAME denominator (METHODOLOGY §2 canonical AST
decision count).  This is the headline invariant.
"""
import argparse, json, sys
from pathlib import Path

DATA = Path("/home/samson/workspace/esbmc/notes/coverage/data")

BENCHES = [
    "aqua_Aqua",
    "cross_chain_swap_EscrowDst",
    "cross_chain_swap_EscrowSrc",
    "farming",
    "limit_order_protocol",
    "st1inch_St1inch",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--per-file", action="store_true")
    args = ap.parse_args()

    rows = []
    for b in BENCHES:
        p = DATA / f"esbmc_{b}.json"
        # A MISSING BENCHMARK USED TO `continue`. It then left BOTH the
        # numerator and the denominator of the AGGREGATE row, so a four-
        # benchmark aggregate printed in exactly the same shape as a six-
        # benchmark one, with no count anywhere to tell them apart.
        if not p.exists():
            sys.exit(f"missing {p}: this is an aggregate over all "
                     f"{len(BENCHES)} benchmarks or it is not an aggregate")
        d = json.loads(p.read_text())
        t = d["no_function"]["total"]
        rows.append((b, d, t))

    if args.json:
        print(json.dumps([{"bench": b, "total": t} for b, _, t in rows], indent=2))
        return

    print("=" * 100)
    print("  LOCKED comparison (notes/coverage/METHODOLOGY.md)")
    print("  Denominator = canonical AST decision count, SAME for ESBMC and native.")
    print("=" * 100)
    print(f'  {"benchmark":<32} {"branchesTotal":>14}  {"ESBMC":>17}  {"native":>17}  delta')
    print("-" * 100)
    s_denom = s_esbmc = s_native = 0
    for b, d, t in rows:
        denom = t["branchesTotal"]
        e_r = t["esbmcReached"]; e_p = t["esbmcCoveragePct"]
        n_r = t["nativeReached"]; n_p = t["nativeCoveragePct"]
        delta = round(n_p - e_p, 2)
        verdict = "ESBMC ≥ test" if e_p >= n_p else f"-{delta}pp"
        print(f'  {b:<32} {denom:>14}  {e_r:>4}/{denom:<4} ({e_p:>5.1f}%)  {n_r:>4}/{denom:<4} ({n_p:>5.1f}%)  {verdict}')
        s_denom += denom; s_esbmc += e_r; s_native += n_r
    print("-" * 100)
    e_pct = round(100*s_esbmc/s_denom, 2) if s_denom else 0
    n_pct = round(100*s_native/s_denom, 2) if s_denom else 0
    print(f'  {"AGGREGATE":<32} {s_denom:>14}  {s_esbmc:>4}/{s_denom:<4} ({e_pct:>5.1f}%)  {s_native:>4}/{s_denom:<4} ({n_pct:>5.1f}%)')

    # PROVENANCE, printed unconditionally. These rows are not automatically
    # commensurable: they are separate files written by separate runs, and a
    # re-collection touches them one at a time. A table mixing two binaries'
    # output looks exactly like a table from one. `nativeSource` exists so a
    # carried-forward native column announces itself, and it was read by
    # nothing until now.
    print("-" * 100)
    print("  provenance")
    import datetime
    for b, d, _t in rows:
        p = DATA / f"esbmc_{b}.json"
        when = datetime.datetime.fromtimestamp(
            p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        ns = d.get("nativeSource", "(not recorded)")
        kind = "native CARRIED FORWARD" if ns.startswith("CARRIED") else \
               ("native from lcov" if ns.startswith("lcov")
                else "native provenance NOT RECORDED")
        print(f'  {b:<32} collected {when}   {kind}')

    if args.per_file:
        print()
        print("=" * 100)
        print("  PER-FILE breakdown + instrumentation gap")
        print("=" * 100)
        for b, d, _ in rows:
            print(f'\n## {b}')
            print(f'  {"file":<55}  {"AST":>4}  {"ESBMC instr":>12}  {"ESBMC reach":>12}  {"lcov instr":>11}  {"lcov reach":>11}')
            for p in d["no_function"]["perFile"]:
                e = p["esbmc"]; n = p["native"]
                e_gap = p["astDecisions"] - e["instrumented"]
                n_gap = p["astDecisions"] - n["instrumented"]
                gap_e = f'  (-{e_gap})' if e_gap > 0 else f'  (+{-e_gap})' if e_gap < 0 else ''
                gap_n = f'  (-{n_gap})' if n_gap > 0 else f'  (+{-n_gap})' if n_gap < 0 else ''
                print(f'  {p["file"]:<55}  {p["astDecisions"]:>4}  '
                      f'{e["instrumented"]:>4}{gap_e:<8}  {e["reached"]:>4} ({e["coveragePct"]:>5.1f}%)  '
                      f'{n["instrumented"]:>4}{gap_n:<7}  {n["reached"]:>4} ({n["coveragePct"]:>5.1f}%)')

if __name__ == "__main__":
    main()
