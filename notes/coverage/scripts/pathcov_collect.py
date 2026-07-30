#!/usr/bin/env python3
"""pathcov_collect.py -- the external invocation of --solidity-path-coverage.

The PRODUCT side of the branch-coverage gate. `collect.py` is the baseline side
and stays untouched (it is LOCKED); this file is deliberately its mirror image,
reusing its benchmark table, its scope rules and its AST helpers so that the two
sides cannot drift into measuring different things.

Every flag here is justified by notes/path-coverage-invocation-contract.md,
which was read out of the source rather than out of --help. The four that are
NOT free choices:

  --solidity-max-tx 1
      `--solidity-max-tx 0` is the SHALLOWEST setting under any coverage mode,
      not the unbounded one: bound 0 emits `while(nondet){body}` and coverage
      then rewrites the back-edge to a SKIP, leaving ONE transaction. Path
      coverage's own default is 2 (it is absent from `unbounded_modes`), and
      N>=2 is the configuration the tool itself warns produces mis-attributed
      Foundry tests. 1 is the only value that is both explicit and honest.
      It is also what the locked branch-coverage dataset actually ran at --
      branch coverage IS in `unbounded_modes`, so it got bound 0, so one
      transaction. The two sides are at the same transaction depth.

  --cov-report-json
      Not optional. Without it the per-claim slicer removes every state write
      and environment read (a path claim's guard mentions only the ghost
      accumulators) and the counterexample payload comes back empty. It is also
      what turns on the decision-sequence recording this whole script consumes.

  no --timeout
      `emit_branch_coverage_on_timeout` is gated on `branch_cov_active`, so a
      path-coverage run killed by --timeout emits NOTHING AT ALL. There is no
      partial result to salvage: a run either finishes or contributes zero.
      That is why the per-method shape below is the primary configuration and
      not merely the stronger one -- it is the shape whose individual runs are
      small enough to finish.

  one CWD per run
      The report filename is hardcoded `cov-report.json` in the current
      directory (bmc.cpp). Two runs in one directory silently overwrite each
      other's results.

Usage:
    python3 pathcov_collect.py <bench-key> [--whole] [--timeout S] [--goals N]
    python3 pathcov_collect.py --list
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collect as base  # noqa: E402  -- the LOCKED baseline side, reused verbatim

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
INPUTS = REPO / "notes/coverage/inputs"
OUT = REPO / "notes/coverage/pathcov"

# 8g matches the baseline's --memlimit. Running several ESBMC processes at once
# is what exhausted this machine's memory once already, so this script is
# strictly serial and says so rather than leaving it to a caller's discretion.
MEMLIMIT = "8g"
DEFAULT_OUTER_TIMEOUT = 300
DEFAULT_MAX_GOALS = 10000


def esbmc_cmd(solast, flat, primary, focus, goals, library=False):
    cmd = [
        str(ESBMC), str(solast), "--sol", str(flat),
        "--solidity-path-coverage",
        "--solidity-max-tx", "1",
        "--cov-report-json",
        "--path-cov-max-goals", str(goals),
        "--memlimit", MEMLIMIT,
    ]
    if library:
        # A pure library has no dispatcher harness, so `--contract <Lib>` errors
        # with "No verification targets(contracts) were found" -- MEASURED on
        # limit-order-protocol, 14/14 runs. collect.py routes libraries through
        # `--function fn` for the same reason and this mirrors it.
        #
        # It does not rescue the benchmark. A UNIT is a public/external
        # function, and this library's functions are all `internal`, so the run
        # reports "0 complete path(s) across 0 unit(s)" and names them:
        # "in-scope function(s) are internal/private and are therefore not
        # units; they have no path set of their own and appear inside the paths
        # of the units that call them". That is the method's definition working
        # as intended on a flat that contains no caller -- it is NOT a reach of
        # zero, and `unitsEnumerated` below is what keeps the two apart.
        return cmd + ["--function", focus]
    if primary:
        # scope_contract. NOTE the asymmetry with the baseline, which passes
        # --coverage-whole-unit and then excludes contracts one by one: the
        # path-coverage dispatch never reads `exclude_contracts`, so the only
        # scoping lever it has is this one. Out-of-scope decisions that slip
        # through are removed on the analysis side instead, by intersecting
        # with the canonical project-own decision lines -- which is exactly
        # what the baseline does to ITS numerator too, so the two remain
        # commensurable.
        cmd += ["--contract", primary]
    if focus:
        cmd += ["--focus-function", focus]
    return cmd


def one_run(tag, cmd, timeout, workdir):
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in workdir.glob("*"):
        stale.unlink()
    t0 = time.time()
    killed = False
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout, cwd=str(workdir))
        rc, out = cp.returncode, cp.stdout + cp.stderr
    except subprocess.TimeoutExpired as e:
        killed = True
        rc = -1

        # TimeoutExpired carries BYTES even under text=True, and each stream
        # independently. Decoding the concatenation is what raised TypeError on
        # the first real timeout (aqua's `ship`), losing the partial log of the
        # very run whose log is most worth having.
        def _dec(x):
            if x is None:
                return ""
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else x

        out = _dec(e.stdout) + _dec(e.stderr)
    wall = time.time() - t0
    (workdir / "run.log").write_text(out)

    report = workdir / "cov-report.json"
    rec = {
        "tag": tag,
        "cmd": " ".join(cmd),
        "wallSeconds": round(wall, 2),
        "exitCode": rc,
        # A killed path-coverage run yields NOTHING -- the timeout rescue path
        # is gated on branch coverage. Recorded explicitly so a zero here is
        # never read as "this configuration reached nothing".
        "killedByOuterTimeout": killed,
        "reportPresent": report.exists(),
    }
    # THREE DIFFERENT ZEROS, kept apart. "0 units enumerated" (the code has no
    # externally-callable function in scope), "0 paths in the units there are",
    # and "no report at all" are three distinct outcomes that all present as an
    # empty numerator. Parsed off the run's own summary line rather than
    # inferred from the report, so a run that died before writing one still
    # says which of the three it was.
    m = re.search(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)",
                  out)
    if m:
        rec["pathsInstrumented"] = int(m.group(1))
        rec["unitsEnumerated"] = int(m.group(2))
    if "are internal/private and are therefore not units" in out:
        rec["nonUnitFunctionsPresent"] = True
    if "No verification targets" in out:
        rec["noVerificationTargets"] = True
    if report.exists():
        try:
            d = json.loads(report.read_text())
        except ValueError as e:
            rec["reportParseError"] = str(e)
            return rec, None
        s = d.get("summary", {})
        rec["pathsTotal"] = s.get("paths_total")
        rec["F"] = s.get("F_feasible_with_ce")
        rec["U"] = s.get("U_undecided")
        rec["uReasons"] = s.get("U_reasons")
        rec["decisionSequences"] = s.get("decision_sequences")
        return rec, d
    return rec, None


def collect(bench_key, whole, timeout, goals):
    flat_rel, primary, _solc, project = base.BENCHES[bench_key]
    flat = INPUTS / flat_rel
    solast = INPUTS / (flat_rel + ".solast")
    if not solast.exists():
        sys.exit(f"missing AST: {solast} (run collect.py first, it generates it)")

    out_dir = OUT / bench_key
    out_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(exist_ok=True)

    # RESUMABLE, and not as a convenience. A benchmark's per-method sweep runs
    # for longer than any single supervising call can wait, and a sweep that has
    # to restart from scratch is a sweep that never finishes. Each run's record
    # is appended the moment it completes, so an interrupted collection loses at
    # most the run that was in flight -- and re-running the command continues
    # rather than repeating.
    journal = out_dir / "runs.jsonl"
    done = {}
    if journal.exists():
        for line in journal.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["tag"]] = r

    # THE REPORTS DIRECTORY IS RECONCILED WITH THE JOURNAL, and this is not
    # housekeeping. `work/` is emptied per run (one_run), but `reports/` never
    # was, so a report written by an EARLIER collection survived into the next
    # one -- and branch_gate.py builds the product-side numerator by globbing
    # this directory. A stale file therefore credits the current build with an
    # earlier build's witnessed paths, in a row that looks exactly like a clean
    # result. It came within one command of happening: quarantining a pre-fix
    # collection renamed index.json and runs.jsonl and left ~54 MB of pre-fix
    # cov-report.json in place.
    #
    # The journal is the authority on what this collection has actually run, so
    # anything in `reports/` that the journal does not name is removed, and the
    # removal is COUNTED ON STDOUT -- a silent cleanup would hide the fact that
    # a previous collection's output was here at all.
    orphans = [p for p in sorted(reports_dir.glob("*.json"))
               if p.stem not in done]
    for p in orphans:
        p.unlink()
    if orphans:
        print(f"  [reports] removed {len(orphans)} report(s) not named by "
              f"{journal.name}: " + ", ".join(p.stem for p in orphans[:8])
              + (" ..." if len(orphans) > 8 else ""), flush=True)

    pkind = base.primary_contract_kind(flat, primary)
    runs = []

    def record(rec):
        runs.append(rec)
        with journal.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")

    if whole:
        # Pair-1 analogue: one run, no focus. Kept available because it is the
        # only configuration in which cross-function state can be established
        # inside a single transaction (each dispatch guard is independent), and
        # therefore the only one whose U's mean "no witness" rather than "this
        # function was never offered to the dispatcher".
        tag = "whole"
        if tag in done:
            runs.append(done[tag])
        else:
            rec, d = one_run(tag,
                             esbmc_cmd(solast, flat,
                                       None if pkind == "library" else primary,
                                       None, goals),
                             timeout, out_dir / "work" / tag)
            record(rec)
            if d is not None:
                (reports_dir / f"{tag}.json").write_text(json.dumps(d))
    else:
        # Pair-2 analogue, and the primary configuration. Two independent
        # reasons, both from the source:
        #   * it is the STRONGER baseline -- the locked dataset's per-method
        #     union beats its whole-contract run on both Escrows and reaches
        #     100% on four of six benchmarks;
        #   * a path-coverage run killed by a timeout emits nothing at all, so
        #     the configuration that finishes is the only one that measures.
        # NO second filter by contract NAME. `enumerate_own_callable_functions`
        # already restricts to functions whose flat block is a project-own file
        # marker, which is the authoritative scope rule (METHODOLOGY 3) and the
        # one collect.py's own Pair 2 uses.
        #
        # Adding `c in project_own_contract_names(project)` on top looks like a
        # tightening and is actually a silent truncation: that helper reads the
        # project's checked-out source tree, and for three of the six
        # benchmarks that tree is not present, so it returns an EMPTY set and
        # every contract method is dropped. MEASURED -- aqua ran 2 of its 8
        # callables (only the two library ones, which the `k == "library"`
        # escape hatch let through) and reported "2/2 run(s) produced a report",
        # which reads like a complete collection.
        todo = list(base.enumerate_own_callable_functions(flat, project))
        for i, (cname, fname, ckind) in enumerate(todo, 1):
            tag = f"{cname}__{fname}"
            if tag in done:
                print(f"  [{i}/{len(todo)}] {tag}  (already done)", flush=True)
                runs.append(done[tag])
                continue
            print(f"  [{i}/{len(todo)}] {tag}", flush=True)
            cmd = esbmc_cmd(solast, flat,
                            None if ckind == "library" else primary,
                            fname, goals, library=(ckind == "library"))
            rec, d = one_run(tag, cmd, timeout, out_dir / "work" / tag)
            rec["contract"], rec["function"], rec["kind"] = cname, fname, ckind
            record(rec)
            if d is not None:
                (reports_dir / f"{tag}.json").write_text(json.dumps(d))

    index = {
        "benchmark": bench_key,
        "project": project,
        "primary": {"name": primary, "kind": pkind},
        "flatInput": str(flat),
        "config": {
            "mode": "whole" if whole else "per-method",
            "solidityMaxTx": 1,
            "pathCovMaxGoals": goals,
            "memlimit": MEMLIMIT,
            "outerTimeoutSeconds": timeout,
            "innerEsbmcTimeout": None,
            "innerTimeoutNote":
                "--timeout is deliberately NOT passed: the partial-result "
                "rescue is gated on branch_cov_active, so a path-coverage run "
                "killed by it emits nothing. Bounding is done from outside and "
                "a killed run is recorded as such rather than as a zero reach",
        },
        "runs": runs,
        "reportsDir": str(reports_dir),
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--whole", action="store_true",
                    help="Pair-1 analogue: one whole-contract run, no focus")
    ap.add_argument("--timeout", type=int, default=DEFAULT_OUTER_TIMEOUT)
    ap.add_argument("--goals", type=int, default=DEFAULT_MAX_GOALS)
    a = ap.parse_args()
    if a.list or not a.bench:
        for k in base.BENCHES:
            print(k)
        return 0
    if a.bench not in base.BENCHES:
        sys.exit(f"unknown bench: {a.bench}")
    idx = collect(a.bench, a.whole, a.timeout, a.goals)
    ok = sum(1 for r in idx["runs"] if r["reportPresent"])
    killed = sum(1 for r in idx["runs"] if r["killedByOuterTimeout"])
    print(f"{a.bench}: {ok}/{len(idx['runs'])} run(s) produced a report, "
          f"{killed} killed by the outer timeout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
