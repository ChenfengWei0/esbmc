#!/usr/bin/env python3
"""Print a cov-report.json's `summary` block, and the per-status claim census.

A PARTIAL report is announced before any number, and a report that does not say
either way is announced as UNSTATED rather than assumed complete. See the
`partial` handling in main(): a run killed mid-solve now writes a real report to
the same filename, so completeness is a property that has to be read, not one
that can be inferred from the file existing.

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

        # PARTIAL FIRST, BEFORE ANY NUMBER IS PRINTED.
        #
        # A partial report is written to the same `cov-report.json` a complete
        # one is -- there is nowhere else to put it that a consumer would look
        # -- so the ONLY thing separating them is this field, and a reader that
        # printed F/U/percentages above it would have already done the damage:
        # every count in a partial report is a lower bound, and quoting one as
        # a measurement deflates whatever it is compared against.
        #
        # Read from BOTH levels because they are written at both, and treat a
        # MISSING field as unknown rather than as False. A report from a build
        # older than the partial marker genuinely cannot say, and defaulting
        # that to "complete" is the same silent assumption this whole field
        # exists to remove.
        partial = d.get("partial", s.get("partial"))
        if partial is True:
            print("  ** PARTIAL REPORT -- NOT A MEASUREMENT **")
            reason = d.get("partial_reason") or s.get("partial_reason") or "?"
            print(f"     reason                  {reason}")
            if "claims_decided" in s:
                print(f"     claims decided          {s['claims_decided']}"
                      f" of {s.get('claims_total', '?')}")
            print("     every count below is a LOWER BOUND; the paths the run "
                  "never reached carry")
            print("     u_reason 'run-died-before-solving', which is NOT "
                  "'not-solved-this-run'")
        elif partial is None:
            print("  completeness               UNSTATED (report predates the "
                  "`partial` field; cannot be read as complete)")
        else:
            print("  completeness               complete")

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

        # PER UNIT, because `paths_total` is a CONTRACT-level number and has
        # already been misread as a unit's once. On a hand-written PoC the
        # per-unit count is the whole experiment: the prediction is written in
        # the contract's comment and this is what it is checked against.
        per_unit = {}
        for c in d.get("claims", []):
            fn = c.get("path_function") or c.get("function") or "?"
            e = per_unit.setdefault(fn, Counter())
            e[c.get("status", "?")] += 1
            e["all"] += 1
        if len(per_unit) > 1 or "?" not in per_unit:
            print("  per unit")
            for fn in sorted(per_unit):
                e = per_unit[fn]
                print(f"    {fn:<24} paths {e['all']:>4}   F {e['F']:>4}   "
                      f"U {e['U']:>4}   I {e['I']:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
