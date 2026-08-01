#!/usr/bin/env python3
"""Per-unit table for one benchmark: what did each run actually produce?

WHY: `runs.jsonl` already carries, per unit, the F count, every U reason, the
skip reason, whether the outer timeout killed it, and WHICH BINARY produced it.
Nothing reads those columns together, so questions like "which unit has the most
`bounded-holds`" -- i.e. which unit is the best candidate for the
focus-set + tx>=2 recipe -- get answered from memory instead of from the file.

The binary column is printed on purpose: the st1inch rows were found to span
THREE different builds, and any aggregate over them is a mixture nobody had
noticed.

Usage: python3 bench_unit_table.py <pathcov/<bench>/runs.jsonl>
"""
import json
import sys
from pathlib import Path


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    p = Path(argv[1])
    if not p.exists():
        sys.exit(f"no such file: {p}")

    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    print(f"## {p}\n")
    print(f"{'unit':<34}{'kind':<10}{'paths':>6}{'F':>5}{'U':>5}"
          f"{'bhold':>7}{'sunk':>6}{'wall':>8}  binary  note")
    binaries = {}
    for r in rows:
        tag = r.get("tag", "?")
        kind = r.get("kind", "?")
        b = (r.get("binary") or {}).get("head", "?")
        binaries[b] = binaries.get(b, 0) + 1
        if r.get("skipped"):
            print(f"{tag:<34}{kind:<10}{'-':>6}{'-':>5}{'-':>5}{'-':>7}{'-':>6}"
                  f"{'-':>8}  {b}  SKIPPED: {r['skipped']}")
            continue
        if not r.get("reportPresent"):
            why = ("outer-timeout" if r.get("killedByOuterTimeout")
                   else f"no report (exit {r.get('exitCode')})")
            print(f"{tag:<34}{kind:<10}"
                  f"{str(r.get('pathsInstrumented', '?')):>6}{'-':>5}{'-':>5}"
                  f"{'-':>7}{'-':>6}{r.get('wallSeconds', 0):>8.1f}  {b}  {why}")
            continue
        u = r.get("uReasons") or {}
        print(f"{tag:<34}{kind:<10}"
              f"{str(r.get('pathsTotal', '?')):>6}"
              f"{str(r.get('F', '?')):>5}{str(r.get('U', '?')):>5}"
              f"{str(u.get('bounded-holds', 0)):>7}"
              f"{str(u.get('solver-unknown', 0)):>6}"
              f"{r.get('wallSeconds', 0):>8.1f}  {b}")

    print("\nbinaries that produced these rows:")
    for b, n in sorted(binaries.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3} run(s)  {b}")
    if len(binaries) > 1:
        print("  ⚠ MORE THAN ONE BUILD. Any aggregate over these rows is a "
              "mixture; say so before quoting one.")

    # The recipe candidate: highest bounded-holds among units that reported.
    best = None
    for r in rows:
        if not r.get("reportPresent"):
            continue
        bh = (r.get("uReasons") or {}).get("bounded-holds", 0)
        if best is None or bh > best[1]:
            best = (r.get("tag"), bh, r.get("F"), r.get("pathsTotal"))
    if best and best[1] > 0:
        print(f"\nbest candidate for the focus-set + tx>=2 recipe: "
              f"{best[0]} — {best[1]} bounded-holds against F={best[2]} "
              f"of {best[3]} path(s)")
    else:
        print("\nno unit in this benchmark has any `bounded-holds`, so the "
              "focus-set + tx>=2 recipe has nothing to unlock here.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
