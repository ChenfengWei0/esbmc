#!/usr/bin/env python3
"""Tabulate every recorded run's BACKEND FLAGS against its verdict counts.

WHY THIS EXISTS. `solver-unknown` is a st1inch-only outcome in this corpus, and
the obvious next move is to look for a st1inch-only property of the SOURCE. That
move is only legitimate once the per-run INPUTS have been ruled out, and the
solver backend is a per-run input. If st1inch is also the only benchmark solved
with a particular backend, then "st1inch is the only contract with
solver-unknown" and "z3 is the only backend with solver-unknown" are the same
observation, and no source-level PoC can separate them.

It prints the flags actually present on each recorded command line -- not the
flags a script is believed to pass -- because the command line is the only place
the configuration is recorded per run.

Usage: python3 backend_vs_unknown.py <pathcov-root>
       python3 backend_vs_unknown.py ../pathcov
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

FLAGS = ("--z3", "--boolector", "--bitwuzla", "--cvc", "--cvc5", "--mathsat",
         "--yices", "--smtlib", "--tuple-node-flattener",
         "--tuple-sym-flattener", "--no-slice", "--cov-report-json",
         "--solidity-path-coverage", "--path-cov-arith-resolve")


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else "../pathcov")
    rows = defaultdict(Counter)
    solver_only = defaultdict(Counter)
    for rj in sorted(root.glob("*/runs.jsonl")):
        bench = rj.parent.name
        for line in rj.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            toks = (r.get("cmd") or "").split()
            used = tuple(f for f in FLAGS if f in toks)
            u = r.get("uReasons") or {}
            for key, c in ((used, rows[(bench, used)]),
                           (None, solver_only[tuple(
                               f for f in used if f.startswith(
                                   ("--z3", "--boolector", "--bitwuzla",
                                    "--cvc", "--mathsat", "--yices")))])):
                c["runs"] += 1
                c["F"] += r.get("F", 0) or 0
                c["U"] += r.get("U", 0) or 0
                c["solver-unknown"] += u.get("solver-unknown", 0) or 0
                c["bounded-holds"] += u.get("bounded-holds", 0) or 0
                c["no-report"] += 0 if r.get("reportPresent") else 1

    print(f"{'benchmark':<28} {'flags on the command line':<62} {'runs':>5} "
          f"{'F':>6} {'U':>6} {'unk':>5} {'bhold':>6} {'norep':>6}")
    for (b, f), c in sorted(rows.items()):
        print(f"{b:<28} {(' '.join(f) or '(none)'):<62} {c['runs']:>5} "
              f"{c['F']:>6} {c['U']:>6} {c['solver-unknown']:>5} "
              f"{c['bounded-holds']:>6} {c['no-report']:>6}")

    print(f"\nCOLLAPSED BY SOLVER FLAG ONLY")
    print(f"{'solver flag(s)':<32} {'runs':>5} {'F':>6} {'U':>6} "
          f"{'unk':>5} {'bhold':>6}")
    for f, c in sorted(solver_only.items()):
        print(f"{(' '.join(f) or '(default: no solver flag)'):<32} "
              f"{c['runs']:>5} {c['F']:>6} {c['U']:>6} "
              f"{c['solver-unknown']:>5} {c['bounded-holds']:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
