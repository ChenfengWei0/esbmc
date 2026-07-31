#!/usr/bin/env python3
"""Read the (unwind x strategy) matrix off the REPORTS, not off exit codes.

For each cell directory produced by unwind_vs_strategy.sh, print one row:
  cell | rc | wall | F | bounded-holds | not-solved | unit-not-entered |
  decision_steps | report bound.unwind | #truncated-loop warning | k steps run

The exit code is deliberately NOT the verdict -- it is known not to be
comparable across bounding strategies (notes/coverage/option-matrix-round1.md
result 3). Everything except rc/wall comes from cov-report.json; the warning
and k-step columns come from the run log, which is the only place the
"Coverage may be UNDER-REPORTED" line and the per-k "Checking base case, k = N"
lines exist.

usage: unwind_vs_strategy_summarize.py <matrix-dir>
"""
import json
import sys
from pathlib import Path

WARN = "Coverage may be UNDER-REPORTED"
UNWIND_ASSERT = "unwinding assertion loop"


def scan_log(p: Path):
    """Return (warn_lines, k_steps, not_unwinding_loops, unwind_assert_claims)."""
    warn = []
    ks = []
    notunw = set()
    uassert = 0
    if not p.exists():
        return warn, ks, notunw, uassert
    with p.open(errors="replace") as f:
        for line in f:
            if WARN in line:
                warn.append(line.strip())
            if "Checking base case, k =" in line or \
               "Checking inductive step, k =" in line or \
               "Checking forward condition, k =" in line:
                ks.append(line.strip())
            if line.lstrip().startswith("Not unwinding loop"):
                parts = line.split()
                # "Not unwinding loop N iteration M   <loc>"
                try:
                    notunw.add(parts[3])
                except IndexError:
                    pass
            if UNWIND_ASSERT in line:
                uassert += 1
    return warn, ks, notunw, uassert


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <matrix-dir>")
    root = Path(argv[1])
    cells = sorted(d for d in root.iterdir() if d.is_dir())
    hdr = (f"{'cell':<22} {'rc':>3} {'wall':>6} {'paths':>6} {'F':>3} "
           f"{'bh':>4} {'nso':>5} {'nsr':>4} "
           f"{'une':>5} {'dsteps':>6} {'rep.unwind':>10} {'trunc-warn':>10} "
           f"{'ksteps':>6} {'notunw-loops'}")
    print(hdr)
    print("-" * len(hdr))
    for c in cells:
        meta = (c / "meta.txt")
        rc = wall = "?"
        if meta.exists():
            t = meta.read_text().split()
            for tok in t:
                if tok.startswith("rc="):
                    rc = tok[3:]
                if tok.startswith("wall="):
                    wall = tok[5:]
        rep = c / "cov-report.json"
        F = bh = nso = nsr = une = ds = bu = pt = "-"
        if rep.exists():
            try:
                d = json.loads(rep.read_text())
                s = d.get("summary", {})
                pt = s.get("paths_total", "-")
                F = s.get("F_feasible_with_ce", "-")
                ur = s.get("U_reasons", {})
                bh = ur.get("bounded-holds", "-")
                nso = ur.get("named-obstacle", "-")
                nsr = ur.get("not-solved-this-run", "-")
                une = ur.get("unit-not-entered", "-")
                ds = s.get("decision_sequences", {}).get("decision_steps", "-")
                bu = s.get("bound", {}).get("unwind", "-")
            except Exception as e:  # a truncated/absent report is a result
                F = f"ERR:{e.__class__.__name__}"
        warn, ks, notunw, uassert = scan_log(c / "run.log")
        kdesc = f"{len(ks)}"
        print(f"{c.name:<22} {rc:>3} {wall:>6} {str(pt):>6} {str(F):>3} "
              f"{str(bh):>4} {str(nso):>5} "
              f"{str(nsr):>4} {str(une):>5} {str(ds):>6} {str(bu):>10} "
              f"{len(warn):>10} {kdesc:>6} {','.join(sorted(notunw))}")
    print()
    for c in cells:
        warn, ks, notunw, uassert = scan_log(c / "run.log")
        if warn:
            # The warning text is identical in every cell; print it ONCE in
            # full, then only the per-cell count. Printing it per cell buries
            # the k-phase lines, which are the discriminating column.
            print(f"# {c.name} truncation warnings: {len(warn)}")
        if uassert:
            print(f"# {c.name} 'unwinding assertion loop' occurrences: {uassert}")
        if ks:
            print(f"# {c.name} k phases: {ks[0]} ... {ks[-1]} ({len(ks)} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
