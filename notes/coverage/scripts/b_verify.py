#!/usr/bin/env python3
"""Verify WORKORDER's deliverable-B gates against the EMITTED FILE.

`put_all.py --forge-only` checks the same gates, but gates 1-3 there come from
`put.json` -- numbers the DRIVER wrote about what it believes it emitted. This
reads the `.t.sol`. The two can disagree, and when they do the file is right:
it is what forge runs.

WHAT IS CHECKED, per `test_put_*` function:

  1 FUZZ PARAMETERS   the function takes at least one parameter. A `()` signature
                      is a single deterministic point however many `runs:` forge
                      prints, and forge prints none for it.
  2 BOUND WIDTH > 1   at least ONE `bound(x, lo, hi)` with hi > lo. Every bound is
                      listed with its width, because a PUT whose bounds are all
                      single points explores one input and the parameter list
                      alone cannot show that -- measured on FarmingPool.transfer
                      enc=14, whose `to` is `[0,0]` while `value` is the full
                      uint256.
  3 assert*           at least one assert*/vm.expect* INSIDE the PUT body. The
                      concrete `test_cov_*` cases in the same file are NOT the
                      deliverable and their assertions must not count -- so the
                      body is sliced from the `function test_put_` line to the
                      matching close, not searched file-wide. Measured on aqua:
                      four PUTs whose body is `try c0.f(...) {} catch {}` and
                      whose FILE nevertheless contains an `assertFalse` belonging
                      to a `test_cov_` case.

Gates 4 (forge green) and 5 (corpus contract) are NOT checked here: only forge
can answer 4, and 5 is a property of the input, not the text.
"""

import argparse
import os
import re
import sys

SIG_RE = re.compile(r"function\s+(test_put_\w+)\s*\(([^)]*)\)")
BOUND_RE = re.compile(r"\bbound\s*\(\s*[^,]+,\s*([0-9]+)\s*,\s*([0-9]+)\s*\)")
ASSERT_RE = re.compile(r"\b(assert\w*|vm\s*\.\s*expect\w*)\s*\(")


def put_body(lines, start):
    """Lines of the PUT function, from its signature to its matching brace.

    Brace counting rather than "to the next function": a body sliced by the next
    `function` keyword would swallow nothing here but would silently include a
    nested contract's members in a file laid out differently, and gate 3 counting
    an assertion from OUTSIDE the PUT is the exact failure this file exists to
    prevent.
    """
    depth, out, started = 0, [], False
    for line in lines[start:]:
        out.append(line)
        depth += line.count("{") - line.count("}")
        if "{" in line:
            started = True
        if started and depth <= 0:
            break
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help=".t.sol files to verify")
    a = ap.parse_args()

    print(f"{'file':<52}{'1.fuzz':>7}{'2.width':>8}{'3.assert':>9}  detail")
    ok_n = 0
    for f in sorted(a.files):
        if not os.path.exists(f):
            print(f"{os.path.basename(f):<52}{'-':>7}{'-':>8}{'-':>9}  FILE MISSING")
            continue
        lines = open(f).read().splitlines()
        hit = [(i, m) for i, l in enumerate(lines)
               for m in [SIG_RE.search(l)] if m]
        if not hit:
            print(f"{os.path.basename(f):<52}{'NO':>7}{'NO':>8}{'NO':>9}"
                  f"  no test_put_* function in this file")
            continue
        for i, m in hit:
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            body = put_body(lines, i)
            text = "\n".join(body)
            widths = [(int(b.group(2)) - int(b.group(1)))
                      for b in BOUND_RE.finditer(text)]
            asserts = ASSERT_RE.findall(text)
            g1 = len(params) > 0
            g2 = any(w > 0 for w in widths)
            g3 = len(asserts) > 0
            ok = g1 and g2 and g3
            ok_n += 1 if ok else 0
            wtxt = ("bounds=" + ",".join(
                ("point" if w == 0 else f"2^{w.bit_length()-1}~")
                for w in widths)) if widths else "NO bound() at all"
            print(f"{os.path.basename(f):<52}"
                  f"{('yes' if g1 else 'NO'):>7}{('yes' if g2 else 'NO'):>8}"
                  f"{('yes' if g3 else 'NO'):>9}  "
                  f"{len(params)} param(s), {wtxt}, {len(asserts)} assert(s)"
                  + ("   **1-2-3 PASS**" if ok else ""))
    print()
    print(f"  {ok_n} file(s) pass gates 1-3 read from the TEXT.")
    print("  Gate 4 (forge green on the unmodified contract) and gate 5 (the "
          "contract is a corpus contract, not a hand-written PoC) are NOT "
          "checked here -- only forge can answer 4, and 5 is a property of the "
          "input. A '1-2-3 PASS' is not B on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
