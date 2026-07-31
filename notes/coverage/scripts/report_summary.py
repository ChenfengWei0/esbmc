#!/usr/bin/env python3
"""Print a cov-report.json's `summary` block, and the per-status claim census.

A path-coverage report is ~1.6 MB, almost all of it counterexample payload. The
question asked of it is nearly always one of four numbers -- F, U, the U-reason
histogram, and how many F claims carry inputs -- and reading the file to find
them is not viable. This prints exactly those, for one or more reports, so a
verdict is read off the artefact rather than off an exit code (which is NOT
comparable across bounding strategies).

Usage: python3 report_summary.py <cov-report.json> [...]
"""
import json
import sys
from collections import Counter
from pathlib import Path


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <cov-report.json> [...]")
    for p in argv[1:]:
        path = Path(p)
        print(f"\n# {path}")
        if not path.exists():
            print("  MISSING -- an absent report is not a measured zero")
            continue
        d = json.loads(path.read_text())
        s = d.get("summary", {})
        for k in ("paths_total", "covered", "uncovered", "percentage",
                  "F_feasible_with_ce", "I_proven_unreachable", "U_undecided",
                  "U_of_which_bounded_holds", "revert_exit_paths"):
            if k in s:
                print(f"  {k:<26} {s[k]}")
        if "U_reasons" in s:
            print("  U_reasons")
            for k, v in sorted(s["U_reasons"].items()):
                print(f"    {k:<24} {v}")
        if "bound" in s:
            print(f"  bound                      {s['bound']}")
        if "decision_sequences" in s:
            print(f"  decision_sequences         {s['decision_sequences']}")

        st = Counter()
        f_inputs = 0
        f_total = 0
        for c in d.get("claims", []):
            st[c.get("status", "?")] += 1
            if c.get("status") == "F":
                f_total += 1
                if c.get("inputs"):
                    f_inputs += 1
        print(f"  claims by status           {dict(st)}")
        print(f"  F claims carrying inputs   {f_inputs}/{f_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
