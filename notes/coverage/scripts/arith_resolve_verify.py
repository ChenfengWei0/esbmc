#!/usr/bin/env python3
"""Does --path-cov-arith-resolve actually replace the chain-rejected witness?

A MUST-FLIP PAIR, not a single run. The mechanism is only shown to work if the
SAME command WITHOUT the flag still produces the old (wrapping) witness: a run
that merely looks right proves nothing when the failure could have gone away on
its own, and this project has shipped a guard that was always true and a
function that was never called.

  off   --overflow-check alone            -> the wrap must STILL be there
  on    --overflow-check + the flag        -> the witness must CHANGE, and the
                                              new one must not wrap
  refuse the flag with no check enabled    -> must ABORT with a named reason,
                                              not run and report a clean sweep

`bal` starts at 500 in D10, so a non-wrapping `amt` is one with
amt + 500 <= 2^256-1. The check is done on the FINAL STATE the report itself
publishes -- final_state == entry + amt without wrapping -- rather than by
re-deriving the arithmetic here, because a second implementation of an
arithmetic the model already performs is free to disagree with it.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
POC = Path("/home/samson/workspace/esbmc/notes/coverage/poc")
OUT = Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
           "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/arith_verify")
MASK = (1 << 256) - 1


def run(stem, contract, cell, extra):
    wd = OUT / stem / cell
    wd.mkdir(parents=True, exist_ok=True)
    sol = POC / f"{stem}.sol"
    ast = POC / f"{stem}.solast"
    cmd = [ESBMC, str(ast) if ast.exists() else str(sol), "--sol", str(sol),
           "--contract", contract, "--solidity-path-coverage",
           "--cov-report-json", "--solidity-max-tx", "1",
           "--memlimit", "4g"] + extra
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                           timeout=180, start_new_session=True)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out = -9, (e.stdout or b"").decode("utf-8", "replace")
    (wd / "full.log").write_text(out)
    return rc, out, wd, time.time() - t0


def rows(wd):
    f = wd / "cov-report.json"
    if not f.exists():
        return None
    r = json.loads(f.read_text())
    out = []
    for c in r.get("claims", []):
        if c.get("status") != "F":
            continue
        out.append({
            "path": c.get("path_id"),
            "exit": c.get("exit_kind"),
            "inputs": c.get("inputs", {}),
            "entry": c.get("entry_storage", {}),
            "final": c.get("final_state", {}),
            "arith_revert_only": c.get("arith_revert_only", False),
        })
    out.sort(key=lambda d: str(d["path"]))
    return out, r.get("summary", {}).get("arith_resolve"), \
        r.get("summary", {}).get("arith_revert_only_paths")


def wraps(row):
    """Does the report's OWN numbers say this witness wrapped? entry + amt is
    compared against the published final, so nothing is re-derived beyond the
    addition the contract performs."""
    try:
        amt = int(list(row["inputs"].values())[0], 0)
        e = int(list(row["entry"].values())[0], 0)
        f = int(list(row["final"].values())[0], 0)
    except (ValueError, IndexError, TypeError):
        return None
    return (e + amt) > MASK and f == ((e + amt) & MASK)


def main():
    for stem, contract in (("D10_WrapNotPanic", "D10_WrapNotPanic"),
                           ("Tiny2", "Tiny2")):
        print(f"\n===== {stem}")
        res = {}
        for cell, extra in (
                ("off", ["--overflow-check"]),
                ("on", ["--overflow-check", "--path-cov-arith-resolve"])):
            rc, out, wd, dt = run(stem, contract, cell, extra)
            got = rows(wd)
            if got is None:
                print(f"  {cell:4s} rc={rc} {dt:.1f}s  NO REPORT ({wd}/full.log)")
                res[cell] = None
                continue
            rr, cost, n_only = got
            res[cell] = rr
            print(f"  {cell:4s} rc={rc} {dt:5.1f}s  {len(rr)} F   "
                  f"arith_resolve={cost}  revert_only={n_only}")
            for d in rr:
                w = wraps(d)
                mark = "WRAPS" if w else ("ok" if w is False else "?")
                print(f"      path {d['path']:>4} exit={d['exit']:<12} "
                      f"{mark:<6} inputs={d['inputs']} final={d['final']}"
                      + ("  [arith_revert_only]" if d["arith_revert_only"]
                         else ""))
        a, b = res.get("off"), res.get("on")
        if a is None or b is None:
            print("  [must-flip] NOT COMPARABLE (a report is missing)")
            continue
        wa = sum(1 for d in a if wraps(d))
        wb = sum(1 for d in b if wraps(d))
        print(f"  [must-flip] wrapping witnesses: off={wa}  on={wb}   "
              + ("PASS" if wa > 0 and wb < wa else
                 "** FAIL: " + ("the wrap is not reproducing without the flag, "
                                "so this run proves nothing"
                                if wa == 0 else
                                "the flag did not remove any wrap") + " **"))

    # The refusal, which is the third half of the pair: a flag that quietly does
    # nothing when its precondition is unmet is the shape this project keeps
    # shipping.
    print("\n===== refusal: the flag with NO arithmetic check enabled")
    rc, out, wd, dt = run("D10_WrapNotPanic", "D10_WrapNotPanic", "refuse",
                          ["--path-cov-arith-resolve"])
    said = "needs the conditions it is supposed to assume" in out
    print(f"  rc={rc}  names its reason: {said}   "
          + ("PASS" if rc != 0 and said else "** FAIL: it should refuse **"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
