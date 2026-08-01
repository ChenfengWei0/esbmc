#!/usr/bin/env python3
"""Stage-2 across the PoC SET: the certification rate on the contracts we wrote.

WHY IT DID NOT EXIST. `certify_all.py` sweeps the BENCHMARKS table, so every
certification number this project has ever quoted is about the four real
contracts. The PoC set -- 35 hand-written contracts, each isolating one shape
that has actually failed here -- has never been certified at all. That is the
set where a certification rate is INTERPRETABLE: on a real contract "not
certified" mixes the method's limits with the contract's difficulty, while on a
PoC the contract is one shape and nothing else.

The two numbers are different questions and both are worth having:

    test conversion rate   poc_funnel.py:  paths -> F -> rendered -> GREEN
                           i.e. does a witnessed path become a test that is
                           green on the unmodified contract
    CE certification rate  this file:      of the witnessed paths, how many get
                           a certified non-trivial input REGION rather than the
                           single counterexample point

A path can convert and not certify (the test exists, but pinned to one point)
and it can certify and not convert (the region is proved, the emitter cannot
render the type). Quoting one for the other is the mistake this file exists to
make impossible.

NOTHING IS COPIED FROM certify_all.py -- its `parse_driver`, `bucket` and
`binary_identity` are IMPORTED. A second copy of a parser is a second thing that
can drift from the driver's output format, and this repository has already paid
for exactly that.

Usage:  certify_poc.py [--only NAME] [--timeout S] [--out FILE] [--redo]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import certify_all as ca  # noqa: E402  -- parser/bucket/identity, not copied

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
POC = REPO / "notes/coverage/poc"
DRIVER = REPO / "scripts/solidity_path_generalise.py"
OUT = REPO / "notes/coverage/certify/poc_results.jsonl"


def units_of(solast_path):
    """Public/external functions of the PoC, read from its own AST.

    Not from a name convention and not from the enumeration run: the AST is the
    only place that says what is a UNIT (a unit is public or external), and
    reading it here means the unit set does not depend on a run finishing.
    """
    raw = solast_path.read_text()
    start = raw.find("\n{")
    ast = json.loads(raw[start + 1:] if start >= 0 else raw)
    out = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
            return
        if not isinstance(n, dict):
            return
        if n.get("nodeType") == "FunctionDefinition" and \
                n.get("visibility") in ("public", "external") and \
                n.get("kind") != "constructor" and n.get("name"):
            out.append(n["name"])
        for k, v in n.items():
            if isinstance(v, (dict, list)):
                walk(v)

    walk(ast)
    # dedup, order preserved: an overloaded name is one --unit argument
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="one PoC stem, e.g. D10_WrapNotPanic")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per DRIVER invocation, i.e. one unit's whole loop")
    ap.add_argument("--memlimit", default="8g")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workdir", default="/tmp/certify_poc")
    a = ap.parse_args()

    ident = ca.binary_identity()
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Path(a.workdir).mkdir(parents=True, exist_ok=True)

    # Same two gates certify_all.py now has, for the same two reasons: a journal
    # that resumes across builds quotes a dead build's numbers, and a --redo
    # that appends leaves two rows with one key.
    if a.redo and out_path.exists():
        keep = str(out_path) + ".superseded"
        os.replace(str(out_path), keep)
        print(f"[poc] --redo moved the previous results to {keep}", flush=True)

    done = set()
    if out_path.exists():
        stale = []
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            done.add((r.get("poc"), r.get("unit")))
            if r.get("binary") != ident:
                stale.append((r.get("poc"), r.get("unit"), r.get("binary")))
        if stale:
            print(f"[poc] REFUSING to resume: {len(stale)} of {len(done)} "
                  f"record(s) came from a DIFFERENT binary.\n  now: {ident}")
            for p, u, was in stale[:5]:
                print(f"  was: {p}/{u} -> {was}")
            print("Re-run with --redo, or move the file aside to keep it.")
            return 1

    sols = sorted(POC.glob("*.sol"))
    if a.only:
        sols = [p for p in sols if p.stem == a.only]
        if not sols:
            sys.exit(f"no PoC named {a.only}")

    print(f"[poc] {len(sols)} PoC contract(s), --memlimit {a.memlimit}, "
          f"--timeout {a.timeout}s per unit, serial", flush=True)

    for i, sol in enumerate(sols, 1):
        solast = sol.with_suffix(".solast")
        if not solast.exists():
            rc = subprocess.run(["solc", "--ast-compact-json", str(sol)],
                                capture_output=True, text=True, timeout=120)
            if rc.returncode != 0:
                print(f"[{i}/{len(sols)}] {sol.stem}: DOES NOT COMPILE",
                      flush=True)
                continue
            solast.write_text(rc.stdout)
        try:
            units = units_of(solast)
        except (ValueError, OSError) as e:
            print(f"[{i}/{len(sols)}] {sol.stem}: AST UNREADABLE ({e})",
                  flush=True)
            continue
        if not units:
            print(f"[{i}/{len(sols)}] {sol.stem}: no public/external unit",
                  flush=True)
            continue
        print(f"[{i}/{len(sols)}] {sol.stem}: {len(units)} unit(s): "
              f"{', '.join(units)}", flush=True)
        for unit in units:
            if (sol.stem, unit) in done:
                print(f"    {unit} — already recorded", flush=True)
                continue
            uwd = Path(a.workdir) / sol.stem / unit
            uwd.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(DRIVER), "--esbmc", str(ESBMC),
                   "--sol", str(sol), "--ast", str(solast),
                   "--contract", sol.stem, "--unit", unit, "--focus",
                   "--level0", "--memlimit", a.memlimit,
                   "--timeout", str(min(a.timeout, 180)),
                   "--workdir", str(uwd)]
            t0 = time.time()
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True)
            try:
                out, _ = p.communicate(timeout=a.timeout)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                ca._killpg(p)
                try:
                    out, _ = p.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    out = ""
                out = (out or "") + f"\n[run] TIMEOUT after {a.timeout}s\n"
                rc = 124
            finally:
                ca._killpg(p)
            wall = time.time() - t0
            rec = ca.parse_driver(out)
            rec.update({"poc": sol.stem, "unit": unit,
                        "bucket": ca.bucket(rec, rc, out),
                        "wall_s": round(wall, 1), "exit": rc,
                        "binary": ident})
            (uwd / "driver.log").write_text(out)
            with out_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
            print(f"    {unit}: {rec['bucket']}, "
                  f"{len(rec['certified'])} certified / "
                  f"{len(rec['not_certified'])} not, "
                  f"{len(rec['coords'])} free coordinate(s), {wall:.0f}s",
                  flush=True)

    # ---- the rate, computed here so it cannot be quoted without its buckets --
    recs = [json.loads(l) for l in out_path.read_text().splitlines()
            if l.strip()]
    from collections import Counter
    buckets = Counter(r["bucket"] for r in recs)
    cert_paths = sum(len(r.get("certified") or {}) for r in recs)
    notcert_paths = sum(len(r.get("not_certified") or {}) for r in recs)
    decided = cert_paths + notcert_paths
    print("\n## PoC certification\n")
    print("| bucket | units |")
    print("|---|---|")
    for b, n in sorted(buckets.items()):
        print(f"| {b} | {n} |")
    print(f"\nunits total {len(recs)}")
    print(f"paths certified {cert_paths}, not certified {notcert_paths}")
    if decided:
        print(f"CE certification rate = {cert_paths}/{decided} = "
              f"{100.0 * cert_paths / decided:.1f}% of the paths that got a "
              f"verdict")
    print("\nA KILLED or NO-PATH unit contributes NOTHING to the rate rather "
          "than a zero: a budget outcome filed as a search result is the "
          "failure-as-result pattern this corpus keeps hitting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
