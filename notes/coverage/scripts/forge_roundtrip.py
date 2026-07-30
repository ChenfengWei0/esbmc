#!/usr/bin/env python3
"""END-TO-END: what does the GENERATED Foundry suite actually cover, per forge?

WHY THIS EXISTS, and why the gate is not enough. `branch_gate.py` compares which
canonical decisions the VERIFIER's exploration touched. That is a proxy. The
deliverable of this method is a Foundry test suite, and the number that supports
the claim is what THAT SUITE covers, measured by the same tool the projects'
own suites are measured with (forge -> lcov). Those two questions can come apart
in both directions: a path can be witnessed by a counterexample the emitter
cannot render (proxy counts it, forge does not), and a rendered test can execute
code the witnessed path never identified (forge counts it, proxy does not).

WHAT IT NEEDS, and what it deliberately does NOT need. It does NOT need the
project's checked-out repository: the project's own sources are inside the flat
(the `// File <path>` blocks), and the projects' own coverage is already
recorded in notes/coverage/data/esbmc_<bench>.json as `native`. It needs forge,
forge-std, and the flat. That matters because those repositories are gone.

WHAT IT MEASURES. The same two operations collect.py applies to BOTH of its
columns, so the third column is commensurable with them by construction:
    reached = { lcov BRDA lines with a non-zero arm }  n  canonical decision lines
    capped per file at that file's canonical decision count
The lcov lines are ORIGINAL-file lines of the flat, and the canonical decision
lines are flat lines, so no mapping is needed -- the flat IS the compilation
unit forge sees.

Usage:  forge_roundtrip.py <bench-key> [--timeout S] [--keep]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect as base  # noqa: E402
from ast_decisions import canonical_decisions  # noqa: E402

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
INPUTS = REPO / "notes/coverage/inputs"
FORGE_STD = (REPO / "notes/coverage-comparison/_foundry_roundtrip/aqua_forge"
             / "lib" / "forge-std")
OUT = REPO / "notes/coverage/forge_roundtrip"

FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
solc = "0.8.30"
"""


def run(cmd, timeout, cwd=None):
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout, cwd=cwd)
        return cp.returncode, cp.stdout + cp.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        def dec(x):
            if x is None:
                return ""
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else x
        return -1, dec(e.stdout) + dec(e.stderr), time.time() - t0


def emit_tests(bench, flat, solast, primary, project, proj, timeout, journal):
    """One esbmc run per callable, each in its own CWD (the emitted filename is
    hardcoded), each renamed so the suite can hold all of them at once."""
    callables = base.enumerate_own_callable_functions(flat, project)
    tests, recs = [], []
    for i, (cname, fname, ckind) in enumerate(callables, 1):
        tag = f"{cname}__{fname}"
        cwd = proj / "_gen" / tag
        cwd.mkdir(parents=True, exist_ok=True)
        for stale in cwd.glob("*"):
            stale.unlink()
        cmd = [str(ESBMC), str(solast), "--sol", str(flat),
               "--solidity-path-coverage", "--solidity-max-tx", "1",
               "--generate-foundry-testcase", "--memlimit", "8g",
               "--result-only"]
        if ckind == "library":
            cmd += ["--function", fname]
        else:
            cmd += ["--contract", primary, "--focus-function", fname]
        print(f"  [{i}/{len(callables)}] {tag}", flush=True)
        rc, out, wall = run(cmd, timeout, cwd=str(cwd))
        (cwd / "run.log").write_text(out)

        produced = sorted(cwd.glob("*.cov.t.sol"))
        rec = {"tag": tag, "exitCode": rc, "wallSeconds": round(wall, 2),
               "killed": rc == -1, "emitted": [p.name for p in produced]}
        # The collector's own classification, printed rather than inferred
        # later. "no test emitted" has at least four distinct causes and they
        # are not interchangeable.
        if "is ambiguous" in out:
            rec["ambiguousEntryName"] = True
        if "No verification targets" in out:
            rec["noVerificationTargets"] = True
        if "are internal/private and are therefore not units" in out:
            rec["nonUnitFunctionsPresent"] = True
        m = re.search(r"Generated Foundry coverage test with (\d+) case", out)
        if m:
            rec["cases"] = int(m.group(1))
        for p in produced:
            txt = p.read_text()
            # Every emitted file declares the same contract name and imports the
            # flat as a sibling. Both have to change or the suite cannot hold
            # more than one unit's tests.
            stem = p.name[: -len(".cov.t.sol")]
            newc = f"{stem}CovTest_{cname}_{fname}"
            txt = txt.replace(f"contract {stem}CovTest is Test",
                              f"contract {newc} is Test")
            txt = txt.replace(f'import {{{stem}}} from "./{flat.name}"',
                              f'import {{{stem}}} from "../src/{flat.name}"')
            txt = re.sub(r'from "\./', 'from "../src/', txt)
            dest = proj / "test" / f"{newc}.t.sol"
            dest.write_text(txt)
            tests.append(dest.name)
        recs.append(rec)
        with journal.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return tests, recs


