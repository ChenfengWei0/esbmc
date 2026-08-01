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

# THE SOLVER/ENCODER IS A CONFIGURATION VARIABLE, NOT SCAFFOLDING.
#
# It was previously not passed at all, so every run took whatever ESBMC
# auto-selects. On st1inch that is bitwuzla, and MEASURED: 22 runs, 22 killed by
# the outer timeout, 0 reports, 0 F claims -- a whole benchmark contributing a
# zero that reads like "reached nothing" and is actually "never returned".
#
# The cause is not the source. Three backends fail three different ways on one
# query (bitwuzla never returns; cvc5 raises bad_alloc at 4 GB with 0.000 s of
# decision-procedure time; z3 refuses at ENCODING time with "datatype is not
# well-founded"), and a hand-written self-referential Solidity struct
# (notes/coverage/poc/D05_RecursiveStruct.sol) is accepted by all three -- so the
# recursion is introduced by ESBMC's own tuple encoding. That made the ENCODER
# the variable to change, and notes/coverage/scripts/st_encoders.py is the
# six-cell matrix that found the one that decides it: with
# `--z3 --tuple-node-flattener` the same run produces a complete report in 43 s.
#
# Recorded as a TABLE WITH ITS JUSTIFICATION, applied automatically, announced on
# stdout, and written into index.json -- rather than hardcoded in the command
# builder. A corpus whose every row came from one silently-chosen cell is exactly
# the failure this project has already had; the flags a row was produced with
# have to be visible in that row.
#
# HALF THIS TABLE'S ORIGINAL JUSTIFICATION IS NOW WRONG, AND IT IS REWRITTEN
# RATHER THAN DELETED. It used to say z3's default tuple encoding refuses at
# encoding time, so the node flattener was needed to avoid building the
# datatype. That refusal was a real defect and it is FIXED -- two library or
# contract structs sharing a short name were handed one z3 tuple sort
# (solidity_convert_decl.cpp gave the type a bare tag while its symbol id used
# canonicalName; z3_conv.cpp:1030 names the sort from the tag). Plain `--z3`
# now completes on st1inch where it used to core-dump.
#
# The entry STAYS because the OTHER half is still true and was re-measured
# after the fix: bitwuzla still never returns at 120 s. And the flattener is
# still what the corpus was collected with, so removing it would change the
# configuration a table was produced under, which is the thing this table
# exists to prevent.
#
# What the fix did NOT buy, stated because it is the tempting claim: it does
# not make st1inch's claims decidable. setFeeReceiver reports 5 claims / 5
# `solver-unknown` under BOTH `--z3` and `--z3 --tuple-node-flattener`, and the
# full sweep is 59 solver-unknown out of 128 claims. The encoder was never what
# made them undecidable.
ENCODER_EXCEPTIONS = {
    "st1inch_St1inch": (
        ["--z3", "--tuple-node-flattener"],
        "default (bitwuzla) never returns on this benchmark: 22/22 runs killed, "
        "0 reports, and re-measured as still not returning at 120 s after the "
        "struct-tag fix. z3 decides the encoding either way now; the flattener "
        "is kept because it is the configuration this corpus was collected "
        "under. NOTE: it does not make the claims decidable -- 5/5 "
        "solver-unknown on setFeeReceiver with and without it.",
    ),
}


def solver_flags_for(bench_key, override):
    """(flags, reason). An explicit --solver-flags always wins and says so."""
    if override:
        return list(override), "explicit --solver-flags"
    if bench_key in ENCODER_EXCEPTIONS:
        return list(ENCODER_EXCEPTIONS[bench_key][0]), \
            ENCODER_EXCEPTIONS[bench_key][1]
    return [], "tool default (no solver flag passed)"


def esbmc_cmd(solast, flat, primary, focus, goals, solver_flags=()):
    cmd = [
        str(ESBMC), str(solast), "--sol", str(flat),
        "--solidity-path-coverage",
        "--solidity-max-tx", "1",
        "--cov-report-json",
        "--path-cov-max-goals", str(goals),
        "--memlimit", MEMLIMIT,
    ] + list(solver_flags)
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


