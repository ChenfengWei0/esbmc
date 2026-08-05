#!/usr/bin/env python3
"""Search for a COUNTEREXAMPLE to the claim that half the ladder is derivable.

---- THE CLAIM UNDER TEST -------------------------------------------------

The ladder emits six R1 rungs per candidate variable and pays one solver query
for each:

    post == pre   post != pre   post >= pre   post <= pre   post > pre   post < pre

Three of them are the negations of the other three, so on a FEASIBLE path
(one with a witness, which is the only kind the ladder runs on) these
implications hold:

    (eq HOLDS)              =>  (ne REFUTED)
    (ge HOLDS and le HOLDS) =>  (gt REFUTED) and (lt REFUTED)

If they hold on every row ever measured, three of the six queries can be
DERIVED instead of solved -- a 2x cut in ladder solver time with NO precision
traded away. That is worth checking before trading any.

⛔ THIS TOOL LOOKS FOR THE ROW THAT BREAKS IT, not for confirmation. A single
variable showing `eq HOLDS` together with `ne HOLDS` refutes the claim, and
that is what is printed loudest. Every row parsed is counted against the
number of rows found, so the reader can see the sweep was total.

⛔ AND IT IS NOT A PROOF. Agreement across every row on disk is evidence about
the corpus that has been run, not a theorem about the encoding. The
implication above is only sound if the path is FEASIBLE; a vacuous path makes
every assertion hold, and both arms of a complementary pair would read HOLDS.
That is exactly the row this search would surface.

usage:
    ladder_complement_audit.py <log-or-dir> [more ...]
"""
import os
import re
import sys

ROW = re.compile(r"^\s*\[put\]\s+(\S+):\s+(post [=!<>]=? pre)\s+"
                 r"(HOLDS|REFUTED|UNDECIDED\S*|VACUOUS\S*)\s*$")

COMPLEMENT = {"post == pre": "post != pre", "post != pre": "post == pre",
              "post >= pre": "post < pre", "post < pre": "post >= pre",
              "post <= pre": "post > pre", "post > pre": "post <= pre"}


def walk(t):
    if os.path.isfile(t):
        return [t]
    out = []
    for root, _d, files in os.walk(t):
        for f in sorted(files):
            if f.endswith((".log", ".txt", ".out")):
                out.append(os.path.join(root, f))
    return sorted(out)


def main(argv):
    args = argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    files = []
    for t in args:
        files += walk(t)

    rows = 0
    # (file, unit-block-index, var) -> {text: verdict}
    groups = {}
    block = 0
    for p in files:
        try:
            lines = open(p, errors="replace").read().splitlines()
        except OSError:
            continue
        for ln in lines:
            if ln.startswith("--- ") or "step 2b" in ln:
                block += 1
            m = ROW.match(ln)
            if not m:
                continue
            rows += 1
            var, text, verdict = m.group(1), m.group(2), m.group(3)
            groups.setdefault((p, block, var), {})[text] = verdict

    violations = []
    pairs_checked = 0
    pairs_agreeing = 0
    incomplete = 0
    both_hold = 0
    derivable = 0
    for (p, blk, var), d in sorted(groups.items()):
        for text, comp in COMPLEMENT.items():
            if text not in d or comp not in d:
                continue
            if text > comp:      # each unordered pair once
                continue
            pairs_checked += 1
            a, b = d[text], d[comp]
            # HOW MANY QUERIES THE ONE-DIRECTIONAL RULE ACTUALLY SAVES.
            # `X HOLDS => not-X REFUTED` is sound; the converse is not. So a
            # pair is derivable exactly when ONE side HOLDS: solve that side,
            # skip the other. When BOTH are REFUTED -- which happens, and is
            # what refutes the two-directional version -- nothing is saved.
            # Counted rather than assumed, because "50%" and "the fraction
            # where a side holds" are different numbers.
            if "HOLDS" in (a, b) and "REFUTED" in (a, b):
                pairs_agreeing += 1
                derivable += 1
            elif a == "HOLDS" and b == "HOLDS":
                both_hold += 1
                violations.append((os.path.basename(p), blk, var, text, a,
                                   comp, b))
            elif a == "REFUTED" and b == "REFUTED":
                violations.append((os.path.basename(p), blk, var, text, a,
                                   comp, b))
            else:
                incomplete += 1

    print("%d file(s) scanned, %d ladder row(s), %d (var, block) group(s)"
          % (len(files), rows, len(groups)))
    print("    complementary pairs found       : %d" % pairs_checked)
    print("    ... exactly one HOLDS, one REFUTED: %d" % pairs_agreeing)
    print("    ... one side UNDECIDED/VACUOUS    : %d  (not evidence either "
          "way)" % incomplete)
    print("    ⛔ VIOLATIONS                      : %d" % len(violations))
    for f, blk, var, t, a, c, b in violations:
        print("        %s block %d  %s:  %s=%s  AND  %s=%s"
              % (f, blk, var, t, a, c, b))
    print("    checksum: %d + %d + %d = %d"
          % (pairs_agreeing, incomplete, len(violations), pairs_checked))
    print()
    print("---- WHAT THE SOUND (ONE-DIRECTIONAL) RULE WOULD SAVE ----")
    print("    `X HOLDS => not-X REFUTED` is sound; the converse is NOT.")
    print("    pairs where one side HOLDS -> the other is DERIVABLE : %d"
          % derivable)
    print("    pairs where BOTH are REFUTED -> nothing saved         : %d"
          % (len(violations) - both_hold))
    print("    pairs where BOTH HOLD -> would REFUTE even the sound  : %d"
          % both_hold)
    if pairs_checked:
        print("    => %.1f%% of complementary queries removable, no precision "
              "traded" % (100.0 * derivable / pairs_checked))
    if both_hold:
        print("\n⛔ %d pair(s) have BOTH SIDES HOLDING. That refutes even the "
              "one-directional rule and must be understood before any query "
              "is skipped -- it is the signature of a VACUOUS path, where "
              "every assertion holds because no execution reaches it."
              % both_hold)
        return 1
    if violations:
        print("\nThe two-directional claim is REFUTED by the %d both-REFUTED "
              "pair(s) above: over a REGION, `for all: post==pre` and `for "
              "all: post!=pre` can BOTH fail, so `eq REFUTED` implies nothing "
              "about `ne`. The one-directional rule survives that -- none of "
              "those rows contradicts it." % (len(violations) - both_hold))
        return 0
    if pairs_checked == 0:
        print("\n⛔ NO COMPLEMENTARY PAIR WAS FOUND AT ALL. That is not "
              "support for the claim -- it means this sweep looked at the "
              "wrong files, or the row format changed. Fix the input before "
              "reading anything into the zero.")
        return 2
    print("\nNo counterexample in %d pair(s). Evidence FOR deriving three of "
          "the six rungs -- on the runs measured here, not a theorem."
          % pairs_checked)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
