#!/usr/bin/env python3
"""Is st1inch's F=0 a BACKEND choice? Five solvers, two vehicles, one control each.

WHY. st1inch scores 0/86 on the branch-coverage gate, and D29 established the
ordering: withdrawal/degradation cannot be the cause because F = 0, and with no
witnesses there is no `decisions` array for it to have thinned. F = 0 is a SOLVER
outcome -- of 4372 solves across the corpus, 464 were SAT and st1inch contributed
NONE. Coverage needs SAT: an `F` IS a SAT.

`INVOCATION_DECISIONS` row 7 says "let it auto-select", DECIDED on one contract.
ESBMC's auto-selection carries reasons (aqua: "detected >=3-level nested-mapping
shape; Bitwuzla aborts on the CONST_ARRAY-initialised infinite mapping array"),
but a router that is right about ABORTING is not thereby right about DECIDING --
those are different questions and only the first has been checked. And the corpus
confounds them completely: st1inch is the only benchmark run under
`--z3 --tuple-node-flattener` and the only one with any `solver-unknown`.

Two of that row's measurements are ALSO STALE and must be re-taken rather than
quoted: `--cvc5` was recorded raising `std::bad_alloc` at 4 g (this runs at 8 g),
and plain `--z3` was recorded core-dumping at ENCODING time on a defect that has
since been FIXED (two structs sharing a short name got one z3 tuple sort). Both
readings predate the current binary.

## THE CONTROL IS PER SOLVER, AND THAT IS THE WHOLE DESIGN

A backend that returns F = 0 on st1inch has said nothing until it is shown
capable of returning F > 0 AT ALL, on this build, in this mode. So every solver
runs BOTH vehicles:

  CONTROL   `aqua --focus-function safeBalances`  -- recorded at 2 SAT
            (0.130-0.145 s), 9 UNSAT, 0 no-verdict under the default backend.
  QUESTION  `st1inch --focus-function setFeeReceiver` -- an owner check and one
            assignment; D14 measured 10 VCCs of which 8 no-verdict, z3's own
            reason `out of memory`, IDENTICAL at 4 g and 16 g.

A solver that fails the control has its st1inch cell reported **VOID**, never as
a zero. This project has four discriminators on record that could not distinguish
their own outcomes; a per-arm control is the cheap fix.

## READINGS, FIXED BEFORE THE RUN

  1  some solver gives F > 0 on st1inch while auto gives 0
       -> THE AUTO-ROUTER IS LEAVING COVERAGE ON THE TABLE. Row 7 ("let it
          auto-select") is wrong for this benchmark and the gate's st1inch row
          is an artefact of backend selection, not of the contract.
  2  every solver that PASSES the control gives F = 0 on st1inch
       -> the zero is not a backend choice. It is the contract's encoding size,
          which is what D14's out-of-memory reading already says, and no solver
          flag is the fix.
  3  no solver passes the control
       -> the vehicle or the build is broken; nothing about st1inch may be read
          from this run at all.
  4  a solver passes the control but does not RETURN on st1inch
       -> that cell contributes nothing, NOT zero, and is printed as such.

Serial by construction, `--memlimit 8g`, `start_new_session` + `killpg` so a
timeout cannot leave an 8 GiB orphan. Running two ESBMCs at once has taken this
machine down before.

Usage: python3 solver_arms.py [outdir] [--timeout S]
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ESBMC = REPO / "build/src/esbmc/esbmc"
INPUTS = REPO / "notes/coverage/inputs"

BASE = ["--solidity-path-coverage", "--cov-report-json", "--solidity-max-tx", "1",
        "--path-cov-max-goals", "10000", "--memlimit", "8g"]

SOLVERS = [
    ("auto", []),
    ("z3", ["--z3"]),
    ("z3+node-flat", ["--z3", "--tuple-node-flattener"]),
    ("cvc5", ["--cvc5"]),
    ("bitwuzla", ["--bitwuzla"]),
]

# (label, flat stem, contract, unit, expected F for the control or None)
VEHICLES = [
    ("CONTROL aqua.safeBalances", "aqua__Aqua.flat.sol", "Aqua",
     "safeBalances", 2),
    ("QUESTION st1inch.setFeeReceiver", "st1inch__St1inch.flat.sol", "St1inch",
     "setFeeReceiver", None),
]

RE_TIME = re.compile(r"Runtime decision procedure:\s*([0-9.]+)s")


def run(flat, ast, contract, unit, flags, wd, timeout):
    cmd = ([str(ESBMC), str(ast), "--sol", str(flat), "--contract", contract,
            "--focus-function", unit] + BASE + list(flags))
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "cmd.txt").write_text(" ".join(cmd) + "\n")
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=str(wd), start_new_session=True)
    try:
        out, rc = p.communicate(timeout=timeout)[0], p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out, rc = (p.communicate()[0] or ""), None
    (wd / "run.log").write_text(out or "")
    wall = round(time.time() - t0, 1)
    rep = wd / "cov-report.json"
    s = {}
    if rep.exists():
        try:
            s = json.loads(rep.read_text()).get("summary", {})
        except ValueError:
            s = {}
    times = [float(m.group(1)) for m in RE_TIME.finditer(out or "")]
    return {
        "rc": rc, "wall": wall,
        "paths": s.get("paths_total"), "F": s.get("F_feasible_with_ce"),
        "U": s.get("U_undecided"),
        "reasons": {k: v for k, v in (s.get("U_reasons") or {}).items() if v},
        "times": times, "report": rep.exists(),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", nargs="?", default="solver_arms_out")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args(argv[1:])
    if not ESBMC.exists():
        sys.exit(f"no esbmc at {ESBMC}")
    root = Path(a.outdir)

    print("## Is st1inch's F=0 a BACKEND choice?\n")
    print(f"binary  : {ESBMC}  (mtime {int(ESBMC.stat().st_mtime)})")
    print(f"per-cell outer timeout: {a.timeout}s   memlimit 8g   serial\n")

    res = {}
    for vlabel, stem, contract, unit, expect in VEHICLES:
        flat, ast = INPUTS / stem, INPUTS / (stem + ".solast")
        if not flat.exists() or not ast.exists():
            sys.exit(f"missing input for {vlabel}: {flat} / {ast} -- an absent "
                     f"input is not a measured zero")
        print(f"### {vlabel}"
              + (f"   (control: expects F={expect})" if expect is not None
                 else ""))
        for sname, flags in SOLVERS:
            r = run(flat, ast, contract, unit, flags,
                    root / stem.split("__")[0] / sname, a.timeout)
            res[(vlabel, sname)] = r
            band = (f"{min(r['times']):.3f}-{max(r['times']):.3f}s over "
                    f"{len(r['times'])}" if r["times"] else "no solve timed")
            print(f"    {sname:<14} rc={str(r['rc']):<6} {r['wall']:>6}s  "
                  f"paths={r['paths']} F={r['F']} U={r['U']}  {band}"
                  + (f"  {r['reasons']}" if r["reasons"] else "")
                  + ("" if r["report"] else "   NO REPORT"))
        print()

    ctrl_label = VEHICLES[0][0]
    q_label = VEHICLES[1][0]
    expect = VEHICLES[0][4]
    passed = [s for s, _ in SOLVERS
              if res[(ctrl_label, s)]["F"] == expect]

    print("=" * 74)
    print(f"  solvers that PASSED the control (F={expect} on aqua."
          f"safeBalances): "
          + (", ".join(passed) if passed else "NONE"))
    if not passed:
        print("\n  ⛔ READING 3: no solver reproduced the control, so NOTHING "
              "about st1inch may\n     be read from this run. Fix the vehicle "
              "or the build first -- every st1inch\n     cell below would be a "
              "zero of unknown provenance.")
        return 1

    print("\n  st1inch cells, VOID unless that solver passed the control:\n")
    winners = []
    for s, _ in SOLVERS:
        q = res[(q_label, s)]
        if s not in passed:
            print(f"    {s:<14} VOID -- failed the control "
                  f"(F={res[(ctrl_label, s)]['F']}), so its st1inch result is "
                  f"not a measurement")
            continue
        if q["rc"] is None:
            print(f"    {s:<14} DID NOT RETURN in {a.timeout}s -- contributes "
                  f"nothing, NOT zero")
            continue
        if not q["report"]:
            print(f"    {s:<14} produced NO REPORT (rc={q['rc']}) -- "
                  f"contributes nothing, NOT zero")
            continue
        print(f"    {s:<14} F={q['F']}  U={q['U']}  {q['reasons'] or ''}")
        if (q["F"] or 0) > 0:
            winners.append((s, q["F"]))

    print()
    if winners:
        print("  ✅ READING 1: a backend that PASSES the control witnesses "
              "paths on st1inch:")
        for s, f in winners:
            print(f"        {s}: F={f}")
        print("     ⇒ the auto-selection is leaving coverage on the table, and "
              "row 7's\n        'let it auto-select' is wrong for this "
              "benchmark. The gate's st1inch row\n        is then an artefact "
              "of backend selection rather than of the contract.")
        return 0
    print("  ⛔ READING 2: every solver that passed the control returns F=0 on "
          "st1inch.\n     The zero is NOT a backend choice -- consistent with "
          "D14's reading that z3's\n     own reason is `out of memory`, "
          "unchanged between 4 g and 16 g. No solver flag\n     is the fix, and "
          "the next question is the ENCODING SIZE, not the router.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
