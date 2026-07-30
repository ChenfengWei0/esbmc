#!/usr/bin/env python3
"""Two measurements the commensurability audit asked for, both pure.

C. Can the PRODUCT score canonical decisions the BASELINE was forbidden to
   instrument?  Branch coverage is scoped by --coverage-exclude-contract;
   complete-path coverage never reads exclude_contracts, and it splices callee
   bodies into their callers carrying the callee's own source locations. So a
   contract that is (a) excluded from the baseline and (b) declared inside a
   file the marker rule accepts, contributes decisions to our numerator that the
   bar structurally cannot have. Sum those decisions per benchmark. Anything
   above 0 is a difference that flatters us.

F. Does the product's numerator pool decision steps from files OTHER than the
   flat? branch_gate reads only `line` from each decision step, never `file`, and
   the path-coverage recorder has no location_pool filter. A step at line 137 of
   some other file would score against canonical flat-line 137.

Reads only; writes nothing.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
sys.path.insert(0, str(REPO / "notes/coverage/scripts"))
from collect import BENCHES, INPUTS, is_project_own_marker  # noqa: E402
from ast_decisions import canonical_decisions, parse_flat_file_blocks  # noqa: E402

OWN = json.loads((INPUTS / "own_contracts.json").read_text())
PATHCOV = REPO / "notes/coverage/pathcov"
DECL = re.compile(r"^\s*(?:abstract\s+)?(contract|library|interface)\s+(\w+)\b")


def part_c():
    print("=" * 78)
    print("C. Canonical decisions owned by contracts the BASELINE excluded but")
    print("   that live in a file the marker rule accepts.")
    print("   > 0 means the product can score what the bar structurally cannot.")
    print("=" * 78)
    for bench, (flat_rel, _primary, _solc, project) in BENCHES.items():
        flat = INPUTS / flat_rel
        own = set(OWN["benchmarks"][bench]["ownContracts"])
        by_file, blocks = canonical_decisions(flat)
        lines = flat.read_text(errors="replace").splitlines()

        # Which contract owns which line range: a top-level declaration runs
        # until the next top-level declaration or the end of its file block.
        decls = []
        for i, line in enumerate(lines, 1):
            m = DECL.match(line)
            if m:
                decls.append((i, m.group(2)))
        decls.append((len(lines) + 1, None))

        offenders = {}
        for idx in range(len(decls) - 1):
            start, name = decls[idx]
            end = decls[idx + 1][0] - 1
            if name in own:
                continue
            marker = None
            for s, e, mk in blocks:
                if s <= start <= e:
                    marker = mk
                    break
            if not is_project_own_marker(marker, project):
                continue
            canon = by_file.get(marker, set())
            hit = {ln for ln in canon if start <= ln <= end}
            if hit:
                offenders.setdefault((name, marker), set()).update(hit)

        total = sum(len(v) for v in offenders.values())
        print(f"\n{bench}: {total} decision(s)")
        for (name, marker), hit in sorted(offenders.items()):
            print(f"    {name:<24} {marker:<44} {len(hit)} line(s) "
                  f"{sorted(hit)[:10]}")


def part_f():
    print()
    print("=" * 78)
    print("F. Decision steps whose `file` is not the flat, per benchmark.")
    print("   > 0 means the numerator pooled lines from another file.")
    print("=" * 78)
    for bench, (flat_rel, _p, _s, _pr) in BENCHES.items():
        rdir = PATHCOV / bench / "reports"
        if not rdir.exists():
            print(f"\n{bench}: not collected")
            continue
        files = sorted(rdir.glob("*.json"))
        seen, steps = Counter(), 0
        for p in files:
            d = json.loads(p.read_text())
            for c in d.get("claims", []):
                if c.get("status") != "F":
                    continue
                for e in c.get("decisions", []) or []:
                    if "unrecorded_prefix_enc" in e:
                        continue
                    steps += 1
                    seen[e.get("file")] += 1
        foreign = {k: v for k, v in seen.items()
                   if k is not None and flat_rel not in str(k)}
        print(f"\n{bench}: {len(files)} report(s), {steps} decision step(s)")
        for k, v in sorted(seen.items(), key=lambda kv: -kv[1]):
            mark = "   <-- NOT THE FLAT" if k in foreign else ""
            print(f"    {v:>6}  {k}{mark}")


part_c()
part_f()
