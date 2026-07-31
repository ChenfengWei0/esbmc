#!/usr/bin/env python3
"""The invocation matrix: run every option cell and record what it actually did.

WHY THIS EXISTS. The external invocation of `--solidity-path-coverage` was
treated as settled after ONE cell was made to run: `--contract C
--focus-function f --solidity-max-tx 1`, no slicing flag, no simplification
flag, no bounding strategy. Every corpus number so far comes from that single
cell. The gate result (0 of 5 benchmarks) may therefore be measuring the cell
rather than the method, and nothing in any table distinguishes the two.

A cell is not judged by whether it "works". Each run records six things, and
three of them are ways a cell can look fine and be wrong:

  exit / report      did it produce anything at all
  paths, units       what it ENUMERATED (independent of what it solved)
  F                  how many paths got a counterexample -- the deliverable
  U by reason        WHY the rest did not, which is the diagnosis
  wall, peak RSS     what the cell costs
  the death line     for a cell that died, the last non-repetitive line

CELLS ARE NOT ASSUMED LEGAL. Several combinations abort by design
(`--coverage-multi-tx` with `--solidity-max-tx`; `--coverage-multi-tx` without a
bounding strategy). Those are RUN anyway and their abort message is recorded as
the cell's result, because "this is illegal" is a finding that belongs in the
table rather than a reason to leave a hole in it.

RESOURCE RULES, not negotiable (this machine has been taken down once):
  * strictly serial -- one esbmc at a time;
  * every run inside `timeout` AND `--memlimit`;
  * start_new_session + killpg, because subprocess timeout kills only the
    direct child and an orphaned esbmc keeps its memory.

Usage:
    python3 option_matrix.py --flat <flat.sol> --contract C [--focus f]
                             [--timeout S] [--memlimit 8g] [--out DIR]
                             [--only DIM=VAL ...]
"""
import argparse
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"

# --------------------------------------------------------------------------
# The dimensions. Each value is (label, extra argv).
# --------------------------------------------------------------------------
SCOPE = [
    ("focus", None),        # filled in from --focus at runtime
    ("whole", []),
]

TX = [
    ("tx1", ["--solidity-max-tx", "1"]),
    ("tx2", ["--solidity-max-tx", "2"]),
    # `--solidity-max-tx 0` is NOT the unbounded setting under coverage: bound 0
    # emits `while(nondet){body}` and coverage then rewrites the back-edge to a
    # SKIP, leaving ONE transaction. It is included precisely so the table shows
    # that it equals tx1 rather than leaving the belief untested.
    ("tx0", ["--solidity-max-tx", "0"]),
    # The only configurations that keep the back edge alive. Each REQUIRES a
    # global bounding strategy or the tool aborts, so the strategy is part of
    # the cell rather than a separate dimension.
    ("multitx-unwind", ["--coverage-multi-tx", "--unwind", "4"]),
    ("multitx-incr", ["--coverage-multi-tx", "--incremental-bmc"]),
    ("multitx-kind", ["--coverage-multi-tx", "--k-induction"]),
    # Deliberately illegal, to pin the abort in the table.
    ("multitx-nostrategy", ["--coverage-multi-tx"]),
    ("multitx-plus-maxtx", ["--coverage-multi-tx", "--solidity-max-tx", "1"]),
]

SLICE = [
    ("slice-default", []),
    ("no-slice", ["--no-slice"]),
]

SIMPLIFY = [
    ("simplify-default", []),
    ("no-simplify", ["--no-simplify"]),
]

BASE = ["--solidity-path-coverage", "--cov-report-json",
        "--path-cov-max-goals", "10000"]

