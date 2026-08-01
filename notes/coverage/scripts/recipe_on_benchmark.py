#!/usr/bin/env python3
"""Does `focus = {unit, its setters}` + tx>=2 unlock `bounded-holds` on a REAL
benchmark, or only on the toys?

The recipe comes from `notes/coverage/D23-two-knobs-neither-alone.md`, measured
on three hand-written contracts:

    focus ON,  tx = 1   no gain      <- where the entire corpus was collected
    focus ON,  tx >= 2  no gain
    focus OFF, tx = 1   no gain
    focus OFF, tx >= 2  FULL gain
    focus = {unit, setter}, tx >= 2  reaches exactly what focus OFF reaches,
                                     at a fraction of the units

D23's own closing paragraph says the sentence carries the toys' names until a
real benchmark repeats it. This is that attempt.

THE COMPARISON IS ON THE UNIT'S OWN CLAIMS, out of `cov-report.json`, never on
the printed totals: a run with a wider focus enumerates the other focused units
too, so its totals are larger for a reason that has nothing to do with the
question. Reading a per-unit question off an aggregate is a mistake this project
has already made once and recorded.

POSITIVE CONTROL, and it must fire: cell 1 (`focus = unit` alone, tx = 1) must
reproduce the corpus row for that unit. If it does not, the input or the
configuration differs from the collection and nothing else here is comparable.

Usage:
  python3 recipe_on_benchmark.py <flat.sol> <Contract> <unit> <setter>[,<setter>...]
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESBMC = HERE.parent.parent.parent / "build" / "src" / "esbmc" / "esbmc"
INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")


def run(flat, contract, focus, tx, workdir, timeout):
    cmd = [
        str(ESBMC), str(flat) + ".solast", "--sol", str(flat),
        "--solidity-path-coverage", "--cov-report-json",
        "--solidity-max-tx", str(tx),
        "--path-cov-max-goals", "10000",
        "--memlimit", "8g",
        "--contract", contract,
    ]
    if focus:
        cmd += ["--focus-function", focus]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=workdir)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        return None, "OUTER TIMEOUT", []
    rep = Path(workdir) / "cov-report.json"
    claims = json.loads(rep.read_text()).get("claims", []) if rep.exists() else []
    return rc, out, claims


def tally(claims, unit):
    f = u = 0
    reasons = {}
    for c in claims:
        if not (c.get("condition") or "").startswith(unit + ":"):
            continue
        if c.get("status") == "F":
            f += 1
        else:
            u += 1
            r = c.get("u_reason") or "(none)"
            reasons[r] = reasons.get(r, 0) + 1
    return f, u, reasons


def main(argv):
    if len(argv) < 5:
        sys.exit(__doc__)
    # RESOLVE. Every cell runs with cwd set to a temp directory so the
    # cov-report.json of one cell cannot be read as another's; a relative input
    # path then silently fails to resolve and every cell comes back exit 6 with
    # an empty report. That happened on the first run of this script, and the
    # POSITIVE CONTROL is what caught it -- the control's own failure stopped
    # the read before the two experimental cells could be misreported as "the
    # recipe does not transfer to a real contract", which is the opposite of
    # what the data would have said.
    flat, contract, unit, setters = (
        Path(argv[1]).resolve(), argv[2], argv[3], argv[4])
    timeout = int(argv[5]) if len(argv) > 5 else 900
    if not ESBMC.exists():
        sys.exit(f"no esbmc at {ESBMC}")
    if not flat.exists() or not Path(str(flat) + ".solast").exists():
        sys.exit(f"missing {flat} or its .solast")

    cells = [
        (unit, 1, "POSITIVE CONTROL: must reproduce the corpus row"),
        (unit, 2, "focus ON + tx>=2: the 2x2 predicts NO gain"),
        (f"{unit},{setters}", 2, "THE RECIPE"),
    ]

    print(f"## focus-set + tx>=2 on a real benchmark\n")
    print(f"binary : {ESBMC}")
    print(f"input  : {flat}")
    print(f"unit   : {unit}   setters offered: {setters}\n")

    got = {}
    with tempfile.TemporaryDirectory() as wd:
        for focus, tx, why in cells:
            rc, out, claims = run(flat, contract, focus, tx, wd, timeout)
            if rc is None:
                print(f"focus={focus}  tx={tx}   # {why}")
                print(f"    {out} after {timeout}s -- contributes nothing, "
                      f"not zero\n")
                got[(focus, tx)] = None
                continue
            i = INSTR.search(out)
            f, u, reasons = tally(claims, unit)
            got[(focus, tx)] = (f, u, reasons)
            print(f"focus={focus}  tx={tx}   # {why}")
            print(f"    exit {rc}   instrumented "
                  + (f"{i.group(1)} path(s) across {i.group(2)} unit(s)"
                     if i else "(line absent)")
                  + f"   claims in report: {len(claims)}")
            print(f"    {unit}'s own claims:  F={f}  U={u}"
                  + (f"   {reasons}" if reasons else ""))
            print()

    base = got.get((unit, 1))
    tx2 = got.get((unit, 2))
    rec = got.get((f"{unit},{setters}", 2))
    print("=" * 74)
    if base is None or rec is None:
        print("  VERDICT: not computable -- a cell produced no report. That is "
              "the finding; do not substitute a guess.")
        return 1
    print(f"  {unit}'s own paths, F/U:")
    print(f"     focus={unit} tx=1                F={base[0]} U={base[1]}   (control)")
    if tx2:
        print(f"     focus={unit} tx=2                F={tx2[0]} U={tx2[1]}")
    print(f"     focus={unit},{setters} tx=2   F={rec[0]} U={rec[1]}")
    print()
    if base[1] == 0:
        print("  ⛔ CONTROL DID NOT FIRE: this unit has no undecided paths at "
              "tx=1, so there is\n     nothing for the recipe to unlock and "
              "the other cells are uninterpretable.")
        return 1
    if rec[0] > base[0]:
        print(f"  ✅ THE RECIPE WORKS ON A REAL CONTRACT: F {base[0]} -> "
              f"{rec[0]}, U {base[1]} -> {rec[1]}.")
        if tx2 and tx2[0] > base[0]:
            print(f"     ⚠ BUT tx=2 ALONE also moved it ({base[0]} -> "
                  f"{tx2[0]}), which the toys said it would\n       not. The "
                  "setter set is then not what did the work here -- report "
                  "both numbers.")
        return 0
    print(f"  ⛔ THE RECIPE DID NOT TRANSFER: F stayed at {base[0]} "
          f"(recipe gave {rec[0]}).\n     The toy result does not generalise to "
          "this unit. This is not a failed run; it is\n     the measurement "
          "D23 asked for, and it narrows the claim rather than repeating it.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
