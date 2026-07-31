#!/usr/bin/env python3
"""Do two runs recorded under different contracts share one command line?

`collection_health.py` printed three farming rows -- FarmAccounting,
FarmingLib and FarmingPool, all `startFarming` -- whose recorded `cmd` looked
identical, yet one produced a report with 26 F claims and the other two exited
6 in five seconds. A deterministic binary cannot do that, so exactly one of
these is true and they call for opposite actions:

  * the commands really are identical -> the collector ran ONE command and
    filed its outcome under several contract names. Then the per-contract rows
    are not measurements of those contracts at all, and every table keyed on
    them (including the bucket-A "unit never entered" rows) is mis-attributed.
  * the commands differ somewhere the health table did not print -> there is
    no bug here, and the abort is a real per-unit fact.

This script decides it by grouping ALL runs by their exact command string.
It does not summarise: for any command shared by more than one run it prints
every run under it with its own outcome, because a group whose outcomes agree
is a harmless duplicate and a group whose outcomes disagree is the defect.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATHCOV = HERE.parent / "pathcov"

BENCHES = ["aqua_Aqua", "cross_chain_swap_EscrowDst",
           "cross_chain_swap_EscrowSrc", "farming",
           "limit_order_protocol", "st1inch_St1inch"]


def main(argv):
    benches = argv[1:] or BENCHES
    print("# Command-line collision check\n")
    any_conflict = False
    for bench in benches:
        idx = PATHCOV / bench / "index.json"
        if not idx.exists():
            print(f"\n## `{bench}`\n\nno index.json")
            continue
        runs = json.loads(idx.read_text()).get("runs", [])
        by_cmd = defaultdict(list)
        for r in runs:
            by_cmd[r.get("cmd")].append(r)

        dup = {c: rs for c, rs in by_cmd.items() if len(rs) > 1}
        print(f"\n## `{bench}`  {len(runs)} run(s), "
              f"{len(by_cmd)} distinct command(s), "
              f"{len(dup)} command(s) used more than once\n")
        for cmd, rs in sorted(dup.items(), key=lambda kv: -len(kv[1])):
            outcomes = {(r.get("exitCode"), bool(r.get("reportPresent")))
                        for r in rs}
            verdict = ("SAME OUTCOME (harmless duplicate)" if len(outcomes) == 1
                       else "**DIFFERENT OUTCOMES FROM ONE COMMAND**")
            if len(outcomes) > 1:
                any_conflict = True
            print(f"- {len(rs)} runs share one command -- {verdict}")
            for r in rs:
                print(f"    {r.get('contract')}.{r.get('function')}  "
                      f"exit={r.get('exitCode')}  "
                      f"report={r.get('reportPresent')}  "
                      f"units={r.get('unitsEnumerated')}  "
                      f"F={r.get('F')}  wall={r.get('wallSeconds')}")
            print(f"    ```\n    {cmd}\n    ```")

    print("\n## Verdict\n")
    if any_conflict:
        print("At least one command produced different recorded outcomes under "
              "different contract names. A deterministic binary cannot do "
              "that, so the outcome was recorded against a contract that did "
              "not produce it, and every per-contract row derived from these "
              "runs is mis-attributed until the collector is fixed.")
    else:
        print("No command produced conflicting outcomes. Duplicate commands, "
              "where they exist, agree with each other -- the collector ran "
              "the same query more than once and filed the same answer, which "
              "wastes time but does not mis-attribute anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
