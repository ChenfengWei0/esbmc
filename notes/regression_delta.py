#!/usr/bin/env python3
"""Compare two ctest runs and answer ONE question: did this change break a test?

WHY THIS IS A COMMITTED TOOL AND NOT A ONE-LINER EACH TIME. The improvised
version of this comparison had a hole that took a deliberate perturbation test
to find: it computed `set(failing_now) - set(failing_before)`, which reports
NOTHING about a test that went from `Timeout` to `Failed` -- both are in the
failure set, so the difference is empty and the transition is invisible. The
fix is not a different set expression. It is to compare the STATE of every
test and print every transition, so that no state change can be absent from
the report.

THE CRITERION, fixed here so it cannot be relaxed after seeing a result:

    a test REGRESSED if it is hard-failing NOW and was PASSING before.

`hard-failing` = anything that is not `Passed`: Failed, Timeout, Exception,
Not Run. A test that was already not passing was already broken, and calling
that this change's regression would make every pre-existing failure re-fire on
every comparison. But it is NOT dropped: it appears under "already broken",
and if its verdict changed shape (Timeout -> Failed) that is printed too. The
rule being enforced is "nothing disappears from the report", not "only new
names count".

BOTH LOGS MUST NAME THEIR BINARY. A ctest log does not record which binary
produced it, and comparing a run against a log written by a different build is
the same defect the path-coverage collector already had (it resumed a journal
across builds and reused its reports). So --before-id / --after-id are REQUIRED
free-text labels, they are printed at the top of the report, and they must
differ -- comparing a run to itself is not a check, it is a way to print
"0 regressions".

usage:
    regression_delta.py --before A.log --after B.log \
                        --before-id "HEAD fa2abcb pristine" \
                        --after-id  "HEAD + hoist_operands_read_by"
exit 0 = no regression, 1 = at least one regression, 2 = no regression among
the tests both runs covered but the test SETS differ, 3 = refused to compare at
all (same --*-id, or a log with no parseable test lines). 3 is distinct from 1
on purpose: a refusal must not be readable as "it found a regression", and a
caller that treats every non-zero exit the same would report a mislabelled
comparison as a broken build.
"""
import argparse
import re
import sys
from pathlib import Path

# ctest prints one line per finished test:
#   12/34 Test  #12: esbmc-solidity/foo ..........   Passed    0.31 sec
#   13/34 Test  #13: esbmc-solidity/bar ..........***Failed    1.20 sec
#   14/34 Test  #14: esbmc-solidity/baz ..........***Timeout  60.02 sec
# The name may contain spaces; the verdict is the token after the dots.
LINE = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>.+?)\s*\.+\s*"
    r"(?P<verdict>\*\*\*[A-Za-z ]+|Passed)\s")


def parse(path):
    out = {}
    dupes = []
    for ln in Path(path).read_text(errors="replace").splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        name = m.group("name").strip()
        verdict = m.group("verdict").replace("***", "").strip()
        if name in out and out[name] != verdict:
            dupes.append((name, out[name], verdict))
        out[name] = verdict
    return out, dupes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--before-id", required=True)
    ap.add_argument("--after-id", required=True)
    a = ap.parse_args()

    if a.before_id.strip() == a.after_id.strip():
        print("REFUSING: --before-id and --after-id are the same string. "
              "If the two logs really came from the same binary there is "
              "nothing to compare; if they did not, label them.")
        return 3

    before, dup_b = parse(a.before)
    after, dup_a = parse(a.after)
    if not before or not after:
        print(f"REFUSING: parsed {len(before)} test(s) from {a.before} and "
              f"{len(after)} from {a.after}. A log with no parseable test "
              f"lines is not an empty result set, it is an unreadable file.")
        return 3

    print(f"BEFORE  {a.before_id}\n        {a.before}   "
          f"({len(before)} tests)")
    print(f"AFTER   {a.after_id}\n        {a.after}   "
          f"({len(after)} tests)\n")

    for tag, d in (("before", dup_b), ("after", dup_a)):
        for name, v1, v2 in d:
            print(f"  ! {tag}: {name} appears twice with different verdicts "
                  f"({v1} then {v2}); the later one is used")

    only_b = sorted(set(before) - set(after))
    only_a = sorted(set(after) - set(before))
    if only_b or only_a:
        print(f"\n  ! {len(only_b)} test(s) ran only BEFORE, "
              f"{len(only_a)} only AFTER -- the two runs do not cover the "
              f"same set, so 'no regression' would be a claim about a "
              f"different population.")
        for n in only_b:
            print(f"      only before: {n}")
        for n in only_a:
            print(f"      only after:  {n}")

    def hard(v):
        return v != "Passed"

    regressed, fixed, still, changed_shape = [], [], [], []
    for name in sorted(set(before) & set(after)):
        b, c = before[name], after[name]
        if hard(c) and not hard(b):
            regressed.append((name, b, c))
        elif hard(b) and not hard(c):
            fixed.append((name, b, c))
        elif hard(b) and hard(c):
            still.append((name, b, c))
            if b != c:
                changed_shape.append((name, b, c))

    print(f"\nfixed by this change: {len(fixed)}")
    for n, b, c in fixed:
        print(f"    {b} -> {c}   {n}")

    print(f"\nalready broken before, still broken: {len(still)}")
    for n, b, c in still:
        mark = "  (shape changed)" if b != c else ""
        print(f"    {b} -> {c}   {n}{mark}")

    print(f"\nACCEPTANCE CRITERION -- hard-failing now, was not before: "
          f"{len(regressed)}")
    for n, b, c in regressed:
        print(f"    {b} -> {c}   {n}")

    # A detected regression is a fact whether or not the test sets line up, so
    # it is never swallowed by the set-mismatch refusal. The refusal only
    # governs the right to say "no regression": that is a claim about tests
    # that were not all run.
    if regressed:
        if only_b or only_a:
            print("\nVERDICT: REGRESSED (and the test sets differ, so the "
                  "list above may still be incomplete)")
        else:
            print("\nVERDICT: REGRESSED")
        return 1
    if only_b or only_a:
        print("\nVERDICT: CANNOT COMPARE (test sets differ; no regression "
              "among the tests both runs did cover)")
        return 2
    print("\nVERDICT: NO REGRESSION")
    return 0


if __name__ == "__main__":
    sys.exit(main())
