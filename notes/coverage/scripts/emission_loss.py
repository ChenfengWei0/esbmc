#!/usr/bin/env python3
"""Which decisions did the ENUMERATION walk that the EMITTED TESTS do not run?

The shortfall against the bar is two independent losses and they have different
causes, so they need separate lists:

    bar         branch coverage                              (locked JSON)
    enumerated  decisions our witnessed (F) paths walk        (cov-report.json)
    emitted     decisions our generated tests execute         (forge lcov)

`bar - enumerated` is a search/bound question. `enumerated - emitted` is pure
RECONSTRUCTION FIDELITY: the path was witnessed with a counterexample, so the
values exist in the model and the test simply did not carry them. That second
list is the one that should drive emitter work -- it is line numbers, not a
theory, and the last two emitter changes were guesses that measurement refuted.

Both inputs are already on disk; this only joins them. Reads only.

Usage: emission_loss.py <bench-key>
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
sys.path.insert(0, str(REPO / "notes/coverage/scripts"))
from collect import BENCHES, INPUTS, is_project_own_marker  # noqa: E402
from ast_decisions import canonical_decisions  # noqa: E402

PATHCOV = REPO / "notes/coverage/pathcov"
ROUNDTRIP = REPO / "notes/coverage/forge_roundtrip"


def enumerated_lines(bench):
    rdir = PATHCOV / bench / "reports"
    if not rdir.exists() or not any(rdir.glob("*.json")):
        return None
    out = set()
    for p in sorted(rdir.glob("*.json")):
        d = json.loads(p.read_text())
        for c in d.get("claims", []):
            if c.get("status") != "F":
                continue
            for e in c.get("decisions") or []:
                if "unrecorded_prefix_enc" in e or e.get("synthetic_abi_gate"):
                    continue
                ln = e.get("line")
                if isinstance(ln, int) and ln > 0:
                    out.add(ln)
    return out


def emitted_lines(bench, flat_name):
    lcov = ROUNDTRIP / bench / "lcov.info"
    if not lcov.exists():
        return None
    out, cur = set(), None
    for raw in lcov.read_text().splitlines():
        if raw.startswith("SF:"):
            cur = raw[3:]
        elif raw.startswith("BRDA:") and cur and flat_name in cur:
            parts = raw[5:].split(",")
            if parts[3] != "-" and int(parts[3]) > 0:
                out.add(int(parts[0]))
    return out


def main():
    bench = sys.argv[1]
    if bench not in BENCHES:
        sys.exit(f"unknown bench: {bench}")
    flat_rel, _primary, _solc, project = BENCHES[bench]
    flat = INPUTS / flat_rel
    by_file, blocks = canonical_decisions(flat)
    own = sorted({m for _s, _e, m in blocks
                  if is_project_own_marker(m, project)})
    canon = {m: by_file.get(m, set()) for m in own if by_file.get(m)}

    enum = enumerated_lines(bench)
    emit = emitted_lines(bench, flat.name)
    if enum is None:
        sys.exit(f"{bench}: no path-coverage reports -- run pathcov_collect.py")
    if emit is None:
        sys.exit(f"{bench}: no forge lcov -- run forge_roundtrip.py")

    print("=" * 78)
    print(f"{bench}: what the enumeration walked but the emitted tests do not")
    print("=" * 78)
    tot_e = tot_m = tot_lost = 0
    for f in sorted(canon):
        c = canon[f]
        ee, mm = enum & c, emit & c
        lost = sorted(ee - mm)
        gained = sorted(mm - ee)
        tot_e += len(ee)
        tot_m += len(mm)
        tot_lost += len(lost)
        print(f"  {f}")
        print(f"      canon {len(c):>3}   enumerated {len(ee):>3}   "
              f"emitted {len(mm):>3}   LOST IN EMISSION {len(lost):>3}")
        if lost:
            print(f"      lost lines   : {lost}")
        if gained:
            # A test executing a decision the witnessed path never walked is not
            # a bonus: it means the emitted call went somewhere the
            # counterexample did not, so the provenance link is broken for it.
            print(f"      EMITTED-ONLY : {gained}   <-- the test went where the "
                  f"counterexample did not")
    print(f"  TOTAL enumerated {tot_e}, emitted {tot_m}, "
          f"lost in emission {tot_lost}")


if __name__ == "__main__":
    main()
