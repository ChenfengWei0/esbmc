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

# `via_ir` + optimizer are needed for the larger flats, and the failure they fix
# is NOT an emitter defect: farming's flat trips solc's "Stack too deep" at
# `farming__FarmingPool.flat.sol:3777`, inside the CONTRACT, before any generated
# test is even looked at. Without this the round-trip reports "forge build
# failed" on farming and an unwary reader files it against the generator.
#
# `forge coverage` then needs `--ir-minimum` (see the coverage invocation), which
# is why the two are set together rather than one at a time.
FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
# NO `solc = ` PIN. It was hardcoded to 0.8.30, and EscrowSrc's flat pins
# `=0.8.23`, so forge refused the whole project with "No solc version exists
# that matches the version requirement" -- which arrives right after "emitted N
# test file(s)" and reads exactly like the generator producing something
# unbuildable. It is the harness. collect.py's BENCHES table has recorded the
# correct per-benchmark solc all along; rather than copy that mapping and give
# it a second place to drift, let forge satisfy the flat's own pragma.
via_ir = true
optimizer = true
optimizer_runs = 200
"""


def binary_identity():
    """Which build produced these records.

    NOT the resume guard the sibling collectors have, and the difference is
    worth stating because it was got wrong twice today. `emit.jsonl` is UNLINKED
    at the start of every run (see main), so this script cannot reuse a record
    from an older build -- the "resumed across a different binary" hazard that
    `pathcov_collect`, `certify_all` and `option_matrix` each had does not exist
    here.

    What DID exist is a provenance gap in the artefact: the funnel this script
    measures -- emitted, compiled, forge-passed -- is subgoal 1's headline
    number, and the journal it leaves on disk carried nothing saying which build
    produced it. Reading those files a week later, or comparing two benchmarks
    collected either side of a fix, there was no way to tell. Same three fields
    as the siblings, on purpose: a record here and a record there have to be
    comparable when someone asks which build a number came from.
    """
    def _sh(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  cwd=str(REPO), timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    try:
        mtime = int(ESBMC.stat().st_mtime)
    except OSError:
        mtime = 0
    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": mtime,
    }


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


def emit_tests(bench, flat, solast, primary, project, proj, timeout, journal,
               max_tx, scope="focus"):
    """One esbmc run per callable, each in its own CWD (the emitted filename is
    hardcoded), each renamed so the suite can hold all of them at once."""
    callables = base.enumerate_own_callable_functions(flat, project)
    ident = binary_identity()
    tests, recs = [], []
    for i, (cname, fname, ckind) in enumerate(callables, 1):
        tag = f"{cname}__{fname}"
        if ckind == "library":
            # `--function` IS NOT AVAILABLE, and the reason is soundness.
            #
            # A pure library has no dispatcher harness, so `--contract <Lib>`
            # errors with "No verification targets(contracts) were found", and
            # this loop used to fall back to `--function fname`. But
            # `--function` verifies in ISOLATION from an ARBITRARY contract
            # state, so a counterexample can rest on a state combination no
            # `constructor() -> transaction sequence` reaches on chain -- and
            # THIS script's whole point is that the emitted test must be GREEN
            # on the unmodified contract. An isolated-state counterexample is
            # precisely a red test, and the self-check gate below would disable
            # it while the run that produced it looked fine.
            #
            # It never fired: every library-route run reported "0 complete
            # path(s) across 0 unit(s)" because the functions reached were
            # `internal`, and internal functions are not units. But
            # `ImmutablesLib.protocolFeeAmountCd` and three siblings are
            # `external` -- units by visibility, sitting on this route in the
            # corpus today. The route was correct only because its inputs
            # happened to be internal, which nothing checks.
            #
            # Refused and recorded rather than approximated. Same decision as
            # pathcov_collect.py; see notes/coverage/INVOCATION_DECISIONS.md
            # row 10.
            rec = {"tag": tag, "exitCode": None, "wallSeconds": 0.0,
                   "killed": False, "emitted": [],
                   "skipped": "library-has-no-dispatcher",
                   "skippedDetail":
                       "a library has no dispatcher harness, so --contract "
                       "<Lib> finds no verification targets; the only other "
                       "route is --function, which verifies in isolation from "
                       "an arbitrary state and can yield a counterexample no "
                       "reachable state supports -- i.e. a RED test. Internal "
                       "library functions are covered through their callers' "
                       "paths; external ones are an unmeasured gap under this "
                       "configuration"}
            print(f"  [{i}/{len(callables)}] {tag}  (skipped: library)",
                  flush=True)
            rec["binary"] = ident
            recs.append(rec)
            with journal.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            continue
        cwd = proj / "_gen" / tag
        cwd.mkdir(parents=True, exist_ok=True)
        for stale in cwd.glob("*"):
            stale.unlink()
        # `--cov-report-json` IS NOT OPTIONAL HERE, and it is not for diagnostics.
        #
        # The `enumerated` column of the emission-loss table used to be read from
        # notes/coverage/pathcov/<bench>/reports/ -- a DIFFERENT set of esbmc
        # runs. Numerator and denominator were then a join across two runs that
        # merely shared a benchmark name and a timeout, which does not support
        # the word "retains": a ratio between two runs measures the difference
        # between the runs as much as anything the emitter did. Asking THIS run
        # for its own report makes the two columns the same run by construction.
        #
        # It is not free: the flag registers no-slice exemptions, so the sliced
        # formula differs and the solver may pick different model values, hence
        # possibly different tests. That is the correct trade -- a slightly
        # different suite whose denominator is its own, over a suite whose
        # denominator came from somewhere else.
        # THE CELL, and why it is an argument rather than a constant.
        # INVOCATION_DECISIONS.md prints two settled command lines --
        # whole-contract at --solidity-max-tx 2 (the ARTEFACT question: what can
        # this method reach) and --focus-function at 1 (the GATE question: how
        # does it compare against a baseline measured to run at one
        # transaction) -- and forbids quoting a run of one into the other table.
        # This script measures the DELIVERABLE with forge, so which question it
        # answers has to be chosen rather than inherited.
        #
        # `focus` stays the default so every recorded number reproduces. What it
        # costs is stated there and not hidden here: a focused run cannot reach
        # cross-function state at ANY transaction bound, because every
        # transaction is another call to the same entry.
        cmd = [str(ESBMC), str(solast), "--sol", str(flat),
               "--solidity-path-coverage", "--solidity-max-tx", str(max_tx),
               "--generate-foundry-testcase", "--cov-report-json",
               "--memlimit", "8g", "--result-only",
               "--contract", primary]
        if scope == "focus":
            cmd += ["--focus-function", fname]
        print(f"  [{i}/{len(callables)}] {tag}", flush=True)
        rc, out, wall = run(cmd, timeout, cwd=str(cwd))
        (cwd / "run.log").write_text(out)

        produced = sorted(cwd.glob("*.cov.t.sol"))
        rec = {"tag": tag, "exitCode": rc, "wallSeconds": round(wall, 2),
               "killed": rc == -1, "emitted": [p.name for p in produced],
               "cell": {"scope": scope, "maxTx": max_tx}}
        # The collector's own classification, printed rather than inferred
        # later. "no test emitted" has at least four distinct causes and they
        # are not interchangeable.
        if "is ambiguous" in out:
            rec["ambiguousEntryName"] = True
        m = re.search(r"Foundry: (\d+) counterexample\(s\) REFUSED -- every "
                      r"call they reconstructed is a CONSTRUCTOR", out)
        if m:
            rec["refusedEmptyBody"] = int(m.group(1))
        m = re.search(r"Foundry: (\d+) counterexample\(s\) REFUSED -- their "
                      r"path is a NAMED OBSTACLE", out)
        if m:
            rec["refusedObstacle"] = int(m.group(1))
        m = re.search(r"Foundry: (\d+) call\(s\) carry (\d+) DEFAULTED "
                      r"argument\(s\) \(([^)]*)\)", out)
        if m:
            rec["defaultedCalls"] = int(m.group(1))
            rec["defaultedArgs"] = int(m.group(2))
            rec["defaultedByType"] = m.group(3)
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
        rec["binary"] = ident
        recs.append(rec)
        with journal.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return tests, recs


def forge_branch_universe(locked):
    """How many branches forge's instrument records for this benchmark's files.

    NOT the AST decision count, and the difference is not cosmetic: the two
    instruments disagree about what a decision IS, in BOTH directions. On aqua
    forge omits the two `for` LOOP HEADERS (2249, 2258), so `native 6/8` is
    really 6 of 6; on farming and st1inch forge records MORE branches than the
    AST count. A count difference therefore says nothing about WHICH lines are
    missing -- only intersecting the two sets does.

    The field lives ONLY in the whole-contract (`no_function`) entry. The
    per-method entry carries `reached` alone, and reading that returns None for
    every benchmark -- which prints as "no ceiling known" and is
    indistinguishable from having read the wrong section. That was the first
    version of this, in two places at once; caught by running it.

    Returns None when the benchmark has no native lcov record, because "we do
    not know the ceiling" and "the ceiling is the whole denominator" are
    different statements and only one of them is safe to quote.

    ONE implementation, imported by instrument_ceiling.py rather than copied --
    a second copy is a second thing that can drift, which is the failure the
    `solc` pin in this same file was already fixed for.
    """
    out = None
    for pf in locked.get("no_function", {}).get("perFile", []):
        nat = pf.get("native") or {}
        if isinstance(nat.get("instrumented"), int):
            out = (out or 0) + nat["instrumented"]
    return out


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
    # The transaction bound is a PARAMETER of the result, not a constant of the
    # pipeline. Aqua's uncovered branches sit behind a `require` on a mapping a
    # fresh deploy leaves empty, so they need state an EARLIER transaction
    # establishes -- at 1 neither the model nor the test can reach them, and no
    # emitter change moves that. Raising it is the measurement that separates
    # "our tests are weak" from "one transaction cannot get there".
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--scope", choices=("focus", "whole"), default="focus",
                    help="focus passes --focus-function per callable (with "
                         "--max-tx 1 this is the GATE cell); whole drops it and "
                         "lets the dispatcher choose (with --max-tx 2 this is "
                         "the ARTEFACT cell, the only configuration measured to "
                         "reach cross-function state). Printed with the result, "
                         "because one cell's run may not be quoted into the "
                         "other's table.")
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
                             a.timeout, journal, a.max_tx, a.scope)
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

    # ---- SELF-CHECK GATE: run every emitted test on the UNMODIFIED contract --
    #
    # A test that is RED on the contract it was generated from is not a
    # deliverable, and its coverage is not ours to claim. The generator cannot
    # always know: MEASURED on aqua, `pull`'s first case carries
    # `// [asserted] path exits normally` -- the exit census confirmed a normal
    # exit -- and forge reports `[FAIL: SafeTransferFromFailed()]`. The census is
    # not wrong about the MODEL; the model gives an external call a nondet return
    # and may choose success where the chain fails. No amount of census reading
    # closes that, so the check has to be empirical.
    #
    # Red tests are DISABLED rather than deleted (renamed out of forge's `test*`
    # prefix) so the artefact still shows what was generated and why it was not
    # counted, and the count goes to stdout. Coverage is then measured over the
    # tests that actually pass, which is the only suite we could hand anyone.
    print("=== forge test (self-check) ===", flush=True)
    rc, out, _ = run(["forge", "test"], 1800, cwd=str(proj))
    (proj / "test.log").write_text(out)
    # DEDUPED, because forge prints each failure TWICE -- once under its suite
    # and once in the closing "Failing tests:" block. The first version of this
    # parser reported `RED, disabled: 2` for one failing test, which is the
    # same class of defect it exists to catch: a count that is not the thing it
    # names. Keyed on (file, contract, function), which is what identifies a
    # test uniquely.
    reds = []
    seen_red = set()
    passes = 0
    cur_file = cur_contract = None
    # BOTH SUITE HEADERS. forge prints every failure twice -- under
    # `Ran N tests for <file>:<contract>` and again under
    # `Encountered N failing test(s) in <file>:<contract>`. Matching only the
    # first leaves `cur_file` pointing at the LAST suite that ran, so the
    # closing block's duplicates are attributed to it. Here that is not merely
    # a miscount: the attribution drives the RENAME below, so the disable is
    # attempted in the wrong file -- a no-op that leaves the genuinely red test
    # enabled while the log says it was disabled. MEASURED on the PoC funnel,
    # which shares this parser: 4 RED reported where forge said 3, the extra
    # one filed against a contract with no arithmetic whose own suite result
    # was `0 failed`.
    for line in out.splitlines():
        m = re.match(r"Ran \d+ tests? for (\S+):(\S+)", line)
        if m:
            cur_file, cur_contract = m.group(1), m.group(2)
            continue
        m = re.match(r"Encountered \d+ failing tests? in (\S+):(\S+)", line)
        if m:
            cur_file, cur_contract = m.group(1), m.group(2)
            continue
        m = re.match(r"\[PASS[^\]]*\]\s+(\w+)\(", line)
        if m and cur_file:
            key = (cur_file, cur_contract, m.group(1))
            if key not in seen_red:
                seen_red.add(key)
                passes += 1
            continue
        m = re.match(r"\[FAIL[^\]]*\]\s+(\w+)\(", line)
        if m and cur_file:
            key = (cur_file, cur_contract, m.group(1))
            if key not in seen_red:
                seen_red.add(key)
                reds.append(key)

    # CROSS-CHECK AGAINST forge's OWN TOTAL. This reconstructs a number the
    # tool already prints; checking one against the other is what turns the
    # next unknown header shape into a loud failure instead of a confident
    # wrong rename.
    m = re.search(r"(\d+) tests? passed, (\d+) failed", out)
    if m:
        fg, fr = int(m.group(1)), int(m.group(2))
        if (fg, fr) != (passes, len(reds)):
            print(f"** PARSE DISAGREES WITH forge's OWN SUMMARY: parsed "
                  f"{passes} passed / {len(reds)} failed, forge reported "
                  f"{fg} passed / {fr} failed. The disable step below would "
                  f"act on the wrong set. **", flush=True)
        else:
            print(f"    parse agrees with forge: {fg} passed, {fr} failed",
                  flush=True)
    if reds:
        print(f"=== {len(reds)} RED test(s) on the unmodified contract ===",
              flush=True)
        by_file = defaultdict(list)
        for f, c, fn in reds:
            by_file[f].append(fn)
            print(f"    RED  {f}:{c}.{fn}", flush=True)
        for f, fns in by_file.items():
            p = proj / f
            txt = p.read_text()
            for fn in fns:
                # forge runs a function iff its name starts with `test`.
                txt = txt.replace(
                    f"  function {fn}() public {{",
                    f"  // DISABLED: RED on the unmodified contract, so its\n"
                    f"  // coverage is not ours to claim. Kept, renamed out of\n"
                    f"  // forge's `test*` prefix, so the artefact still shows\n"
                    f"  // what was generated.\n"
                    f"  function disabled_{fn}() public {{")
            p.write_text(txt)
        rc, out, _ = run(["forge", "build"], 900, cwd=str(proj))
        (proj / "build2.log").write_text(out)
        if rc != 0:
            sys.exit(f"forge build failed after disabling red tests (rc={rc})")

    print("=== forge coverage ===", flush=True)
    # --ir-minimum is required once via_ir is on: forge's coverage
    # instrumentation cannot run against a full viaIR pipeline.
    rc, out, _ = run(["forge", "coverage", "--report", "lcov",
                      "--report-file", "lcov.info", "--ir-minimum"],
                     3600, cwd=str(proj))
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
    # Computed HERE, after `locked` exists. The first version of this sat above
    # `reached = ...`, forty lines before `locked` is assigned, and every run
    # died with UnboundLocalError AFTER the whole expensive pipeline had
    # finished -- emission, build, self-check and coverage all completed, then
    # the summary crashed. A cheap mistake in the most expensive possible place.
    forge_universe = forge_branch_universe(locked)

    print()
    print("=" * 78)
    print(f"{a.bench}: FORGE coverage of the GENERATED suite")
    cell = ("ARTEFACT" if (a.scope, a.max_tx) == ("whole", 2) else
            "GATE" if (a.scope, a.max_tx) == ("focus", 1) else "UNNAMED")
    print(f"CELL {cell}: scope={a.scope} --solidity-max-tx={a.max_tx}"
          + ("   -- neither settled command line, so this table belongs to no "
             "comparison" if cell == "UNNAMED" else ""))
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
    print(f"  bar    (branch coverage, esbmcReached) : {p2['esbmcReached']}"
          f"   [ESBMC universe: {tot_c}]")
    print(f"  native (project's own suite, lcov)     : {p2['nativeReached']}"
          + (f"   [forge universe: {forge_universe}]"
             if forge_universe is not None else ""))
    print(f"  ours   (generated suite, forge lcov)   : {tot_ours}"
          + (f"   [forge universe: {forge_universe}]"
             if forge_universe is not None else ""))
    if forge_universe is not None and forge_universe < tot_c:
        print()
        print(f"  ** CEILING: forge instruments only {forge_universe} of the "
              f"{tot_c} canonical decision(s) in these files, so `native` and "
              f"`ours` CANNOT exceed {forge_universe} whatever the tests do. "
              f"`bar` is ESBMC's column and its universe is {tot_c}. Comparing "
              f"ours/{tot_c} with bar/{tot_c} mixes two instruments' universes; "
              f"the honest reading of `ours` is {tot_ours}/{forge_universe} of "
              f"what its own instrument can see. The lines forge omits are loop "
              f"HEADERS, which its branch coverage does not treat as branches.")
    print()
    print(f"  emitted test files : {emitted}")
    print(f"  runs               : {len(recs)}")
    # Printed unconditionally, like every other count here. A refused run is
    # not a run that reached nothing -- it is a callable this configuration
    # declines to measure, and leaving it out of the table would make the
    # denominator quietly smaller than the callable set.
    print(f"  refused, library   : "
          f"{sum(1 for r in recs if r.get('skipped'))}")
    print(f"  killed by timeout  : {sum(1 for r in recs if r['killed'])}")
    print(f"  ambiguous name     : "
          f"{sum(1 for r in recs if r.get('ambiguousEntryName'))}")
    # Every count printed every time, zeros included: a category that stops
    # occurring is noticed, one that silently disappears from the output is not.
    print(f"  refused, empty body: "
          f"{sum(r.get('refusedEmptyBody', 0) for r in recs)}")
    print(f"  refused, obstacle  : "
          f"{sum(r.get('refusedObstacle', 0) for r in recs)}")
    print(f"  RED, disabled      : {len(reds)}")
    print(f"  defaulted args     : "
          f"{sum(r.get('defaultedArgs', 0) for r in recs)} in "
          f"{sum(r.get('defaultedCalls', 0) for r in recs)} call(s)")
    types = [r["defaultedByType"] for r in recs if r.get("defaultedByType")]
    if types:
        print(f"  defaulted by type  : {'; '.join(sorted(set(types)))}")
    print(f"  project            : {proj}")


if __name__ == "__main__":
    main()
