#!/usr/bin/env python3
"""Read `certify_all.py`'s JSONL and print what EXECUTION_PLAN step 2.2 asks for.

Step 2.2 names two deliverables: the `adm(c)` distribution, and "the fraction of
coordinates cut to a SINGLE POINT -- the only honest applicability metric". The
second is the one worth the file. A region reported as

    x in [40, 44] \\ {42}, msg.value == 0

has one coordinate generalised and one pinned, and a pipeline that counted both
as "bounded" would report a unit as fully generalised when half its region is a
constant. A coordinate cut to `[v, v]` is a concrete test with extra syntax.

FOUR NUMBERS, and they are deliberately not combined into one score:

  * units by OUTCOME BUCKET -- certified / not-certified / no-coordinate /
    killed / crashed. Kept apart because a killed unit is a budget outcome and a
    no-coordinate unit is a coordinate-KIND outcome, and averaging them into a
    "certification rate" would let a slow machine look like a weak method.
  * paths certified over paths witnessed, over the units that got that far.
  * the coordinate funnel: free / pinned / single-point-after-certification.
  * where the S10 msg.value pin fired, declined, or could not be read. On the
    one contract it has been measured on this is the difference between 0-of-5
    and 4-of-5, so a summary that omitted it would hide its own largest input.

Everything is read out of the records. Nothing is recomputed from the source, so
this file cannot disagree with the sweep about what was measured.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..",
                                                "scripts")))

from solidity_path_generalise import parse_intervals, parse_holes  # noqa: E402

BUCKETS = ["CERTIFIED", "NOT-CERTIFIED", "NO-COORDINATE", "NO-PATH",
           "KILLED", "CRASHED", "NO-UNIT-LIST"]


def region_stats(text):
    """(bounded, single_point, holes) for one certified region's text.

    `msg.value == 0` and the other pins are rendered as `name == v`, not as an
    interval, so `parse_intervals` does not see them at all -- which is what
    makes "bounded" mean generalised-over rather than merely mentioned. The pins
    are counted separately by the caller from the driver's own pin line.
    """
    iv = parse_intervals(text)
    holes = parse_holes(text)
    pts = sum(1 for lo, hi in iv.values() if lo == hi)
    return len(iv), pts, sum(len(v) for v in holes.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="?",
                    default=os.path.join(HERE, "..", "certify",
                                         "results.jsonl"))
    args = ap.parse_args()

    if not os.path.exists(args.results):
        print(f"no results at {args.results}; run certify_all.py first")
        return 1
    recs = []
    with open(args.results) as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))

    by_bench = {}
    for r in recs:
        by_bench.setdefault(r["benchmark"], []).append(r)

    print("=" * 78)
    print("STAGE 2 ACROSS THE CORPUS — units by outcome")
    print("=" * 78)
    hdr = f"{'benchmark':<30}" + "".join(f"{b[:9]:>11}" for b in BUCKETS)
    print(hdr)
    tot = {b: 0 for b in BUCKETS}
    for bench in sorted(by_bench):
        counts = {b: 0 for b in BUCKETS}
        for r in by_bench[bench]:
            if r["bucket"] in counts:
                counts[r["bucket"]] += 1
                tot[r["bucket"]] += 1
        print(f"{bench:<30}" + "".join(f"{counts[b]:>11}" for b in BUCKETS))
    print(f"{'TOTAL':<30}" + "".join(f"{tot[b]:>11}" for b in BUCKETS))

    print()
    print("=" * 78)
    print("PATHS certified, over the units that produced a region at all")
    print("=" * 78)
    for bench in sorted(by_bench):
        w = c = 0
        for r in by_bench[bench]:
            if r["bucket"] in ("CERTIFIED", "NOT-CERTIFIED"):
                w += r.get("witnessed") or 0
                c += len(r.get("certified") or {})
        pct = f"{100.0 * c / w:.0f}%" if w else "n/a"
        print(f"  {bench:<30} {c:>5} of {w:<5} witnessed path(s)   {pct}")

    print()
    print("=" * 78)
    print("THE APPLICABILITY METRIC — coordinates cut to a SINGLE POINT")
    print("=" * 78)
    print("  A coordinate certified as [v, v] is a concrete test with extra")
    print("  syntax. Counting it as 'bounded' is what would make a unit look")
    print("  generalised when half its region is a constant.")
    print()
    gb = gp = gh = 0
    for bench in sorted(by_bench):
        b = p = h = 0
        for r in by_bench[bench]:
            for text in (r.get("certified") or {}).values():
                bb, pp, hh = region_stats(text)
                b += bb
                p += pp
                h += hh
        gb, gp, gh = gb + b, gp + p, gh + h
        pct = f"{100.0 * p / b:.0f}%" if b else "n/a"
        print(f"  {bench:<30} {p:>5} of {b:<5} bounded coordinate(s) are "
              f"single points  {pct}   holes: {h}")
    pct = f"{100.0 * gp / gb:.0f}%" if gb else "n/a"
    print(f"  {'TOTAL':<30} {gp:>5} of {gb:<5} {pct}   holes: {gh}")

    print()
    print("=" * 78)
    print("S10 — where the msg.value pin fired")
    print("=" * 78)
    pin = {}
    for r in recs:
        k = r.get("msg_value_pin") or "not seen"
        pin[k] = pin.get(k, 0) + 1
    for k in sorted(pin):
        print(f"  {k:<30} {pin[k]:>5} unit(s)")

    print()
    print("=" * 78)
    print("WHY units did NOT certify — the reason, verbatim, never merged")
    print("=" * 78)
    why = {}
    for r in recs:
        for text in (r.get("not_certified") or {}).values():
            # Bucketed by the FIRST clause only. The rest of each message is the
            # divergence text, which names per-path quantities and would make
            # every path its own bucket -- and the point here is the shape of
            # the failure, not its instance. The full text stays in the JSONL.
            head = text.split(";")[0].strip()
            why[head] = why.get(head, 0) + 1
    for k in sorted(why, key=lambda x: -why[x]):
        print(f"  {why[k]:>5}  {k}")
    if not why:
        print("  (none)")

    print()
    print("NOT A GATE. No threshold is applied, because the Phase 2 bar has to")
    print("be picked from a distribution -- and picking it from the first one")
    print("measured is choosing the bar after seeing the scores.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
