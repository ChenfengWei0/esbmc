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

      IT IS ALSO WHAT THE LOCKED BRANCH-COVERAGE BASELINE RUNS AT, AND THAT IS
      NOW MEASURED RATHER THAN INFERRED. This clause used to read "branch
      coverage IS in `unbounded_modes`, so it got bound 0, so one transaction"
      -- a chain of reasoning off an option table, on which the commensurability
      of the entire gate rested. `notes/coverage/D25-baseline-is-one-
      transaction.md` ran the baseline's own command shape on `poc/Tiny.sol`,
      whose line 41 is reachable only after a preceding call:

        baseline verbatim / plain BMC / either + `--solidity-max-tx 1`
                                      -> Reached 5 of 8, line 41 NOT reached
        plain BMC + `--solidity-max-tx 2`
                                      -> Reached 8 of 8, line 41 reached

      So the baseline is at one transaction, and `--k-induction
      --unlimited-k-steps` buys it no reach at all (the havoc suspicion is
      refuted, not merely unproven).

      ⛔ AND THIS IS WHY THE VALUE MUST NOT BE RAISED HERE, even though
      INVOCATION_DECISIONS rows 1 and 2 overturned tx=1 for the METHOD. The
      baseline itself gains 5/8 -> 8/8 at tx=2 and is LOCKED, so it cannot be
      re-run to restore parity. Running this side at 2 against a baseline at 1
      would be running deeper than the thing being compared to. The artefact /
      enumeration run may use whole-contract tx=2; THIS script produces the gate
      row and stays at 1. Two command lines, and INVOCATION_DECISIONS now prints
      both.

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


