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

    buckets, by_subject = {}, {}
    print(f"{'subject':<30}{'unit':<22}{'bucket':<16}{'regions':>8}"
          f"{'secs':>7}  reason")
    for r in sorted(rows, key=lambda r: (subject(r), r.get("unit") or "")):
        b = r.get("bucket") or "?"
        buckets[b] = buckets.get(b, 0) + 1
        by_subject.setdefault(subject(r), {}).setdefault(b, 0)
        by_subject[subject(r)][b] += 1
        n = len(r.get("certified") or {})
        secs = r.get("seconds")
        reason = (r.get("reason") or r.get("error") or "")
        if isinstance(reason, str) and len(reason) > 150:
            reason = reason[:150] + f" ...[{len(reason)} chars]"
        print(f"{subject(r):<30}{(r.get('unit') or ''):<22}{b:<16}{n:>8}"
              f"{(round(secs) if isinstance(secs, (int, float)) else '-'):>7}"
              f"  {reason}")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
