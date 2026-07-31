#!/usr/bin/env python3
"""Where does a path-coverage run's time actually GO?

Every mitigation applied to the OOM/timeout problem so far -- a bigger
`--memlimit`, a longer outer timeout, a partial report on death, covered-set
escalation, narrowing what is instrumented -- routes AROUND the cost. None of
them asked what the cost is made of, and the answer has been printed in every
run log all along, per claim:

    Slicing time: 0.016s (removed 949 assignments)
    Encoding to solver time: 0.028s
    Runtime decision procedure: 0.245s

plus the once-per-run `Symex completed in`, `Generated N VCC(s), M remaining`
and `GOTO program creation time`.

This aggregates them so "it is slow" becomes "X% of the wall is in the solver,
on claims that look like Y". A profile decides which of these is worth doing:

  * fewer JOBS          -- 98% of a focused run's claims are `unit-not-entered`
  * cheaper per JOB     -- each job re-slices and re-encodes a copy of the whole
                           equation
  * a different SOLVER  -- CVC5 is auto-selected on nested-mapping contracts
                           because Bitwuzla aborts on the CONST_ARRAY-initialised
                           infinite mapping array; that is a MODELLING choice,
                           and Bitwuzla is much faster on 256-bit bit-vectors
  * fewer PATHS         -- internal-call expansion multiplied aqua 39 -> 2846,
                           72.97x

It reads logs only. No esbmc run, no rebuild.

Usage: python3 claim_cost_profile.py <run.log> [...]
"""
import re
import sys
from collections import Counter
from pathlib import Path

SLICE_RE = re.compile(r"^Slicing time: ([0-9.]+)s")
ENCODE_RE = re.compile(r"^Encoding to solver time: ([0-9.]+)s")
SOLVE_RE = re.compile(r"^Runtime decision procedure: ([0-9.]+)s")
SYMEX_RE = re.compile(r"^Symex completed in: ([0-9.]+)s \((\d+) assignments\)")
VCC_RE = re.compile(r"^Generated (\d+) VCC\(s\), (\d+) remaining after "
                    r"simplification \((\d+) assignments\)")
# TWO GOTO TIMINGS, AND THEY ARE DIFFERENT PHASES. The first version of this
# script parsed only `creation` and did not parse `processing` at all, so the
# instrumentation pass -- the thing every proposed change to instrumentation
# would actually shrink -- was silently attributed ZERO, and `creation` was
# quoted in its place. Measured, they differ by an order of magnitude:
#
#   St1inch.setFeeReceiver   creation 13.398 s   processing 1.800 s
#   Aqua.dock                creation  1.241 s   processing 0.258 s
#   FarmingPool.deposit      creation  5.277 s   processing 0.205 s
#
# `creation` is the frontend Solidity -> GOTO conversion and is printed BEFORE
# "Adding Solidity complete-path coverage assertions..."; nothing done to the
# coverage pass can move it. `processing` is where the pass runs. Quoting the
# first as if it were the second overstated a proposed change's benefit by
# roughly 7x, which is the exact failure mode this file exists to prevent: an
# answer about the wrong thing.
GOTO_RE = re.compile(r"^GOTO program creation time: ([0-9.]+)s")
PROC_RE = re.compile(r"^GOTO program processing time: ([0-9.]+)s")
CLAIM_RE = re.compile(r"^Solving claim '([^']+)' with solver (\S+)")
PASS_RE = re.compile(r"^✓ PASSED: '([^']+)'")
FAIL_RE = re.compile(r"^✗ FAILED: '([^']+)'")
INSTR_RE = re.compile(r"instrumented (\d+) complete path\(s\) across "
                      r"(\d+) unit\(s\)")
EXPAND_RE = re.compile(r"before internal-call expansion the same units had "
                       r"(\d+) path\(s\), so expansion multiplied them by "
                       r"([0-9.]+)x")


def pct(x, total):
    return f"{100.0 * x / total:5.1f}%" if total else "    -"