INSTR_RE = re.compile(
    r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")


def _killpg(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_cell(cmd, timeout, workdir):
    """One esbmc run, its own directory, its own process group.

    The report filename is hardcoded `cov-report.json` in the CURRENT
    directory, so two cells sharing a directory silently overwrite each other.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in workdir.glob("*"):
        if stale.is_file():
            stale.unlink()

    t0 = time.time()
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    killed = False
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=str(workdir), start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        killed = True
        _killpg(p)
        try:
            out, _ = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
        rc = -1
    finally:
        _killpg(p)

    wall = time.time() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    (workdir / "run.log").write_text(out or "")

    rec = {"exit": rc, "killed": killed, "wall": round(wall, 1),
           "peakChildKB": max(after, before)}

    m = INSTR_RE.search(out or "")
    if m:
        rec["paths"] = int(m.group(1))
        rec["units"] = int(m.group(2))

    # THE DEATH LINE. For a cell that died, the useful line is the last one that
    # is not part of a repeated shape -- a 1.1 MB log is mostly `Unwinding loop`
    # and `PASSED` lines, and the abort message sits under them. Shapes are
    # counted over the WHOLE log rather than a tail being guessed at.
    lines = (out or "").splitlines()
    shapes = Counter(re.sub(r"\d+", "#", ln) for ln in lines)
    rare = [ln for ln in lines if shapes[re.sub(r"\d+", "#", ln)] < 3 and ln.strip()]
    if rc != 0 and rare:
        rec["lastRareLines"] = rare[-4:]

    rep = workdir / "cov-report.json"
    rec["report"] = rep.exists()
    if rep.exists():
        try:
            d = json.loads(rep.read_text())
        except ValueError as e:
            rec["reportParseError"] = str(e)
            return rec
        s = d.get("summary", {})
        rec["pathsTotal"] = s.get("paths_total")
        rec["F"] = s.get("F_feasible_with_ce")
        rec["U"] = s.get("U_undecided")
        rec["uReasons"] = s.get("U_reasons")
        rec["covered"] = s.get("covered")
        rec["bound"] = s.get("bound")
        # THE INPUTS QUESTION. Slicing is on by default with an exemption list;
        # whether a counterexample's INPUT values survive it is the thing
        # `--no-slice` exists to test here, so it is measured per cell rather
        # than argued about: how many F claims carry a non-empty `inputs`.
        f_with_inputs = 0
        f_total = 0
        for c in d.get("claims", []):
            if c.get("status") != "F":
                continue
            f_total += 1
            if c.get("inputs"):
                f_with_inputs += 1
        rec["fWithInputs"] = f_with_inputs
        rec["fClaims"] = f_total
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flat", required=True)
    ap.add_argument("--contract")
    ap.add_argument("--focus")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--memlimit", default="8g")
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", action="append", default=[],
                    help="restrict a dimension, e.g. --only tx=tx1,multitx-unwind")
    a = ap.parse_args()

    flat = Path(a.flat).resolve()
    solast = Path(str(flat) + ".solast")
    if not solast.exists():
        solast = flat.with_suffix(".sol.solast")
    if not solast.exists():
        sys.exit(f"missing AST beside the flat: {solast}")

    scope = list(SCOPE)
    scope[0] = ("focus", ["--focus-function", a.focus] if a.focus else None)
    if a.focus is None:
        # A MISSING INPUT MUST NOT SILENTLY BECOME A NARROWER MATRIX. Dropping
        # the focus row when --focus is absent would print a table that looks
        # complete and silently answers a different question.
        scope = [s for s in scope if s[0] != "focus"]
        print("NOTE: no --focus given, the `focus` scope row is NOT in this "
              "matrix; the table below covers whole-contract cells only")

    restrict = {}
    for spec in a.only:
        dim, _, vals = spec.partition("=")
        restrict[dim] = set(vals.split(","))

    def keep(dim, label):
        return dim not in restrict or label in restrict[dim]

    out_dir = Path(a.out) if a.out else (
        REPO / "notes/coverage/option_matrix" / flat.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    journal = out_dir / "cells.jsonl"
    done = {}
    if journal.exists():
        for ln in journal.read_text().splitlines():
            if ln.strip():
                r = json.loads(ln)
                done[r["cell"]] = r

    cells = []
    for sl, sa in scope:
        if not keep("scope", sl):
            continue
        for tl, ta in TX:
            if not keep("tx", tl):
                continue
            for cl, ca in SLICE:
                if not keep("slice", cl):
                    continue
                for pl, pa in SIMPLIFY:
                    if not keep("simplify", pl):
                        continue
                    cells.append((f"{sl}|{tl}|{cl}|{pl}",
                                  (sa or []) + ta + ca + pa))

    print(f"# option matrix on {flat.name}\n\n{len(cells)} cell(s), serial, "
          f"timeout {a.timeout}s, memlimit {a.memlimit}\n", flush=True)

    for i, (cell, extra) in enumerate(cells, 1):
        if cell in done:
            print(f"[{i}/{len(cells)}] {cell}  (already done)", flush=True)
            continue
        cmd = [str(ESBMC), str(solast), "--sol", str(flat)] + BASE + \
              ["--memlimit", a.memlimit]
        if a.contract:
            cmd += ["--contract", a.contract]
        cmd += extra
        print(f"[{i}/{len(cells)}] {cell}", flush=True)
        rec = run_cell(cmd, a.timeout, out_dir / "work" / cell.replace("|", "__"))
        rec["cell"] = cell
        rec["cmd"] = " ".join(cmd)
        with journal.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        done[cell] = rec
        print(f"    exit={rec['exit']} report={rec['report']} "
              f"paths={rec.get('paths')} F={rec.get('F')} "
              f"wall={rec['wall']}s", flush=True)

    print("\n## Result\n")
    print("| cell | exit | report | paths | units | F | U | F w/ inputs | "
          "wall s | peak child MB |")
    print("|" + "---|" * 10)
    for cell, _e in cells:
        r = done.get(cell)
        if r is None:
            print(f"| `{cell}` | - | - | - | - | - | - | - | - | - |")
            continue
        print(f"| `{cell}` | {r['exit']}{' KILLED' if r['killed'] else ''} | "
              f"{r['report']} | {r.get('paths', '-')} | {r.get('units', '-')} | "
              f"{r.get('F', '-')} | {r.get('U', '-')} | "
              f"{r.get('fWithInputs', '-')}/{r.get('fClaims', '-')} | "
              f"{r['wall']} | {round(r['peakChildKB'] / 1024)} |")

    print("\n## Cells that did not exit 0, with their last distinctive lines\n")
    for cell, _e in cells:
        r = done.get(cell)
        if not r or r["exit"] == 0:
            continue
        print(f"- `{cell}` exit={r['exit']}"
              f"{' (killed by outer timeout)' if r['killed'] else ''}")
        for ln in r.get("lastRareLines", []):
            print(f"      {ln[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
