#!/usr/bin/env python3
"""THE FOUR-STAGE FUNNEL, with instrumentation as 100%.

    stage 1a  INSTRUMENTED   paths the instrumenter created         = 100%
    stage 1b  REPORTED       paths that came back in a report       (the gap is
                             units the run KILLED before reporting)
    stage 2   CE             paths witnessed with a counterexample  (status F)
    stage 3   REGION         paths whose region was CERTIFIED
    stage 4   ORACLE         emitted .t.sol carrying >=1 assert on post-state or
                             the return value, and of those, B (all five gates)

⛔ ONE CELL ONLY. Stage 1 is read from notes/coverage/pathcov/<bench>/runs.jsonl,
whose recorded command line is `--focus-function <unit> --solidity-max-tx 1`;
stage 2 is read from a certification ledger run in the same cell. The script
PRINTS both and prints every row's binary. It does not attempt to reconcile
different cells and it never sums two arms.

⚠ WHAT THE STAGE-3 COLUMN IS NOT. The certification ledger and the stage-1
ledger were produced by SEPARATE runs, so a unit's `witnessed` in one need not
equal its `F` in the other. The script prints BOTH and flags every disagreement
rather than silently preferring one. A funnel whose stages come from runs that
disagree about the stage before is not a funnel.

usage:  python3 scripts/pipeline_funnel.py
"""
import json
import glob
import os
import collections

PATHCOV = "notes/coverage/pathcov"
# The default-unwind arm of each benchmark: the directory with no `__suffix`.
# The __unwind8 / __dock_* directories are OTHER arms and are never summed in.
CERT = "notes/coverage/certify/results_pieces_corpus.jsonl"
POC_CERT = "notes/coverage/certify/poc_results.jsonl"
PUT_ROOT = "notes/coverage/put_roundtrip"


def load_jsonl(p):
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]


def stage1():
    """per (benchmark, unit): instrumented, reported, F, U, skipped-reason."""
    out = {}
    bins = collections.Counter()
    cmds = collections.Counter()
    for p in sorted(glob.glob(f"{PATHCOV}/*/runs.jsonl")):
        bench = os.path.basename(os.path.dirname(p))
        if "__" in bench:
            continue  # a different arm; never merged
        for r in load_jsonl(p):
            unit = r.get("function")
            bins[json.dumps(r.get("binary"), sort_keys=True)] += 1
            c = r.get("cmd")
            if c:
                toks = c if isinstance(c, list) else c.split()
                cell = []
                for i, t in enumerate(toks):
                    if t in ("--solidity-max-tx", "--focus-function",
                             "--path-cov-max-goals"):
                        cell.append(
                            t + " " + (toks[i + 1] if t != "--focus-function"
                                       else "<unit>")
                        )
                cmds[" ".join(cell)] += 1
            out[(bench, unit)] = {
                "instrumented": r.get("pathsInstrumented"),
                "reported": r.get("pathsTotal"),
                "F": r.get("F"),
                "U": r.get("U"),
                "skipped": r.get("skipped"),
                "killed": r.get("killedByOuterTimeout"),
                "exit": r.get("exitCode"),
            }
    return out, bins, cmds


