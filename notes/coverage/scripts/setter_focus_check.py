#!/usr/bin/env python3
"""Is `focus = {unit, its setter}` + tx>=2 as good as the whole contract + tx>=2?

This is the question that decides whether multi-name `--focus-function` is worth
wiring into the corpus collector. See `notes/coverage/poc/F02_SetterFocus.sol`
for the full framing and `notes/coverage/D23-two-knobs-neither-alone.md` for the
2x2 that produced it.

THE COMPARISON IS ON WITHDRAW'S OWN PATHS, NOT ON TOTALS. The whole-contract run
also enumerates `noise`, so its F and its denominator are both larger for a
reason unrelated to the question. This script therefore reads the per-claim
`condition` field out of `cov-report.json` and counts F/U for claims whose
condition starts with `withdraw:` only. Comparing the printed `Path Status`
totals instead would silently answer a different question -- which is the shape
this project has already been burned by (a per-function field read as an
aggregate).

POSITIVE CONTROL, and it must fire or the negative cells mean nothing:
`focus=withdraw, tx=2` MUST leave withdraw's guarded paths undecided. If they
are F there, the setter is not what establishes the state and every reading
below is void.

Usage: python3 setter_focus_check.py [<esbmc>] [<poc-dir>]
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ESBMC = HERE.parent.parent.parent / "build" / "src" / "esbmc" / "esbmc"
DEFAULT_POC = HERE.parent / "poc"
CONTRACT = "F02_SetterFocus"
UNIT = "withdraw"

INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")

CELLS = [
    ("withdraw", 2, "POSITIVE CONTROL: setter not callable -> must stay U"),
    ("seed,withdraw", 2, "THE CHEAP CELL: does naming the setter suffice?"),
    (None, 2, "REFERENCE: whole contract"),
    ("withdraw", 1, "for contrast: the cell the corpus was collected in"),
    (None, 1, "for contrast: focus off but tx=1"),
]


def run(esbmc, poc, focus, tx, workdir):
    cmd = [
        str(esbmc),
        str(poc / f"{CONTRACT}.solast"),
        "--sol", str(poc / f"{CONTRACT}.sol"),
        "--solidity-path-coverage",
        "--cov-report-json",
        "--solidity-max-tx", str(tx),
        "--memlimit", "4g",
        "--contract", CONTRACT,
    ]
    if focus:
        cmd += ["--focus-function", focus]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       cwd=workdir)
    rep = Path(workdir) / "cov-report.json"
    claims = []
    if rep.exists():
        claims = json.loads(rep.read_text()).get("claims", [])
    return p.returncode, p.stdout + p.stderr, claims


def unit_tally(claims, unit):
    """F / U counts for THIS unit's claims only, plus the U reasons seen."""
    f = u = 0
    reasons = {}
    for c in claims:
        cond = c.get("condition") or ""
        if not cond.startswith(unit + ":"):
            continue
        if c.get("status") == "F":
            f += 1
        else:
            u += 1
            r = c.get("u_reason") or "(none)"
            reasons[r] = reasons.get(r, 0) + 1
    return f, u, reasons


def main(argv):
    esbmc = Path(argv[1]) if len(argv) > 1 else DEFAULT_ESBMC
    poc = Path(argv[2]) if len(argv) > 2 else DEFAULT_POC
    if not esbmc.exists():
        sys.exit(f"no esbmc at {esbmc}")
    if not (poc / f"{CONTRACT}.solast").exists():
        sys.exit(f"missing {poc / (CONTRACT + '.solast')} -- generate it with "
                 f"solc --ast-compact-json")

    print(f"## Does naming the setter reach what the whole contract reaches?\n")
    print(f"binary : {esbmc}")
    print(f"input  : {poc / (CONTRACT + '.sol')}  (units: seed, noise, "
          f"{UNIT})\n")

    got = {}
    with tempfile.TemporaryDirectory() as wd:
        for focus, tx, why in CELLS:
            rc, out, claims = run(esbmc, poc, focus, tx, wd)
            i = INSTR.search(out)
            f, u, reasons = unit_tally(claims, UNIT)
            got[(focus, tx)] = (f, u, reasons, len(claims))
            print(f"focus={focus or '(none)'}  tx={tx}   # {why}")
            print(f"    exit {rc}   instrumented "
                  + (f"{i.group(1)} path(s) across {i.group(2)} unit(s)"
                     if i else "(line absent)")
                  + f"   claims in report: {len(claims)}")
            print(f"    {UNIT}'s own claims:  F={f}  U={u}"
                  + (f"   reasons={reasons}" if reasons else ""))
            print()

    ctrl = got.get(("withdraw", 2))
    cheap = got.get(("seed,withdraw", 2))
    ref = got.get((None, 2))
    print("=" * 74)
    if not ctrl or not cheap or not ref:
        print("  VERDICT: not computable -- a cell produced no report.")
        return 1
    print(f"  {UNIT}'s own paths, F/U:")
    print(f"     focus={UNIT:<14} tx=2   F={ctrl[0]} U={ctrl[1]}   (control)")
    print(f"     focus=seed,{UNIT:<9} tx=2   F={cheap[0]} U={cheap[1]}")
    print(f"     whole contract      tx=2   F={ref[0]} U={ref[1]}   (reference)")
    print()

    if ctrl[1] == 0:
        print("  ⛔ THE POSITIVE CONTROL DID NOT FIRE: with only `withdraw` "
              "focused, its paths are\n     already all F, so this input does "
              "not exercise the setter at all and every\n     other cell here "
              "is uninterpretable. A negative result whose control is dead is\n"
              "     not a negative result. Fix the input before reading "
              "anything below.")
        return 1

    if cheap[0] == ref[0] and cheap[1] == ref[1]:
        print("  ✅ VERDICT: naming the setter reaches EXACTLY what the whole "
              "contract reaches.\n"
              "     Multi-name --focus-function is the cheap version of the "
              "winning cell and is\n"
              "     worth wiring into the collector: focus={unit, its setters} "
              "+ tx>=2.")
        return 0
    if cheap[0] > ctrl[0]:
        print(f"  ⚠ VERDICT: naming the setter helps ({ctrl[0]} -> {cheap[0]}) "
              f"but does NOT match the\n     whole contract ({ref[0]}). "
              "Something else the whole contract offers is also\n     needed; "
              "report the gap rather than rounding it to either side.")
        return 1
    print(f"  ⛔ VERDICT: naming the setter buys nothing ({ctrl[0]} -> "
          f"{cheap[0]}) while the whole\n     contract gets {ref[0]}. "
          "Multi-name focus is NOT the cheap version of the winning\n"
          "     cell, and this measurement belongs next to the decision to cut "
          "it.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
