#!/usr/bin/env python3
"""Run the queued esbmc probes AS SOON AS the stage-2 sweep releases the machine.

The project rule is one esbmc at a time, so every probe that needs the verifier
has been queuing behind the corpus re-measurement. Rather than poll by hand and
start them late, this waits on that process and then runs them in order, each
time-boxed, each writing its own log.

WHAT IT RUNS, and why each is a DISCRIMINATOR rather than a data-gathering run:

  1. certify_summary.py -- the stage-2 funnel on the NEW binary. The previous
     one (7 certified of 133 witnessed paths) came from `fe550519c7` and
     certify_all.py refused to resume across the change, which is the guard
     working. This is the replacement number.

  2. put_all.py --auto-unwind 4 -- the corpus stage-4 table, with the ladder
     enabled for the first time. PREDICTION, written before the run: aqua
     `dock` is the one region of the seven that produced no PUT at all, refused
     UNDECIDED-TRUNCATED, and the tool named `__memset_impl`. If the ladder
     works it becomes a PUT; if it does not, put.json carries the attempts and
     says at which k it was still truncated. Either outcome is readable; what
     must not happen is the count moving with no attempts recorded.

  3. st1inch tx=1 vs tx=2, WHOLE CONTRACT -- the discriminator for task #48.
     st1inch's 128 claims are 81 `bounded-holds` and 47 `solver-unknown`, and
     the report says in every summary that a bounded-holds may be a path
     "guarded by state that an earlier transaction would have to establish".
     The bounded-holds concentrate in exactly the units that need a prior
     deposit. THE RUN MUST BE WHOLE-CONTRACT: a focused run cannot reach
     cross-function state at ANY transaction bound, because every transaction is
     another call to the same entry (INVOCATION_DECISIONS.md rows 1-2), so
     `--focus-function ... --solidity-max-tx 2` would answer nothing.
     PREDICTION: if the hypothesis holds, tx=2 converts some of those
     bounded-holds into F. If tx=1 and tx=2 give the same census, the cause is
     not transaction depth and the next probe is `--unwindset`.

     ⚠ Whole-contract st1inch may not finish in the budget. That is a MEASURED
     OUTCOME and is recorded as one -- it is not evidence about the hypothesis
     either way, and the summary says so rather than reading a timeout as "no
     change".

Usage: after_certify_probes.py --wait-pid <pid> [--budget-s 900]
"""
import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[3]
ESBMC = REPO / "build/src/esbmc/esbmc"
INPUTS = REPO / "notes/coverage/inputs"
OUT = pathlib.Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
                   "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/probes")


def alive(pid):
    return os.path.exists(f"/proc/{pid}")


def run(cmd, log, budget, cwd=None):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    out = p.stdout + p.stderr
    log.write_text(" ".join(str(c) for c in cmd) + "\n\n" + out)
    return p.returncode, out, time.time() - t0


def census(report):
    """(paths_total, F, U by reason) from a cov-report.json, or None."""
    if not report.exists():
        return None
    d = json.loads(report.read_text())
    c = collections.Counter()
    for cl in d.get("claims", []):
        st = cl.get("status")
        c[st] += 1
        if st == "U":
            c["U:" + str(cl.get("u_reason"))] += 1
    s = d.get("summary") or {}
    return {"paths_total": s.get("paths_total"), "partial": s.get("partial"),
            "counts": dict(c)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-pid", type=int, required=True)
    ap.add_argument("--budget-s", type=int, default=900,
                    help="outer timeout for each st1inch whole-contract run")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    while alive(a.wait_pid):
        time.sleep(30)
    print(f"[probes] pid {a.wait_pid} is gone; the machine is free", flush=True)

    print("\n===== 1. stage-2 funnel on the current binary =====", flush=True)
    rc, out, w = run([sys.executable,
                      str(REPO / "notes/coverage/scripts/certify_summary.py")],
                     OUT / "certify_summary.log", None)
    print(out, flush=True)

    print("\n===== 2. stage-4 corpus table WITH the unwind ladder =====",
          flush=True)
    rc, out, w = run([sys.executable,
                      str(REPO / "notes/coverage/scripts/put_all.py"),
                      "--auto-unwind", "4"],
                     OUT / "put_all_autounwind.log", None)
    print(out[-6000:] if len(out) > 6000 else out, flush=True)

    print("\n===== 3. st1inch tx=1 vs tx=2, WHOLE CONTRACT (task #48) =====",
          flush=True)
    flat = INPUTS / "st1inch__St1inch.flat.sol"
    results = {}
    for tx in (1, 2):
        wd = OUT / f"st1inch_whole_tx{tx}"
        wd.mkdir(parents=True, exist_ok=True)
        cmd = ["setsid", "timeout", "-k", "30s", f"{a.budget_s}s", str(ESBMC),
               str(flat) + ".solast", "--sol", str(flat),
               "--contract", "St1inch",
               "--solidity-path-coverage", "--solidity-max-tx", str(tx),
               "--cov-report-json", "--z3", "--tuple-node-flattener",
               "--memlimit", "8g", "--result-only"]
        print(f"[probes] st1inch whole tx={tx} ...", flush=True)
        rc, out, w = run(cmd, wd / "run.log", None, cwd=str(wd))
        results[tx] = {"exit": rc, "wall_s": round(w, 1),
                       "census": census(wd / "cov-report.json")}
        print(f"[probes]   exit={rc} {w:.0f}s  {results[tx]['census']}",
              flush=True)

    (OUT / "st1inch_tx_discriminator.json").write_text(
        json.dumps(results, indent=2))
    print("\n--- task #48 verdict ---", flush=True)
    c1, c2 = results[1]["census"], results[2]["census"]
    if c1 is None or c2 is None:
        print("NO VERDICT: one of the two runs produced no report "
              f"(tx=1 exit {results[1]['exit']}, tx=2 exit {results[2]['exit']}). "
              "A run that did not finish is not evidence that the depth "
              "changes nothing.", flush=True)
    else:
        f1 = c1["counts"].get("F", 0)
        f2 = c2["counts"].get("F", 0)
        b1 = c1["counts"].get("U:bounded-holds", 0)
        b2 = c2["counts"].get("U:bounded-holds", 0)
        print(f"tx=1: F={f1} bounded-holds={b1}   tx=2: F={f2} "
              f"bounded-holds={b2}", flush=True)
        print("HYPOTHESIS SUPPORTED (some bounded-holds became witnessed)"
              if f2 > f1 else
              "HYPOTHESIS NOT SUPPORTED at this bound: the depth alone does not "
              "convert them, so the next probe is --unwindset on the loops the "
              "tool names", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
