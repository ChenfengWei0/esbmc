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
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collect as base  # noqa: E402  -- the LOCKED baseline side, reused verbatim

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
COLLECTION_SCHEMA = "veriput-pathcov-collection/2"


def file_identity(path):
    """Identity of an exact file consumed by the collection."""
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {"path": str(resolved), "size": None, "mtime_ns": None}


def quarantine_collection(out_dir):
    """Move an existing collection aside and return its new path, if any."""
    if not out_dir.exists() or not any(out_dir.iterdir()):
        return None
    archived = out_dir.with_name(f"{out_dir.name}.superseded.{time.time_ns()}")
    out_dir.rename(archived)
    return archived


def kill_process_group(proc):
    """Kill a process and all descendants started in its session."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, AttributeError):
        pass


# ---- WHERE THE INPUT LIVES IS NOW THE POC'S ANSWER, NOT A CONSTANT ----
#
# The shared corpus directory `notes/coverage/inputs/` has been DELETED. It was
# the benchmark: every driver here resolves `INPUTS / <basename>` off a
# benchmark key, so while it existed a whole-corpus sweep was one command away
# no matter how many refusals were bolted on above.
#
# Each PoC now owns hardlinks to exactly the files ITS unit needs, under
# `notes/coverage/poc_units/<poc-id>/inputs/`, and `poc_one.py` names that
# directory here. Overridden at the DEFINITION rather than at each use: every
# consumer then gets the PoC's copy by construction, and the copy is the same
# inode as the corpus row was measured on, so nothing downstream can tell the
# difference except that it cannot see the other five benchmarks.
#
# Unset, this still points at the old location -- which no longer exists, so a
# benchmark-shaped invocation dies on a missing file instead of sweeping. That
# is the intended failure, and `collect()` says so by name.
INPUTS = Path(os.environ.get("VERIPUT_INPUTS_DIR", str(REPO / "notes/coverage/inputs")))
# `collect` resolves the project scope file out of ITS own copy of this
# constant, and collect.py is LOCKED (it is the branch-coverage baseline side).
# Rebinding the imported module's attribute is how the override reaches it
# without editing a locked file.
base.INPUTS = INPUTS
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


def esbmc_cmd(solast,
              flat,
              primary,
              focus,
              goals,
              solver_flags=(),
              max_tx=1,
              instrument_only=None,
              unwind=None,
              probe_witnesses=0):
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
        str(ESBMC),
        str(solast),
        "--sol",
        str(flat),
        "--solidity-path-coverage",
        "--solidity-max-tx",
        str(max_tx),
        "--cov-report-json",
        "--path-cov-max-goals",
        str(goals),
        "--memlimit",
        MEMLIMIT,
    ] + list(solver_flags)
    if unwind is not None:
        # ---- THE CALL-DEPTH BOUND IS THIS FLAG, AND IT WAS NEVER PASSED ----
        #
        # The pass prints "expanded N internal call(s) into their calling unit
        # (call depth bound = U)" and U is `path_cov_unwind`, i.e. --unwind. It
        # defaults to 4 ("no --unwind given; bounding symbolic execution at 4 to
        # match the path enumeration's own loop bound"), and this collector had
        # no way to move it -- so every corpus row was produced at 4 and the
        # bound was invisible in the data.
        #
        # WHY IT MATTERS FOR THE GATE, measured on the current collection: the
        # ONE benchmark that clears the branch-coverage gate is the only one
        # with NOTHING truncated by this bound.
        #     aqua        0/6  runs truncated, 0 sites past the bound   PASS
        #     EscrowDst   4/4                  8                        FAIL
        #     EscrowSrc   6/6                 20                        FAIL
        #     farming    12/12                42                        FAIL
        # A call site past the bound is a callee whose decisions never join the
        # caller's path identity, so they cannot appear in the numerator.
        #
        # IT CANNOT INFLATE THE GATE. The denominator is the canonical in-scope
        # decision set, which is a property of the SOURCE and does not move; a
        # larger bound can only let more of those same decisions be walked. The
        # comparison stays honest as long as the value TRAVELS WITH THE ROW,
        # which is why it is recorded in index.json below rather than only
        # appearing in a shell history.
        cmd += ["--unwind", str(unwind)]
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
    if instrument_only:
        # THE ALPHABET AND THE DENOMINATOR ARE TWO DIFFERENT SETS.
        #
        # `--focus-function` decides which entries the harness may CALL;
        # `--path-cov-instrument-only` decides which units are ENUMERATED, i.e.
        # what the published denominator is. Passing the second is what makes a
        # `set` cell comparable with a `single` cell at all.
        #
        # MEASURED without it: `--focus-function dock,ship` instrumented 2796
        # paths (dock 63 + ship 2733) instead of 63, and both the tx=1 and tx=2
        # cells were killed at the 300 s outer timeout with no usable answer. So
        # this is not a speed knob -- without it the widened-alphabet cell is
        # neither affordable NOR comparable.
        cmd += ["--path-cov-instrument-only", instrument_only]
    if probe_witnesses:
        cmd += [
            "--branch-function-coverage", "--path-cov-probe", "--all-witnesses", "--max-witnesses",
            str(probe_witnesses)
        ]
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
            return subprocess.run(args, capture_output=True, text=True, cwd=str(REPO),
                                  timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": int(ESBMC.stat().st_mtime) if ESBMC.exists() else 0,
    }


def _named_values_to_dict(values, *, env=False):
    out = {}
    if isinstance(values, dict):
        items = values.items()
    else:
        items = ((v.get("name"), v.get("value")) for v in values or [] if isinstance(v, dict))
    for name, value in items:
        if not name:
            continue
        if env:
            for p in ("msg_", "tx_", "block_"):
                if name.startswith(p):
                    name = p[:-1] + "." + name[len(p):]
                    break
        out[name] = value
    return out


def _extcall_returns(values):
    out = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        name = item.get("symbol") or item.get("name")
        if name:
            out.append({"symbol": name, "value": item.get("value")})
    return out


def _claim_parts(msg):
    m = re.match(r"(?P<pf>.+):path:(?P<pid>\d+)$", msg or "")
    if not m:
        return None, None, None
    pf = m.group("pf")
    pid = m.group("pid")
    fm = re.search(r"@F@([^#]+)#", pf)
    unit = fm.group(1) if fm else pf.rsplit("@F@", 1)[-1].split("#", 1)[0]
    return pf, unit, pid


def _log_path_depths(log):
    depths = {}
    for m in re.finditer(r"path enc=(\d+) depth=(\d+)", log or ""):
        depths[m.group(1)] = int(m.group(2))
    return depths


def report_from_ce_journal(journal, log=""):
    """Build a stage-2 enumeration report from the live CE journal.

    The journal is not a coverage report and must stay marked partial. It is,
    however, authoritative evidence for claims that already reached the solver
    and were refuted before an outer timeout killed the run.
    """
    if journal.get("kind") != "solidity-complete-path-ce-journal":
        return None
    witnessed = journal.get("witnesses") or {}
    if not isinstance(witnessed, dict) or not witnessed:
        return None
    depths = _log_path_depths(log)
    claims = []
    for entry in witnessed.values():
        if not isinstance(entry, dict):
            continue
        msg = entry.get("claim")
        path_function, unit, path_id = _claim_parts(msg)
        if not path_function or path_id is None:
            continue
        depth = entry.get("path_depth") or entry.get("decision_depth")
        if depth is None:
            depth = depths.get(str(path_id))
        if depth is None:
            # Legacy journals predate the explicit path_depth field. ESBMC's
            # path encoding starts at 1 and appends each decision bit with
            # `tr = tr*2 + guard_value`, so floor(log2(enc)) is the exact
            # decision depth for those rows.
            try:
                depth = int(path_id).bit_length() - 1
            except ValueError:
                continue
        claim = {
            "bound": {
                "kind": "bounded"
            },
            "ce_extraction": {
                "compact_trace": bool(entry.get("compact_trace")),
                "harness_nondets_dropped": entry.get("dropped_internal"),
                "payload_symbols_exempt_from_slicing": bool(entry.get("payload_symbols_protected")),
                "scoped_to_claim": bool(entry.get("scoped_to_claim")),
                "sliced": bool(entry.get("sliced")),
                "witness_count": entry.get("witness_count", 1),
            },
            "column": 0,
            "condition": f"{unit}:path:{path_id}",
            "decisions": entry.get("decisions") or [],
            "entry_storage": _named_values_to_dict(entry.get("entry_storage")),
            "env": _named_values_to_dict(entry.get("env"), env=True),
            "events": entry.get("events") or [],
            "exit_kind": "revert" if entry.get("revert_pre_rollback") else "normal",
            "extcall_returns": _extcall_returns(entry.get("extcall_returns")),
            "file": "",
            "final_state": _named_values_to_dict(entry.get("final_state")),
            "function": "",
            "inputs": _named_values_to_dict(entry.get("inputs")),
            "line": 0,
            "path_depth": int(depth),
            "path_function": path_function,
            "path_id": str(path_id),
            "return_value": entry.get("return_value"),
            "return_value_known": bool(entry.get("return_value_known")),
            "state_written_value_unavailable": entry.get("state_written_unrendered") or [],
            "status": "F",
            "witnessed_in_earlier_round": False,
        }
        witnesses = []
        for w in entry.get("witnesses") or []:
            if not isinstance(w, dict):
                continue
            witnesses.append({
                "entry_storage": _named_values_to_dict(w.get("entry_storage")),
                "env": _named_values_to_dict(w.get("env"), env=True),
                "extcall_returns": _extcall_returns(w.get("extcall_returns")),
                "final_state": _named_values_to_dict(w.get("final_state")),
                "inputs": _named_values_to_dict(w.get("inputs")),
                "return_value": w.get("return_value"),
                "return_value_known": bool(w.get("return_value_known")),
            })
        if witnesses:
            claim["witnesses"] = witnesses
        claims.append(claim)
    if not claims:
        return None
    total = journal.get("claims_total") or len(claims)
    decided = journal.get("claims_decided")
    return {
        "claims": claims,
        "coverage_type": "solidity-complete-path",
        "partial": True,
        "source_files": [],
        "summary": {
            "F_feasible_with_ce":
            len(claims),
            "F_with_multiple_witnesses":
            sum(1 for c in claims if (c.get("ce_extraction") or {}).get("witness_count", 1) > 1),
            "I_proven_unreachable":
            0,
            "U_of_which_bounded_holds":
            0,
            "U_reasons": {
                "bounded-holds": 0,
                "claim-budget-exceeded": 0,
                "named-obstacle": 0,
                "not-solved-this-run": max(0,
                                           int(total) - int(decided or len(claims))),
                "run-died-before-solving": 0,
                "solver-unknown": 0,
                "unit-not-entered": 0,
            },
            "U_undecided":
            max(0,
                int(total) - len(claims)),
            "covered":
            len(claims),
            "partial":
            True,
            "paths_total":
            total,
            "percentage": (100.0 * len(claims) / total) if total else 0.0,
            "total":
            total,
            "uncovered":
            max(0,
                int(total) - len(claims)),
            "witnesses_total":
            sum((c.get("ce_extraction") or {}).get("witness_count", 1) for c in claims),
        },
        "veriput_salvage": {
            "from": "cov-ce-journal.json",
            "claims_decided": decided,
            "claims_total": total,
            "reason": "outer-timeout-with-feasible-path-witnesses",
        },
    }


def one_run(tag, cmd, timeout, workdir):
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in workdir.glob("*"):
        stale.unlink()
    t0 = time.time()
    killed = False
    proc = subprocess.Popen(cmd,
                            cwd=str(workdir),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        rc, out = proc.returncode, stdout + stderr
    except subprocess.TimeoutExpired:
        killed = True
        kill_process_group(proc)
        stdout, stderr = proc.communicate()
        rc, out = -1, stdout + stderr
    except BaseException:
        kill_process_group(proc)
        raise
    finally:
        kill_process_group(proc)
    wall = time.time() - t0
    (workdir / "run.log").write_text(out)

    report = workdir / "cov-report.json"
    rec = {
        "tag": tag,
        "cmd": " ".join(cmd),
        "cmdArgv": [str(arg) for arg in cmd],
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
    m = re.search(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)", out)
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
    m = re.search(r"(\d+) call site\(s\) are deeper than the call depth bound \((\d+)\)", out)
    if m:
        rec["depthBoundUnexpandedSites"] = int(m.group(1))
        rec["depthBound"] = int(m.group(2))
    else:
        rec["depthBoundUnexpandedSites"] = 0
    # ---- AND WHICH ONES, BY NAME ----
    #
    # The count alone cannot be acted on. The tool prints the mangled ids of
    # every function it did not expand, and those names are the join key to the
    # other modelling limits: on farming/setDistributor the list contains
    # `FarmingLib@F@_contextToInfo` and `FarmingLib@F@_infoToContext`, which are
    # exactly the two functions whose inline assembly does the struct
    # reinterpret that havocs `FarmingLib.Info` (see asmHavocStructTypes below).
    # Two separate over-approximations landing on ONE object is a different
    # finding from two unrelated ones, and only the names show it.
    #
    # `[]` means the warning did not appear; a record predating this field has
    # no key at all.
    m = re.search(
        r"call site\(s\) are deeper than the call depth bound \(\d+\) and "
        r"were NOT expanded \(([^)]*)\)", out)
    rec["depthBoundUnexpandedNames"] = ([n.strip() for n in m.group(1).split(",")
                                         if n.strip()] if m else [])

    # ---- TWO MORE MODELLING LIMITS THAT NOBODY READ ----
    #
    # Both are printed by every run and neither reached any record.
    #
    # LOOPS TRUNCATED: the tool's own words are "silently assumed away", and it
    # says the goals behind them "are counted as uncovered" -- i.e. this deflates
    # the numerator of the very gate this project is measured against, and does
    # so without any per-path token. None, not 0, when the warning is absent:
    # a run that did not reach the warning point did not say there were none.
    #
    # ARITHMETIC RE-SOLVE: recorded as the STRING the tool printed, because the
    # consequence is asymmetric and a boolean would hide it -- OFF means a
    # witnessed path whose counterexample wraps is emitted as a normal-exit test
    # and is RED on the unmodified contract, which is a defect in the DELIVERABLE
    # and not merely a coverage loss.
    m = re.search(r"(\d+) loop\(s\) hit the unwind bound", out)
    rec["loopsTruncatedAtUnwindBound"] = int(m.group(1)) if m else None
    m = re.search(r"Arithmetic Re-solve: (\w+)", out)
    rec["arithResolve"] = m.group(1) if m else None
    # Each DEGRADED unit line names one unit whose call sites were withdrawn to
    # fit the goal cap. Counted, not just flagged: st1inch shows twelve on a
    # single run and the count is what distinguishes that from an isolated one.
    rec["degradedUnits"] = out.count("DEGRADED unit ")
    m = re.search(r"(\d+) short-circuit site\(s\)[^.\n]*cap", out)
    rec["scSitesOverCap"] = int(m.group(1)) if m else 0

    # ---- THE THREE MULTI-PROPERTY SELF-ASSESSMENT COUNTERS ----
    #
    # These already existed in ESBMC's output and NOTHING in this pipeline read
    # them. That is the exact shape this project keeps paying for: an instrument
    # that is printed, never parsed, and then re-derived by hand from a
    # suspicion. The suspicion here was specific -- that `--multi-property`
    # loses verdicts it had already obtained when a later claim blocks or the
    # outer timeout cuts the run -- and all three counters exist to answer it.
    #
    # Recorded as `None` when the line is ABSENT rather than as 0, because the
    # line is printed only by a run that got as far as its own summary: a run
    # killed before then has no counter, and "the run did not say" is not "the
    # run said zero". Same third-state rule as `depthBoundUnexpandedSites`
    # above, and the opposite default, deliberately -- that one is a census of
    # sites which is genuinely 0 when unmentioned, this one is a self-report.
    m = re.search(r"Claim Budget: (\d+)s per claim — (\d+) claim\(s\) "
                  r"abandoned over budget", out)
    rec["claimBudgetSeconds"] = int(m.group(1)) if m else None
    rec["claimsAbandonedOverBudget"] = int(m.group(2)) if m else None
    m = re.search(r"Verdicts Preserved: (\d+)", out)
    rec["verdictsPreserved"] = int(m.group(1)) if m else None
    m = re.search(r"Claim Multiplicity: (\d+) extra solve\(s\)", out)
    rec["claimExtraSolves"] = int(m.group(1)) if m else None
    m = re.search(r"(\d+) decided verdict\(s\) superseded by a different "
                  r"decided verdict", out)
    rec["claimVerdictsSuperseded"] = int(m.group(1)) if m else None

    # ---- WHAT THE ASSEMBLY OVER-APPROXIMATION TOOK AWAY, BY NAME ----
    #
    # A certification refutation can fail for a reason this driver cannot name:
    # a quantity OUTSIDE the coordinate set is still free, so the witness escapes
    # the path through it. Where that quantity was made free by ESBMC's own
    # inline-assembly over-approximation, ESBMC SAYS SO, in its own words, on
    # stdout -- and until now those words were thrown away with the rest of the
    # log. Parsing them turns "the two reasons cannot be told apart" into a
    # NAME, which is what R14 bucket (1) requires as its first item.
    #
    # TWO FAMILIES, kept apart because they have different consequences:
    #
    #   * unsupported Yul construct -- the assembly BLOCK is over-approximated.
    #     Names the construct (mload/mstore/call/staticcall/chainid/byte/
    #     convert_failure) and the source line.
    #   * struct/value reinterpret -- ESBMC names the variable it HAVOC'D and
    #     the two struct types involved. This is the one that reaches a
    #     coordinate: the havoc'd local's declared type is the join key back to
    #     a refused state variable. MEASURED on farming/setDistributor: 20
    #     warnings on a unit with no assembly of its own, of which the last two
    #     havoc 'tag-struct FarmingLib.Info' -- the declared type of
    #     `state._farm`, which the generalise driver refuses as a coordinate.
    #
    # `tag-struct ` is ESBMC's internal type-tag prefix and is stripped, so the
    # recorded name is the Solidity type a reader can look up. The RAW line is
    # not kept: run.log beside this record has all 20 verbatim.
    #
    # COUNTS ARE ALWAYS SET (0, not None) -- unlike the three counters above,
    # this is a census over lines that appear during parsing, long before any
    # summary, so a run that produced no such line genuinely produced none.
    asm_constructs = {}
    asm_lines = set()
    for m in re.finditer(
            r"\[approx\] inline assembly at \S+?:(\d+): over-approximating "
            r"- unsupported Yul construct '([^']+)'", out):
        asm_lines.add(int(m.group(1)))
        k = m.group(2)
        asm_constructs[k] = asm_constructs.get(k, 0) + 1
    asm_havoc_locals = set()
    asm_havoc_types = set()
    for m in re.finditer(
            r"\[approx\] inline assembly at \S+?:(\d+): over-approximating "
            r"struct/value reinterpret '[^']*' \(struct '([^']+)' := "
            r"struct '([^']+)'\) - '([^']+)' havoc'd to nondet", out):
        asm_lines.add(int(m.group(1)))
        for t in (m.group(2), m.group(3)):
            asm_havoc_types.add(t[len("tag-struct "):] if t.startswith("tag-struct ") else t)
        asm_havoc_locals.add(m.group(4))
    rec["asmApproxSites"] = out.count("[approx] inline assembly at ")
    rec["asmApproxLines"] = sorted(asm_lines)
    rec["asmApproxConstructs"] = asm_constructs
    rec["asmHavocLocals"] = sorted(asm_havoc_locals)
    rec["asmHavocStructTypes"] = sorted(asm_havoc_types)

    d = None
    if report.exists():
        try:
            d = json.loads(report.read_text())
        except ValueError as e:
            rec["reportParseError"] = str(e)
            return rec, None
    else:
        journal = workdir / "cov-ce-journal.json"
        if journal.exists():
            try:
                d = report_from_ce_journal(json.loads(journal.read_text()), out)
            except ValueError as e:
                rec["journalParseError"] = str(e)
            if d is not None:
                rec["reportPresent"] = True
                rec["reportFromJournal"] = True
                rec["reportPartial"] = True
                rec["journalClaimsDecided"] = d.get("veriput_salvage", {}).get("claims_decided")
                rec["journalClaimsTotal"] = d.get("veriput_salvage", {}).get("claims_total")
                report.write_text(json.dumps(d, indent=2) + "\n")
    if d is not None:
        s = d.get("summary", {})
        rec["pathsTotal"] = s.get("paths_total")
        rec["F"] = s.get("F_feasible_with_ce")
        rec["U"] = s.get("U_undecided")
        rec["uReasons"] = s.get("U_reasons")
        rec["decisionSequences"] = s.get("decision_sequences")
        return rec, d
    return rec, None


def collect(bench_key,
            whole,
            timeout,
            goals,
            out_suffix="",
            solver_override=(),
            esbmc_extra=(),
            fresh=False,
            max_tx=1,
            focus_with=(),
            scope="single",
            adhoc=None,
            only=(),
            unwind=None,
            probe_witnesses=0):
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
    esbmc_flags = list(sflags) + list(esbmc_extra)
    print(f"  [solver] {' '.join(sflags) if sflags else '(none)'} -- {sreason}", flush=True)
    if esbmc_extra:
        print(f"  [esbmc-arg] {' '.join(esbmc_extra)}", flush=True)
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
        # SAY WHICH OF THE TWO THIS IS. "Run collect.py first" was true while a
        # shared corpus directory existed; it has been deleted, and the common
        # way to land here now is a BENCHMARK-shaped invocation -- exactly the
        # run the deletion exists to stop. Telling that operator to regenerate
        # an AST would send them to rebuild the corpus.
        if os.environ.get("VERIPUT_INPUTS_DIR"):
            sys.exit(f"missing AST: {solast}\n"
                     f"  VERIPUT_INPUTS_DIR is set to {INPUTS}, so this is a "
                     f"PoC whose private input directory is incomplete. "
                     f"Rebuild it: python3 "
                     f"notes/coverage/scripts/poc_split.py")
        sys.exit(f"missing AST: {solast}\n"
                 f"  The shared corpus directory notes/coverage/inputs/ HAS BEEN "
                 f"DELETED on purpose: it was the benchmark, and a benchmark is not "
                 f"a runnable thing here. Each PoC owns its input.\n"
                 f"    python3 notes/coverage/scripts/poc_one.py --list\n"
                 f"    python3 notes/coverage/scripts/poc_one.py <poc-id>\n"
                 f"  To restore the corpus for a baseline re-measurement (it is in "
                 f"git, nothing was lost):\n"
                 f"    git checkout -- notes/coverage/inputs/")

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
    # `unwind` joins the list for the same reason max_tx is on it: it is a
    # CONFIGURATION, the gate's directory holds exactly one configuration, and a
    # collection rewrites its index.json wholesale. A deeper-bound run landing
    # there would replace the gate's collection with one the gate is not
    # entitled to read, and the operator would discover it as "the gate is
    # broken" rather than as "I overwrote it".
    if (max_tx != 1 or focus_with or scope != "single" or only or unwind is not None
            or probe_witnesses) and not out_suffix:
        sys.exit(f"{bench_key}: refusing to write a LADDER cell (scope={scope}, "
                 f"max-tx={max_tx}, unwind={unwind if unwind is not None else 'default'}, "
                 f"only={','.join(only) or 'all'}, "
                 f"focus-with={','.join(focus_with) or 'none'}) into the gate's own "
                 f"directory {OUT / bench_key}.\n"
                 f"The unsuffixed directory holds the collection every gate row is "
                 f"computed from, and a collection rewrites its index.json wholesale. "
                 f"Pass --out-suffix (e.g. --out-suffix __tx{max_tx}" +
                 (f"__unwind{unwind}" if unwind is not None else "") +
                 (f"__{scope}" if scope != "single" else "") +
                 ("__focusset" if focus_with else "") + ").")
    pkind = base.primary_contract_kind(flat, primary)
    out_dir = OUT / (bench_key + out_suffix)
    if fresh:
        archived = quarantine_collection(out_dir)
        if archived is not None:
            print(f"  [fresh] preserved the previous collection at "
                  f"{archived}", flush=True)
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

    # A journal tag is reusable only inside the configuration that produced it.
    # The binary check below is necessary but insufficient: scope, tx depth,
    # witness multiplicity and solver flags can all change while the executable
    # stays byte-identical. Refuse old/unversioned manifests rather than rewrite
    # their index with today's values around yesterday's reports.
    old_index_path = out_dir / "index.json"
    if done and not fresh:
        try:
            old_index = json.loads(old_index_path.read_text())
        except (OSError, ValueError) as exc:
            sys.exit(f"{bench_key}: cannot resume {journal}: its collection "
                     f"manifest {old_index_path} is missing or invalid ({exc}). "
                     "Pass --fresh or move the directory aside.")
        expected = {
            "schema": COLLECTION_SCHEMA,
            "primary": {
                "name": primary,
                "kind": pkind
            },
            "flatInputIdentity": file_identity(flat),
            "astInputIdentity": file_identity(solast),
            "esbmcIdentity": file_identity(ESBMC),
            "scope": scope,
            "onlyUnits": list(only),
            "solidityMaxTx": max_tx,
            "unwind": unwind,
            "focusWith": list(focus_with),
            "pathCovMaxGoals": goals,
            "probeWitnesses": probe_witnesses,
            "memlimit": MEMLIMIT,
            "solverFlags": sflags,
        }
        actual = {
            "schema": old_index.get("schema"),
            "primary": old_index.get("primary"),
            "flatInputIdentity": old_index.get("flatInputIdentity"),
            "astInputIdentity": old_index.get("astInputIdentity"),
            "esbmcIdentity": old_index.get("esbmcIdentity"),
            **{
                key: (old_index.get("config") or {}).get(key)
                for key in expected if key not in {
                    "schema", "primary", "flatInputIdentity", "astInputIdentity", "esbmcIdentity"
                }
            },
        }
        changed = [key for key in expected if actual.get(key) != expected[key]]
        if changed:
            detail = "\n".join(f"  {key}: recorded={actual.get(key)!r}, "
                               f"requested={expected[key]!r}" for key in changed)
            sys.exit(f"{bench_key}: REFUSING to resume reports made under an "
                     f"incompatible or unversioned collection:\n{detail}\n"
                     "Pass --fresh to re-measure, or move the directory aside "
                     "to preserve it.")

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
    # resumed. Refused, not auto-cleared: moving someone's collection because a
    # timestamp moved would still surprise a resumable run. The operator must
    # explicitly choose --fresh, which quarantines the directory above.
    ident = binary_identity()
    stale = {t: r.get("binary") for t, r in done.items() if r.get("binary") != ident}
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
        mt_moved = sum(1 for b in stale.values() if (b or {}).get("binaryMtime") != mt_now)
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
            f"  now:  {ident}\n" + "".join(f"  was:  {t} -> {b}\n" for t, b in shown) +
            (f"  ... and {len(stale) - len(shown)} more\n" if len(stale) > len(shown) else "") +
            "Refused either way, because resuming would skip those runs and "
            "reuse their reports. Re-run with --fresh to preserve this "
            "collection under .superseded.* and start over.")
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
    orphans = [p for p in sorted(reports_dir.glob("*.json")) if p.stem not in done]
    for p in orphans:
        p.unlink()
    if orphans:
        print(f"  [reports] removed {len(orphans)} report(s) not named by "
              f"{journal.name}: " + ", ".join(p.stem for p in orphans[:8]) +
              (" ..." if len(orphans) > 8 else ""),
              flush=True)

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
            rec, d = one_run(
                tag,
                esbmc_cmd(solast,
                          flat,
                          None if pkind == "library" else primary,
                          None,
                          goals,
                          esbmc_flags,
                          max_tx,
                          unwind=unwind,
                          probe_witnesses=probe_witnesses), timeout, out_dir / "work" / tag)
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
            sys.exit(f"--sol/--contract is an AD-HOC target with no project tree, "
                     f"so the per-method scope rule cannot be applied and "
                     f"--scope {scope} is unavailable for it. Use --scope whole "
                     f"(the ad-hoc target's purpose is the LENGTH axis, which needs "
                     f"no unit enumeration), or add the contract to collect.py's "
                     f"BENCHES if it is a real benchmark.")
        todo = list(base.enumerate_own_callable_functions(flat, project))
        if only:
            # ---- THE LADDER NEEDS ONE UNIT, NOT A BENCHMARK ----
            #
            # A tx ladder is three runs of ONE unit; without this filter the
            # cheapest way to get them was to sweep every unit of the benchmark
            # three times, which is a full-corpus run wearing a ladder's name.
            #
            # EVERY REQUESTED NAME MUST EXIST, and a miss is fatal rather than
            # quiet (R8). A silent miss here is the worst shape this project
            # has: `--only dcok` would run zero units, print "0/0 run(s)
            # produced a report", and read as "this unit reaches nothing".
            wanted = list(only)
            have = {fn for _c, fn, _k in todo}
            missing = [w for w in wanted if w not in have]
            if missing:
                sys.exit(f"{bench_key}: --only names {', '.join(missing)}, which "
                         f"this benchmark's callable-unit enumeration does not "
                         f"contain. It has: {', '.join(sorted(have))}.\n"
                         f"Refused rather than run the ones that did match: a "
                         f"partial ladder that prints a clean summary is how a typo "
                         f"becomes a measurement.")
            todo = [t for t in todo if t[1] in set(wanted)]
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
                    "tag":
                    tag,
                    "cmd":
                    None,
                    "wallSeconds":
                    0.0,
                    "exitCode":
                    None,
                    "killedByOuterTimeout":
                    False,
                    "reportPresent":
                    False,
                    "skipped":
                    "library-has-no-dispatcher",
                    "skippedDetail":
                    "a library has no dispatcher harness, so --contract "
                    "<Lib> finds no verification targets; the only other "
                    "route is --function, which verifies in isolation from "
                    "an arbitrary state and can yield a counterexample no "
                    "reachable state supports. Internal library functions "
                    "are covered through their callers' paths; external "
                    "ones are an unmeasured gap under this configuration",
                    "contract":
                    cname,
                    "function":
                    fname,
                    "kind":
                    ckind,
                    "binary":
                    binary_identity(),
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
                print(f"  [{i}/{len(todo)}] {tag}  (skipped: library" +
                      (f"; cleared {removed} stale file(s) from work/{tag})" if removed else ")"),
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
            # A `set` cell widens the ALPHABET and must NOT widen the
            # DENOMINATOR: the whole point is to compare it against the `single`
            # cell of the same unit, and that comparison is meaningless if the
            # two ran with different path totals. So the instrumented set is
            # pinned to the unit itself whenever extra letters were added.
            # `single` passes nothing, so its command line is byte-identical to
            # what it always was.
            cmd = esbmc_cmd(solast,
                            flat,
                            primary,
                            focus_arg,
                            goals,
                            esbmc_flags,
                            max_tx,
                            fname if focus_with else None,
                            unwind=unwind,
                            probe_witnesses=probe_witnesses)
            rec, d = one_run(tag, cmd, timeout, out_dir / "work" / tag)
            rec["contract"], rec["function"], rec["kind"] = cname, fname, ckind
            record(rec)
            if d is not None:
                (reports_dir / f"{tag}.json").write_text(json.dumps(d))

    index = {
        "schema": COLLECTION_SCHEMA,
        "benchmark": bench_key,
        "project": project,
        "primary": {
            "name": primary,
            "kind": pkind
        },
        "flatInput": str(flat),
        "flatInputIdentity": file_identity(flat),
        "astInput": str(solast),
        "astInputIdentity": file_identity(solast),
        "esbmcIdentity": file_identity(ESBMC),
        "config": {
            "mode":
            "whole" if whole else "per-method",
            # THE WIDTH AXIS AS A NAME, beside the two values it is computed
            # from. `mode` collapses `single` and `set` into "per-method", which
            # is fine for a human reading the table and wrong for anything
            # deciding whether a row belongs to the gate: the gate cell is
            # scope=single AND max_tx=1, and a `set` run has the same `mode`
            # string as a `single` one.
            "scope":
            scope,
            # Which units this collection actually ran. Empty = all of them.
            # A ladder row covers ONE unit and no table may read it as the
            # benchmark's row, so the restriction travels with the data rather
            # than living only in the shell history that produced it.
            "onlyUnits":
            list(only),
            # Whether this row came from a corpus benchmark or a hand-written
            # minimal reproduction. A PoC row is not a corpus row and no table
            # may mix them; recorded rather than inferred from the key's prefix.
            "adhocTarget":
            None if adhoc is None else str(flat),
            # RECORDED FROM THE ARGUMENT, not from a literal. It used to be a
            # hardcoded 1 beside a hardcoded flag; the two agreed only because
            # neither could change. `branch_gate.assert_gate_config` reads THIS
            # field to decide whether a collection may be quoted into the gate
            # table, so a literal here would let a ladder cell present itself as
            # the gate cell.
            "solidityMaxTx":
            max_tx,
            # The CALL-DEPTH BOUND, which is --unwind. `null` means the
            # flag was NOT passed and the tool chose its own 4; that is a
            # different fact from "we asked for 4", and the two must not
            # be written the same way -- a row that cannot say which is a
            # row whose truncation numbers have no bound recorded.
            "unwind":
            unwind,
            # The extra names added to every unit's focus set. Empty for the gate
            # and artefact cells; non-empty for the middle cell of the width axis.
            "focusWith":
            list(focus_with),
            # Whether the DENOMINATOR was pinned to the unit while the ALPHABET
            # was widened (--path-cov-instrument-only). It decides what the
            # published path total MEANS: with it, a `set` cell's total is the
            # unit's own and is comparable with the `single` cell of the same
            # unit; without it the total is the union of every named unit's
            # paths and the two cells answer different questions.
            "instrumentOnlyUnit":
            bool(focus_with),
            "pathCovMaxGoals":
            goals,
            "probeWitnesses":
            probe_witnesses,
            "memlimit":
            MEMLIMIT,
            # Written into the index so no later table can quote a row without
            # the encoder it was produced with. A benchmark whose runs needed a
            # non-default encoder is not comparable to one that did not, and
            # that difference has to travel WITH the data.
            "solverFlags":
            esbmc_flags,
            "solverFlagsReason":
            sreason,
            "outerTimeoutSeconds":
            timeout,
            "innerEsbmcTimeout":
            None,
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
    global MEMLIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--whole",
                    action="store_true",
                    help="Pair-1 analogue: one whole-contract run, no focus")
    ap.add_argument("--timeout", type=int, default=DEFAULT_OUTER_TIMEOUT)
    ap.add_argument("--goals", type=int, default=DEFAULT_MAX_GOALS)
    ap.add_argument("--out-suffix",
                    default="",
                    help="appended to the output directory name; two "
                    "configurations must never share one directory")
    ap.add_argument("--fresh",
                    action="store_true",
                    help="move this collection aside under a unique "
                    ".superseded.* name, then collect from scratch. "
                    "Required after a binary/configuration change; never "
                    "silently implied and never deletes prior evidence")
    ap.add_argument("--solver-flags",
                    default="",
                    help="space-separated ESBMC solver/encoder flags, e.g. "
                    "'--z3 --tuple-node-flattener'. Overrides the "
                    "per-benchmark ENCODER_EXCEPTIONS table; whichever "
                    "applies is printed and recorded in index.json")
    ap.add_argument("--esbmc-arg", action="append", default=[], metavar="ARG",
                    help="one extra ESBMC argument appended after the "
                         "solver/encoder flags and recorded in index.json. "
                         "Repeat it for two-token options, e.g. "
                         "--esbmc-arg=--path-cov-fixture "
                         "--esbmc-arg=fixture.json. Stage 2 validates this "
                         "exact list before reusing the enumeration report.")
    ap.add_argument("--memlimit-gib",
                    type=int,
                    default=8,
                    metavar="N",
                    help="per ESBMC process. The official POC runner passes 8 "
                    "explicitly; recorded in index.json.")
    ap.add_argument("--probe-witnesses",
                    type=int,
                    default=0,
                    metavar="N",
                    help="request up to N witnesses for each complete path and "
                    "each exit-latched branch-function probe. Probe models "
                    "are attributed by observed (path id, depth). Recorded "
                    "in the manifest so stage 2 can reuse this one run.")
    ap.add_argument("--scope",
                    choices=("single", "set", "whole"),
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
    ap.add_argument("--only",
                    default="",
                    help="comma-separated FUNCTION names to run, instead of "
                    "every callable unit of the benchmark. A tx ladder is "
                    "three runs of ONE unit; without this the cheapest way "
                    "to get them was to sweep the whole benchmark three "
                    "times. Every name must exist or the run is refused "
                    "(a typo that silently ran nothing would print "
                    "'0/0 run(s) produced a report'). Requires "
                    "--out-suffix, because a one-unit collection is not "
                    "the benchmark's collection.")
    ap.add_argument("--sol",
                    default="",
                    help="AD-HOC TARGET: a flat .sol outside BENCHES (its "
                    "<file>.solast must sit beside it). Requires "
                    "--contract and --scope whole. Exists so the ladder "
                    "has a MINIMAL subject: R6 requires a <80-line "
                    "reproduction before any investigation, and until now "
                    "this collector could only be pointed at the six "
                    "locked corpus entries.")
    ap.add_argument("--contract",
                    default="",
                    help="contract name for --sol (the --contract value ESBMC "
                    "is given)")
    ap.add_argument("--unwind",
                    type=int,
                    default=None,
                    help="the CALL-DEPTH BOUND, i.e. --unwind. The pass prints "
                    "'expanded N internal call(s) ... (call depth bound = "
                    "U)' and U is this value; unset, the tool picks 4. A "
                    "call site deeper than the bound is a callee whose "
                    "decisions never join the caller's path identity, so "
                    "they cannot enter the gate's numerator. MEASURED on "
                    "the current corpus: the one benchmark that CLEARS "
                    "the branch-coverage gate (aqua) is the only one with "
                    "nothing truncated by this bound, while EscrowSrc, "
                    "EscrowDst and farming are truncated on EVERY run (up "
                    "to 42 sites past it). Requires --out-suffix, and is "
                    "recorded in index.json -- a truncation count whose "
                    "bound is not in the row is a number nobody can "
                    "interpret.")
    ap.add_argument("--max-tx",
                    type=int,
                    default=1,
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
    ap.add_argument("--focus-with",
                    default="",
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
    if a.memlimit_gib <= 0:
        sys.exit("--memlimit-gib must be positive")
    if a.probe_witnesses < 0:
        sys.exit("--probe-witnesses must be non-negative")
    MEMLIMIT = f"{a.memlimit_gib}g"
    if a.list or not (a.bench or a.sol):
        for k in base.BENCHES:
            print(k)
        return 0

    # `--whole` is kept as an alias of `--scope whole` rather than removed:
    # it is what every existing invocation and every recorded command line in
    # this tree says, and silently changing the spelling of a configuration is
    # how a later table stops matching the command that produced it.
    scope = "whole" if a.whole else a.scope
    focus_with = tuple(s for s in (x.strip() for x in a.focus_with.split(",")) if s)
    if scope == "set" and not focus_with:
        sys.exit("--scope set needs --focus-with: without extra names the "
                 "alphabet is {unit} and the run is byte-identical to "
                 "--scope single, which would file the same measurement under "
                 "two different configuration names")
    if scope != "set" and focus_with:
        sys.exit(f"--focus-with is only meaningful with --scope set; under "
                 f"--scope {scope} the names would be " +
                 ("appended to a focus this run does not pass" if scope ==
                  "whole" else "silently ignored"))

    only = tuple(s for s in (x.strip() for x in a.only.split(",")) if s)
    if only and scope == "whole":
        sys.exit("--only selects UNITS to focus on, and --scope whole passes "
                 "no --focus-function at all, so there is nothing to select; "
                 "the whole-contract run is one run over every unit by "
                 "construction")

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

    # ---- THE BENCHMARK IS NOT A RUNNABLE THING ANY MORE ----
    #
    # The corpus is split into PoCs, one per TARGET public/external function
    # (`poc_split.py`). A benchmark key names 8 to 28 units and this script
    # sweeps all of them, which is the shape that turns a one-unit question
    # into a multi-hour run -- and it is banned.
    #
    # Refused rather than defaulted to the first unit: picking one silently
    # would file a one-unit measurement under the benchmark's name, which is
    # the one-fact-two-ledgers failure this file already refuses in three other
    # places.
    #
    # AD-HOC `--sol` TARGETS ARE UNAFFECTED. Those are hand-written minimal
    # reproductions, which the work order REQUIRES before any investigation;
    # they are the opposite of a full-corpus run.
    if adhoc is None:
        if scope == "whole":
            sys.exit(f"{a.bench}: --scope whole enumerates EVERY unit of this "
                     f"benchmark in one run, and the corpus no longer has runnable "
                     f"benchmarks -- it has PoCs, one per target public/external "
                     f"function.\n"
                     f"  python3 notes/coverage/scripts/poc_split.py --list\n"
                     f"  python3 notes/coverage/scripts/poc_one.py <poc-id>\n"
                     f"An ad-hoc minimal reproduction (--sol <file> --contract C "
                     f"--scope whole) is still allowed and is the intended route "
                     f"for an investigation.")
        if len(only) != 1:
            sys.exit(f"{a.bench}: this collector now runs exactly ONE unit per "
                     f"invocation, and --only named "
                     f"{len(only)} ({', '.join(only) or 'nothing'}).\n"
                     f"  The corpus is split into PoCs, one per target "
                     f"public/external function; a benchmark key by itself would "
                     f"sweep every unit it has, which is the run this work order "
                     f"bans.\n"
                     f"  python3 notes/coverage/scripts/poc_split.py --list\n"
                     f"  python3 notes/coverage/scripts/poc_one.py <poc-id>")

    idx = collect(a.bench, scope == "whole", a.timeout, a.goals, a.out_suffix,
                  a.solver_flags.split(), a.esbmc_arg, a.fresh, a.max_tx,
                  focus_with, scope, adhoc, only, a.unwind, a.probe_witnesses)
    ok = sum(1 for r in idx["runs"] if r["reportPresent"])
    killed = sum(1 for r in idx["runs"] if r["killedByOuterTimeout"])
    print(f"{a.bench}: {ok}/{len(idx['runs'])} run(s) produced a report, "
          f"{killed} killed by the outer timeout")
    if only and ok == 0:
        print(f"{a.bench}: REFUSING success for --only "
              f"{','.join(only)} because no Stage-1 report was produced. "
              f"Stage 2 has no witnessed path universe to certify; continuing "
              f"would turn a timeout into an empty measurement.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
