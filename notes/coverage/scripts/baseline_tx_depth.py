#!/usr/bin/env python3
"""Is the LOCKED branch-coverage baseline really at ONE transaction, and does
k-induction hand it an entry state that no constructor -> tx sequence reaches?

WHY THIS EXISTS. `branch_gate.py` puts us at 1/6. Both sides of that gate are
supposed to be commensurable, and `pathcov_collect.py`'s docstring states the
reason our side is pinned at `--solidity-max-tx 1`:

    "It is also what the locked branch-coverage dataset actually ran at --
     branch coverage IS in `unbounded_modes`, so it got bound 0, so one
     transaction. The two sides are at the same transaction depth."

That sentence is an INFERENCE from an option table, not a measurement, and two
things make it worth checking before anything is changed on the strength of it:

  * `collect.py` (LOCKED) never passes `--solidity-max-tx` at all -- the depth
    is whatever the mode's default resolves to, which nothing here has observed;
  * every baseline command carries `--k-induction --unlimited-k-steps`, which our
    own notes already record as changing what a coverage pass sees
    (INVOCATION_DECISIONS row 3: k-induction's havoc+assume preambles trip the
    path-coverage pass's named-obstacle criterion). k-induction's inductive step
    starts from a HAVOC'd state. `EXECUTION_PLAN.md` section 3.5 records that a
    havoc'd entry state is exactly the false-positive posture this project bans
    for its own artefact, because a witness resting on it becomes a RED test.

If the baseline's "reached" includes arms proven reachable only from a havoc'd
state, then the gate's bar is not the same kind of quantity as our numerator, and
1/6 is not a comparison. That has to be settled by measurement, and settled
BEFORE anyone touches `pathcov_collect.py`'s tx value -- raising a bound because
a number is low is the move this project has already been caught making.

THE CONTRACT. `poc/Tiny.sol`, ten lines, chosen because its ground truth is not
in dispute:

    bal starts at 0; `deposit` is the only writer that can raise it;
    withdraw:  require(amt > 0);          <- line 39
               require(bal >= amt);       <- line 40
               if (amt > 100) { ... }     <- line 41   <-- THE OBSERVABLE

Line 41 is reachable ONLY after `bal >= amt > 0`, i.e. only after a preceding
`deposit`. Under a genuine one-transaction-per-entry model with a focused
dispatcher, no execution reaches it. Complete-path coverage agrees: `Tiny`
whole-contract at tx=1 leaves exactly those two arms as `bounded-holds` and
needs tx=2 to witness them (INVOCATION_DECISIONS, rows 1 and 2).

So: DOES THE BASELINE MARK LINE 41 REACHED, AND UNDER WHICH FLAG?

THE POSITIVE CONTROL IS NOT DECORATION. Line 39 (`require(amt > 0)`) is
reachable in every model, including the most restrictive one. A cell that does
not reach line 39 did not measure anything, and its silence about line 41 is not
evidence of absence -- this project has already recorded three discriminators
that could never fire. Any cell missing line 39 is reported VOID, never as a 0.

CELLS. Each is one esbmc run, serial, `--memlimit 4g`, killed at 120 s.

  A  baseline verbatim          k-induction, unlimited-k, whole-unit, no tx flag
  B  A minus the strategy       plain BMC, everything else identical
  C  A + --solidity-max-tx 1    does naming the bound change A?
  D  B + --solidity-max-tx 1    does naming the bound change B?
  E  B + --solidity-max-tx 2    the depth our own decision table now says to use
  F  baseline Pair-2 shape      A + --focus-function withdraw  (this is the
                                command `collect.py:470-473` actually issues,
                                and Pair 2 is the row the gate compares against)

READINGS, FIXED BEFORE THE RUN:

  1  A reaches 41 and B does not
       -> K-INDUCTION IS THE MECHANISM. The baseline's reach includes an arm
          that is unreachable from the constructor in the transaction depth it
          runs at; it is reachable only from a havoc'd inductive-step state.
          The gate's bar and our numerator are then different quantities, and
          the honest fix is to say so in the paper, NOT to raise our tx.
  2  A and B both reach 41
       -> the baseline is running MORE THAN ONE TRANSACTION regardless of
          strategy. The docstring's "two sides are at the same transaction
          depth" is false, our tx=1 is the unfair half, and raising it is
          justified BY COMMENSURABILITY rather than by the score. Cells D/E say
          what depth restores parity.
  3  neither A nor B reaches 41
       -> the docstring is right, both sides are at one transaction, and the
          1/6 gap is NOT about entry state at all. Then the corpus's tx=1 is
          correct and the gap has to be attributed somewhere else entirely --
          and EscrowDst's baseline 18/18 must be re-read, because on that
          reading it contains no state-guarded arm either.
  4  F (focused Pair-2) reaches 41 while the unfocused cells do not
       -> the strongest form of reading 1: under focus NOTHING can call
          `deposit`, so a reached line 41 can only have come from an entry state
          no call sequence produces.

Usage:  python3 baseline_tx_depth.py [--timeout S]
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

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
POC = REPO / "notes/coverage/poc"
SOL = POC / "Tiny.sol"
AST = POC / "Tiny.solast"

# The baseline's own flag list, copied from collect.py (LOCKED) rather than
# retyped from memory: ESBMC_FLAGS_BASE + the Pair-2 inner timeout.
BASE = ["--branch-coverage-claims", "--memlimit", "4g", "--no-assertions"]
STRATEGY = ["--k-induction", "--unlimited-k-steps"]

OBSERVABLE = 41          # `if (amt > 100)` -- needs a preceding deposit
CONTROL = 39             # `require(amt > 0)` -- reachable in every model

CELLS = [
    ("A", "baseline verbatim (k-induction, no tx flag)", STRATEGY, []),
    ("B", "plain BMC, no tx flag", [], []),
    ("C", "baseline + --solidity-max-tx 1", STRATEGY, ["--solidity-max-tx", "1"]),
    ("D", "plain BMC + --solidity-max-tx 1", [], ["--solidity-max-tx", "1"]),
    ("E", "plain BMC + --solidity-max-tx 2", [], ["--solidity-max-tx", "2"]),
    ("F", "baseline Pair-2 shape (+ --focus-function withdraw)",
     STRATEGY, ["--focus-function", "withdraw"]),
]


def run_one(extra_strategy, extra, union_path, workdir, timeout):
    """One serial esbmc run. start_new_session + killpg so a timeout does not
    leave an orphaned esbmc holding 4 GiB -- that has happened here before."""
    cmd = [str(ESBMC), str(AST), "--sol", str(SOL),
           "--contract", "Tiny", "--coverage-whole-unit",
           "--coverage-covered-set", str(union_path)] + BASE \
        + list(extra_strategy) + list(extra)
    if extra_strategy:                      # the baseline pairs a --timeout with
        cmd += ["--timeout", "60"]          # its strategy; keep that coupling
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=str(workdir), start_new_session=True)
    try:
        out = p.communicate(timeout=timeout)[0]
        rc = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out = (p.communicate()[0] or "") + "\n[killed by outer timeout]"
        rc = None
    return cmd, rc, out, round(time.time() - t0, 2)


def union_lines(union_path):
    """Flat-line numbers the covered-set records as reached. Same extraction
    `collect.py:parse_union_json` uses, so this reads the baseline's own
    currency rather than a second opinion about it."""
    if not union_path.exists():
        return None
    try:
        d = json.loads(union_path.read_text())
    except ValueError:
        return None
    out = set()
    for c in d.get("covered", []):
        m = re.search(r"line\s+(\d+)", c.get("loc", ""))
        if m:
            out.add(int(m.group(1)))
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv[1:])
    for p in (ESBMC, SOL, AST):
        if not p.exists():
            sys.exit(f"missing {p}")

    print("## Does the locked baseline reach a line only a PRECEDING CALL "
          "can make reachable?\n")
    print(f"binary     : {ESBMC}  (mtime {int(ESBMC.stat().st_mtime)})")
    print(f"contract   : {SOL}")
    print(f"observable : line {OBSERVABLE}  `if (amt > 100)`   "
          f"-- needs bal >= amt > 0, i.e. a prior deposit")
    print(f"control    : line {CONTROL}  `require(amt > 0)`  "
          f"-- reachable in every model; its absence VOIDS the cell\n")

    seen = {}
    with tempfile.TemporaryDirectory() as wd:
        for key, desc, strat, extra in CELLS:
            union = Path(wd) / f"union_{key}.json"
            if union.exists():
                union.unlink()
            cmd, rc, out, wall = run_one(strat, extra, union, wd, a.timeout)
            lines = union_lines(union)
            br = re.search(r"^Branches\s*:\s*(\d+)\s*$", out, re.M)
            rd = re.search(r"^Reached\s*:\s*(\d+)\s*$", out, re.M)
            (Path(wd) / f"cell_{key}.log").write_text(out)

            if lines is None:
                verdict = "VOID -- no covered-set written"
            elif CONTROL not in lines:
                verdict = f"VOID -- control line {CONTROL} not reached"
            else:
                verdict = ("REACHES line %d" % OBSERVABLE) if OBSERVABLE in lines \
                    else ("does NOT reach line %d" % OBSERVABLE)
            seen[key] = (verdict, lines)

            print(f"--- cell {key}: {desc}")
            print(f"    rc={rc}  wall={wall}s  "
                  + (f"Branches {br.group(1)} / Reached {rd.group(1)}"
                     if br and rd else "no Branches/Reached line"))
            print(f"    covered decision lines: "
                  + (", ".join(str(x) for x in sorted(lines)) if lines else "-"))
            print(f"    => {verdict}\n")

    def reaches(k):
        return seen.get(k, ("", None))[0].startswith("REACHES")

    def void(k):
        return seen.get(k, ("VOID", None))[0].startswith("VOID")

    print("=" * 74)
    if void("A") or void("B"):
        print("  VOID: cell A or B did not reach the control line, so nothing "
              "about the\n  observable can be read from this run. Fix the cell "
              "before drawing any\n  conclusion -- an unfired discriminator is "
              "not a negative result.")
        return 1
    if reaches("A") and not reaches("B"):
        print("  READING 1 -- K-INDUCTION IS THE MECHANISM.\n"
              "  The baseline reaches an arm that plain BMC at the same depth "
              "cannot, so its\n  reach includes states no constructor -> "
              "transaction sequence produces. The\n  gate's bar and our "
              "numerator are then DIFFERENT QUANTITIES; the fix is to say\n"
              "  so, not to raise our --solidity-max-tx.")
        rc = 0
    elif reaches("A") and reaches("B"):
        print("  READING 2 -- THE BASELINE IS DEEPER THAN ONE TRANSACTION.\n"
              "  Both strategies reach it, so the depth and not the strategy is "
              "doing the work.\n  `pathcov_collect.py`'s \"the two sides are at "
              "the same transaction depth\" is\n  FALSE, and raising our bound "
              "is justified by COMMENSURABILITY rather than by\n  the score. "
              "Cells D and E say which depth restores parity.")
        rc = 0
    else:
        print("  READING 3 -- BOTH SIDES REALLY ARE AT ONE TRANSACTION.\n"
              "  The docstring is right and our tx=1 is correct. Then the 1/6 "
              "gap is NOT about\n  entry state, and EscrowDst's baseline 18/18 "
              "has to be re-read: on this reading\n  it contains no "
              "state-guarded arm either.")
        rc = 0
    if reaches("F") and not reaches("A"):
        print("\n  AND READING 4 FIRES: the FOCUSED baseline cell reaches it "
              "while the unfocused\n  one does not. Under focus nothing can "
              "call `deposit`, so that reach cannot\n  have come from any call "
              "sequence at all.")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
