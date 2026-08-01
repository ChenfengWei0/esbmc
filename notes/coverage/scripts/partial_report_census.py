#!/usr/bin/env python3
"""Census every cov-report.json: is it PARTIAL, and do its two U-reason ledgers agree?

WHY THIS EXISTS. A cvc5 run on the real `St1inch.setFeeReceiver` died with
`std::bad_alloc` inside the FIRST solve, and its report filed all five paths as

    "u_reason": "unit-not-entered",
    "u_reason_detail": "harness never entered it (no --focus-function narrowing explains this)"

while the very same run's stdout carries

    Solving claim 'setFeeReceiver:path:15 at' with solver CVC5 1.1.2

so the unit WAS entered and one of its claims DID reach the solver. The bucket
that is true -- `run-died-before-solving`, which `dying-run-keeps-its-work.md`
step 2 built on purpose and keyed on `claims_in_solve_loop` -- read 0.

`unit-not-entered` is a diagnosis about the HARNESS (the dispatcher could not
reach the unit) and `run-died-before-solving` is a diagnosis about the RUN (it
needs a bigger budget). Opposite next actions, so this is not a cosmetic label.

WHAT IS CHECKED, and each is a fact taken from the PUBLISHED file rather than
from anything a writer believes it wrote:

  A. partial / partial_reason, top level AND under summary (several readers only
     ever open `summary`, so a disagreement between the two is its own defect).
  B. summary.U_reasons against a recount of the per-claim `u_reason` strings.
     Two ledgers of one fact; `one-fact-two-ledgers-diverge` says they drift.
  C. the per-claim boolean `not_solved_this_run` against the summary token
     `not-solved-this-run`. These are DIFFERENT facts wearing one name -- the
     token means "the simplifier folded the claim away", the boolean appears to
     mean "no verdict was recorded this run" -- so a mismatch here is expected
     and is reported as OVERLOADED-NAME, not as a drift. It is printed because a
     reader cannot tell them apart from the file alone.
  D. the contradiction that motivated the script: a report marked partial whose
     U-reasons blame `unit-not-entered` while `run-died-before-solving` is 0.

Usage: python3 partial_report_census.py <dir-or-report> [<dir-or-report> ...]
       (a directory is walked for every file named cov-report.json)
"""
import json
import os
import sys

TOKENS = [
    "bounded-holds",
    "claim-budget-exceeded",
    "named-obstacle",
    "not-solved-this-run",
    "run-died-before-solving",
    "solver-unknown",
    "unit-not-entered",
]


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f == "cov-report.json":
                        out.append(os.path.join(root, f))
        else:
            out.append(p)
    return sorted(out)