def esbmc_cmd(solast, flat, primary, focus, goals, solver_flags=(), max_tx=1):
    # `max_tx` IS A PARAMETER NOW, AND IT WAS A LITERAL `"1"` BEFORE.
    #
    # The docstring at the top of this file argues at length for 1 -- correctly,
    # FOR THE GATE. But INVOCATION_DECISIONS.md prints TWO command lines, and the
    # second one (whole contract, tx>=2) had NO COLLECTOR AT ALL: the decision was
    # taken, written down, and half implemented. Every path count this project has
    # is therefore from one cell, and "the tx ladder was never run" is a
    # consequence of there being no way to run it, not of anyone choosing not to.
    #
    # The gate is protected by two independent things, neither of which is this
    # default: `branch_gate.assert_gate_config` REFUSES any collection whose
    # recorded `solidityMaxTx` is not 1, and `collect()` below refuses to write a
    # non-gate cell into the gate's own directory.
    cmd = [
        str(ESBMC), str(solast), "--sol", str(flat),
        "--solidity-path-coverage",
        "--solidity-max-tx", str(max_tx),
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

    # THE THREE MECHANISMS THAT DEFLATE THE GATE'S NUMERATOR, CAPTURED HERE
    # BECAUSE THEY EXIST NOWHERE ELSE A CONSUMER CAN REACH.
    #
    # `branch_gate.py`'s docstring lists them -- internal calls withdrawn by
    # degradation or by the call-depth bound, and a short-circuit site over the
    # operand cap -- and says none is visible in the gate's output. It also
    # records that an earlier version of that paragraph CLAIMED one of them was
    # "reported beside the gate rather than folded into it", which was false:
    # traced end to end, `degraded_call_sites` is surfaced only by
    # `log_warning`, never reaches the report's `summary`, and is read nowhere.
    #
    # The measurement it needs therefore has to be taken HERE, at the only place
    # that sees the run's stdout. `notes/coverage/D27-the-gate-gap-is-named-in-
    # our-own-logs.md` read these same lines out of `work/*/run.log` after the
    # fact and found the shape they explain: the ONE benchmark that clears the
    # gate is the only one with nothing truncated. That census had to trust
    # `work/`, which is NOT reconciled against this journal -- a unit the
    # collection SKIPS never calls `one_run`, so its pre-ban directory survives
    # and 2026-07-30 logs sit beside 2026-08-01 ones. Recording into the journal
    # instead puts the numbers under the same reconciliation as everything else.
    #
    # A record written before this field existed carries NO key, and a consumer
    # must render that as "unrecorded" rather than as 0 -- the same third-state
    # rule the rest of this pipeline follows. Absent is not zero.
    m = re.search(
        r"(\d+) call site\(s\) are deeper than the call depth bound \((\d+)\)",
        out)
    if m:
        rec["depthBoundUnexpandedSites"] = int(m.group(1))
        rec["depthBound"] = int(m.group(2))
    else:
        rec["depthBoundUnexpandedSites"] = 0
    # Each DEGRADED unit line names one unit whose call sites were withdrawn to
    # fit the goal cap. Counted, not just flagged: st1inch shows twelve on a
    # single run and the count is what distinguishes that from an isolated one.
    rec["degradedUnits"] = out.count("DEGRADED unit ")
    m = re.search(r"(\d+) short-circuit site\(s\)[^.\n]*cap", out)
    rec["scSitesOverCap"] = int(m.group(1)) if m else 0
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
            fresh=False, max_tx=1, focus_with=(), scope="single", adhoc=None):
    # ---- THE LADDER'S TWO AXES, AND WHY THEY ARE NOT A PLAIN PRODUCT ----
    #
    # They are LENGTH x ALPHABET, and that is now read out of the source rather
    # than inferred from the option table.
    #
    # `--solidity-max-tx N` is the LENGTH of the call sequence: `emit_tx_driver`
    # (solidity_convert_contract.cpp:672-688) copies the transaction body N times
    # straight-line, each copy `_sol_per_tx_reseed(); _ESBMC_Nondet_Extcall_C();`.
    #
    # `--focus-function a,b` is the ALPHABET of that sequence: it filters which
    # `if (nondet) { f(...); return; }` arms the dispatcher body carries
    # (`get_unbound_function`, solidity_convert_constructor.cpp:346-453).
    #
    # ⚠ THE `return` IS THE WHOLE POINT, and it is why this is not a nested loop.
    # `then.copy_to_operands(then_expr, return_expr)` at :445-446, with the
    # comment "construct return; to avoid fall-through" at :316-317, means the
    # dispatcher RETURNS as soon as one arm is taken. So ONE TRANSACTION IS
    # EXACTLY ONE ENTRY CALL, and the reachable call sequences are the words of
    # length <= N over the focus alphabet.
    #
    # (The doc comment at solidity_convert_contract.cpp:712-729 draws the harness
    # as `while(nondet){ if(nondet)A(); if(nondet)B(); }` and OMITS the return,
    # which reads as "several calls per transaction" and is what this file used
    # to say below. The contrast that settles it is `build_bound_drive_helper`,
    # solidity_convert_constructor.cpp:645, which deliberately does NOT append a
    # return -- so that helper's loop really can call several per iteration.)
    #
    # Everything measured follows from that shape and no longer needs a second
    # explanation: a SINGLE-name focus is an alphabet of size one, so every word
    # is f^k and no tx bound reaches cross-function state (INVOCATION_DECISIONS
    # rows 1-2, poc/Tiny.sol: focus/tx1 60% -> whole/tx1 75% -> whole/tx2 100%;
    # note whole/tx1 is NOT 100%, which is exactly the return).
    #
    # `focus_with` is the middle cell: alphabet = {unit} + the functions that
    # WRITE what the unit reads, i.e. the cheap approximation of whole-contract
    # for benchmarks where whole does not finish. It only buys anything at
    # max_tx >= 2, because a word of length 1 cannot contain both letters.
    sflags, sreason = solver_flags_for(bench_key, solver_override)
    print(f"  [solver] {' '.join(sflags) if sflags else '(none)'} -- {sreason}",
          flush=True)
    if adhoc is not None:
        # AD-HOC TARGET: a hand-written PoC rather than a corpus benchmark.
        # R6 requires every investigation to start from a minimal reproduction,
        # and until now this collector could only be pointed at the six locked
        # BENCHES entries -- so the ladder it exposes had no cheap subject and
        # the only tx=2 measurement in the project was made by hand.
        flat = Path(adhoc[0]).resolve()
        primary = adhoc[1]
        project = None
        solast = Path(str(flat) + ".solast")
        if not solast.exists():
            solast = flat.with_suffix(".solast")
    else:
        flat_rel, primary, _solc, project = base.BENCHES[bench_key]
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
    #
    # ---- THE GATE'S DIRECTORY IS THE GATE'S, AND A LADDER CELL MAY NOT ENTER ----
    #
    # The unsuffixed directory is where every gate row's numerator comes from.
    # A ladder run that landed there would not corrupt one number, it would
    # REPLACE the gate collection with a deeper one, and `index.json` is rewritten
    # at the end of every collection -- so the gate would silently start reporting
    # a cell it is not entitled to read. `branch_gate.assert_gate_config` would
    # then refuse and the operator would be told the gate is broken, which is a
    # confusing way to discover that a ladder run overwrote it.
    #
    # Refused rather than auto-suffixed: inventing a directory name means the
    # operator does not know where the data went, and this file's own rule is
    # that two configurations must be distinguishable BY NAME in every later
    # table.
    # THE GATE CELL IS `scope=single, max_tx=1` AND NOTHING ELSE.
    # `scope` joins the two conditions that were already here, because it is a
    # third way to be a non-gate cell and the directory rule has to cover every
    # one of them or it covers none.
    if (max_tx != 1 or focus_with or scope != "single") and not out_suffix:
        sys.exit(
            f"{bench_key}: refusing to write a LADDER cell (scope={scope}, "
            f"max-tx={max_tx}, "
            f"focus-with={','.join(focus_with) or 'none'}) into the gate's own "
            f"directory {OUT / bench_key}.\n"
            f"The unsuffixed directory holds the collection every gate row is "
            f"computed from, and a collection rewrites its index.json wholesale. "
            f"Pass --out-suffix (e.g. --out-suffix __tx{max_tx}"
            + (f"__{scope}" if scope != "single" else "")
            + ("__focusset" if focus_with else "") + ").")
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
        # ---- SAY WHICH FIELD MOVED. "A DIFFERENT BINARY" IS SOMETIMES FALSE ----
        #
        # The comparison is on the whole dict, which is the right GATE -- but the
        # sentence it printed asserted the binary had changed, and that is not
        # what the dict differing means. MEASURED on this corpus: EscrowDst's 18
        # records carry THREE identities and st1inch's 22 carry three, while
        # `binaryMtime` is IDENTICAL within each benchmark. The binary file never
        # changed; `head` moved because commits were made while the collection
        # ran, and `srcDirty` flipped with them.
        #
        # The two cases need opposite actions and the old message could not tell
        # them apart:
        #   * binaryMtime differs -> a genuinely different binary produced those
        #     runs. Reusing their reports would quote the old build's numbers
        #     under the new build's name. --fresh is the only correct answer.
        #   * only head/srcDirty differ -> the SAME binary file produced them and
        #     the repo moved underneath. The measurement is homogeneous; what is
        #     lost is only the ability to name the source by commit. Discarding
        #     the collection here throws away good data for a bookkeeping change.
        #
        # Still REFUSED in both cases -- this is a gate, and an operator who is
        # told which case it is can act; one who is told a false thing cannot.
        mt_now = (ident or {}).get("binaryMtime")
        mt_moved = sum(1 for b in stale.values()
                       if (b or {}).get("binaryMtime") != mt_now)
        if mt_moved:
            headline = (f"{mt_moved} of them were produced by a genuinely "
                        f"DIFFERENT BINARY (binaryMtime differs)")
        else:
            headline = ("the BINARY IS THE SAME FILE in every one of them "
                        "(binaryMtime is identical) -- only `head`/`srcDirty` "
                        "moved, i.e. the repository was committed to while this "
                        "collection ran. The measurement is homogeneous; what "
                        "is lost is the ability to name its source by commit")
        sys.exit(
            f"{bench_key}: {len(stale)} of {len(done)} journal record(s) do not "
            f"match the identity on disk now, and {headline}.\n"
            f"  now:  {ident}\n"
            + "".join(f"  was:  {t} -> {b}\n" for t, b in shown)
            + (f"  ... and {len(stale) - len(shown)} more\n"
               if len(stale) > len(shown) else "")
            + "Refused either way, because resuming would skip those runs and "
              "reuse their reports. Re-run with --fresh to discard this "
              "collection and start over, or move the directory aside to keep "
              "it.")
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
        # Pair-1 analogue: one run, no focus, i.e. the FULL alphabet.
        #
        # ⚠ THE JUSTIFICATION THAT USED TO STAND HERE IS FALSE AND IS REPLACED
        # RATHER THAN DELETED. It read: "the only configuration in which
        # cross-function state can be established inside a single transaction
        # (each dispatch guard is independent)". The guards are NOT independent
        # -- each arm ends in a `return` (solidity_convert_constructor.cpp:445),
        # so one transaction is exactly one entry call and NO configuration
        # establishes cross-function state inside a single transaction. The
        # measurement said so before the source did: poc/Tiny.sol whole/tx=1 is
        # 75%, not 100%, and only whole/tx=2 reaches 100%.
        #
        # What is true, and is why this branch exists: dropping the focus makes
        # the alphabet the whole contract, so a U here means "no witness within
        # this bound" rather than "that function was never offered to the
        # dispatcher". At max_tx=1 that is still only length-one words, so a
        # state-guarded path stays unreachable; the width is only redeemable at
        # max_tx >= 2.
        tag = "whole"
        if tag in done:
            runs.append(done[tag])
        else:
            rec, d = one_run(tag,
                             esbmc_cmd(solast, flat,
                                       None if pkind == "library" else primary,
                                       None, goals, sflags, max_tx),
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
        if project is None:
            # AN AD-HOC TARGET HAS NO PROJECT TREE, AND THE SCOPE RULE LIVES
            # THERE. `enumerate_own_callable_functions` decides which functions
            # are units by asking whether their flat block came from a
            # project-own file (METHODOLOGY 3), which is the authoritative rule
            # and the one collect.py's own Pair 2 uses. A PoC has no such tree.
            #
            # Refused rather than approximated with a second enumerator: two
            # implementations of one scope rule is the defect this file already
            # documents twice (the `project_own_contract_names` truncation that
            # silently ran 2 of aqua's 8 callables while printing "2/2 run(s)
            # produced a report"). A quiet fallback here would be the same
            # failure with a new name.
            sys.exit(
                f"--sol/--contract is an AD-HOC target with no project tree, "
                f"so the per-method scope rule cannot be applied and "
                f"--scope {scope} is unavailable for it. Use --scope whole "
                f"(the ad-hoc target's purpose is the LENGTH axis, which needs "
                f"no unit enumeration), or add the contract to collect.py's "
                f"BENCHES if it is a real benchmark.")
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
                # AND CLEAR ITS WORKDIR. A skipped unit never calls `one_run`,
                # which is the only thing that empties `work/<tag>/`, so an
                # EARLIER collection's cov-report.json and run.log survive there
                # untouched -- for a unit this collection did not run at all.
                #
                # MEASURED before this line existed: 48 of the 95
                # cov-report.json files under notes/coverage/pathcov/ were
                # leftovers of exactly this kind, every one belonging to a
                # `library-has-no-dispatcher` record (D38 section 4a). `reports/`
                # is reconciled against the journal and `branch_gate.py` refuses
                # a count mismatch, so the GATE never saw them -- but every
                # ad-hoc consumer that walks the tree does, and this project has
                # written more than one of those.
                #
                # Removed rather than left with a marker: the journal already
                # records the skip and its full reasoning, so the directory
                # carries no information that is not better recorded elsewhere,
                # and a stale file that exists is a file something will read.
                wd = out_dir / "work" / tag
                removed = 0
                if wd.is_dir():
                    for p in sorted(wd.glob("*")):
                        if p.is_file():
                            p.unlink()
                            removed += 1
                print(f"  [{i}/{len(todo)}] {tag}  (skipped: library"
                      + (f"; cleared {removed} stale file(s) from work/{tag})"
                         if removed else ")"),
                      flush=True)
                record(rec)
                continue
            print(f"  [{i}/{len(todo)}] {tag}", flush=True)
            # THE FOCUS SET IS A COMMA-SEPARATED STRING, NOT A REPEATED FLAG.
            # `optionst::cmdline()` calls `set_option` per value and `set_option`
            # OVERWRITES, so `--focus-function A --focus-function B` parses
            # cleanly and verifies only B. The multi-name form (task #5) takes one
            # comma-separated string, same as `--contract`.
            focus_arg = ",".join([fname] + [f for f in focus_with if f != fname])
            cmd = esbmc_cmd(solast, flat, primary, focus_arg, goals, sflags,
                            max_tx)
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
            # THE WIDTH AXIS AS A NAME, beside the two values it is computed
            # from. `mode` collapses `single` and `set` into "per-method", which
            # is fine for a human reading the table and wrong for anything
            # deciding whether a row belongs to the gate: the gate cell is
            # scope=single AND max_tx=1, and a `set` run has the same `mode`
            # string as a `single` one.
            "scope": scope,
            # Whether this row came from a corpus benchmark or a hand-written
            # minimal reproduction. A PoC row is not a corpus row and no table
            # may mix them; recorded rather than inferred from the key's prefix.
            "adhocTarget": None if adhoc is None else str(flat),
            # RECORDED FROM THE ARGUMENT, not from a literal. It used to be a
            # hardcoded 1 beside a hardcoded flag; the two agreed only because
            # neither could change. `branch_gate.assert_gate_config` reads THIS
            # field to decide whether a collection may be quoted into the gate
            # table, so a literal here would let a ladder cell present itself as
            # the gate cell.
            "solidityMaxTx": max_tx,
            # The extra names added to every unit's focus set. Empty for the gate
            # and artefact cells; non-empty for the middle cell of the width axis.
            "focusWith": list(focus_with),
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
    ap.add_argument("--scope", choices=("single", "set", "whole"),
                    default="single",
                    help="WIDTH axis, i.e. the ALPHABET of the call sequence: "
                         "'single' passes --focus-function <unit> (one name, "
                         "the GATE cell); 'set' passes --focus-function "
                         "<unit>,<--focus-with names> ; 'whole' passes no "
                         "--focus-function at all. Requires --out-suffix for "
                         "anything but 'single'. NOTE the alphabet only buys "
                         "reach at --max-tx >= 2: one transaction is EXACTLY "
                         "one entry call (each dispatcher arm ends in a "
                         "`return`, solidity_convert_constructor.cpp:445), so a "
                         "length-one word cannot contain two letters however "
                         "wide the alphabet is.")
    ap.add_argument("--sol", default="",
                    help="AD-HOC TARGET: a flat .sol outside BENCHES (its "
                         "<file>.solast must sit beside it). Requires "
                         "--contract and --scope whole. Exists so the ladder "
                         "has a MINIMAL subject: R6 requires a <80-line "
                         "reproduction before any investigation, and until now "
                         "this collector could only be pointed at the six "
                         "locked corpus entries.")
    ap.add_argument("--contract", default="",
                    help="contract name for --sol (the --contract value ESBMC "
                         "is given)")
    ap.add_argument("--max-tx", type=int, default=1,
                    help="DEPTH axis, i.e. the LENGTH of the call sequence: "
                         "--solidity-max-tx. ⚠ ESBMC's OWN DEFAULT IS 2, not 1 "
                         "and not unbounded: --solidity-path-coverage is absent "
                         "from get_tx_bound's `unbounded_modes` "
                         "(solidity_convert_contract.cpp:623-655), so passing "
                         "nothing gives 2. This script defaults to 1, which is "
                         "the GATE cell and the only value the gate table may "
                         "read. This was a hardcoded literal until now, so the "
                         "tx ladder had no command-line entry at all and has "
                         "never been run on a real benchmark -- the only tx=2 "
                         "measurement in the project is on poc/Tiny.sol. "
                         "Requires --out-suffix for any value but 1, so a "
                         "ladder run cannot overwrite the gate's collection. "
                         "NOTE 0 is NOT unbounded: under coverage it is the "
                         "SHALLOWEST setting (the back-edge is rewritten to a "
                         "SKIP, leaving one transaction).")
    ap.add_argument("--focus-with", default="",
                    help="WIDTH axis: comma-separated EXTRA function names "
                         "added to every unit's focus set, so a later "
                         "transaction has something other than the unit itself "
                         "to dispatch. Empty (default) reproduces the "
                         "single-name focus exactly. Use for the middle cell of "
                         "the width axis -- {unit} plus the functions that "
                         "WRITE what the unit reads -- which is the cheap "
                         "approximation of whole-contract on benchmarks where "
                         "whole does not finish. Measured: a SINGLE-name focus "
                         "reaches no cross-function state at ANY tx bound, "
                         "because the other functions are not in the dispatcher "
                         "for a later transaction to call. Requires "
                         "--out-suffix.")
    a = ap.parse_args()
    if a.list or not (a.bench or a.sol):
        for k in base.BENCHES:
            print(k)
        return 0

    # `--whole` is kept as an alias of `--scope whole` rather than removed:
    # it is what every existing invocation and every recorded command line in
    # this tree says, and silently changing the spelling of a configuration is
    # how a later table stops matching the command that produced it.
    scope = "whole" if a.whole else a.scope
    focus_with = tuple(s for s in
                       (x.strip() for x in a.focus_with.split(",")) if s)
    if scope == "set" and not focus_with:
        sys.exit("--scope set needs --focus-with: without extra names the "
                 "alphabet is {unit} and the run is byte-identical to "
                 "--scope single, which would file the same measurement under "
                 "two different configuration names")
    if scope != "set" and focus_with:
        sys.exit(f"--focus-with is only meaningful with --scope set; under "
                 f"--scope {scope} the names would be "
                 + ("appended to a focus this run does not pass"
                    if scope == "whole" else "silently ignored"))

    adhoc = None
    if a.sol:
        if not a.contract:
            sys.exit("--sol needs --contract (ESBMC scopes path coverage by "
                     "--contract; without it the run has no scope_contract and "
                     "the covered-set fingerprint is a different one)")
        if scope != "whole":
            sys.exit(f"--sol supports only --scope whole, not {scope}; see the "
                     f"refusal in collect() for why a second scope rule is not "
                     f"written here")
        p = Path(a.sol).resolve()
        if not p.exists():
            sys.exit(f"--sol: no such file: {p}")
        adhoc = (str(p), a.contract)
        # The key is the row's name in every later table, so it says out loud
        # that the row is a PoC and which file it came from.
        a.bench = "poc__" + p.stem
    elif a.bench not in base.BENCHES:
        sys.exit(f"unknown bench: {a.bench}")

    idx = collect(a.bench, scope == "whole", a.timeout, a.goals, a.out_suffix,
                  a.solver_flags.split(), a.fresh, a.max_tx, focus_with,
                  scope, adhoc)
    ok = sum(1 for r in idx["runs"] if r["reportPresent"])
    killed = sum(1 for r in idx["runs"] if r["killedByOuterTimeout"])
    print(f"{a.bench}: {ok}/{len(idx['runs'])} run(s) produced a report, "
          f"{killed} killed by the outer timeout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
