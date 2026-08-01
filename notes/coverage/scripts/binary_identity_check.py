#!/usr/bin/env python3
"""Did a collection really span two BINARIES, or only two commit labels?

WHY THIS EXISTS. `pathcov_collect.py::binary_identity()` records three things per
run -- `head` (git HEAD), `srcDirty`, and `binaryMtime`. A consumer that warns on
`head` alone raises a cross-build alarm every time a DOC OR TEST commit lands
while a sweep is running, because HEAD moved and the binary did not. That is a
false positive, and a false cross-build warning is expensive here: this project's
standing rule is that an aggregate spanning two builds may not be quoted, so a
spurious one embargoes good data.

`bench_unit_table.py` did exactly that on the 2026-08-01 EscrowDst re-collection
(15 rows `fc3228b725`, 3 rows `ae9c161959`) -- two commits I made MYSELF during
the sweep, neither of which rebuilt anything.

The load-bearing field is `binaryMtime`. `head` says which commit the tree was
on; only the mtime says which executable ran. `srcDirty` matters too: it is what
separates "this row came from the commit it names" from "this row came from a
work-in-progress tree that happened to be at that commit".

Usage: python3 binary_identity_check.py <runs.jsonl> [more.jsonl ...]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    rc = 0
    for p in argv[1:]:
        f = Path(p)
        if not f.exists():
            print(f"MISSING {p}")
            rc = 1
            continue
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        by_mtime = defaultdict(list)
        by_head = defaultdict(list)
        dirty = []
        for r in rows:
            b = r.get("binary") or {}
            by_mtime[b.get("binaryMtime")].append(r.get("tag"))
            by_head[b.get("head")].append(r.get("tag"))
            if b.get("srcDirty"):
                dirty.append(r.get("tag"))

        print(f"## {f}\n")
        print(f"  rows                 {len(rows)}")
        print(f"  distinct binaryMtime {len(by_mtime)}")
        for m, tags in sorted(by_mtime.items(), key=lambda kv: -len(kv[1])):
            print(f"      {len(tags):>3} run(s)  mtime={m}")
        print(f"  distinct git HEAD    {len(by_head)}")
        for h, tags in sorted(by_head.items(), key=lambda kv: -len(kv[1])):
            print(f"      {len(tags):>3} run(s)  head={h}")
        if dirty:
            print(f"  srcDirty on {len(dirty)} run(s): "
                  + ", ".join(dirty[:6])
                  + (" ..." if len(dirty) > 6 else ""))

        if len(by_mtime) > 1:
            print("\n  ⛔ GENUINELY MORE THAN ONE BINARY. Any aggregate over "
                  "these rows is a mixture\n     and must say so before a "
                  "number is quoted.")
            rc = 1
        elif len(by_head) > 1:
            print("\n  ✅ ONE BINARY, several commit labels. The HEAD moved "
                  "during the sweep (a doc or\n     test commit) and nothing "
                  "was rebuilt, so the rows ARE comparable. A warning\n     "
                  "keyed on `head` alone is a false positive here.")
        else:
            print("\n  ✅ ONE BINARY, one commit label.")
        if dirty:
            print("  ⚠ At least one row was produced from a tree with "
                  "uncommitted `src/` changes, so\n    the commit it names does "
                  "not identify the executable that ran.")
        print()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
