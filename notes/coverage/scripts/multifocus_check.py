#!/usr/bin/env python3
"""Does `--focus-function a,b` REALLY take more than one name?

WHY THIS EXISTS. `options.cpp:140-152` documents the flag as accepting
`name[,name...]`, comma- or space-separated, and claims a name the contract does
not have "fails the conversion and says which one". That is the HELP TEXT. In
this repository help and source have disagreed at least four times, three of
them in ways that changed an experiment's design (`--solidity-max-tx 0` is the
SHALLOWEST setting under coverage; path coverage is NOT in `unbounded_modes`;
the locked branch-coverage dataset is also one transaction). And the project has
twice shipped a function that was written and never called. So the flag is not
credited until it is seen to fire.

THE FIRST VERSION OF THIS SCRIPT WAS USELESS AND IS RECORDED HERE RATHER THAN
QUIETLY REPLACED. It used `Tiny2`, which has exactly TWO public functions. On a
two-function contract "focus both" and "ignore the flag and fall back to the
whole contract" produce identical output, so the one outcome that matters —
a silently dropped second name — was invisible by construction. A discriminator
whose two outcomes coincide is not a discriminator.

`F01_MultiFocus.sol` has THREE units with three different path counts, so every
subset has its own total and `one,two` is separable from the whole contract by
the number alone, whether or not the `narrowed INSTRUMENTATION` line prints.

WHAT IS PINNED:

  one            N1, narrowed to 1 unit, 2 others
  two            N2 > N1
  three          N3 > N2
  one,two        MUST be N1 + N2 and MUST be < the unfocused total,
                 and MUST print "narrowed ... to 2 unit(s); 1 other"
  one two        space form: same, or explicitly reported as unsupported
  (no focus)     N1 + N2 + N3, no narrowing line
  one,nosuchfn   MUST fail AND name `nosuchfn`

The load-bearing row is `one,two`. If it equals the unfocused total, the second
name was not parsed and the run silently fell back to the whole contract — the
exact failure the help text promises cannot happen. The bad-name row is the
positive control for the validation half: without it, a run that quietly dropped
an unknown name would look the same as one that validated correctly.

Usage:  python3 multifocus_check.py [<esbmc>] [<poc-dir>]
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ESBMC = HERE.parent.parent.parent / "build" / "src" / "esbmc" / "esbmc"
DEFAULT_POC = HERE.parent / "poc"

NARROW = re.compile(r"narrowed INSTRUMENTATION to (\d+) unit\(s\); (\d+) other")
INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")

CASES = [
    (None, "no --focus-function at all (the whole contract)"),
    ("one", "single name"),
    ("two", "single name"),
    ("three", "single name"),
    ("one,two", "TWO names, comma  <-- the load-bearing row"),
    ("one two", "TWO names, space"),
    ("one,nosuchfn", "one good name + one that does not exist"),
]


def run(esbmc, poc, focus):
    cmd = [
        str(esbmc),
        str(poc / "F01_MultiFocus.solast"),
        "--sol", str(poc / "F01_MultiFocus.sol"),
        "--solidity-path-coverage",
        "--solidity-max-tx", "1",
        "--memlimit", "4g",
        "--contract", "F01_MultiFocus",
    ]
    if focus is not None:
        cmd += ["--focus-function", focus]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def main(argv):
    esbmc = Path(argv[1]) if len(argv) > 1 else DEFAULT_ESBMC
    poc = Path(argv[2]) if len(argv) > 2 else DEFAULT_POC
    if not esbmc.exists():
        sys.exit(f"no esbmc at {esbmc}")

    print("## Does --focus-function take more than one name?\n")
    print(f"binary : {esbmc}")
    print(f"input  : {poc / 'F01_MultiFocus.sol'} "
          f"(public: one, two, three)\n")

    paths = {}
    for focus, label in CASES:
        rc, out = run(esbmc, poc, focus)
        m, i = NARROW.search(out), INSTR.search(out)
        units = int(m.group(1)) if m else None
        others = int(m.group(2)) if m else None
        npath = int(i.group(1)) if i else None
        nunit = int(i.group(2)) if i else None
        paths[focus] = npath

        shown = "(none)" if focus is None else f"'{focus}'"
        print(f"--focus-function {shown}   ({label})")
        print(f"    exit                {rc}")
        print(f"    narrowing line      "
              + (f"{units} unit(s) kept, {others} other(s) dropped"
                 if m else "ABSENT"))
        print(f"    instrumented        "
              + (f"{npath} path(s) across {nunit} unit(s)"
                 if i else "(line absent)"))
        if focus == "one,nosuchfn":
            if rc != 0 and "nosuchfn" in out:
                print("    -> OK: refused AND named the bad name")
            elif rc != 0:
                print("    -> refused, but did NOT print `nosuchfn`: the error "
                      "does not say WHICH name, which on a long list is a "
                      "puzzle rather than a diagnostic")
            else:
                print("    -> BROKEN: an unknown name did NOT fail the "
                      "conversion, so a typo silently narrows the run and the "
                      "reported denominator looks deliberate")
        print()

    n1, n2, n3 = paths.get("one"), paths.get("two"), paths.get("three")
    whole, pair, spaced = paths.get(None), paths.get("one,two"), paths.get("one two")
    print("=" * 72)
    print(f"  one={n1}  two={n2}  three={n3}   whole contract={whole}")
    print(f"  one,two={pair}   one two={spaced}")
    if None in (n1, n2, whole, pair):
        print("\n  VERDICT: cannot be computed — a run did not print its "
              "instrumented-path line. That is itself the finding; do not "
              "substitute a guess.")
        return 1
    print()
    if pair == whole:
        print("  ⛔ VERDICT: `one,two` gives the SAME count as no focus at all "
              "=> the second\n     name was not parsed and the run fell back "
              "to the whole contract, SILENTLY.")
        return 1
    if pair == n1 + n2:
        print(f"  ✅ VERDICT: `one,two` = {pair} = {n1} + {n2}, and strictly "
              f"less than the whole\n     contract's {whole} => both names were "
              "parsed and exactly those two units\n     were instrumented.")
        if spaced == pair:
            print("     The space-separated form gives the same count.")
        else:
            print(f"     ⚠ The space form gives {spaced}, NOT {pair} — the help "
                  "text's claim that\n     names may be space-separated does "
                  "not hold.")
        return 0
    print(f"  ⚠ VERDICT: `one,two` = {pair}, which is neither {n1 + n2} "
          f"(the sum) nor {whole}\n     (the whole contract). Something else "
          "is going on; do not round it to either.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
