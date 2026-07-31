#!/usr/bin/env python3
"""How much DECIDED WORK does a run that dies throw away?

A path-coverage report is written only on a clean exit (bmc.cpp:1976, inside
report_coverage, which multi_property_check calls at bmc.cpp:3661-3670 -- AFTER
the job loop and INSIDE run_thread's try at bmc.cpp:2405). So a run that OOMs in
any job unwinds past the writer into the catch at bmc.cpp:2559 and emits nothing,
and a run killed by SIGALRM/SIGTERM emits nothing either (the rescue
emit_branch_coverage_on_timeout is gated on branch_cov_active, written only by
branch_coverage() at goto_coverage.cpp:2325).

"Nothing" is a report-level statement, not a work-level one. The LOG still
records every claim the run decided before it died. This counts them, so the
cost of the missing partial-report mechanism is a number rather than an
adjective -- in particular how many claims were REFUTED, i.e. how many
counterexamples (the deliverable) were computed and then discarded.

Counting rule, and why it is what it is: the per-claim verdict lines are emitted
by bmc.cpp:2884-2909 as `✓ PASSED: '<claim>'` / `✗ FAILED: '<claim>'`, one per
solved claim, with `Solving claim '<claim>' with solver <S>` (bmc.cpp:2866-2869)
printed before each. Under path coverage `is_cov_silent` is false for every path
claim (its property is "instrumented assertion", goto_coverage.cpp:7703), so
these lines appear for all of them. The claim text is the identity, so
DISTINCT claim texts are counted as well as raw line counts -- a claim re-solved
by a later k phase would otherwise be counted twice.

*** THERE ARE TWO DIFFERENT `✓ PASSED` LINES AND THEY MEAN OPPOSITE THINGS. ***

This is the whole reason this script exists rather than a line count, and
mis-reading it already produced a wrong figure: the aqua whole-contract OOM was
recorded as "5100+ claims solved", when the run solved 938.

  (A) SOLVE-TIME, bmc.cpp:2888 -- `✓ PASSED: '<claim_cstr>'`. The location is
      INSIDE the quotes (claim_cstr is "<msg> at <loc>"), so the line ends with
      the closing quote. This is a claim that reached the solver and came back
      UNSAT. It is decided work.

  (B) SYMEX-TIME, symex_main.cpp:82-85 -- `✓ PASSED: '<comment>' at <loc>`,
      printed by goto_symext::claim when do_simplify folds the claim to `true`
      under --multi-property. The location is OUTSIDE the quotes. This claim
      NEVER REACHED assertion(), has no job index, is never solved, and lands in
      the report as U with `not_solved_this_run: true` (bmc.cpp:1495-1498,
      u_reason `not-solved-this-run`). It is decided work's opposite: a claim
      the run silently dropped, while printing a green tick for it.

The two are told apart by whether the line ends with the closing quote, and both
are reported below. Counting (B) as solved inflates the "work lost" figure by
the exact number of claims the run never did.

Reads the whole log; no tail, no prefix. Writes nothing.

Usage: python3 loss_census.py <run.log> [...]
"""
import re
import sys
from collections import Counter
from pathlib import Path

ANSI = re.compile(r"\033\[[0-9;]*m")
# (A) solve-time: the line ENDS with the closing quote (location is inside it).
PASSED = re.compile(r"^✓ PASSED: '(.*)'\s*$")
FAILED = re.compile(r"^✗ FAILED: '(.*)'\s*$")
SOLVING = re.compile(r"^Solving claim '(.*)' with solver ")
UNKNOWN = re.compile(r"^\? UNKNOWN: '(.*)'\s*$")
# (B) symex-time: `'<comment>' at <loc>` -- the ` at <loc>` sits AFTER the
# closing quote. NOTE the location frequently renders EMPTY, so the line ends in
# a bare `at`; requiring a non-empty location here is what made an earlier
# version of this pattern silently match nothing and report 0 simplified claims
# on a log containing 5116 of them.
SIMPLIFIED = re.compile(r"^✓ PASSED: '(.*)' at(\s.*)?$")

# Lines that say the run did NOT finish cleanly, and the one that says it did.
DEATH = [
    "ERROR: Out of memory",
    "ERROR: SMT solver failed",
    "terminate called after throwing an instance of 'std::bad_alloc'",
    "std::bad_alloc",
    "ERROR: Timed out",
    "ERROR: Terminated",
    "ERROR: Interrupted",
]
REPORT_WRITTEN = "Coverage report written to cov-report.json"


def census(path):
    text = Path(path).read_text(errors="replace")
    lines = [ANSI.sub("", ln) for ln in text.splitlines()]

    solving, passed, failed, unknown = Counter(), Counter(), Counter(), Counter()
    simplified = Counter()
    for ln in lines:
        s = ln.strip()
        # SIMPLIFIED is tried FIRST: its lines also start `✓ PASSED: '`, and
        # letting the solve-time pattern see them first is exactly the
        # misreading this script exists to prevent.
        m = SIMPLIFIED.match(s)
        if m:
            simplified[m.group(1)] += 1
            continue
        for rx, bag in ((SOLVING, solving), (PASSED, passed),
                        (FAILED, failed), (UNKNOWN, unknown)):
            m = rx.match(s)
            if m:
                bag[m.group(1)] += 1
                break

    deaths = [(i, ln) for i, ln in enumerate(lines, 1)
              if any(d in ln for d in DEATH)]
    wrote = [i for i, ln in enumerate(lines, 1) if REPORT_WRITTEN in ln]

    print(f"\n# {path}")
    print(f"  log lines                     {len(lines)}")
    print(f"  'Solving claim' lines         {sum(solving.values())}"
          f"   ({len(solving)} distinct claim(s))")
    print(f"  PASSED (claim held)           {sum(passed.values())}"
          f"   ({len(passed)} distinct)")
    print(f"  FAILED (claim REFUTED = a     {sum(failed.values())}"
          f"   ({len(failed)} distinct)")
    print(f"          witness in hand)")
    if sum(unknown.values()):
        print(f"  UNKNOWN (inductive step)      {sum(unknown.values())}"
              f"   ({len(unknown)} distinct)")
    decided = sum(passed.values()) + sum(failed.values()) + sum(unknown.values())
    print(f"  --> claims DECIDED            {decided}")
    print(f"  symex-time '✓ PASSED' (NOT    {sum(simplified.values())}"
          f"   ({len(simplified)} distinct)")
    print(f"      solved: simplified away,")
    print(f"      reported U/not-solved-this-run)")
    print(f"  cov-report.json written?      "
          f"{'YES (line ' + str(wrote[0]) + ')' if wrote else 'NO'}")
    if not wrote:
        print(f"  --> ALL {decided} decided claim(s) were discarded, "
              f"{sum(failed.values())} of them witnesses")
    if deaths:
        print("  termination lines:")
        for i, ln in deaths:
            print(f"    line {i:>7}  {ln.strip()}")
    if failed:
        print("  REFUTED claims (the witnesses at stake):")
        for k in sorted(failed):
            print(f"    {k}")
    return 0


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <run.log> [...]")
    for p in argv[1:]:
        census(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
