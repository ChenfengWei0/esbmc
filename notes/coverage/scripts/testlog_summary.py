#!/usr/bin/env python3
"""Classify EVERY line of a test log into one of four buckets and print the
counts against the total.

Not a filter: the checksum at the bottom is what makes this readable without
being a truncation. Every line is in exactly one bucket, `ok:` lines are
counted rather than reprinted (there are hundreds and they all say the same
thing), and every FAIL, every ⛔, and every summary line is printed WHOLE.

usage:
    testlog_summary.py <log> [more logs ...]
"""
import sys


def summarise(path):
    rows = open(path, errors="replace").read().splitlines()
    n_ok = 0
    failures, summaries, others = [], [], 0
    for r in rows:
        s = r.strip()
        if s.startswith("ok:"):
            n_ok += 1
        elif s.startswith("FAIL") or s.startswith("⛔") or "Traceback" in s:
            failures.append(r)
        # forge's coverage table and its suite lines count as summary. Without
        # this a `forge coverage` log is 400 lines of solc warnings around an
        # 8-line table, and reading it whole to see the table is the cost this
        # tool exists to remove -- losslessly, since every other line is still
        # counted in `other`.
        # ⛔ `startswith("|")` ALONE IS TOO GREEDY: solc prints its warning
        # underlines as `     |        ^^^^`, and 60 of those drowned the
        # 8-line table this rule was added to surface. A coverage row always
        # carries a `%` or the `File` header, so that is the discriminator.
        elif ((s.startswith("|") and ("%" in s or "File" in s))
              or s.startswith(("+==", "╭", "╰"))
              # THE DELIVERABLE-B TABLE. `B = 4 of 10` and the per-row `**B**`
              # are the single most important lines this project prints, and
              # the first version of this classifier could not see either --
              # they are plain text with no `%` and no `|`, so they landed in
              # `other` and a 340-line log summarised to nothing.
              or "**B**" in s or s.startswith("B = ") or "1.fuzz" in s
              or "Suite result:" in s or "test suites in" in s
              or "test(s) ran" in s or "check(s)" in s or "checks passed" in s
              or s.startswith("===")):
            summaries.append(r)
        else:
            others += 1
    print("%s" % path)
    print("    ok        : %d" % n_ok)
    print("    FAIL/⛔    : %d" % len(failures))
    for f in failures:
        print("        %s" % f)
    print("    summary   : %d" % len(summaries))
    for s in summaries:
        print("        %s" % s)
    print("    other     : %d" % others)
    print("    checksum  : %d + %d + %d + %d = %d line(s)"
          % (n_ok, len(failures), len(summaries), others, len(rows)))
    return len(failures)


def main(argv):
    args = argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    bad = 0
    for p in args:
        bad += summarise(p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
