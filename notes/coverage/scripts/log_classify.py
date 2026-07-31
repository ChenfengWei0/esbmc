#!/usr/bin/env python3
"""Classify EVERY line of an esbmc run log; fold only exact repetition shapes.

A path-coverage run log is dominated by `Unwinding loop N iteration K ...`
lines -- a 1.1 MB log is mostly that. Reading a tail of it is guessing where the
interesting part is, and this workspace bans that for a reason: the answer has
more than once been in the middle.

So this reads the whole file and prints:
  * a shape census -- every line normalised by replacing digit runs with `#`,
    with its count. Every line of the file is in exactly one shape.
  * in full, every line whose shape occurs FEWER than `--fold` times (default
    5). Those are the one-off lines: errors, assertion failures, summary lines,
    the abort message.

Nothing is dropped silently: the census counts sum to the line count, and that
identity is printed and checked.

Usage: python3 log_classify.py <log> [--fold N]
"""
import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

DIGITS = re.compile(r"\d+")
HEX = re.compile(r"0x[0-9a-fA-F]+")


def shape(line):
    s = HEX.sub("0xH", line)
    return DIGITS.sub("#", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--fold", type=int, default=5)
    a = ap.parse_args()

    lines = Path(a.log).read_text(errors="replace").splitlines()
    shapes = Counter()
    where = defaultdict(list)
    for i, ln in enumerate(lines, 1):
        sh = shape(ln)
        shapes[sh] += 1
        if len(where[sh]) < 3:
            where[sh].append((i, ln))

    print(f"# {a.log}\n\n{len(lines)} line(s), {len(shapes)} distinct shape(s)\n")

    total = sum(shapes.values())
    print(f"census sums to {total} of {len(lines)} line(s) -- "
          f"{'OK' if total == len(lines) else 'MISMATCH, do not trust this run'}"
          "\n")

    print("## Folded shapes (count >= "
          f"{a.fold}), with one example each\n")
    for sh, n in shapes.most_common():
        if n < a.fold:
            continue
        i, ex = where[sh][0]
        print(f"- {n:>7} x  (first at line {i})  {ex[:160]}")

    print(f"\n## Every line whose shape occurs fewer than {a.fold} times, "
          "in file order\n")
    rare = {sh for sh, n in shapes.items() if n < a.fold}
    for i, ln in enumerate(lines, 1):
        if shape(ln) in rare:
            print(f"{i:>7}  {ln}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