def binary_identity():
    """Who produced a run record. Both halves are load-bearing.

    `head` is the commit the tree was on; `mtime` is the binary's own
    timestamp, and it is here because HEAD alone lies in exactly the situation
    this project is always in -- an uncommitted fix in the tree means two
    different binaries share one HEAD. `dirty` records whether src/ had
    uncommitted changes, so a row produced from a work-in-progress tree can
    never be mistaken for one produced from the commit it names.
    """
    def _sh(args):
        try:
            return subprocess.run(args, capture_output=True, text=True,
                                  cwd=str(REPO), timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": int(ESBMC.stat().st_mtime) if ESBMC.exists() else 0,
    }


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
        # Which binary produced this record. Checked on resume; see the refusal
        # in collect() for why a journal is not portable across builds.
        "binary": binary_identity(),
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
    # A FOURTH ZERO, and it is not any of the three above.
    # The library route passes `--function <name>` with no contract
    # qualification, so a name declared by more than one contract of the flat
    # ends the run before anything is instrumented:
    #     ERROR: main symbol `claim' is ambiguous
    #     ERROR: CONVERSION ERROR
    # MEASURED on farming: 10 of 28 runs, all of them names that recur across
    # FarmAccounting / FarmingLib / FarmingPool (claim, startFarming,
    # stopFarming, farmed, updateBalances), plus TimelocksLib.get on both
    # Escrows. Without this field the record shows only `exitCode: 6` and no
    # instrumentation line, which reads as "the run reached nothing" -- a tool
    # failure disguised as a measurement of zero. collect.py records the same
    # condition on the baseline side as `status: "ambiguous"`.
    if "is ambiguous" in out:
        rec["ambiguousEntryName"] = True
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


