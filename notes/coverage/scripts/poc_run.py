#!/usr/bin/env python3
"""Run the hand-written PoC set and print one row per contract per bound.

Every contract in notes/coverage/poc/ fits on a screen and every run takes about
a second, so the whole set is a minute. That is the entire justification for the
set existing: a benchmark tells you a number, a contract you wrote tells you
whether the number is right.

The bound is a COLUMN, not a constant. A ten-line contract already showed that
whole-contract enumeration at `--solidity-max-tx 2` reaches paths nothing else
reaches (75% -> 100%), and that the earlier "the transaction dimension buys
nothing" conclusion came from never running the one cell that mattered. So each
contract is run at every bound asked for, and the row shows them side by side.

PROVENANCE IS PRINTED BEFORE ANY NUMBER. A result from a binary that does not
correspond to a commit is not reproducible, and this file has already been used
once against a tree with uncommitted changes in it.

Usage:
    python3 poc_run.py [--tx 1,2] [--only Tiny,P19_ReturnShapes] [--timeout 120]
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
POC = REPO / "notes/coverage/poc"


def sh(cmd, cwd=None, timeout=300):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=cwd, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, _ = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, out


def provenance():
    rc, head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO))
    rc2, dirty = sh(["git", "status", "--porcelain", "--", "src/"],
                    cwd=str(REPO))
    bstat = ESBMC.stat().st_mtime if ESBMC.exists() else 0
    print("## Provenance\n")
    print(f"  HEAD              {head.strip()}")
    print(f"  binary mtime      {time.strftime('%H:%M:%S', time.localtime(bstat))}")
    changed = [ln for ln in dirty.splitlines() if ln.strip()]
    if changed:
        print(f"  ** src/ HAS {len(changed)} UNCOMMITTED CHANGE(S) — these "
              f"results do not correspond to any commit **")
        for ln in changed:
            print(f"      {ln}")
        newest = max((REPO / ln[3:]).stat().st_mtime
                     for ln in changed if (REPO / ln[3:]).exists())
        if newest > bstat:
            print("  ** and a source file is NEWER than the binary, so the "
                  "binary matches neither HEAD nor the tree **")
    else:
        print("  src/ clean")
    print()


def contracts(only):
    out = []
    for p in sorted(POC.glob("*.sol")):
        if only and p.stem not in only:
            continue
        out.append(p)
    return out


def contract_name(path):
    """The contract to verify. Files may declare more than one (a base, a
    library, an interface); the LAST top-level `contract X` is the one under
    test, because that is where the derived/using-for cases put it."""
    name = None
    for ln in path.read_text().splitlines():
        s = ln.strip()
        if s.startswith("contract "):
            name = s.split()[1].split("{")[0].split("(")[0].strip()
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", default="1,2")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memlimit", default="4g")
    a = ap.parse_args()
    bounds = [b.strip() for b in a.tx.split(",") if b.strip()]
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    provenance()

    rows = []
    for sol in contracts(only):
        cname = contract_name(sol)
        if cname is None:
            rows.append((sol.stem, "-", "NO CONTRACT DECLARED", {}))
            continue
        ast = sol.with_suffix(".solast")
        rc, out = sh(["solc", "--ast-compact-json", str(sol)], timeout=120)
        if rc != 0:
            rows.append((sol.stem, cname, f"SOLC FAILED rc={rc}", {}))
            continue
        ast.write_text(out)

        per_bound = {}
        for b in bounds:
            wd = POC / "_runs" / f"{sol.stem}__tx{b}"
            wd.mkdir(parents=True, exist_ok=True)
            for stale in wd.glob("*"):
                if stale.is_file():
                    stale.unlink()
            cmd = [str(ESBMC), str(ast), "--sol", str(sol),
                   "--solidity-path-coverage", "--cov-report-json",
                   "--memlimit", a.memlimit, "--contract", cname,
                   "--solidity-max-tx", b]
            t0 = time.time()
            rc, out = sh(cmd, cwd=str(wd), timeout=a.timeout)
            wall = time.time() - t0
            (wd / "run.log").write_text(out)
            rep = wd / "cov-report.json"
            if not rep.exists():
                per_bound[b] = {"err": f"no report (rc={rc})",
                                "wall": round(wall, 1)}
                continue
            d = json.loads(rep.read_text())
            s = d.get("summary", {})
            ur = s.get("U_reasons", {})
            per_bound[b] = {
                "paths": s.get("paths_total"), "F": s.get("F_feasible_with_ce"),
                "U": s.get("U_undecided"),
                "bh": ur.get("bounded-holds"),
                "pct": round(s.get("percentage", 0), 1),
                "partial": d.get("partial", s.get("partial")),
                "wall": round(wall, 1),
            }
        rows.append((sol.stem, cname, None, per_bound))

    print("## Results — whole contract, no --focus-function\n")
    head = "| contract | unit-under-test |"
    for b in bounds:
        head += f" tx={b} paths | tx={b} F | tx={b} bh | tx={b} % | tx={b} s |"
    print(head)
    print("|" + "---|" * (2 + 5 * len(bounds)))
    for stem, cname, err, per in rows:
        if err:
            print(f"| `{stem}` | {cname} | " + " | ".join(
                [err] + ["-"] * (5 * len(bounds) - 1)) + " |")
            continue
        cells = []
        for b in bounds:
            r = per.get(b, {})
            if "err" in r:
                cells += [r["err"], "-", "-", "-", str(r.get("wall", "-"))]
            else:
                mark = " **PARTIAL**" if r.get("partial") else ""
                cells += [str(r["paths"]) + mark, str(r["F"]), str(r["bh"]),
                          str(r["pct"]), str(r["wall"])]
        print(f"| `{stem}` | {cname} | " + " | ".join(cells) + " |")

    print("\n`bh` = bounded-holds: the run asked and the path held at this "
          "exploration. It is NOT a proof of infeasibility -- `I` is never "
          "emitted -- so a non-zero `bh` is where a genuinely impossible path "
          "and an unreached one are indistinguishable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