def one(path):
    with open(path) as fh:
        rep = json.load(fh)
    summ = rep.get("summary", {})
    claims = rep.get("claims", [])

    top_partial = rep.get("partial", "UNSTATED")
    sum_partial = summ.get("partial", "UNSTATED")
    reason = rep.get("partial_reason") or summ.get("partial_reason") or ""

    published = summ.get("U_reasons", {})
    recount = {t: 0 for t in TOKENS}
    unknown_tokens = {}
    nstr_true = 0
    for c in claims:
        if c.get("not_solved_this_run"):
            nstr_true += 1
        if c.get("status") != "U":
            continue
        tok = c.get("u_reason", "")
        if tok in recount:
            recount[tok] += 1
        else:
            unknown_tokens[tok] = unknown_tokens.get(tok, 0) + 1

    drift = {t: (published.get(t, 0), recount[t])
             for t in TOKENS if published.get(t, 0) != recount[t]}

    return {
        "path": path,
        "top_partial": top_partial,
        "sum_partial": sum_partial,
        "reason": reason,
        "claims_decided": summ.get("claims_decided", "?"),
        "claims_total": summ.get("claims_total", "?"),
        "paths_total": summ.get("paths_total", "?"),
        "F": summ.get("F_feasible_with_ce", "?"),
        "U": summ.get("U_undecided", "?"),
        "published": published,
        "recount": recount,
        "drift": drift,
        "unknown_tokens": unknown_tokens,
        "nstr_true": nstr_true,
        "nstr_token": published.get("not-solved-this-run", 0),
        "n_claims_in_json": len(claims),
    }


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    reports = collect(argv[1:])
    if not reports:
        sys.exit("no cov-report.json found under: " + " ".join(argv[1:]))

    rows = [one(p) for p in reports]

    print(f"## {len(rows)} report(s)\n")

    # ---- A. completeness ------------------------------------------------
    partials = [r for r in rows if r["top_partial"] is True
                or r["sum_partial"] is True]
    unstated = [r for r in rows if r["top_partial"] == "UNSTATED"]
    disagree = [r for r in rows if r["top_partial"] != r["sum_partial"]]
    print(f"A. completeness:  PARTIAL {len(partials)}   "
          f"UNSTATED(no field at all) {len(unstated)}   "
          f"top-level disagrees with summary {len(disagree)}")
    for r in partials:
        print(f"     PARTIAL  {r['path']}")
        print(f"              decided {r['claims_decided']} of "
              f"{r['claims_total']} claim(s); {r['reason']}")
    for r in unstated:
        # NOT "predates the partial marker" -- that was this script's first
        # reading and it was wrong. A run that enumerates no path takes a
        # different writer branch (`No complete path enumerated`) whose block
        # carries no completeness marker at all. Section E says whether the file
        # even has a live run behind it.
        print(f"     UNSTATED {r['path']}"
              f"   paths_total={r['paths_total']} claims_in_json="
              f"{r['n_claims_in_json']} — no completeness marker; see E")
    for r in disagree:
        print(f"     SPLIT    {r['path']}   top={r['top_partial']} "
              f"summary={r['sum_partial']}")

    # ---- B. two ledgers of the U-reasons --------------------------------
    drifted = [r for r in rows if r["drift"] or r["unknown_tokens"]]
    print(f"\nB. summary.U_reasons vs a recount of the per-claim u_reason "
          f"strings:  {len(drifted)} report(s) disagree")
    for r in drifted:
        print(f"     {r['path']}")
        for t, (pub, rec) in sorted(r["drift"].items()):
            print(f"        {t:<26} summary={pub:<6} per-claim={rec}")
        for t, n in sorted(r["unknown_tokens"].items()):
            print(f"        !! token not in this script's list: {t!r} x{n}")
    if not drifted:
        print("     (none -- the two ledgers agree on every report)")

    # ---- C. the overloaded name -----------------------------------------
    over = [r for r in rows if r["nstr_true"] != r["nstr_token"]]
    print(f"\nC. per-claim boolean `not_solved_this_run` vs the summary token "
          f"`not-solved-this-run`:  {len(over)} report(s) differ")
    for r in over:
        print(f"     {r['path']}   boolean-true={r['nstr_true']} "
              f"token={r['nstr_token']}  (of {r['n_claims_in_json']} claim(s) "
              f"in the JSON)")
    print("     ^ EXPECTED to differ: one name, two facts. The token means the "
          "simplifier\n       folded the claim away; the boolean appears to "
          "mean no verdict was recorded.\n       Printed because the file alone "
          "does not let a reader tell them apart.")

    # ---- D. the contradiction this script was written for ----------------
    print("\nD. a PARTIAL report that blames the harness "
          "(`unit-not-entered` > 0) while\n   `run-died-before-solving` is 0 "
          "-- the run died, so the run is the reason:")
    hits = [r for r in partials
            if r["published"].get("unit-not-entered", 0) > 0
            and r["published"].get("run-died-before-solving", 0) == 0]
    for r in hits:
        print(f"     ⛔ {r['path']}")
        print(f"        unit-not-entered {r['published'].get('unit-not-entered')}"
              f"  run-died-before-solving "
              f"{r['published'].get('run-died-before-solving')}"
              f"  (F {r['F']}, U {r['U']}, paths {r['paths_total']})")
        print(f"        reason on file: {r['reason']}")
    if not hits:
        print("     (none)")

    # ---- E. is there a LIVE RUN behind each report? -----------------------
    #
    # WHAT THIS SECTION FIRST DID, AND WHY IT WAS WRONG. It dated the binary
    # from the SET of keys under `summary.U_reasons`, reasoning that the
    # breakdown is published with every token including the zeros, so a 5-token
    # report must predate the two tokens added by `dying-run-keeps-its-work.md`.
    # It reported "MORE THAN ONE GENERATION IS PRESENT". That was WRONG, and the
    # alternative explanation was one command away:
    #
    #   * a run that enumerates NO path takes a different writer branch -- its
    #     stdout says `No complete path enumerated` and its `[Coverage]` block
    #     has no `Report Completeness`, no `Path Status` and no `U Reasons` line
    #     at all -- so a short token dict is a SHAPE, not a DATE; and
    #   * every one of those 48 files belongs to a unit the collector SKIPPED,
    #     so no build wrote them during this collection at all.
    #
    # Kept as a comment because the deleted check is the kind that reads as
    # confirmed (48 of 95! a clean split by benchmark!) while measuring nothing.
    # Note it was not even wrong in a safe direction: the corpus DOES span
    # several builds (section F measures it from runs.jsonl), so the discarded
    # check reached a partly-true conclusion through an argument that does not
    # support it -- which is why F reads the recorded binary identity instead of
    # inferring one from the report's shape.
    #
    # WHAT IS ACTUALLY TRUE, and it is worth its own section. Those 48 files are
    # STALE. `runs.jsonl` records them as
    #     "cmd": null, "reportPresent": false, "skipped": "library-has-no-dispatcher"
    # -- the collector deliberately did NOT run esbmc for them, because a library
    # has no dispatcher harness and the only other route is the banned
    # `--function`. The `cov-report.json` in their work dir is a leftover from an
    # earlier collection, and ANY consumer that walks the tree for
    # `cov-report.json` -- this script included, until this section existed --
    # counts it as if this collection had produced it.
    print("\nE. is there a LIVE RUN behind each report? (cross-checked against "
          "the benchmark's\n   runs.jsonl, which records `cmd: null, "
          "reportPresent: false` for a SKIPPED unit)")
    live, stale, unknown = [], [], []
    for r in rows:
        parts = r["path"].split(os.sep)
        if "pathcov" not in parts:
            unknown.append((r, "not under pathcov/ — no runs.jsonl to check"))
            continue
        bench_dir = os.sep.join(parts[:parts.index("pathcov") + 2])
        tag = parts[-2]
        jl = os.path.join(bench_dir, "runs.jsonl")
        if not os.path.exists(jl):
            unknown.append((r, f"no {jl}"))
            continue
        rec = None
        with open(jl) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("tag") == tag:
                    rec = d
        if rec is None:
            stale.append((r, "no entry with this tag in runs.jsonl at all"))
        elif rec.get("skipped"):
            stale.append((r, f"collector SKIPPED it: {rec['skipped']}"))
        elif rec.get("cmd") is None:
            stale.append((r, "runs.jsonl records cmd: null"))
        elif not rec.get("reportPresent", True):
            stale.append((r, "runs.jsonl records reportPresent: false"))
        else:
            live.append(r)
    print(f"     live {len(live)}   STALE {len(stale)}   unknown {len(unknown)}")
    for r, why in stale:
        print(f"     ⛔ STALE  {r['path']}\n               {why}")
    for r, why in unknown:
        print(f"     ?  {r['path']}\n               {why}")

    # ---- F. which BINARY produced each live run -------------------------
    #
    # Read from runs.jsonl, never inferred from the report's shape -- see the
    # note in E for what inferring it cost. `binary_identity()` records head,
    # srcDirty and binaryMtime, and ONLY binaryMtime identifies the executable:
    # head can move with no rebuild, and srcDirty=True says the tree had
    # uncommitted changes, so head does not even identify the SOURCE.
    #
    # Grouped BY BENCHMARK, because that is the comparison the gate actually
    # makes. Several builds in one corpus is bad; the split falling BETWEEN
    # benchmarks is worse, because then "benchmark A differs from benchmark B"
    # and "build A differs from build B" are the same column.
    bins, per_bench = {}, {}
    for r in live:
        parts = r["path"].split(os.sep)
        bench = parts[parts.index("pathcov") + 1]
        bench_dir = os.sep.join(parts[:parts.index("pathcov") + 2])
        jl = os.path.join(bench_dir, "runs.jsonl")
        with open(jl) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("tag") == parts[-2]:
                    b = d.get("binary", {})
                    key = (b.get("head"), b.get("binaryMtime"), b.get("srcDirty"))
                    bins[key] = bins.get(key, 0) + 1
                    per_bench.setdefault(bench, {}).setdefault(
                        b.get("binaryMtime"), set()).add(b.get("head"))
    print(f"\nF. which BINARY produced each of the {len(live)} LIVE run(s), read "
          f"from runs.jsonl\n   (only binaryMtime identifies the executable):")
    for (head, mtime, dirty), n in sorted(bins.items(), key=lambda kv: -kv[1]):
        print(f"     {n:>3} run(s)  binaryMtime={mtime}  head={head} "
              f"srcDirty={dirty}")
    mtimes = {m for _h, m, _d in bins}
    print("\n   per benchmark — a benchmark spanning >1 mtime is internally "
          "inconsistent;\n   two benchmarks on different mtimes cannot be "
          "compared with each other:")
    for bench in sorted(per_bench):
        for mtime, heads in sorted(per_bench[bench].items()):
            print(f"     {bench:<28} binaryMtime={mtime}  "
                  f"head(s)={','.join(sorted(h or '?' for h in heads))}")
        if len(per_bench[bench]) > 1:
            print(f"     {'':<28} ⛔ this benchmark alone spans "
                  f"{len(per_bench[bench])} builds")
    if len(mtimes) > 1:
        print(f"\n     ⛔ {len(mtimes)} DISTINCT BUILDS produced this corpus, so "
              f"its numbers are not one\n        measurement, and a difference "
              f"between two benchmarks may be a difference\n        between two "
              f"binaries.")
    else:
        print("\n     ✅ one binary produced every live run in this corpus.")

    # ---- G. the MIRROR of E: a run that produced NO report ---------------
    #
    # E asks "is there a run behind this report?" and finds phantoms. G asks the
    # other direction -- "did this run leave a report?" -- and finds HOLES, which
    # are the more dangerous of the two.
    #
    # A unit killed by the outer timeout is recorded with
    #     "killedByOuterTimeout": true, "reportPresent": false, "exitCode": -1
    # and leaves NO cov-report.json: `dying-run-keeps-its-work.md` step 2 states
    # the signal arm cannot write JSON, because malloc, iostream and the log
    # mutex are all unsafe in a handler. So a consumer that walks the tree for
    # cov-report.json does not see a zero for that unit -- IT DOES NOT SEE THE
    # UNIT. The paths leave the numerator and the denominator together, and the
    # benchmark's percentage goes UP.
    #
    # `missing-input-silently-rewrites-scope`: an absent input reads as "no
    # limit" when it means "the largest limit". Here an absent report reads as
    # "this benchmark had fewer units" when it means "a unit was too hard".
    print("\nG. the MIRROR check — a RUN in runs.jsonl that left NO report. A "
          "tree walk cannot\n   see these at all, so their paths leave the "
          "numerator AND the denominator:")
    seen_bench, holes = set(), []
    for r in rows:
        parts = r["path"].split(os.sep)
        if "pathcov" not in parts:
            continue
        seen_bench.add(os.sep.join(parts[:parts.index("pathcov") + 2]))
    for bench_dir in sorted(seen_bench):
        jl = os.path.join(bench_dir, "runs.jsonl")
        if not os.path.exists(jl):
            continue
        with open(jl) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("skipped") or d.get("cmd") is None:
                    continue          # deliberately not run; that is E's column
                if d.get("reportPresent"):
                    continue
                holes.append((bench_dir, d))
    if not holes:
        print("     (none — every run that was actually launched left a report)")
    for bench_dir, d in holes:
        why = ("killed by the OUTER TIMEOUT (the signal arm cannot write JSON)"
               if d.get("killedByOuterTimeout")
               else f"exitCode={d.get('exitCode')}, reportPresent=false")
        print(f"     ⛔ HOLE  {os.path.basename(bench_dir)} / {d.get('tag')}")
        print(f"              {why}; wall {d.get('wallSeconds')}s, "
              f"pathsInstrumented={d.get('pathsInstrumented')}")
        print(f"              ⇒ {d.get('pathsInstrumented')} enumerated path(s) "
              f"are absent from BOTH sides of this\n                benchmark's "
              f"ratio, which therefore reads HIGHER than the truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
