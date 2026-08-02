#!/usr/bin/env python3
"""Print every unit of a stage-2 sweep with its bucket and its stated reason.

A sweep's headline is five counts -- CERTIFIED / NOT-CERTIFIED / KILLED /
NO-PATH / NO-COORDINATE -- and four of them are failures whose CAUSES are
different in kind. `certify_all.py` keeps them apart on purpose (its own
docstring says collapsing them is the failure-as-result pattern this repository
keeps hitting), and then the only thing anyone reads is the count.

KILLED in particular is a budget outcome, and on the PoC set -- contracts of a
few dozen lines whose whole purpose is to be cheap -- a large KILLED bucket is
not a property of the contracts. It is either the per-unit timeout being wrong
for the ladder's own cost, or a specific shape blowing up. Which one is
answerable per unit and only from the rows.

EVERY row is printed, including CERTIFIED. A listing of only the failures cannot
show whether the killed units are concentrated in one contract or spread evenly,
and that difference is the whole diagnosis.
"""

import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", help="certify/results.jsonl or poc_results.jsonl")
    a = ap.parse_args()

    rows = []
    for line in open(a.jsonl):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    if not rows:
        sys.exit(f"{a.jsonl}: no records")

    # The two sweeps key their subject differently; read it off the row rather
    # than off a flag, the same rule put_all.py follows.
    def subject(r):
        return r.get("benchmark") or r.get("poc") or "?"

    # ---- THE FIELD NAMES ARE THE SWEEP'S, NOT GUESSES ----
    #
    # The first version of this script read `seconds` and `reason`, neither of
    # which exists, got None for every row, and I published "the sweep records no
    # reason or timing -- a recording gap in certify_all.py". That was a claim
    # about the sweep derived from a bug in the reader, and the sweep in fact
    # records wall_s, exit, unit_timeout_s, run_timeout_s, memlimit_gib and the
    # whole ladder configuration on EVERY row. Read the writer before describing
    # what it writes.
    #
    # `run_timeout_s` is deliberately printed beside `wall_s`: it is the PER-ESBMC
    # budget (min(timeout,180)) rather than the per-unit one, and certify_all's
    # own comment says it is the budget that produces the largest failure bucket.
    buckets, by_subject = {}, {}
    print(f"{'subject':<26}{'unit':<20}{'bucket':<16}{'regions':>8}"
          f"{'wall_s':>8}{'unit_to':>8}{'run_to':>7}{'skipbr':>7}  not-certified reason")
    for r in sorted(rows, key=lambda r: (subject(r), r.get("unit") or "")):
        b = r.get("bucket") or "?"
        buckets[b] = buckets.get(b, 0) + 1
        by_subject.setdefault(subject(r), {}).setdefault(b, 0)
        by_subject[subject(r)][b] += 1
        n = len(r.get("certified") or {})
        nc = r.get("not_certified") or {}
        # One representative reason, and the count, so a unit whose paths failed
        # for DIFFERENT reasons is visible as such rather than collapsed to the
        # first one.
        reasons = sorted({str(v) for v in nc.values()}) if isinstance(nc, dict) \
            else []
        rtxt = ""
        if reasons:
            rtxt = f"[{len(nc)} path(s), {len(reasons)} distinct] {reasons[0]}"
            if len(rtxt) > 160:
                rtxt = rtxt[:160] + f" ...[{len(reasons[0])} chars]"
        w = r.get("wall_s")
        # ABSENT IS NOT FALSE. The first version printed `'yes' if
        # r.get('skip_bracket') else 'no'`, so a sweep whose records do not carry
        # the key at all -- certify_poc.py writes wall_s but not the ladder
        # configuration -- came out as a solid column of "no", i.e. as a positive
        # claim that the bracket RAN. That is the same always-false-reader shape
        # as reading a missing field and calling it a recording gap, twice in one
        # sitting, so the third state is explicit here.
        sb = ("-" if "skip_bracket" not in r
              else ("yes" if r["skip_bracket"] else "no"))
        print(f"{subject(r):<26}{(r.get('unit') or ''):<20}{b:<16}{n:>8}"
              f"{(round(w) if isinstance(w, (int, float)) else '-'):>8}"
              f"{str(r.get('unit_timeout_s', '-')):>8}"
              f"{str(r.get('run_timeout_s', '-')):>7}"
              f"{sb:>7}  {rtxt}")

    print()
    print(f"  {len(rows)} unit record(s)")
    for b, n in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>4}  {b}")
    print(f"    {sum(len(r.get('certified') or {}) for r in rows):>4}  "
          f"CERTIFIED REGIONS (a unit may certify several paths)")
    print()
    print("  by subject, so a bucket concentrated in one contract is visible:")
    for s in sorted(by_subject):
        parts = ", ".join(f"{b}={n}"
                          for b, n in sorted(by_subject[s].items()))
        print(f"    {s:<30}{parts}")
    print()
    print("  KILLED is a BUDGET outcome, not a property of the unit. It says the "
          "driver's per-unit timeout expired, and on a PoC-sized contract that "
          "points at the ladder's own cost or at one shape blowing up -- not at "
          "the contract being hard.")
    print("  Compare wall_s against unit_to: a unit killed at wall_s == unit_to "
          "hit the per-UNIT budget, and one that stopped well short of it did "
          "not -- those two need opposite repairs and the counts alone cannot "
          "tell them apart.")
    # THE CLUSTER IS THE FINDING, and it is computed rather than eyeballed: a
    # KILLED bucket whose wall times are all one value is a WALL, and the
    # certified units sitting just under it say how close the wall is.
    kw = sorted({r.get("wall_s") for r in rows
                 if r.get("bucket") == "KILLED" and r.get("wall_s") is not None})
    cw = sorted((r.get("wall_s") for r in rows
                 if r.get("bucket") == "CERTIFIED"
                 and isinstance(r.get("wall_s"), (int, float))), reverse=True)
    if kw:
        print()
        print(f"  KILLED wall_s values: {kw}"
              + ("   <-- ONE VALUE: this is a WALL, not a spread of difficulty"
                 if len(kw) == 1 else ""))
        if cw:
            near = [w for w in cw if kw and w >= kw[0] - 10]
            print(f"  slowest CERTIFIED wall_s: {cw[:8]}")
            print(f"  {len(near)} unit(s) CERTIFIED within 10s of that wall -- "
                  f"they are the same difficulty as the killed ones and differ "
                  f"only in finishing first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