def parse_lcov_reached(lcov_path):
    """Original-file lines with at least one BRDA arm taken, per source file."""
    by_file = defaultdict(set)
    cur = None
    for raw in lcov_path.read_text().splitlines():
        if raw.startswith("SF:"):
            cur = raw[3:]
        elif raw.startswith("BRDA:") and cur is not None:
            parts = raw[5:].split(",")
            ln, count = int(parts[0]), parts[3]
            if count != "-" and int(count) > 0:
                by_file[cur].add(ln)
    return by_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if a.bench not in base.BENCHES:
        sys.exit(f"unknown bench: {a.bench}")
    flat_rel, primary, _solc, project = base.BENCHES[a.bench]
    flat = INPUTS / flat_rel
    solast = INPUTS / (flat_rel + ".solast")
    if not solast.exists():
        sys.exit(f"missing AST {solast} (run collect.py first)")
    if not FORGE_STD.exists():
        sys.exit(f"missing forge-std at {FORGE_STD}")
    if shutil.which("forge") is None:
        sys.exit("forge is not on PATH")

    proj = OUT / a.bench
    if proj.exists() and not a.keep:
        shutil.rmtree(proj)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "test").mkdir(parents=True, exist_ok=True)
    (proj / "lib").mkdir(parents=True, exist_ok=True)
    (proj / "foundry.toml").write_text(FOUNDRY_TOML)
    shutil.copy(flat, proj / "src" / flat.name)
    if not (proj / "lib" / "forge-std").exists():
        os.symlink(FORGE_STD, proj / "lib" / "forge-std")

    journal = proj / "emit.jsonl"
    if journal.exists():
        journal.unlink()
    print(f"=== {a.bench}: emitting tests ===", flush=True)
    tests, recs = emit_tests(a.bench, flat, solast, primary, project, proj,
                             a.timeout, journal)
    emitted = len(tests)
    print(f"=== emitted {emitted} test file(s) from {len(recs)} run(s) ===",
          flush=True)
    if emitted == 0:
        # NOT a coverage of zero. Refused rather than reported, because a
        # zero-test suite and a suite whose tests all fail produce the same
        # lcov and mean opposite things.
        sys.exit("no test file was emitted at all -- there is nothing to "
                 "measure, and reporting 0% would say the generated suite "
                 "covers nothing when in fact none was generated")

    print("=== forge build ===", flush=True)
    rc, out, _ = run(["forge", "build"], 900, cwd=str(proj))
    (proj / "build.log").write_text(out)
    if rc != 0:
        sys.exit(f"forge build failed (rc={rc}); see {proj/'build.log'}")

    print("=== forge coverage ===", flush=True)
    rc, out, _ = run(["forge", "coverage", "--report", "lcov",
                      "--report-file", "lcov.info"], 3600, cwd=str(proj))
    (proj / "coverage.log").write_text(out)
    lcov = proj / "lcov.info"
    if not lcov.exists():
        sys.exit(f"forge coverage produced no lcov (rc={rc}); see "
                 f"{proj/'coverage.log'}")

    by_file, blocks = canonical_decisions(flat)
    own = sorted({m for _s, _e, m in blocks
                  if base.is_project_own_marker(m, project)})
    canon = {m: by_file.get(m, set()) for m in own if by_file.get(m)}

    reached = parse_lcov_reached(lcov)
    # forge reports the flat under its project-relative path; the flat IS the
    # compilation unit, so every canonical decision line is a line of it.
    flat_lines = set()
    for sf, lines in reached.items():
        if flat.name in sf:
            flat_lines |= lines

    locked = json.loads(
        (REPO / "notes/coverage/data" / f"esbmc_{a.bench}.json").read_text())
    p2 = locked["per_function"]["total"]

    print()
    print("=" * 78)
    print(f"{a.bench}: FORGE coverage of the GENERATED suite")
    print("=" * 78)
    tot_c = tot_ours = 0
    for f in sorted(canon):
        c = canon[f]
        hit = min(len(flat_lines & c), len(c))
        tot_c += len(c)
        tot_ours += hit
        print(f"  {hit:>4} / {len(c):<4}  {f}")
    print(f"  TOTAL {tot_ours} / {tot_c}")
    print()
    print(f"  bar    (branch coverage, esbmcReached) : {p2['esbmcReached']}")
    print(f"  native (project's own suite, lcov)     : {p2['nativeReached']}")
    print(f"  ours   (generated suite, forge lcov)   : {tot_ours}")
    print()
    print(f"  emitted test files : {emitted}")
    print(f"  runs               : {len(recs)}")
    print(f"  killed by timeout  : {sum(1 for r in recs if r['killed'])}")
    print(f"  ambiguous name     : "
          f"{sum(1 for r in recs if r.get('ambiguousEntryName'))}")
    print(f"  project            : {proj}")


if __name__ == "__main__":
    main()
