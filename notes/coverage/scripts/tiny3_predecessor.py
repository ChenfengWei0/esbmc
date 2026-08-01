#!/usr/bin/env python3
"""Tiny/Tiny2/Tiny3: what exactly blocks a state-guarded path — the STATE, or a
PRECEDING CALL, or the path identity itself?

WHY THIS EXISTS. `notes/coverage/poc/Tiny3.sol` was written to separate two
explanations and, per its own header, HAS NEVER BEEN RUN. It matters right now
because the corpus collector uses single-name `--focus-function` everywhere and
the obvious next move is to wire the (verified working) multi-name form into it.
That move is only worth making if having several functions available actually
unlocks state-guarded paths. The measurement recorded in Tiny3's header says it
may not:

    Tiny  (bal starts 0), --focus-function withdraw : 5 paths, 3 F, 2 bounded-holds
    Tiny  whole contract  (the dispatcher MAY call deposit() then withdraw()
                           inside one transaction)   : 8 paths, 6 F,
                           THE SAME 2 bounded-holds
    Tiny2 (constructor sets bal = 500), focus withdraw : 5 paths, 5 F, 100%

i.e. the whole-contract run is the MAXIMAL multi-function focus and it did not
witness the guarded paths, while simply having the state at entry did.

WHAT TINY3 ADDS. `seed()` writes the balance with NO user-level decision in its
body -- no require, no branch. Its only decision is the synthesised ABI
non-payable value gate that every unit has. So it is a predecessor that
contributes (almost) nothing to the accumulator.

READINGS, from Tiny3's own header, fixed before this script ran:

  * withdraw's guarded paths become F in the whole-contract run
        => the damage comes from the PREDECESSOR'S DECISIONS, i.e. accumulator
           pollution, and the fix is to scope the accumulator to the unit call;
  * they stay bounded-holds
        => even a decision-free predecessor breaks it, so the mechanism is
           stronger: the unit must be the FIRST call of the transaction, or the
           state is read at transaction entry rather than live.

Those need different fixes, which is the whole point of running it.

⚠ WHAT THIS CANNOT DECIDE. Three toy contracts. Whatever comes out is a
statement about this shape, and the sentence has to carry their names until a
real benchmark repeats it. This project has had the same generalisation narrowed
by a second and third sample twice already.

Usage: python3 tiny3_predecessor.py [<esbmc>] [<poc-dir>]
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ESBMC = HERE.parent.parent.parent / "build" / "src" / "esbmc" / "esbmc"
DEFAULT_POC = HERE.parent / "poc"

INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")
STATUS = re.compile(r"Path Status: F (\d+), I (\d+), U (\d+)")
UREASON = re.compile(r"U Reasons: (.+)$", re.M)
REACHED = re.compile(r"Reached\s*:\s*(\d+)")

# (contract, focus-or-None, why this cell is here)
CASES = [
    ("Tiny", "withdraw", "baseline: bal starts 0, only deposit can raise it"),
    ("Tiny", None, "whole contract = MAXIMAL multi-function focus"),
    ("Tiny2", "withdraw", "control: constructor puts the state in place"),
    ("Tiny2", None, "control, whole contract"),
    ("Tiny3", "withdraw", "seed() exists but is not called"),
    ("Tiny3", None, "THE DISCRIMINATOR: decision-free predecessor available"),
]


def run(esbmc, poc, contract, focus):
    cmd = [
        str(esbmc),
        str(poc / f"{contract}.solast"),
        "--sol", str(poc / f"{contract}.sol"),
        "--solidity-path-coverage",
        "--solidity-max-tx", "1",
        "--memlimit", "4g",
        "--contract", contract,
    ]
    if focus:
        cmd += ["--focus-function", focus]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def main(argv):
    esbmc = Path(argv[1]) if len(argv) > 1 else DEFAULT_ESBMC
    poc = Path(argv[2]) if len(argv) > 2 else DEFAULT_POC
    if not esbmc.exists():
        sys.exit(f"no esbmc at {esbmc}")

    print("## Does a PRECEDING CALL, or its DECISIONS, block the guarded paths?\n")
    print(f"binary : {esbmc}\n")
    rows = []
    for contract, focus, why in CASES:
        sol = poc / f"{contract}.sol"
        ast = poc / f"{contract}.solast"
        if not sol.exists() or not ast.exists():
            print(f"{contract} focus={focus}: MISSING INPUT ({sol} / {ast}) "
                  f"-- contributes nothing, not zero")
            rows.append((contract, focus, why, None))
            continue
        rc, out = run(esbmc, poc, contract, focus)
        i = INSTR.search(out)
        s = STATUS.search(out)
        r = REACHED.search(out)
        u = UREASON.search(out)
        rec = {
            "rc": rc,
            "paths": int(i.group(1)) if i else None,
            "units": int(i.group(2)) if i else None,
            "F": int(s.group(1)) if s else None,
            "I": int(s.group(2)) if s else None,
            "U": int(s.group(3)) if s else None,
            "reached": int(r.group(1)) if r else None,
            "ureasons": u.group(1).strip() if u else None,
        }
        rows.append((contract, focus, why, rec))
        print(f"{contract}  --focus-function {focus or '(none)'}   # {why}")
        print(f"    exit {rc}   paths {rec['paths']} across {rec['units']} unit(s)"
              f"   Reached {rec['reached']}")
        print(f"    F {rec['F']}  I {rec['I']}  U {rec['U']}")
        print(f"    U Reasons: {rec['ureasons']}")
        print()

    print("=" * 74)
    print("  contract  focus       paths   F    U    U reasons")
    for contract, focus, _why, rec in rows:
        if rec is None:
            print(f"  {contract:<9} {str(focus or '(none)'):<11} "
                  f"MISSING INPUT")
            continue
        print(f"  {contract:<9} {str(focus or '(none)'):<11} "
              f"{str(rec['paths']):>5} {str(rec['F']):>4} {str(rec['U']):>4}   "
              f"{rec['ureasons']}")

    def get(c, f):
        for cc, ff, _w, rr in rows:
            if cc == c and ff == f:
                return rr
        return None

    t3f, t3w = get("Tiny3", "withdraw"), get("Tiny3", None)
    print()
    if not t3f or not t3w or t3f["U"] is None or t3w["U"] is None:
        print("  VERDICT: not computable -- a Tiny3 run did not report a Path "
              "Status line. That is the finding; do not substitute a guess.")
        return 1
    # withdraw's own guarded paths: compare the U count of the focused run with
    # the whole-contract run, since the focused run's U IS withdraw's U.
    if t3w["U"] < t3f["U"]:
        print(f"  READING 1: Tiny3 whole-contract U ({t3w['U']}) is LOWER than "
              f"focused ({t3f['U']}).\n"
              "     A decision-free predecessor DOES unlock paths => what broke "
              "the Tiny run was\n"
              "     the PREDECESSOR'S DECISIONS polluting the accumulator, and "
              "the fix is to scope\n"
              "     the accumulator to the unit call. Multi-function focus is "
              "then worth wiring.")
    elif t3w["U"] == t3f["U"]:
        print(f"  READING 2: Tiny3 whole-contract U ({t3w['U']}) EQUALS focused "
              f"({t3f['U']}).\n"
              "     Even a decision-free predecessor does not unlock them => "
              "the obstacle is not\n"
              "     accumulator pollution. The unit must be the FIRST call of "
              "the transaction, or\n"
              "     the state is read at entry rather than live. **Multi-"
              "function focus would buy\n"
              "     nothing here**, and wiring it into the collector should NOT "
              "be done on this\n"
              "     evidence. This is the outcome that is not a win.")
    else:
        print(f"  READING 3: whole-contract U ({t3w['U']}) is HIGHER than "
              f"focused ({t3f['U']}).\n"
              "     More paths are enumerated whole-contract, so the U counts "
              "are not directly\n"
              "     comparable -- compare per-path, not per-total, before "
              "concluding anything.")
    print("\n  ⚠ Three toy contracts. Any sentence built on this carries their "
          "names until a\n    real benchmark repeats it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
