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
GUARD_OPEN = re.compile(r"^\s*if\s*\(\s*_put_ok\s*\)\s*\{\s*$")


def guarded_asserts(body):
    """How many of the body's assertions sit inside an `if (_put_ok)` block.

    Brace-counted from the guard's own line, so a guard containing a nested
    block is not closed early. Reported apart rather than subtracted silently:
    a conditional assertion is still IN the file, and a reader who cannot see
    how many are conditional is reading a strength the test does not have.
    """
    n, depth, inside = 0, 0, False
    for ln in body:
        if not inside and GUARD_OPEN.match(ln):
            inside, depth = True, 1
            continue
        if inside:
            depth += ln.count("{") - ln.count("}")
            if depth <= 0:
                inside = False
                continue
            n += len(ASSERT_RE.findall(ln))
    return n


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
            # ---- AN ASSERTION UNDER `if (_put_ok)` IS NOT AN ASSERTION -----
            #
            # A revert-tolerant PUT puts the rungs that say the state CHANGED
            # inside `if (_put_ok) { ... }`, which is false exactly when the
            # call reverted. If the guard's true branch is never taken, those
            # lines never execute and the test is green whatever the contract
            # does.
            #
            # ⛔ MEASURED, farming setDistributor enc=13, with guard_probe.py:
            # replacing the two guarded assertions with `assertTrue(false,...)`
            # left forge GREEN over 256 draws, while the same false assertion
            # OUTSIDE the guard went RED. The region pins a sender outside the
            # owner bound, so every draw reverts. Counting them made this file
            # report 12 where the honest number is 10 -- and the emitted file's
            # own header already said `ORACLE: 10` + `CONDITIONAL: 2 further`.
            # Third reader of one fact; the other two now carry the split.
            guarded = guarded_asserts(body)
            uncond = len(asserts) - guarded
            g1 = len(params) > 0
            g2 = any(w > 0 for w in widths)
            g3 = uncond > 0
            atxt = (f"{uncond}+{guarded}c" if guarded else str(len(asserts)))
            ok = g1 and g2 and g3
            ok_n += 1 if ok else 0
            wtxt = ("bounds=" + ",".join(
                ("point" if w == 0 else f"2^{w.bit_length()-1}~")
                for w in widths)) if widths else "NO bound() at all"
            print(f"{os.path.basename(f):<52}"
                  f"{('yes' if g1 else 'NO'):>7}{('yes' if g2 else 'NO'):>8}"
                  f"{('yes' if g3 else 'NO'):>9}  "
                  f"{len(params)} param(s), {wtxt}, {atxt} assert(s)"
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
