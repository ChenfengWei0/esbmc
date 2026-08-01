#!/usr/bin/env python3
"""Can two FOCUSED runs sharing one --coverage-covered-set collide?

WHY. `INVOCATION_DECISIONS.md` recommends a workflow:

    "Splitting a large contract across runs, now that it takes a SET
     (--focus-function deposit,withdraw) and unions through
     --coverage-covered-set."

and `goto_coverage.cpp` fingerprints the covered set on unwind / cap / contract
with an explicit comment that `--focus-function` is DELIBERATELY not in it,
followed by a second comment calling the resulting file "an UNATTRIBUTABLE union
-- a flat array of ids with no record of which configuration witnessed each".

Those two can both be fine, or the recommendation can be unsafe, and which it is
turns on ONE fact: is a stored id namespaced by its unit, or is it the bare path
identity? Path ids are `enc`, and `enc` is built per unit by the same recurrence
(`enc_0 = 1`, `enc_{k+1} = 2*enc_k + bit`), so two different units routinely
carry the SAME small integers. If the stored id is bare, a union across focused
runs would let one unit's witnessed path mark another unit's path covered.

MEASURED RATHER THAN READ. `goto_coverage.cpp` is ~8000 lines and the question is
one field wide; the union file itself answers it directly, and a measurement
cannot be wrong about what the code does the way a reading can.

FIXTURE: `poc/Tiny.sol` -- two units, `deposit` and `withdraw`, both of which
enumerate paths at tx=1.

READINGS, FIXED BEFORE THE RUN:

  A  ids carry the unit (e.g. "deposit:path:3")
       -> no cross-unit collision is possible, the fingerprint's omission of
          focus is SAFE for this workflow, and the recommendation stands as
          written.
  B  ids are bare (an enc, or anything not naming the unit)
       -> two focused runs of the same contract at the same unwind share a
          fingerprint AND a namespace, so one unit's witnessed path can mark
          another unit's covered. The recommendation is unsafe as written and
          has to carry the qualifier.
  C  run 2 does not load run 1's set at all
       -> the union does not happen, so the recommendation does not work for a
          different reason. Report which.

Also observed, because it is the consequence rather than the mechanism: does run
2 report any path as ALREADY COVERED, and does the union grow or get replaced?

Usage: python3 covered_set_focus_check.py [--timeout S]
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ESBMC = REPO / "build/src/esbmc/esbmc"
POC = REPO / "notes/coverage/poc"
SOL = POC / "Tiny.sol"
AST = POC / "Tiny.solast"

INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")


def run_focus(focus, union, wd, timeout):
    cmd = [str(ESBMC), str(AST), "--sol", str(SOL),
           "--solidity-path-coverage", "--contract", "Tiny",
           "--focus-function", focus,
           "--solidity-max-tx", "1", "--cov-report-json",
           "--coverage-covered-set", str(union),
           "--memlimit", "4g"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=str(wd), start_new_session=True)
    try:
        out = p.communicate(timeout=timeout)[0]
        rc = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out, rc = (p.communicate()[0] or "") + "\n[killed]", None
    return cmd, rc, out


def read_union(p):
    if not Path(p).exists():
        return None
    try:
        return json.loads(Path(p).read_text())
    except ValueError as e:
        return {"PARSE ERROR": str(e)}


def ids_of(u):
    if not isinstance(u, dict):
        return []
    v = u.get("covered")
    return list(v) if isinstance(v, list) else []


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv[1:])
    for p in (ESBMC, SOL, AST):
        if not p.exists():
            sys.exit(f"missing {p}")

    print("## Can two FOCUSED runs sharing one covered set collide?\n")
    print(f"binary : {ESBMC}  (mtime {int(ESBMC.stat().st_mtime)})")
    print(f"fixture: {SOL}  -- units `deposit` and `withdraw`\n")

    with tempfile.TemporaryDirectory() as wd:
        union = Path(wd) / "union.json"
        seen = {}
        for i, focus in enumerate(("deposit", "withdraw"), 1):
            t0 = time.time()
            cmd, rc, out = run_focus(focus, union, wd, a.timeout)
            m = INSTR.search(out)
            u = read_union(union)
            ids = ids_of(u)
            already = out.count("already covered")
            print(f"--- run {i}: --focus-function {focus}   "
                  f"rc={rc}  {time.time() - t0:.1f}s")
            print("    " + (f"{m.group(1)} path(s) across {m.group(2)} unit(s)"
                            if m else "(no instrumentation line)"))
            print(f"    union file: "
                  + ("ABSENT" if u is None else
                     f"{len(ids)} id(s) -> {ids}"))
            if isinstance(u, dict):
                other = sorted(k for k in u if k != "covered")
                if other:
                    print(f"    other top-level key(s): {', '.join(other)}")
            if already:
                print(f"    output mentions 'already covered' {already} time(s)")
            print()
            seen[focus] = ids

        after1, after2 = seen["deposit"], seen["withdraw"]

    print("=" * 74)
    if not after2:
        print("  READING C: the second run wrote no ids, so no union happened.\n"
              "  The recommended split-across-runs workflow does not do what "
              "the note says it\n  does, and the reason has to be read off the "
              "run above before it is quoted.")
        return 1
    namespaced = all(not str(i).lstrip("-").isdigit() for i in after2)
    grew = len(after2) > len(after1) and set(after1) <= set(after2)
    print(f"  union after run 1: {len(after1)} id(s)")
    print(f"  union after run 2: {len(after2)} id(s)"
          + ("  (grew, and run 1's ids survived)" if grew else
             "  (did NOT grow as a superset of run 1's)"))
    if namespaced:
        print("\n  ✅ READING A: every stored id is NON-NUMERIC, i.e. it carries "
              "more than the\n     bare `enc`. A cross-unit collision is not "
              "possible on this evidence, so the\n     fingerprint leaving "
              "`--focus-function` out is SAFE for the split-across-runs\n     "
              "workflow INVOCATION_DECISIONS recommends. Print the ids above in "
              "any write-up:\n     the claim is about their SHAPE, and the shape "
              "is what was measured.")
        return 0
    print("\n  ⛔ READING B: at least one stored id is a bare number, so the "
          "namespace is the\n     path identity alone. Two focused runs of the "
          "same contract at the same unwind\n     share a fingerprint AND a "
          "namespace, and `enc` is built per unit by the same\n     recurrence "
          "-- so one unit's witnessed path can mark another unit's covered.\n"
          "     INVOCATION_DECISIONS' split-across-runs recommendation is "
          "unsafe as written.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
