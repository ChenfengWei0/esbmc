#!/usr/bin/env python3
"""What a run EXPANDED, DEGRADED and TRUNCATED -- printed verbatim from its log.

WHY A SCRIPT AND NOT A LOOK. These logs run to megabytes (EscrowDst's `withdraw`
is 1.7 MB, 19742 lines) because symex prints an unwinding line per iteration, so
the handful of lines that say what the path identity actually contains are
unreadable by eye. Every line below is matched on a FIXED prefix emitted by
`--solidity-path-coverage` itself and printed WHOLE -- the file is read in full,
nothing is sampled, and no line is summarised into a count that would hide which
site it named.

The four things it answers, which no other consumer exposes:

  * how many internal calls were EXPANDED into the calling unit, and at what
    call-depth bound -- a callee that is not expanded contributes NO decisions to
    its caller's path identity, so its branches can never appear in any
    `decisions` array however many paths are witnessed;
  * which call sites are DEEPER than that bound, BY NAME;
  * which units were DEGRADED (call points withdrawn to fit the goal cap), by
    name and with the withdrawn callee;
  * which short-circuit sites were dropped over the operand cap.

Motivating case (D39): `EscrowDst._withdraw` holds two canonical decisions that
no witnessed path walks, although its caller `withdraw` witnessed 5 of 5 paths.
"Not expanded" and "expanded but never traversed" are different defects with
different fixes, and only these lines tell them apart.

Usage: python3 expansion_report.py <run.log> [<run.log> ...]
"""
import sys
from pathlib import Path

# Fixed prefixes the producer emits. Kept as literal substrings rather than
# regexes: a regex that stops matching after a message is reworded fails SILENTLY
# and prints "nothing was truncated", which is the exact shape of a check that
# never fires. A literal that stops matching prints an empty section, and the
# COUNTS line below makes an empty section visible rather than reassuring.
WANTED = [
    ("EXPANSION",   "internal call(s) into their calling unit"),
    ("DEPTH BOUND", "deeper than the call depth bound"),
    ("DEGRADED",    "DEGRADED unit "),
    ("DEGRADE-SUM", "degradation summary"),
    ("SHORT-CIRC",  "short-circuit site"),
    ("INSTRUMENT",  "instrumented "),
    ("DISTRIBUTION", "path distribution"),
    ("NOT UNITS",   "are internal/private and are therefore not units"),
    ("FOCUS",       "narrowed INSTRUMENTATION to"),
    ("GOAL CAP",    "path-cov-max-goals"),
]


def one(path):
    p = Path(path)
    text = p.read_text(errors="replace")
    lines = text.splitlines()
    print(f"## {p}   ({len(lines)} line(s), {len(text)} bytes)\n")
    total = 0
    for label, needle in WANTED:
        hits = [ln.strip() for ln in lines if needle in ln]
        total += len(hits)
        print(f"  [{label}]  {len(hits)} line(s)")
        for h in hits:
            print(f"      {h}")
        if not hits:
            print("      (none -- either the run had none, or the producer's "
                  "wording changed and this\n       literal no longer matches; "
                  "an empty section is NOT evidence of zero)")
        print()
    print(f"  {total} matched line(s) out of {len(lines)}\n")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    for a in argv[1:]:
        one(a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