def profile(path):
    lines = Path(path).read_text(errors="replace").splitlines()
    slices, encodes, solves = [], [], []
    symex_t = symex_n = None
    vcc = None
    goto_t = None
    proc_t = None
    instrumented = units = None
    pre_expansion = expansion = None
    claims_started = 0
    passed = failed = 0
    solvers = Counter()
    # Per-claim solve time, keyed by the claim being solved when the timing
    # line appears. The `Solving claim` line precedes its own timings, so the
    # most recent one owns them -- which is also why an unattributed timing is
    # counted separately rather than folded into the last claim.
    cur = None
    per_claim = []
    orphan_solves = 0

    for ln in lines:
        m = CLAIM_RE.match(ln)
        if m:
            cur = m.group(1)
            claims_started += 1
            solvers[m.group(2)] += 1
            continue
        m = PASS_RE.match(ln)
        if m:
            passed += 1
            continue
        m = FAIL_RE.match(ln)
        if m:
            failed += 1
            continue
        m = SLICE_RE.match(ln)
        if m:
            slices.append(float(m.group(1)))
            continue
        m = ENCODE_RE.match(ln)
        if m:
            encodes.append(float(m.group(1)))
            continue
        m = SOLVE_RE.match(ln)
        if m:
            t = float(m.group(1))
            solves.append(t)
            if cur is None:
                orphan_solves += 1
            else:
                per_claim.append((t, cur))
            continue
        m = SYMEX_RE.match(ln)
        if m:
            symex_t, symex_n = float(m.group(1)), int(m.group(2))
            continue
        m = VCC_RE.match(ln)
        if m:
            vcc = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue
        m = GOTO_RE.match(ln)
        if m:
            goto_t = float(m.group(1))
            continue
        m = PROC_RE.match(ln)
        if m:
            proc_t = float(m.group(1))
            continue
        m = INSTR_RE.search(ln)
        if m:
            instrumented, units = int(m.group(1)), int(m.group(2))
            continue
        m = EXPAND_RE.search(ln)
        if m:
            pre_expansion, expansion = int(m.group(1)), float(m.group(2))

    tot_slice, tot_enc, tot_solve = sum(slices), sum(encodes), sum(solves)
    accounted = (tot_slice + tot_enc + tot_solve + (symex_t or 0)
                 + (goto_t or 0) + (proc_t or 0))

    print(f"\n# {path}\n")
    if instrumented is not None:
        print(f"  instrumented        {instrumented} path(s) across {units} "
              f"unit(s)")
    if pre_expansion is not None:
        print(f"  before expansion    {pre_expansion} path(s)  "
              f"-> x{expansion} from internal-call inlining")
    if vcc:
        print(f"  VCCs                {vcc[0]} generated, {vcc[1]} remaining "
              f"after simplification ({vcc[2]} assignments)")
    if symex_t is not None:
        print(f"  symex               {symex_t}s ({symex_n} assignments)")
    if goto_t is not None:
        print(f"  goto creation       {goto_t}s   (frontend Solidity->GOTO; "
              f"the coverage pass cannot move this)")
    if proc_t is not None:
        print(f"  goto processing     {proc_t}s   (the instrumentation pass "
              f"itself)")
    print(f"  claims started      {claims_started}   PASSED {passed}   "
          f"FAILED {failed}")
    if solvers:
        print(f"  solver(s)           "
              + ", ".join(f"{k} x{v}" for k, v in solvers.most_common()))

    print(f"\n  {'phase':<22}{'total s':>10}{'share':>8}{'n':>8}"
          f"{'mean s':>10}{'max s':>10}")
    for name, xs in (("slicing", slices), ("encoding", encodes),
                     ("solving", solves)):
        if not xs:
            print(f"  {name:<22}{0.0:>10}{'    -':>8}{0:>8}")
            continue
        print(f"  {name:<22}{sum(xs):>10.2f}{pct(sum(xs), accounted):>8}"
              f"{len(xs):>8}{sum(xs)/len(xs):>10.3f}{max(xs):>10.2f}")
    for name, t in (("symex", symex_t),
                    ("goto creation (frontend)", goto_t),
                    ("goto processing (the pass)", proc_t)):
        if t is not None:
            print(f"  {name:<22}{t:>10.2f}{pct(t, accounted):>8}"
                  f"{1:>8}{t:>10.3f}{t:>10.2f}")
    print(f"  {'ACCOUNTED':<22}{accounted:>10.2f}")

    # THE TAIL IS THE STORY. A mean hides it: if 1% of claims carry most of the
    # solving time, "make every job cheaper" is the wrong project and "find out
    # what those claims have in common" is the right one.
    if per_claim:
        per_claim.sort(reverse=True)
        top = per_claim[:15]
        share = sum(t for t, _ in top) / tot_solve if tot_solve else 0
        print(f"\n  the 15 most expensive claims carry {100*share:.1f}% of all "
              f"solving time:")
        for t, name in top:
            print(f"    {t:>8.2f}s  {name}")
    if orphan_solves:
        print(f"\n  {orphan_solves} solve timing(s) had no preceding "
              f"`Solving claim` line and are counted in the totals but "
              f"attributed to no claim")


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <run.log> [...]")
    for p in argv[1:]:
        if not Path(p).exists():
            print(f"\n# {p}\n  MISSING")
            continue
        profile(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