def collect(bench_key, whole, timeout, goals, out_suffix="", solver_override=(),
            fresh=False):
    flat_rel, primary, _solc, project = base.BENCHES[bench_key]
    sflags, sreason = solver_flags_for(bench_key, solver_override)
    print(f"  [solver] {' '.join(sflags) if sflags else '(none)'} -- {sreason}",
          flush=True)
    flat = INPUTS / flat_rel
    solast = INPUTS / (flat_rel + ".solast")
    if not solast.exists():
        sys.exit(f"missing AST: {solast} (run collect.py first, it generates it)")

    # THE OUTPUT DIRECTORY IS PART OF THE CONFIGURATION, not a convenience.
    # `index.json` is REWRITTEN at the end of every collection with only the
    # runs of that collection, while `runs.jsonl` is appended. So a --whole run
    # in the per-method directory replaces an 8-run index with a 1-run index,
    # after which branch_gate.py's report-count reconciliation exits with
    # "reports/ holds 8 report(s) but index.json records 1" -- the per-method
    # measurement is not corrupted but is no longer readable, and the failure
    # surfaces far from the command that caused it.
    #
    # Two configurations therefore never share a directory. The suffix is
    # required rather than defaulted for --whole, so that the two are also
    # distinguishable by name in every later table.
    out_dir = OUT / (bench_key + out_suffix)
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

    # RESUMING ACROSS A DIFFERENT BINARY IS THE TRAP THIS FEATURE SETS.
    #
    # The journal makes a sweep resumable, and resumability means "a tag in the
    # journal is skipped and its existing report reused". That is exactly right
    # for continuing an interrupted sweep and exactly WRONG after a fix: re-run
    # the command and every run prints `(already done)`, the stale reports stay,
    # `index.json` is rewritten to look current, and the analysis reports the
    # OLD build's numbers under the NEW build's name. Nothing anywhere says so
    # -- it is a clean-looking complete collection.
    #
    # This is not hypothetical for this corpus. Every existing collection was
    # produced before several defects were fixed (including one that left
    # st1inch with 22/22 killed runs and no reports at all), so the next thing
    # anyone does here IS a re-collection, and the resume path would silently
    # defeat it.
    #
    # So each record carries the identity of the binary that produced it, and a
    # journal whose records came from a different one is REFUSED rather than
    # resumed. Refused, not auto-cleared: deleting someone's collection because
    # a timestamp moved is its own way to lose data, and the operator should
    # choose between --fresh and quarantining the directory.
    ident = binary_identity()
    stale = {t: r.get("binary") for t, r in done.items()
             if r.get("binary") != ident}
    if stale and not fresh:
        shown = list(stale.items())[:5]
        sys.exit(
            f"{bench_key}: {len(stale)} of {len(done)} journal record(s) were "
            f"produced by a DIFFERENT binary than the one on disk now.\n"
            f"  now:  {ident}\n"
            + "".join(f"  was:  {t} -> {b}\n" for t, b in shown)
            + (f"  ... and {len(stale) - len(shown)} more\n"
               if len(stale) > len(shown) else "")
            + "Resuming would skip those runs and reuse their reports, so the "
              "analysis would quote the old build's numbers under the new "
              "build's name. Re-run with --fresh to discard this collection "
              "and start over, or move the directory aside to keep it.")
    if fresh and done:
        print(f"  [fresh] discarding {len(done)} journal record(s) and their "
              f"reports", flush=True)
        done = {}
        journal.unlink()
        for p in reports_dir.glob("*.json"):
            p.unlink()

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
                                       None, goals, sflags),
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
            if ckind == "library":
                # `--function` IS NOT AVAILABLE HERE, and the reason is
                # soundness rather than tidiness.
                #
                # A pure library has no dispatcher harness, so `--contract
                # <Lib>` errors with "No verification targets(contracts) were
                # found" -- MEASURED on limit-order-protocol, 14/14 runs. The
                # previous version of this file therefore routed libraries
                # through `--function fn`, mirroring collect.py.
                #
                # `--function` verifies a function in ISOLATION from an
                # ARBITRARY contract state. A counterexample it produces may
                # rest on a state combination that no `constructor() -> tx
                # sequence` can reach on chain. This project's deliverable is a
                # test that must be GREEN on the unmodified contract, so such a
                # counterexample becomes a RED test with nothing marking it --
                # which is why `--function` is banned from the regressions.
                #
                # It never fired: every library-route run reported "0 complete
                # path(s) across 0 unit(s)" because the functions reached were
                # `internal` and internal functions are not units. But
                # `ImmutablesLib.protocolFeeAmountCd` and three siblings are
                # `external` -- units by visibility, sitting on this route. The
                # channel was correct only because its inputs happened to be
                # internal, which is not a property anything checks.
                #
                # So the run is REFUSED and recorded, not approximated. An
                # internal library function loses nothing: its decisions are
                # covered through the units that inline it. An external one
                # becomes an explicitly reported gap, which is the honest form
                # of "this configuration cannot measure it".
                rec = {
                    "tag": tag, "cmd": None, "wallSeconds": 0.0,
                    "exitCode": None, "killedByOuterTimeout": False,
                    "reportPresent": False,
                    "skipped": "library-has-no-dispatcher",
                    "skippedDetail":
                        "a library has no dispatcher harness, so --contract "
                        "<Lib> finds no verification targets; the only other "
                        "route is --function, which verifies in isolation from "
                        "an arbitrary state and can yield a counterexample no "
                        "reachable state supports. Internal library functions "
                        "are covered through their callers' paths; external "
                        "ones are an unmeasured gap under this configuration",
                    "contract": cname, "function": fname, "kind": ckind,
                    "binary": binary_identity(),
                }
                print(f"  [{i}/{len(todo)}] {tag}  (skipped: library)",
                      flush=True)
                record(rec)
                continue
            print(f"  [{i}/{len(todo)}] {tag}", flush=True)
            cmd = esbmc_cmd(solast, flat, primary, fname, goals, sflags)
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
            # Written into the index so no later table can quote a row without
            # the encoder it was produced with. A benchmark whose runs needed a
            # non-default encoder is not comparable to one that did not, and
            # that difference has to travel WITH the data.
            "solverFlags": sflags,
            "solverFlagsReason": sreason,
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
    ap.add_argument("--out-suffix", default="",
                    help="appended to the output directory name; two "
                         "configurations must never share one directory")
    ap.add_argument("--fresh", action="store_true",
                    help="discard this benchmark's journal and reports and "
                         "collect from scratch. Required when the binary "
                         "changed since the journal was written; without it "
                         "the resume path would reuse the old build's reports")
    ap.add_argument("--solver-flags", default="",
                    help="space-separated ESBMC solver/encoder flags, e.g. "
                         "'--z3 --tuple-node-flattener'. Overrides the "
                         "per-benchmark ENCODER_EXCEPTIONS table; whichever "
                         "applies is printed and recorded in index.json")
    a = ap.parse_args()
    if a.list or not a.bench:
        for k in base.BENCHES:
            print(k)
        return 0
    if a.bench not in base.BENCHES:
        sys.exit(f"unknown bench: {a.bench}")
    if a.whole and not a.out_suffix:
        sys.exit("--whole needs --out-suffix (e.g. --out-suffix __whole): "
                 "writing it into the per-method directory rewrites that "
                 "collection's index.json and leaves its reports unreadable")
    idx = collect(a.bench, a.whole, a.timeout, a.goals, a.out_suffix,
                  a.solver_flags.split(), a.fresh)
    ok = sum(1 for r in idx["runs"] if r["reportPresent"])
    killed = sum(1 for r in idx["runs"] if r["killedByOuterTimeout"])
    print(f"{a.bench}: {ok}/{len(idx['runs'])} run(s) produced a report, "
          f"{killed} killed by the outer timeout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