def main():
    s1, s1bins, s1cmds = stage1()

    print("=" * 78)
    print("THE CELL AND THE BINARY OF STAGE 1")
    print("=" * 78)
    for c, n in s1cmds.most_common():
        print(f"  {n:4d} run(s)  {c}")
    for b, n in s1bins.most_common():
        print(f"  {n:4d} run(s)  binary={b}")
    print()

    cert = load_jsonl(CERT)
    cbins = collections.Counter(
        json.dumps(r.get("binary"), sort_keys=True) for r in cert
    )
    print("=" * 78)
    print("THE BINARY OF STAGE 2/3 (the certification ledger)")
    print("=" * 78)
    print(f"  file: {CERT}")
    for b, n in cbins.most_common():
        print(f"  {n:4d} row(s)  binary={b}")
    print()

    cmap = {(r["benchmark"], r["unit"]): r for r in cert}

    # ---- stage 4, read from the emitted text, not from a promise ----------
    # Counted per FORGE PROJECT, never summed across arms.
    puts = collections.defaultdict(lambda: {"files": 0, "oracle": 0})
    for proj in sorted(glob.glob(f"{PUT_ROOT}/*")):
        name = os.path.basename(proj)
        tdir = os.path.join(proj, "test")
        if not os.path.isdir(tdir):
            continue
        for f in sorted(glob.glob(os.path.join(tdir, "*.t.sol"))):
            txt = open(f).read()
            if "function test_put_" not in txt:
                continue
            puts[name]["files"] += 1
            # the emitter writes its own ORACLE header line; count the file as
            # carrying an oracle only when that header says a nonzero number
            for line in txt.splitlines():
                if line.strip().startswith("// ORACLE:"):
                    if not line.strip().startswith("// ORACLE: 0 "):
                        puts[name]["oracle"] += 1
                    break

    print("=" * 78)
    print("STAGE-1 FUNNEL, PER BENCHMARK (default-unwind arm only)")
    print("=" * 78)
    hdr = (f"{'benchmark':30s} {'instr':>7s} {'report':>7s} {'CE(F)':>7s} "
           f"{'U':>7s} {'skipped':>8s} {'killed':>7s}")
    print(hdr)
    tot = collections.Counter()
    per_bench = collections.defaultdict(collections.Counter)
    for (bench, unit), v in sorted(s1.items()):
        b = per_bench[bench]
        if v["skipped"]:
            b["skipped"] += 1
            continue
        b["instrumented"] += v["instrumented"] or 0
        b["reported"] += v["reported"] or 0
        b["F"] += v["F"] or 0
        b["U"] += v["U"] or 0
        if v["reported"] is None:
            b["killed"] += 1
    for bench in sorted(per_bench):
        b = per_bench[bench]
        print(f"{bench:30s} {b['instrumented']:7d} {b['reported']:7d} "
              f"{b['F']:7d} {b['U']:7d} {b['skipped']:8d} {b['killed']:7d}")
        for k in ("instrumented", "reported", "F", "U"):
            tot[k] += b[k]
    print("-" * 78)
    print(f"{'TOTAL':30s} {tot['instrumented']:7d} {tot['reported']:7d} "
          f"{tot['F']:7d} {tot['U']:7d}")
    print()

    # ---- the join, and every disagreement named ---------------------------
    print("=" * 78)
    print("STAGE 2 -> 3, JOINED TO STAGE 1 PER UNIT")
    print("=" * 78)
    print(f"{'benchmark::unit':46s} {'s1.F':>5s} {'s2.witn':>8s} "
          f"{'certified':>10s}  note")
    disagree = 0
    j = collections.Counter()
    for key in sorted(cmap):
        bench, unit = key
        r = cmap[key]
        w = r.get("witnessed")
        c = len(r.get("certified") or {})
        f1 = s1.get(key, {}).get("F")
        note = ""
        if isinstance(w, int) and isinstance(f1, int) and w != f1:
            note = "⚠ stage-1 F and stage-2 witnessed DISAGREE"
            disagree += 1
        if key not in s1:
            note = "⚠ no stage-1 row for this unit"
        print(f"{bench + '::' + unit:46s} {str(f1):>5s} {str(w):>8s} "
              f"{c:10d}  {note}")
        j["witnessed"] += w if isinstance(w, int) else 0
        j["certified"] += c
    print("-" * 78)
    print(f"{'TOTAL':46s} {'':>5s} {j['witnessed']:8d} {j['certified']:10d}")
    if disagree:
        print(f"\n  ⚠ {disagree} unit(s) disagree between the two ledgers. They "
              "are separate runs of the same cell; the funnel below uses the "
              "certification ledger's own `witnessed`, because that is the "
              "number its `certified` was computed against.")
    print()

    print("=" * 78)
    print("STAGE 4, PER FORGE PROJECT (⛔ arms are NEVER summed)")
    print("=" * 78)
    print(f"{'project':52s} {'PUT files':>10s} {'with oracle':>12s}")
    for name in sorted(puts):
        v = puts[name]
        print(f"{name:52s} {v['files']:10d} {v['oracle']:12d}")
    print()
    print("  B (all five WORKORDER gates, incl. forge green) is NOT computed")
    print("  here -- it needs a real forge run. Use scripts/count_b.py.")
    print()


if __name__ == "__main__":
    main()
