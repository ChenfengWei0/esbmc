#!/usr/bin/env python3
"""Print the STRUCTURAL lines of every run log in a matrix directory.

A path-coverage run log is 200 KB-2 MB of counterexample state dumps and
per-claim solver chatter. The lines that carry the run's structure -- what the
pass instrumented, which bound it used, which k phases ran, which warnings
fired, and the final coverage census -- are a few dozen per file. This reads
each log IN FULL and prints exactly those, in file order, with line numbers, so
the shape of a run can be compared across cells without guessing at a tail.

Every printed line is quoted verbatim; nothing is reformatted. The count of
lines NOT printed is reported per file so the reader knows what was left out.

usage: unwind_vs_strategy_phases.py <matrix-dir> [cell ...]
"""
import sys
from pathlib import Path

# Prefixes/substrings that mark a structural line. Deliberately generous:
# a false positive costs one extra line, a false negative hides a phase.
MARKERS = (
    "--solidity-path-coverage",
    "WARNING:",
    "ERROR:",
    "Checking base case",
    "Checking forward condition",
    "Checking inductive step",
    "Bug found",
    "Solution found",
    "VERIFICATION",
    "[Multi-property]",
    "[Coverage]",
    "Complete Paths",
    "Reached :",
    "Path Coverage:",
    "Path Exits:",
    "Path Status:",
    "U Reasons:",
    "Properties:",
    "Coverage report written",
    "Exclusion is per UNIT",
    "(a) ",
    "(b) ",
    "Violated property",
    "Not unwinding loop",
    "Unable to prove",
    "Adding Solidity complete-path coverage",
)


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <matrix-dir> [cell ...]")
    root = Path(argv[1])
    wanted = set(argv[2:])
    cells = sorted(d for d in root.iterdir() if d.is_dir())
    for c in cells:
        if wanted and c.name not in wanted:
            continue
        log = c / "run.log"
        print(f"\n===== {c.name} =====")
        if not log.exists():
            print("  (no run.log)")
            continue
        lines = log.read_text(errors="replace").splitlines()
        shown = 0
        for i, ln in enumerate(lines, 1):
            if any(m in ln for m in MARKERS):
                print(f"{i:>7}  {ln}")
                shown += 1
        print(f"  [{shown} structural line(s) of {len(lines)}; "
              f"{len(lines) - shown} not printed]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
