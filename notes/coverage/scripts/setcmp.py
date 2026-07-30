#!/usr/bin/env python3
"""The one honest SET comparison, per file.

The gate compares COUNTS, because the locked JSON records only a count. But the
baseline's per-decision identity survives in the run: collect.py keeps
`--coverage-covered-set /tmp/cov_<bench>/union_pair2.json`, which records a
{cond, loc} per covered edge. So for a benchmark re-collected today, both sides'
SETS are available:

    baseline_lines = lines(union_pair2.json)  n canon(file)
    product_lines  = lines(F decisions)       n canon(file)

A count comparison cannot detect product-side OVER-count -- the numerator is
capped at the denominator and the gate asks only `ours >= bar`. The symmetric
difference can. Report BOTH directions:

    ONLY BASELINE -- decisions branch coverage reached that we did not
    ONLY PRODUCT  -- decisions we counted that branch coverage never reached

Reads only; writes nothing.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
sys.path.insert(0, str(REPO / "notes/coverage/scripts"))
from collect import BENCHES, INPUTS, is_project_own_marker  # noqa: E402
from ast_decisions import canonical_decisions  # noqa: E402

PATHCOV = REPO / "notes/coverage/pathcov"
LOC_RE = re.compile(r"line\s+(\d+)")


def baseline_lines(bench):
    p = Path(f"/tmp/cov_{bench}/union_pair2.json")
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    out = set()
    for c in d.get("covered", []):
        m = LOC_RE.search(c.get("loc", ""))
        if m:
            out.add(int(m.group(1)))
    return out


def product_lines(bench):
    # AN EMPTY reports/ IS NOT A MEASURED ZERO. The directory is created when a
    # collection STARTS, so a benchmark whose sweep is still running -- or which
    # produced no report at all -- would otherwise print `both 0, only-baseline
    # N`, which reads exactly like "we reached nothing" instead of "we have not
    # looked yet". Observed live: st1inch printed 0/72 while its sweep was on
    # its fifth unit.
    rdir = PATHCOV / bench / "reports"
    if not rdir.exists():
        return None
    if not any(rdir.glob("*.json")):
        return None
    out = set()
    n = 0
    for p in sorted(rdir.glob("*.json")):
        d = json.loads(p.read_text())
        for c in d.get("claims", []):
            if c.get("status") != "F":
                continue
            for e in c.get("decisions", []) or []:
                if "unrecorded_prefix_enc" in e or e.get("synthetic_abi_gate"):
                    continue
                ln = e.get("line")
                if isinstance(ln, int) and ln > 0:
                    out.add(ln)
                    n += 1
    return out


for bench, (flat_rel, _p, _s, project) in BENCHES.items():
    flat = INPUTS / flat_rel
    by_file, blocks = canonical_decisions(flat)
    own = sorted({m for _s2, _e2, m in blocks
                  if is_project_own_marker(m, project)})
    canon = {m: by_file.get(m, set()) for m in own if by_file.get(m)}

    b = baseline_lines(bench)
    q = product_lines(bench)
    print("=" * 78)
    print(bench)
    if b is None:
        print("  baseline union_pair2.json absent (re-run collect.py)")
        continue
    if q is None:
        print("  product reports absent (not collected yet)")
        continue
    tot_only_b = tot_only_q = tot_both = 0
    for f in sorted(canon):
        c = canon[f]
        bb, qq = b & c, q & c
        only_b, only_q, both = bb - qq, qq - bb, bb & qq
        tot_only_b += len(only_b)
        tot_only_q += len(only_q)
        tot_both += len(both)
        print(f"  {f}")
        print(f"      canon {len(c):>3}   both {len(both):>3}   "
              f"only-baseline {len(only_b):>3}   only-product {len(only_q):>3}")
        if only_q:
            print(f"      ONLY PRODUCT lines: {sorted(only_q)}")
        if only_b:
            print(f"      only baseline    : {sorted(only_b)[:20]}")
    print(f"  TOTAL  both {tot_both}   only-baseline {tot_only_b}   "
          f"only-product {tot_only_q}")
