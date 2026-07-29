#!/usr/bin/env python3
"""Group the whole-contract run's claim verdicts by unit, out of a log already
on disk.

Section 7 item 20, question 2: before making a new measurement, check whether an
existing artifact already carries the answer. The 319-second whole-contract run
finished and its log records every `Solving claim '<unit>:path:<id>'` with its
verdict; the rerun that added --cov-report-json was killed at 551s because
exempting 200+ symbols from slicing makes the same run far slower. The answer
was already on disk.
"""
import re
import sys

CLAIM = re.compile(r"^(✓ PASSED|✗ FAILED): '([A-Za-z_$][A-Za-z0-9_$]*):path:(\d+)")

per = {}
with open(sys.argv[1]) as f:
    for line in f:
        m = CLAIM.match(line.strip())
        if not m:
            continue
        verdict, unit, pid = m.group(1), m.group(2), m.group(3)
        d = per.setdefault(unit, {"F": [], "P": []})
        d["F" if verdict.startswith("✗") else "P"].append(int(pid))

print("verdicts per unit (F = witnessed with a counterexample):")
for u in sorted(per):
    d = per[u]
    print(f"  {u}: F={len(d['F'])} {sorted(d['F'])}   passed={len(d['P'])}")
