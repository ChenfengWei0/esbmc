#!/usr/bin/env python3
"""Compare two stage-2 sweeps PER UNIT AND PER REGION, never by counts.

Two arms of a sweep differ in one flag and the tempting summary is "arm 2
certified 12 more units". That summary is compatible with arm 2 certifying 20 new
units and LOSING 8, and it cannot distinguish the two outcomes that matter:

    a unit newly decided with a region IDENTICAL to the other arm's
        -> the dropped work bought nothing
    a unit newly decided with a region that DIFFERS
        -> the dropped work was doing something, and the difference is the thing
           to look at

So every unit present in either arm is printed with its bucket on both sides and,
where both certified, a per-path region comparison. A unit in one arm and absent
from the other is called ABSENT rather than being silently treated as a failure:
the two arms may not have covered the same unit set, and a rate computed across
different denominators is the mistake this whole file exists to prevent.
"""

import argparse
import json
import re
import sys

# Byte for byte the driver's own region printer, the same grammar put_all.py
# parses. One grammar, three readers; if the driver changes how it prints a
# region this must fail loudly rather than silently read half of it.
INTERVAL_RE = re.compile(r"(\S+) in \[(\d+), (\d+)\]")


def widths(text):
    """{coordinate: hi - lo} for every bounded coordinate in a region string.

    Pins (`x == v`) are deliberately NOT included: a pin is width 0 by
    construction and counting it would make an arm that pins more look tighter
    for a reason that is not about the ladder.
    """
    return {m.group(1): int(m.group(3)) - int(m.group(2))
            for m in INTERVAL_RE.finditer(text or "")}


def tightness(ta, tb):
    """Which of two region strings is NARROWER, per shared coordinate.

    Returns (n_a_tighter, n_b_tighter, n_equal, n_only_one_side). A region is
    not one number, so "tighter" is reported as a count over coordinates and
    never collapsed into a verdict: an arm can be tighter on one coordinate and
    wider on another, and that case has to stay visible.
    """
    wa, wb = widths(ta), widths(tb)
    shared = set(wa) & set(wb)
    a_t = sum(1 for c in shared if wa[c] < wb[c])
    b_t = sum(1 for c in shared if wb[c] < wa[c])
    eq = sum(1 for c in shared if wa[c] == wb[c])
    only = len(set(wa) ^ set(wb))
    return a_t, b_t, eq, only


