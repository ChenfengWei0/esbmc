#!/usr/bin/env python3
"""Aggregate a benchmark's path-coverage verdicts and U-reasons across its runs.

WHY THIS EXISTS. `branch_gate.py` prints one number per benchmark -- st1inch's
is 0 -- and a 0 there has at least three different meanings: the units were never
entered, the paths were never enumerated, or every claim was enumerated and none
was decided. Those are a scope result, a budget result and a solver result, and
the paper has to say which. `report_summary.py` answers it for ONE report; this
answers it for the benchmark, which is the unit the gate reports on.

WHAT IT REFUSES TO DO.

  * A PARTIAL report's counts are lower bounds. They are aggregated, but the
    number of partial reports is printed FIRST and separately, so a total that
    contains one cannot be quoted as a measurement without seeing that.
  * A report that does not state completeness is counted as UNSTATED, never as
    complete. It predates the marker, and defaulting it to complete is the
    silent assumption the marker exists to remove.
  * A run with NO report contributes to `no report`, never a zero. This corpus
    has repeatedly been read as if a missing measurement were a measured zero.

Usage: python3 u_reason_census.py <bench-dir> [...]
       python3 u_reason_census.py ../pathcov/st1inch_St1inch
"""
import json
import sys
from collections import Counter
from pathlib import Path


def census(bench_dir):
    bench = Path(bench_dir)
    reports_dir = bench / "reports"
    print(f"\n# {bench.name}")
    if not reports_dir.is_dir():
        print("  no reports/ directory -- nothing measured here")
        return

    reports = sorted(reports_dir.glob("*.json"))
    partial = unstated = complete = unreadable = 0
    verdicts = Counter()
    reasons = Counter()
    paths_total = 0
    per_unit = {}

    for rp in reports:
        try:
            d = json.loads(rp.read_text())
        except (json.JSONDecodeError, OSError) as e:
            unreadable += 1
            print(f"  UNREADABLE {rp.name}: {e}")
            continue
        s = d.get("summary", {})
        p = d.get("partial", s.get("partial"))
        if p is True:
            partial += 1
        elif p is None:
            unstated += 1
        else:
            complete += 1

        paths_total += s.get("paths_total", 0) or 0
        for k, v in (s.get("U_reasons") or {}).items():
            reasons[k] += v
        for c in d.get("claims", []):
            st = c.get("status", "?")
            verdicts[st] += 1
            fn = c.get("path_function") or c.get("function") or "?"
            per_unit.setdefault(fn, Counter())[st] += 1

    # Completeness BEFORE any count, for the reason in the docstring.
    print(f"  reports read               {len(reports)}"
          f"   (complete {complete}, PARTIAL {partial}, "
          f"completeness unstated {unstated}, unreadable {unreadable})")
    if partial:
        print("  ** contains PARTIAL report(s): every count below is a LOWER "
              "BOUND **")

    print(f"  paths_total (summed)       {paths_total}")
    decided = verdicts.get("F", 0) + verdicts.get("I", 0)
    total = sum(verdicts.values())
    print(f"  claims by status           {dict(sorted(verdicts.items()))}")
    print(f"  decided (F+I) / all        {decided}/{total}")
    if reasons:
        print("  U_reasons (summed)")
        for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {k:<26} {v}")
    else:
        print("  U_reasons                  none recorded")

    # Per unit, because a benchmark-level 0 hides whether it is 0 everywhere or
    # 0 on the units that carry the decisions.
    if per_unit:
        print("  per unit")
        for fn in sorted(per_unit):
            e = per_unit[fn]
            print(f"    {fn:<34} F {e.get('F', 0):>4}  "
                  f"I {e.get('I', 0):>4}  U {e.get('U', 0):>4}")


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <bench-dir> [...]")
    for d in argv[1:]:
        census(d)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