def load(path):
    rows = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        key = (r.get("poc") or r.get("benchmark") or "?", r.get("unit") or "?")
        rows[key] = r
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arm_a")
    ap.add_argument("arm_b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    a = ap.parse_args()

    A, B = load(a.arm_a), load(a.arm_b)
    if not A or not B:
        sys.exit("one of the arms has no records")

    # The configuration each arm ran under, printed before any comparison. Two
    # arms that differ in more than the flag under test are not an experiment,
    # and the fields are on the rows precisely so this can be checked rather
    # than remembered.
    def config(rows, label):
        keys = ("unit_timeout_s", "run_timeout_s", "skip_bracket", "pin_env",
                "level0", "memlimit", "binary")
        seen = {}
        for r in rows.values():
            for k in keys:
                if k in r:
                    seen.setdefault(k, set()).add(json.dumps(r[k]))
        print(f"  {label}: " + ", ".join(
            f"{k}=" + (next(iter(v)) if len(v) == 1 else f"MIXED{sorted(v)}")
            for k, v in sorted(seen.items())) or f"  {label}: (no config fields)")

    print("## arm configuration, read off the records")
    config(A, a.label_a)
    config(B, b_label := a.label_b)

    # ---- WHICH PART OF `binary` DECIDES WHETHER THE ARMS ARE COMPARABLE ----
    #
    # `binary` is {head, srcDirty, binaryMtime}. `head` moves with EVERY commit,
    # including the many that only touch these scripts, so two arms run minutes
    # apart around a script commit have different `head` and the SAME esbmc. The
    # field that decides is `binaryMtime`: same mtime, same binary, comparable.
    # Reporting "different binary" off `head` alone would refuse a valid
    # experiment; reporting "same binary" off nothing would accept an invalid one.
    def mtimes(rows):
        return {(r.get("binary") or {}).get("binaryMtime")
                for r in rows.values() if isinstance(r.get("binary"), dict)}
    ma, mb = mtimes(A), mtimes(B)
    if ma and mb:
        if ma == mb:
            print(f"  binaries MATCH on binaryMtime {sorted(ma)} -- the arms are "
                  f"comparable even where `head` differs, because `head` moves "
                  f"with script-only commits and does not rebuild esbmc")
        else:
            print(f"  ⚠ binaryMtime DIFFERS: {a.label_a}={sorted(ma)} "
                  f"{b_label}={sorted(mb)}. The arms were produced by DIFFERENT "
                  f"esbmc builds, so any difference below is confounded with the "
                  f"build and this is not a one-variable experiment")

    print()
    print(f"{'subject':<26}{'unit':<20}{a.label_a:<16}{b_label:<16}  regions")
    same = diff = only_a = only_b = absent = 0
    tight_a = tight_b = tight_eq = tight_only = 0
    for key in sorted(set(A) | set(B)):
        ra, rb = A.get(key), B.get(key)
        ba = ra.get("bucket") if ra else "-ABSENT-"
        bb = rb.get("bucket") if rb else "-ABSENT-"
        note = ""
        if ra is None or rb is None:
            absent += 1
            note = "unit not present in both arms; NOT comparable"
        else:
            ca_, cb_ = ra.get("certified") or {}, rb.get("certified") or {}
            if ca_ and cb_:
                encs = sorted(set(ca_) | set(cb_), key=str)
                d = [e for e in encs if ca_.get(e) != cb_.get(e)]
                if d:
                    diff += 1
                    # WHICH ARM IS TIGHTER, per coordinate. "20 regions differ"
                    # is not a result: a differing region is the dropped work
                    # earning its cost only if the arm that paid for it is
                    # NARROWER, and the opposite outcome (paying more for a
                    # WIDER region) is a defect, not a trade-off. Counted per
                    # coordinate because an arm can be tighter on one and wider
                    # on another and that case must stay visible.
                    ta = tb_ = eqc = onlyc = 0
                    for e in d:
                        x, y, z, o = tightness(ca_.get(e, ""), cb_.get(e, ""))
                        ta += x
                        tb_ += y
                        eqc += z
                        onlyc += o
                    tight_a += ta
                    tight_b += tb_
                    tight_eq += eqc
                    tight_only += onlyc
                    note = (f"{len(d)} of {len(encs)} region(s) DIFFER: "
                            + ", ".join(f"enc={e}" for e in d[:3])
                            + f"   [tighter: {a.label_a}={ta} {b_label}={tb_}"
                            + (f" same={eqc}" if eqc else "")
                            + (f" one-sided={onlyc}" if onlyc else "") + "]")
                else:
                    same += 1
                    note = f"{len(encs)} region(s) IDENTICAL"
            elif ca_ and not cb_:
                only_a += 1
                note = f"only {a.label_a} certified ({len(ca_)} region(s))"
            elif cb_ and not ca_:
                only_b += 1
                note = f"only {b_label} certified ({len(cb_)} region(s))"
        print(f"{key[0]:<26}{key[1]:<20}{ba:<16}{bb:<16}  {note}")

    print()
    print(f"  units in both arms with regions on both sides:")
    print(f"    {same:>4}  IDENTICAL regions   -- the dropped work bought nothing")
    print(f"    {diff:>4}  DIFFERING regions   -- the dropped work was doing something")
    print(f"    {only_a:>4}  certified only in {a.label_a}")
    print(f"    {only_b:>4}  certified only in {b_label}")
    print(f"    {absent:>4}  unit(s) present in only ONE arm -- NOT comparable, and "
          f"not counted either way")
    print()
    if diff:
        print()
        print(f"  across the {diff} unit(s) whose regions DIFFER, per bounded "
              f"coordinate:")
        print(f"    {tight_a:>4}  coordinate(s) NARROWER in {a.label_a}")
        print(f"    {tight_b:>4}  coordinate(s) NARROWER in {b_label}")
        print(f"    {tight_eq:>4}  same width (the regions differ elsewhere -- a "
              f"different interval of the same size, or a pin)")
        print(f"    {tight_only:>4}  coordinate(s) bounded in ONE arm only -- not "
              f"a width comparison at all")
        print("    A differing region is the extra work earning its cost only "
              "where the arm that PAID is narrower. Paying more for a WIDER "
              "region is a defect, not a trade-off, and the two counts above are "
              "kept apart so it cannot hide inside 'the regions differ'.")
    print()
    print("  A count of 'how many more certified' is NOT the result. The result is "
          "the IDENTICAL / DIFFERING split: identical regions mean the arms agree "
          "about the answer and disagree only about the price.")
    if diff == 0 and same > 0:
        # ⚠ THE DISCRIMINATOR HAS NOT BEEN SEEN TO FIRE IN BOTH DIRECTIONS.
        # Comparing a file with ITSELF can only ever produce IDENTICAL, so a
        # smoke test on one file proves the reader runs and proves nothing about
        # the DIFFERING branch. Said out loud on any run that produced no
        # DIFFERING row, because "0 differing" from a working comparator and
        # "0 differing" from one that cannot report a difference look the same.
        print()
        print("  ⚠ no DIFFERING row in this run. That is a real result only if "
              "the two files are genuinely different runs -- comparing a file "
              "with itself produces exactly this output and says nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
