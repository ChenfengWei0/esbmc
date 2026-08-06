#!/usr/bin/env python3
"""Stage 4: turn a CERTIFIED REGION plus a POST-STATE ASSERTION LADDER into a
parameterised Foundry unit test (a PUT) with an assertion oracle.

WHY THIS FILE EXISTS, in one number. The pipeline measured
instrumented -> witnessed -> concrete -> certified -> PUT as
922 -> 171 -> 209 -> 7 -> 0, and the last column was 0 for a WIRING reason and
not a yield one: `foundry_generator` never reads a certified region and never
reads an assertion ladder, so every emitted test is a fixed replay of one
counterexample. Stage 2 (regions) and stage 3 (the ladder) both existed, both
had green regressions, and neither reached the emitted suite.

THE ROUTE, AND WHY IT IS NEITHER OF THE TWO OBVIOUS ONES.

  (a) emit the PUT from this driver, reproducing the deploy/preamble that
      foundry.cpp writes.  Cheap -- and it puts the preamble in a SECOND place,
      which is the shape of defect this project has already been bitten by.

  (b) give ESBMC a `--path-cov-emit-put <spec.json>` mode that reuses
      foundry_generator for the preamble.  Principled in the abstract, and the
      code says it is not available:

        * `write_foundry_file` (src/goto-symex/foundry.cpp:2631) is the ONLY
          writer and is private (foundry.h:393); there is no
          "write the preamble for this unit" entry point;
        * the import set is computed FROM THE CASES (foundry.cpp:2758-2795),
          not from the unit;
        * `plan_of` (foundry.cpp:2664) takes a `test_case` and reads the
          constructor call's RECONSTRUCTED argument literals
          (`join_args(*it->second)`, foundry.cpp:2693) plus the ctor
          warp/deployer/value pins -- every one of them an `expr2tc` obtained
          from `smt_conv.get(...)` inside `reconstruct()`;
        * `non_instantiable` and `libraries`, which `plan_of` needs, are
          populated ONLY inside `reconstruct()` (foundry.cpp:2416-2423);
        * `setUp()` is written inline inside the group loop
          (foundry.cpp:2899-2955), after that group's mock instances are
          derived from its cases (foundry.cpp:2869-2885).

      So a JSON-driven PUT mode could not RENDER a preamble; it would have to
      re-derive one from a fresh counterexample, i.e. be a verification run
      rather than a rendering pass.  And the region does not reach that run at
      all: bmc.cpp hands the generator only `(equation, smt_conv, ns)` at all
      three call sites (186, 2000, 3080), and the certify branch `continue`s
      past instrumentation (goto_coverage.cpp:6709) so its run has an EMPTY
      exit census and could not emit a test even if asked.

  (c) WHAT THIS FILE DOES.  The driver LIFTS the emitter's own output.  ESBMC
      is run once in exactly the existing emit configuration, which writes
      `<Primary>.cov.t.sol` containing the real preamble and the real concrete
      case; this script then takes that preamble VERBATIM and rewrites one
      call statement into a parameterised one.  The preamble is not
      reproduced, it is REUSED, so there is no second copy to drift -- and
      requirements 4 (same deploy/preamble) and 5 (the R0 exit-kind
      expectation: bare call / vm.expectRevert / try-catch) are satisfied BY
      CONSTRUCTION rather than by re-implementation.

WHAT A PUT CONTAINS, and where each part comes from:

  1. `function test_put_<C>_<u>_path<enc>(<typed params>) public` -- the free
     coordinates of the certified region, as parameters.
  2. `x = bound(x, lo, hi);` per bounded coordinate and
     `vm.assume(x != h);` per hole (Definition 5).
  3. the pins as concrete values: an argument the region does NOT bound keeps
     the literal the emitter chose, because that is exactly the slice the
     region is a statement about.
  4. the deploy/preamble of the concrete tests -- the same `setUp()`, reused.
  5. the R0 exit-kind expectation -- the concrete case's own call statement
     shape, preserved.
  6. the assertion oracle -- the surviving (HOLDS) rungs of
     `--path-cov-assert`.  Two sources, and they are read differently: a
     POST-STATE rung through `vm.load` at the slot solc itself reports, and a
     RETURN-VALUE rung by binding the call's own result to a local.  A return
     rung is emitted only when the ladder's `retlive` witness came back
     REFUTED -- that is the tool saying some execution of this path actually
     reaches a return, without which every return rung holds vacuously.

READING STATE WITHOUT A GETTER.  The ladder names state variables, not
getters, and most are private.  The slot is NOT guessed: it comes from
`forge inspect <C> storageLayout --json`, i.e. from solc.  A variable absent
from that layout is a `constant`/`immutable` -- it has no storage slot at all
-- and its rungs are DROPPED with the reason printed, never silently.  That
distinction is load-bearing: on aqua the ladder's only variable is `_DOCKED`,
`post == pre` HOLDS, and the layout shows it is not in storage, so the
"oracle" there would have been a compile-time tautology.

A STATE COORDINATE IS SET, NOT ASSUMED.  A region bound on `state.<v>` is a
statement about the ENTRY state.  `vm.assume` on it would reject every fuzz
input whenever the deployed value differs (measured on farming: the emitter
deploys with `_distributor = address(1)` while the certified region is
`state._distributor in [0, 0]`), which forge reports as a rejected run rather
than as the precondition it is.  So a state coordinate is ESTABLISHED with a
read-modify-write `vm.store` at the slot/offset solc reports.  Parameters are
passed; state is stored; environment pins are checked.
"""

import argparse
import itertools
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time

from solidity_ast_dependencies import (SLOT_DEPENDENCY_POLICY,
                                        path_function_declaration_id,
                                        unit_state_dependencies)

UINT256_MAX = (1 << 256) - 1


# ---------------------------------------------------------------------------
# Running ESBMC
# ---------------------------------------------------------------------------

# ---- WHICH EXTRA ESBMC FLAGS THIS DRIVER WILL PASS ON, AND WHICH IT REFUSES --
#
# `--esbmc-arg` exists because the tool's OWN refusal names a repair this driver
# had no way to apply. `--path-cov-assert` answers UNDECIDED-TRUNCATED with:
#
#   "TO GET A VERDICT: raise --unwind, use --unwindset/--unwindsetname for the
#    loop(s) named here, or pass --partial-loops"
#
# and then names them (on aqua `dock`: loop 64, `__memset_impl`,
# src/c2goto/library/string.c:298). Without a passthrough the only response to a
# named, one-line repair was to record the refusal.
#
# THE STRATEGY FLAGS ARE NOT ACCEPTED, and the honest reason is that the only
# evidence about them on this pipeline is STALE.
#
# `notes/coverage/unwind-vs-strategy.md` ran the whole bound x strategy matrix
# and concluded that none of them may be used. Its findings are of two kinds and
# they do not age the same way:
#
#   * SOURCE facts -- that everything in ESBMC's `is_k_induction` disjunction
#     also runs `goto_k_induction` BEFORE the path pass instruments, that
#     `do_bmc_strategy` overwrites the `unwind` option with the current `k` at
#     every phase, and that the goal set is built once before the strategy loop
#     is entered. These are structural, and if they still hold then a strategy
#     answers path claims under a symex bound the enumeration did not choose.
#   * NUMBERS -- 2796 paths excluded as a NAMED OBSTACLE, the k-loop stopping at
#     2, three of seven `--incremental-bmc` cells producing no report at all.
#
# ⚠ THE NUMBERS ARE NOT CURRENT AND MUST NOT BE QUOTED AS IF THEY WERE. That
# file's own §0.1 says so: every cell came from a snapshot binary taken BEFORE
# `d09536838a`, on ONE unit (`Aqua.dock`), and it records "UNVERIFIED: whether
# these numbers reproduce on a build of `d09536838a`". The tree has moved well
# past that commit since. The SOURCE line numbers it cites have not been
# re-checked here either.
#
# So this list is a REFUSAL TO GUESS, not a quotation. A strategy flag changes
# both which claims exist and the bound they are answered under; the last time
# anyone measured what that does, it silently disqualified the focused unit
# while `F` and `Path Coverage` still read normally. Until that matrix is re-run
# against a current build, passing one through here would ship a PUT whose
# provenance nobody can state. RE-MEASURING IS WHAT LIFTS THIS, not argument.
#
# Note also that ESBMC's own under-report warning RECOMMENDS `--k-induction` and
# `--incremental-bmc`. That warning fired in every cell of the old matrix that
# produced a report -- including the cells where its own advice had been taken --
# so it does not distinguish the case where the remedy worked from the case
# where it did not.
STRATEGY_FLAGS_REFUSED = {
    "--k-induction": "it reaches goto_k_induction, which rewrites loops BEFORE "
                     "the path pass instruments, and it caps symex at whatever "
                     "k the inductive step stops on regardless of --unwind",
    "--k-induction-parallel": "same GOTO transform as --k-induction",
    "--inductive-step": "same GOTO transform as --k-induction",
    "--loop-invariant": "reaches the same GOTO transform as --k-induction",
    "--incremental-bmc": "the goal set is frozen before the strategy loop, so "
                         "every k re-asks the same claims under a different "
                         "bound",
    "--falsification": "a strategy: do_bmc_strategy overwrites the unwind bound "
                       "the enumeration was built for",
    "--termination": "a strategy: do_bmc_strategy overwrites the unwind bound "
                     "the enumeration was built for",
    "--forward-condition": "it is short-circuited in Solidity dispatcher mode, "
                           "and its report-writing call site is gated off",
}


def check_esbmc_args(extra):
    """The refusal, or None. Applied to what the CALLER passes, never to the
    flags this driver adds itself."""
    for a in extra:
        if a in STRATEGY_FLAGS_REFUSED:
            return (f"--esbmc-arg {a} is not accepted: "
                    f"{STRATEGY_FLAGS_REFUSED[a]}.\n"
                    f"This is a refusal to guess rather than a current "
                    f"measurement: the matrix in "
                    f"notes/coverage/unwind-vs-strategy.md ran on a SNAPSHOT "
                    f"binary predating d09536838a, on one unit, and that file "
                    f"marks its own numbers UNVERIFIED on newer builds. A "
                    f"strategy changes both which claims exist and the bound "
                    f"they are answered under, and the last measurement of "
                    f"that showed it disqualifying the focused unit while F "
                    f"and Path Coverage still read normally -- i.e. silently. "
                    f"Re-run that matrix against a current build to lift this.\n"
                    f"If a specific loop needs more iterations -- which is what "
                    f"the ladder's UNDECIDED-TRUNCATED refusal actually names "
                    f"-- widen THAT loop with `--esbmc-arg --unwindset "
                    f"--esbmc-arg <loop>:<n>`. That moves only the symex side, "
                    f"so it explores a SUPERSET of executions and cannot make "
                    f"a path look infeasible that is not")
    return None


# ---- WHICH CELL A RUN IS IN, AND WHY THE ARTEFACT HAS TO SAY SO --------------
#
# `notes/coverage/INVOCATION_DECISIONS.md` prints TWO command lines and one rule:
#
#   (a) ARTEFACT / enumeration : whole contract, --solidity-max-tx 2
#   (b) GATE                   : --focus-function <u>, --solidity-max-tx 1
#   "A run of (a) may never be quoted into the branch-coverage gate table, and a
#    run of (b) may never be quoted as the method's reach."
#
# This driver ran only (b) and said nothing about it, so every PUT it produced
# was quotable into either table. That is not a bookkeeping detail: rows 1 and 2
# of that file are marked OVERTURNED because a FOCUSED run cannot reach
# cross-function state at ANY transaction bound -- every transaction is another
# call to the same entry. Measured there on a ten-line contract: `Tiny.sol` is
# 60% focused/tx=1, 75% whole/tx=1, 100% whole/tx=2; and `Tiny2.sol`, identical
# except that the CONSTRUCTOR establishes the state, is 100% at focused/tx=1.
# The obstacle was never the state, it was that a call has to happen first.
#
# So the cell is a property of the measurement and travels with it: named on the
# emitted test, and recorded in put.json.
#
# ⚠ The cost of (a) is stated rather than hidden: ESBMC itself warns that
# `--solidity-max-tx N>=2` "reconstructs multi-transaction sequences unreliably
# (methods can be mis-attributed across transactions)" for Foundry emission. So
# (a) is not a better default, it is a different question with its own open
# problem.
CELLS = {
    ("whole", 2): ("ARTEFACT",
                   "whole contract at --solidity-max-tx 2: the only "
                   "configuration measured to reach cross-function state. May "
                   "NOT be quoted into the branch-coverage gate table"),
    ("focus", 1): ("GATE",
                   "--focus-function at --solidity-max-tx 1, matching the "
                   "LOCKED branch-coverage baseline, which is measured to run "
                   "at one transaction. May NOT be quoted as the method's "
                   "reach"),
}


def cell_of(scope, max_tx):
    """(name, rule) for this run's configuration. Never guesses a name."""
    if scope not in ("focus", "whole") and max_tx == 2:
        return ("ARTEFACT",
                f"dispatcher set {{{scope}}} at --solidity-max-tx 2: the "
                f"target plus its recorded state writers. May NOT be quoted "
                f"into the branch-coverage gate table")
    return CELLS.get((scope, max_tx),
                     ("UNNAMED",
                      f"scope={scope} --solidity-max-tx={max_tx} is neither of "
                      f"the two command lines INVOCATION_DECISIONS.md settles, "
                      f"so this run belongs to no table. Say what it is for "
                      f"before quoting it anywhere"))


def run_esbmc(esbmc, sol, ast, contract, unit, extra, cwd, max_tx, timeout,
              memlimit, scope="focus"):
    """One ESBMC invocation, in its own cwd (the emitted filename is hardcoded).

    `--focus-function`, NEVER `--function`.  `--function` verifies the unit in
    isolation from an ARBITRARY contract state, so its counterexamples can
    depend on a state no `constructor() -> tx sequence` reaches -- a false
    positive, which in this pipeline becomes a test that is RED on the
    unmodified contract.  `--focus-function` narrows which unit is entered and
    leaves the entry state as the post-constructor state.

    `scope="whole"` drops `--focus-function` entirely; see CELLS above for why
    that is a different measurement rather than a slower one.

    `setsid` + `timeout -k` so a kill takes the whole process group: an
    orphaned esbmc grandchild has taken this machine down once.
    """
    cmd = ["setsid", "timeout", "-k", "30s", f"{timeout}s", esbmc]
    if ast:
        cmd.append(os.path.abspath(ast))
    cmd += ["--sol", os.path.abspath(sol),
            "--contract", contract,
            "--solidity-path-coverage", "--solidity-max-tx", str(max_tx),
            "--memlimit", memlimit, "--result-only"]
    if scope == "focus":
        cmd += ["--focus-function", unit]
    elif scope != "whole":
        cmd += ["--focus-function", scope]
    cmd += extra
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = p.stdout + p.stderr
    # ---- ONE LOG PER INVOCATION, OR THE FIRST ONE'S EVIDENCE IS DESTROYED ---
    #
    # `cwd` is the same directory for every ladder call of a unit -- the first
    # pass and each R2 pass all land in `assert/` -- and this wrote `run.log`
    # with mode "w". So the R2 pass silently overwrote the FIRST pass's output.
    #
    # MEASURED, and it is what blocked a live diagnosis rather than being a
    # tidiness point. aqua `push` proposed 48 mapping-slot candidates to the
    # ladder and got back 6 rows over one unrelated variable. The tool prints a
    # per-candidate REFUSAL saying why each name carries no candidate -- and
    # that text was in the first pass's log, which the R2 pass had already
    # replaced by the time anyone looked. One file, two writers, and the
    # question could not be answered from disk.
    #
    # `run.log` still exists and still holds the LAST invocation, so every
    # existing reader is unchanged; the per-invocation copies sit beside it.
    with open(os.path.join(cwd, "run.log"), "w") as f:
        f.write(" ".join(cmd) + "\n\n" + out)
    n = 1
    while os.path.exists(os.path.join(cwd, f"run.{n}.log")):
        n += 1
    with open(os.path.join(cwd, f"run.{n}.log"), "w") as f:
        f.write(" ".join(cmd) + "\n\n" + out)
    return out, p.returncode, time.time() - t0


# ---------------------------------------------------------------------------
# The assertion ladder
# ---------------------------------------------------------------------------

LADDER_ROW_RE = re.compile(
    r"^--path-cov-assert: (\S+): (.*?)  "
    r"(HOLDS|REFUTED|NO VERDICT \(solver unknown\)|"
    r"NO VERDICT \(never reached the solver\))(?:  \[|$)")
LADDER_SUMMARY_RE = re.compile(
    r"^--path-cov-assert: ladder summary -- (\d+) candidate\(s\): (\d+) HOLDS, "
    r"(\d+) REFUTED, (\d+) no verdict \(solver unknown\), (\d+) no verdict "
    r"\(never reached the solver\)")
LADDER_REFUSAL_RE = re.compile(r"--path-cov-assert: unit '[^']*' -- "
                               r"REFUSING THE LADDER(?::|,|\s+)(.*)$")
LADDER_VACUOUS_RE = re.compile(r"--path-cov-assert: THE REGION IS VACUOUS")

# ---- THE `RESULT:` TOKENS OF THE ASSERT GATE, AND WHY AN UNKNOWN ONE IS FATAL
#
# This parser is a set of recognisers over lines, so anything it does not
# recognise is IGNORED -- which for a gate is the worst possible default. Before
# this, a new refusal token from the tool produced `rows=[] summary=None
# refusal=None vacuous=False`, and main() read that as "the ladder simply had
# nothing to say", emitted the PUT with no oracle, and exited 0. A refusal the
# driver cannot read must never be quieter than one it can.
#
# So the RESULT line is matched by SHAPE and the token looked up in a table with
# no default. UNDECIDED-TRUNCATED is the token the tool prints instead of
# `THE REGION IS VACUOUS` when a loop was cut at the unwind bound while
# unwinding assertions were disabled: the vacuity may have been manufactured by
# the bound rather than being a property of the region. It refuses the PUT for
# the same reason vacuity does -- a PUT whose `bound()` maps every fuzz input
# into a set the path may never be taken from is 256 green runs standing for
# nothing -- but it says something DIFFERENT to the operator, because it names a
# repair (raise --unwind / --unwindset) where vacuity names none.
LADDER_RESULT_RE = re.compile(
    r"^(?:ERROR: )?--path-cov-assert: RESULT: ([A-Z][A-Z-]*)")
LADDER_RESULT_MAP = {
    "UNDECIDED-TRUNCATED": "truncated",
}


# ---------------------------------------------------------------------------
# HOW MANY UNWINDS DOES THIS UNIT NEED? -- ask the tool, it already says
# ---------------------------------------------------------------------------
#
# `notes/coverage/INVOCATION_DECISIONS.md` records the open item as "we still
# have no mechanism for knowing how many unwinds are needed", and the bound of 4
# as "not chosen or argued". But ESBMC does not merely truncate silently: every
# time it cuts a loop at the bound it NAMES the loop, and both of the places it
# does so were being read by nobody.
#
# TWO SHAPES, and they are different sentences from different code paths. A
# detector written for one and pointed at the other never fires, which is a
# failure this project has already paid for -- so both are pinned with VERBATIM
# captures in scripts/test_solidity_path_put.py, and the parser reports WHICH
# shape it matched so a reworded message shows up as a zero rather than as
# "nothing was truncated".
#
#   (1) the assert gate's refusal, semicolon-joined on ONE line:
#       Loops truncated: loop 1 at file .../stdlib.c line 38 column 3 function
#       __ESBMC_atexit_handler; loop 62 at file ...aqua__Aqua.flat.sol line 2258
#       function dock; loop 64 at file .../string.c line 298 column 3 function
#       __memset_impl
#
#   (2) the under-report warning, one line per loop:
#       WARNING: Coverage may be UNDER-REPORTED: 2 loop(s) hit the unwind bound...
#       WARNING:   loop 55 at file .../solidity_string.c line 206 column 5
#       function _str_assign
#
# WHY WIDENING IS SAFE, and why it is `--unwindset` rather than `--unwind`.
# `--unwindset <loop>:<n>` moves ONLY the symex side (symex_goto.cpp), so the
# run explores a SUPERSET of executions: it cannot make a path look infeasible
# that is not. `--unwind N` moves the ENUMERATION bound too, which changes the
# goal set and therefore what is being measured. INVOCATION_DECISIONS.md states
# the same asymmetry: use --unwindset to widen symex past the enumeration bound,
# never to narrow it.
#
# MEASURED PRECEDENT: aqua `dock` certifies but its ladder answers
# UNDECIDED-TRUNCATED, and `--unwindset 64:512` on the named library loop
# (`__memset_impl`) is what brings its two witnesses back.
TRUNC_LOOP_RE = re.compile(
    r"loop (\d+) at file (\S+) line (\d+)(?: column \d+)? function (\S+)")


def truncated_loops(log):
    """[(loop_id, file, line, function)] the run reports cutting, plus a
    per-shape count so a reworded producer message is visible.

    Only lines that BELONG to one of the two reports are scanned. The regex
    alone would also match prose that happens to describe a loop, and a
    numerator built from prose is not a measurement.
    """
    found, shapes = [], {"assert-gate": 0, "under-report-warning": 0}
    for line in log.splitlines():
        s = line.strip()
        if "Loops truncated:" in s:
            shape = "assert-gate"
        elif s.startswith("WARNING:") and " loop " in s:
            shape = "under-report-warning"
        else:
            continue
        for m in TRUNC_LOOP_RE.finditer(s):
            shapes[shape] += 1
            row = (int(m.group(1)), m.group(2), int(m.group(3)), m.group(4))
            if row not in found:
                found.append(row)
    return found, shapes


def unwindset_args(loops, k):
    """ONE `--unwindset` carrying a comma-separated `<id>:<k>` list.

    NOT one flag per loop, and that is MEASURED rather than stylistic:

      ERROR: option '--unwindset' cannot be specified more than once

    esbmc rejects the repeat outright, in 0.0s, before any analysis. So every
    widened attempt this driver has ever made died on the command line, and the
    retry loop in main() had never once run a widened query -- a whole feature
    wired up, printing confident progress lines, and never connected. The
    comma-separated form is accepted: verified on aqua `dock` with
    `--unwindset 1:8,62:8,64:8`, whose log then shows all three loops unwinding
    to iteration 7 and "Not unwinding ... iteration 8".

    The ids are DEDUPED too. The two truncation reports name the same loop with
    different function spellings (`dock` from one, `dock;` from the other), so
    the union carried loop 1 and loop 62 twice.
    """
    ids = sorted({lid for lid, _f, _l, _fn in loops})
    if not ids:
        return []
    return ["--unwindset", ",".join(f"{lid}:{k}" for lid in ids)]


def attempt_is_usable(rows, blocker):
    """Did a widened re-run actually produce a LADDER to read?

    A separate, named predicate because the alternative -- letting the retry's
    parse result overwrite the state unconditionally -- silently DELETED the
    refusal it was trying to lift.

    MEASURED, on aqua `dock` with --auto-unwind 3 before this existed: the
    attempt died on the command line (exit 64, 0.0s), `parse_ladder` of an
    error message returned `rows=[] summary=None refusal=None blocker=None`,
    that None replaced the "truncated" blocker, the UNDECIDED-TRUNCATED gate in
    main() therefore did NOT fire, and the driver emitted an oracle-free PUT
    and exited 0. A crashed retry turned a correct refusal into a green run.

    `blocker is None and not rows` is exactly "this run said nothing at all",
    which is the one answer that must never be read as "nothing to object to".
    """
    return bool(rows) or blocker is not None


def parse_ladder(log):
    """(rows, summary, refusal, blocker) from a --path-cov-assert run's output.

    A row is (var, text, verdict).  ONLY `HOLDS` rows become an oracle; the
    other three states are kept so the report can say how many were dropped
    and why -- an absent row and a refuted one are different facts, and the
    mode's own report says so.

    The verdict line of the RUN is deliberately not read: the mode documents
    that VERIFICATION SUCCESSFUL/FAILED is not its result (the non-vacuity
    witness is refuted on every non-empty region, so a working ladder exits 1).

    `blocker` is None, "vacuous", or "truncated" -- the two states in which the
    region cannot support a PUT at all.  It replaces the old boolean `vacuous`
    because those two need different words to the operator: vacuity says the
    region admits nothing, truncation says the run could not tell, and only the
    second names a repair.  An unrecognised `RESULT:` token raises rather than
    being ignored; see LADDER_RESULT_MAP.
    """
    rows, summary, refusal, blocker = [], None, None, None
    for line in log.splitlines():
        s = line.strip()
        m = LADDER_ROW_RE.match(s)
        if m:
            rows.append((m.group(1), m.group(2).strip(), m.group(3)))
            continue
        m = LADDER_SUMMARY_RE.match(s)
        if m:
            summary = tuple(int(x) for x in m.groups())
            continue
        m = LADDER_RESULT_RE.match(s)
        if m:
            token = m.group(1)
            if token not in LADDER_RESULT_MAP:
                raise SystemExit(
                    f"[put] ESBMC printed an unrecognised assert-gate token "
                    f"'RESULT: {token}'. This driver knows only "
                    f"{', '.join(sorted(LADDER_RESULT_MAP))}. Refusing to "
                    f"continue: this parser IGNORES what it does not "
                    f"recognise, so falling through would emit the PUT as "
                    f"though the gate had said nothing -- and the gate is the "
                    f"only thing standing between a certified region and a "
                    f"test that is green while standing for nothing. Teach "
                    f"this script the token instead")
            blocker = LADDER_RESULT_MAP[token]
            refusal = s
            continue
        if LADDER_VACUOUS_RE.search(s):
            blocker = "vacuous"
            refusal = ("THE REGION IS VACUOUS -- no execution the region "
                       "admits walks this path, so every rung would hold for "
                       "want of an execution")
            continue
        m = LADDER_REFUSAL_RE.search(s)
        if m and refusal is None:
            refusal = m.group(1)
    return rows, summary, refusal, blocker


def ladder_answer_gap(asked, rows):
    """(unanswered, unasked) -- names put in `vars` that came back with NO row,
    and names that came back that `vars` never mentioned.

    ⛔ A SET DIFFERENCE OVER NAMES, DELIBERATELY NOT A LOG PARSE. esbmc does
    print a reason beside each candidate it drops, and reading that prose was
    the obvious implementation. But the prose has ALREADY CHANGED ONCE: a single
    global "REFUSING THE LADDER" was split into per-candidate drops, and a
    detector keyed to the old sentence would have gone on reporting nothing
    wrong while every candidate died. What cannot drift is that the driver
    wrote N names into the spec and got rows about none of them.

    ---- MEASURED, aqua `push` enc=6, and it is why this exists --------------

    48 slot names were written into `vars`. Seven rows came back, ALL of them
    for `_DOCKED` -- a name the spec never mentions. The component loop is
    deliberately NOT whitelisted by a slot-only spec (goto_coverage.cpp, "A
    SLOT ENTRY MUST NOT TURN THE COMPONENT LOOP INTO A WHITELIST"), so rows for
    unasked names are EXPECTED and are not an error. What was not expected, and
    what nothing on either side counted, is 0 of 48.

    So `answered` is INTERSECTED with `asked` rather than merely counted: seven
    rows from forty-eight questions is not "7 of 48 answered". It is ZERO of 48
    answered, plus seven nobody asked for, and those two numbers have to be
    printed separately or the second disguises the first.

    The cause on aqua turned out to be that every one of the 48 names keys a
    level with `strategyHash`, which resolves to an AGGREGATE and cannot be a
    coordinate -- a real capability limit. But finding that took an hour of
    reading C++ and hunting for a log that a later pass had overwritten, and
    the ONE LINE this function prints would have said "48 asked, 0 answered"
    immediately.
    """
    asked_set = set(asked or [])
    answered = {v for v, _t, _d in rows}
    return (sorted(asked_set - answered), sorted(answered - asked_set))


CHANGE_UNDER_CATCH = (
    "this rung asserts the state CHANGED, and the emitted call is "
    "REVERT-TOLERANT (`try {} catch {}`) because the exit kind could not be "
    "confirmed. A revert leaves storage untouched, so this assertion is FALSE "
    "on exactly the outcome the wrapper exists to tolerate -- it would produce "
    "a RED test on the unmodified contract. The `post == pre` rungs of the "
    "same ladder are unaffected: they hold on a revert too. DROPPED rather "
    "than emitted, and rather than the whole PUT being refused, because the "
    "unchanged rungs are still a sound (weaker) oracle over this region")


def rung_asserts_a_change(text):
    """Does this rung claim the post-state DIFFERS from the pre-state?

    ---- WHY THIS DISTINCTION IS LOAD-BEARING, MEASURED ----------------------
    A revert leaves storage exactly as it was. So on a call the emitter could
    not confirm exits normally -- one it wrapped in `try {} catch {}` -- every
    "nothing changed" rung stays true whatever happens, and every "something
    changed" rung is FALSE the moment the call reverts. The two are not equally
    safe under the same wrapper, and until now both were rendered.

    MEASURED, farming.setDistributor enc=13. The certified region is
        msg.sender in [821886973, 821886973]
        state._owner in [1, 821886972]        <- DROPPED, width > 1
    -- two DISJOINT sets for a unit guarded by `onlyOwner`. The owner bound is
    correctly dropped (the entry state is not havoc'd, so it constrained
    nothing in the query), which leaves the constructor's owner = 1 against a
    pranked sender of 821886973. On chain that call REVERTS, the try/catch
    swallows it, `_distributor` never moves, and

        assertTrue(_post_distributor != _pre_distributor, "post != pre")

    fails at `distributor_ = 100`. The ladder was not wrong -- it answered
    about a model whose entry state the test cannot reproduce -- but the
    EMITTED TEST is unsound, and it is unsound in the direction that produces a
    RED test on an unmodified contract, which is the one outcome this pipeline
    must never produce.

    The sibling enc=12 is the control: its surviving rungs are all `post ==
    pre`, it is wrapped in the same try/catch, and it is GREEN. So the wrapper
    alone is not the fault and the fix is not "never emit under try/catch".
    """
    if re.match(r"^post (!=|>|<) pre$", text):
        return True
    # A value/interval tied to an input or arithmetic term is a postcondition
    # of the successful call. On a swallowed revert `post == pre`; unless the
    # candidate is literally that R1 equality, executing it unconditionally
    # can make the generated test red on the unmodified contract.
    if text.startswith("post == ") and text != "post == pre":
        return True
    if text.startswith("post in ["):
        return True
    # A delta rung with a NON-ZERO lower bound says the value moved by at least
    # that much, which is a change. A symbolic lower bound might be nonzero, so
    # only a literal zero is safe to execute after a swallowed revert.
    m = re.match(r"^(?:post - pre|pre - post) in \[([^,]+),", text)
    return bool(m) and m.group(1).strip() != "0"


# Rung text -> a renderer producing forge-std assertion lines.  `post`/`pre`
# are the expression texts the caller has already built.
# An R2 endpoint: a decimal, or a name. A name is only ever accepted after it
# is looked up in the emitter's own identifier table -- see `bound_term`.
_BND = r"([0-9]+|[A-Za-z_]\w*)"


def bound_term(tok, idents):
    """The Solidity text for one R2 endpoint, or None if it cannot be spelled.

    ⛔ RETURNING None DROPS THE WHOLE RUNG, and that is the intended
    behaviour. The alternative -- emitting the name and hoping it resolves --
    fails at `forge build`, i.e. it breaks the entire generated file rather
    than losing one assertion out of it.
    """
    if tok.isdigit():
        return tok
    if idents and tok in idents:
        return idents[tok]
    return None


NUMERIC_TY = re.compile(r"^u?int(\d+)?$")
BOOL_TY = re.compile(r"^bool$")
# An endpoint may also NAME an identity rather than an amount. `address` and a
# contract type are the two that occur; both are ordered integers underneath, so
# the C++ builds the comparison in the CANDIDATE's type and an equality over
# them is well formed. They are kept apart from the numeric ones because the
# distinction is not cosmetic -- see `endpoint_candidates`.
IDENTITY_TY = re.compile(r"^(address|contract\s|interface\s|enum\s)")

# Worst case is (numeric params) + (identity params) + (a refuted-only second
# round), and every one of those is an esbmc invocation. Capped, and the cap is
# LOGGED with what it dropped: a silent truncation reads exactly like "there was
# nothing more to ask", which is the one thing a yield number must never mean.
R2_MAX_QUERIES = 6
R2_TERM_BUDGET = 96
R2_CANDIDATE_BUDGET = 128
RETURN_VAR = "return"
RETLIVE_PREFIX = "a value IS returned on this path"


# How many bytes a value of this endpoint type occupies in storage. Used to
# decide WHICH CANDIDATES an identity endpoint may be asked about; see
# `propose_r2_specs`. `None` means "do not filter on width".
IDENTITY_BYTES = {"address": 20, "address payable": 20}


def endpoint_bytes(t):
    """Storage width of an identity endpoint type, or None if not known."""
    if t in IDENTITY_BYTES:
        return IDENTITY_BYTES[t]
    if t.startswith(("contract ", "interface ")):
        return 20
    return None


def endpoint_candidate(name, sol_type):
    """One R2 endpoint candidate for a declared Solidity type, or None."""
    if not name:
        return None
    t = _norm_ty(sol_type)
    if NUMERIC_TY.match(t):
        return (name, "num", None)
    if BOOL_TY.match(t):
        return (name, "bool", 1)
    if IDENTITY_TY.match(t):
        return (name, "id", endpoint_bytes(t))
    return None


def endpoint_candidates(params):
    """[(name, kind, nbytes)] for the R2 endpoints this unit can name.

    kind is `"num"`, `"id"` or `"bool"`, and the split decides WHICH BOUND
    CLASS the name may appear in:

      num  -- an amount. May bound a DELTA (`post - pre in [amt, amt]`) and an
              ABSOLUTE value (`post in [amt, amt]`).
      id   -- an identity: an address, a contract, an enum. May bound an
              ABSOLUTE value only. `post - pre in [newOwner, newOwner]` is not
              a weak claim, it is a MEANINGLESS one -- the difference of two
              balances is not an address -- and asking it would spend a query
              to be told REFUTED about a question nobody has.
      bool -- a two-point flag. May appear only in STRUCTURED EQUALITY
              candidates. There is no ordering, interval or delta over bool.

    ⛔ WHY `id` EXISTS AT ALL, rather than the numeric filter simply standing.
    The filter was `NUMERIC_TY.match(...)` and nothing else, so a unit whose
    only parameter is an address proposed NO R2 QUERY WHATSOEVER. That is the
    SETTER, the most common shape in the corpus: `_distributor = d`. Its
    ordering rungs both come back REFUTED (the new value can be above or below
    the old one), which is also the arm under which the delta proposer says
    "no single direction is sound" and stops. So the two filters agreed, for
    different reasons, to leave the setter with no R2 at all -- while the one
    property a setter obviously has, `post == the argument`, was expressible
    the whole time.
    """
    out = []
    for pn, pt in params or []:
        candidate = endpoint_candidate(pn, pt)
        if candidate is not None:
            out.append(candidate)
    return out


def propose_r2_specs(ladder_rows, params, log=None, var_bytes=None):
    """R2 delta specs to ASK FOR, derived from a ladder pass already measured.

    ---- WHY R2 HAS NEVER RUN --------------------------------------------

    `spec["vars"]` was built as `[{"name": s} for s in slot_vars]` -- names
    only. Nothing in 128 driver files ever wrote `abs_lo` or `delta_dir`, so
    the whole R2 class (`post in [lo, hi]`, `post - pre in [lo, hi]`) was
    reachable only from a hand-written spec. The ladder's R1 rungs are emitted
    unconditionally by the tool; R2 has to be REQUESTED, and nobody requested.

    ---- THE DIRECTION IS READ, NOT GUESSED -------------------------------

    `delta_dir` is mandatory in the spec and defaulting it is a
    false-certificate route: candidates are unsigned, so `post - pre` wraps on
    a decrease and a spec meaning "decreases by 1..10" answered as `inc` is
    answered about the wrapped difference. The direction is therefore taken
    from the ordering rungs the FIRST ladder pass already measured:

        ge HOLDS, le REFUTED  -> only ever increases  -> inc
        le HOLDS, ge REFUTED  -> only ever decreases  -> dec
        both HOLD             -> post == pre on the whole region. The delta is
                                 identically 0 and `post - pre in [p, p]` is
                                 false for every nonzero p. NOTHING PROPOSED --
                                 not because it is hard, because it is empty.
        both REFUTED          -> the region contains an increasing execution
                                 AND a decreasing one, so NO single direction
                                 is sound. Nothing proposed, and said out loud.

    ---- ONE ENTRY PER VARIABLE PER SPEC, WHICH IS WHY THIS RETURNS A LIST -

    `goto_coverage.cpp` keeps ONE `assert_vart` per name (it now refuses a
    duplicate outright rather than silently keeping the last). So a variable
    can carry one endpoint pair per query, and testing it against P different
    parameters needs P queries. Returning the list makes that cost visible
    instead of hiding it behind a spec that would have been half-dropped.

    ---- THE ABSOLUTE BOUND IS FREE, AND IT WAS NEVER ASKED FOR -----------

    `assert_vart` carries `has_abs` and `has_delta` as TWO INDEPENDENT flags on
    the SAME entry (goto_coverage.cpp:4283-4286), and both rungs are emitted
    from the same walk of the same candidate list (:9816-9853). So adding
    `abs_lo`/`abs_hi` beside `delta_*` asks a second question per variable at
    the cost of ZERO extra queries -- the query count stays one per endpoint
    name, exactly as before.

    That half of R2 had never been requested by anything. The class was
    reachable only from a hand-written spec, the same way the delta class was
    before this proposer existed, and the consequence is concentrated in one
    shape: a SETTER gets nothing today. Its ordering rungs both come back
    REFUTED, so no `delta_dir` is sound and the delta arm correctly declines --
    but `post in [d, d]` needs no direction, and it is the entire property.

    ---- AND A SECOND ROUND THAT ONLY ASKS ABOUT WHAT FAILED --------------

    `delta in [p, p]` is the strongest delta a single parameter can express and
    is therefore refuted whenever the unit takes a fee, scales by a rate, or
    splits the amount. Refuted there does NOT mean the delta is unbounded, so a
    stage-2 spec asks the cap `delta in [0, p]` -- "it moved by at most what
    you passed in", which is the property a withdraw-shaped unit is about.

    ⛔ STAGE 2 IS FILTERED BY STAGE 1'S ANSWERS, in `run_r2_passes`, and only
    the variables whose exact bound came back REFUTED stay in it. Asking the
    cap about a variable whose exact bound already HOLDS buys a strictly weaker
    rung for a whole query; asking it about one that got no verdict buys a
    second no-verdict. A stage-2 spec left with no variables is not run.

    ---- AND AN IDENTITY IS ONLY ASKED ABOUT CANDIDATES IT COULD EQUAL ----

    `var_bytes` maps a candidate to its storage width, so an `address`
    endpoint (20 bytes) is proposed only for the 20-byte candidates.

    MEASURED on farming setDistributor enc=15, which is why the filter exists
    rather than being an optimisation on paper. The identity query went out
    with all TEN candidates:

        _distributor   20 bytes   HOLDS      <- the answer that was wanted
        _owner         20 bytes   REFUTED    <- a real question: did it also
                                                overwrite the owner?
        _totalSupply   32 bytes   REFUTED  \\
        _balances[..]  32 bytes   REFUTED   |  a balance is not an address;
        _MAX_BALANCE   no slot    REFUTED   |  eight questions nobody has
        _allowances[..][..]  x4   NO VERDICT (solver unknown)

    Four of the eight did not merely waste solver time, they ran it to
    exhaustion: the nested-mapping shape is the one this corpus already knows
    returns `solver-unknown`. Cutting them costs no verdict at all -- every
    row removed was REFUTED-by-construction or undecided -- and the query
    keeps both rows anyone would read.

    ⛔ A CANDIDATE OF UNKNOWN WIDTH IS EXCLUDED, NOT INCLUDED. `_MAX_BALANCE`
    has no storage slot, so no test can read it and any rung over it is
    dropped downstream anyway; asking about it spends solver time to obtain a
    row that is discarded. With `var_bytes=None` nothing is filtered and the
    behaviour is exactly what it was, so a caller that cannot supply the
    layout is not silently narrowed.
    """
    say = log or (lambda _m: None)
    verdicts = {}
    for var, text, verdict in ladder_rows or []:
        verdicts.setdefault(var, {})[text] = verdict

    direction = {}
    for var, d in sorted(verdicts.items()):
        ge, le = d.get("post >= pre"), d.get("post <= pre")
        if ge == "HOLDS" and le == "REFUTED":
            direction[var] = "inc"
        elif le == "HOLDS" and ge == "REFUTED":
            direction[var] = "dec"
        elif ge == "HOLDS" and le == "HOLDS":
            say(f"[put]   R2 not proposed for {var}: `post == pre` over the "
                f"whole region, so every delta is 0 and a `[p, p]` bound is "
                f"false for every nonzero p")
        elif ge == "REFUTED" and le == "REFUTED":
            say(f"[put]   R2 not proposed for {var}: the region contains BOTH "
                f"an increasing and a decreasing execution, so no single "
                f"delta_dir is sound. This is a fact about the region, not a "
                f"gap in the proposer")
        else:
            say(f"[put]   R2 not proposed for {var}: the ordering rungs did "
                f"not both decide (ge={ge}, le={le})")

    # EVERY candidate the ladder ranged over is eligible for an ABSOLUTE bound,
    # not just the directed ones. `direction` is the delta arm's whitelist and
    # using it for both is what tied the setter's fate to an ordering verdict
    # that a setter can never produce.
    allvars = sorted(verdicts)
    ends = [e for e in endpoint_candidates(params) if e[1] != "bool"]
    numeric = [pn for pn, k, _b in ends if k == "num"]
    identity = [(pn, b) for pn, k, b in ends if k == "id"]
    if not ends:
        say("[put]   R2 not proposed at all: the unit has no parameter an "
            "endpoint could name -- no integer to bound an amount and no "
            "address/contract/enum to bound an identity")
        return []
    if not allvars:
        say("[put]   R2 not proposed at all: the first pass ranged over no "
            "candidate, so there is nothing for a bound to be about")
        return []

    # ---- A CANDIDATE WITH NO STORAGE SLOT COSTS A QUERY AND BUYS NOTHING ----
    #
    # Whatever verdict comes back for it, the emitter drops the rung with "no
    # storage slot: ... a rung over it is a compile-time tautology, not an
    # oracle" -- no test can read the value at all. Asking is a query spent on
    # a row that is discarded downstream.
    #
    # MEASURED on aqua `push`, where `_DOCKED` is the ONLY candidate the ladder
    # ranged over: the whole R2 pass went out, came back `post in [amount,
    # amount] REFUTED`, and was then dropped for having no slot. One esbmc
    # query for a row nobody could use.
    #
    # `var_bytes is None` means the caller could not supply the layout, and an
    # absent table must read as "no information", never as "no candidates".
    if var_bytes is None:
        slotted, unslotted = allvars, []
    else:
        slotted = [v for v in allvars if v in var_bytes]
        unslotted = [v for v in allvars if v not in var_bytes]
    if unslotted:
        say(f"[put]     {len(unslotted)} candidate(s) have NO storage slot, so "
            f"no R2 bound is asked about them -- solc's layout does not list "
            f"them, which makes them constant/immutable and unreadable by any "
            f"test: {', '.join(unslotted)}")

    out, dropped = [], []
    for p in numeric:
        # ONE entry per variable carrying BOTH questions where both apply.
        entries = []
        for v in slotted:
            e = {"name": v, "abs_lo": p, "abs_hi": p}
            if v in direction:
                e["delta_dir"] = direction[v]
                e["delta_lo"] = p
                e["delta_hi"] = p
            entries.append(e)
        if not entries:
            say(f"[put]     amount `{p}`: every candidate is unreadable by a "
                f"test, so no query is sent. This is a fact about the "
                f"contract's storage, not a gap in the proposer")
            continue
        out.append({"param": p, "stage": 1, "kind": "num", "vars": entries})
    for p, pbytes in identity:
        if var_bytes is None or pbytes is None:
            fit, unfit = allvars, []
        else:
            fit = [v for v in allvars if var_bytes.get(v) == pbytes]
            unfit = [v for v in allvars if v not in fit]
        if unfit:
            # ⛔ THE MESSAGE MAY NOT NAME A CAUSE IT HAS NOT CHECKED. What
            # stood here ended "...and four of these are the nested-mapping
            # shape that answers solver-unknown", which is a fact about ONE
            # run on farming welded into a message every contract prints. On
            # aqua the single excluded candidate is `_DOCKED`, a constant --
            # not a mapping, and not four of anything. A diagnostic that
            # asserts a specific mechanism it did not measure is worse than
            # one that says less: the next reader believes it.
            say(f"[put]     identity `{p}` is {pbytes} byte(s), so "
                f"{len(unfit)} candidate(s) of a different (or unknown) "
                f"width are NOT asked about it -- an equality between values "
                f"of different widths is false by construction, and a "
                f"candidate with no storage slot has no width to compare: "
                f"{', '.join(unfit)}")
        if not fit:
            say(f"[put]     identity `{p}`: NO candidate has its width, so no "
                f"query is sent. This is a fact about the contract's storage, "
                f"not a gap in the proposer")
            continue
        out.append({"param": p, "stage": 1, "kind": "id",
                    "vars": [{"name": v, "abs_lo": p, "abs_hi": p}
                             for v in fit]})
    # Stage 2 exists only where a direction was established; `run_r2_passes`
    # narrows it further to the variables stage 1 actually refuted.
    for p in numeric:
        # Same slot rule as stage 1: a cap on a value no test can read is a
        # query spent on a row the emitter drops.
        cap_vars = [v for v in sorted(direction) if v in slotted]
        if cap_vars:
            out.append({"param": p, "stage": 2, "kind": "cap",
                        "vars": [{"name": v, "delta_dir": direction[v],
                                  "delta_lo": "0", "delta_hi": p}
                                 for v in cap_vars]})
    if len(out) > R2_MAX_QUERIES:
        dropped = out[R2_MAX_QUERIES:]
        out = out[:R2_MAX_QUERIES]
    say(f"[put]   R2 proposed: {len(out)} query(ies) over "
        f"{len(allvars)} candidate(s) ({', '.join(allvars)}).")
    if numeric:
        say(f"[put]     stage 1 / amounts ({', '.join(numeric)}): "
            f"`post in [p, p]` for every candidate, and "
            f"`delta in [p, p]` for the {len(direction)} with a decided "
            f"direction -- both on the same spec entry, so the second costs "
            f"no extra query")
    if identity:
        say(f"[put]     stage 1 / identities "
            f"({', '.join(p for p, _b in identity)}): "
            f"`post in [p, p]` only. A delta of two identities is not a "
            f"weaker question, it is a meaningless one")
    if numeric and direction:
        say(f"[put]     stage 2 / caps: `delta in [0, p]`, run ONLY for the "
            f"variables stage 1 refuted the exact bound on")
    for s in dropped:
        say(f"[put]     ⛔ NOT ASKED (over the {R2_MAX_QUERIES}-query cap): "
            f"stage {s['stage']} {s['kind']} bound on `{s['param']}`")
    return out


def r2_term_text(term):
    """Canonical, report-safe spelling of one structured R2 term."""
    kind = term.get("kind")
    if kind == "pre":
        return "pre"
    if kind == "coord":
        return term["name"]
    if kind == "literal":
        return str(term["value"])
    if kind == "op":
        op = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[
            term["op"]]
        return f"({r2_term_text(term['lhs'])} {op} " \
               f"{r2_term_text(term['rhs'])})"
    raise ValueError(f"unknown R2 term kind: {kind!r}")


def r2_term_mentions_pre(term):
    """Whether a structured R2 term depends on the target's entry snapshot."""
    kind = term.get("kind")
    if kind == "pre":
        return True
    if kind in ("coord", "literal"):
        return False
    if kind == "op":
        return (r2_term_mentions_pre(term["lhs"]) or
                r2_term_mentions_pre(term["rhs"]))
    raise ValueError(f"unknown R2 term kind: {kind!r}")


def source_r2_literals(ast_path, contract, unit, arity=None,
                       declaration_id=None):
    """Integer atoms from the target body and literal-valued constants."""
    try:
        target = _select_def(_function_defs(ast_path, contract, unit), arity,
                             declaration_id)
    except (OSError, ValueError):
        return [], ["R2 source atoms unavailable: AST is absent or unreadable"]
    if target is None:
        return [], ["R2 source atoms unavailable: target declaration missing"]
    try:
        ast = _load_ast(ast_path)
    except (OSError, ValueError):
        return [], ["R2 source atoms unavailable: AST is absent or unreadable"]
    values = set()
    evidence = []

    def numeric_literal(node):
        if not isinstance(node, dict) or node.get("nodeType") != "Literal":
            return None
        if node.get("subdenomination"):
            return None
        value = str(node.get("value") or "")
        if node.get("kind") == "number" and value.isdigit():
            return value
        return None

    def collect(node, origin):
        if isinstance(node, dict):
            value = numeric_literal(node)
            if value is not None:
                values.add(value)
                evidence.append(f"R2 integer atom {value} from {origin} at "
                                f"AST src {node.get('src') or '?'}")
            for child in node.values():
                collect(child, origin)
        elif isinstance(node, list):
            for child in node:
                collect(child, origin)

    collect(target.get("body"), f"unit {unit}")

    def constants(node):
        if isinstance(node, dict):
            if (node.get("nodeType") == "VariableDeclaration"
                    and node.get("constant") and node.get("name")):
                value = numeric_literal(node.get("value"))
                if value is not None:
                    values.add(value)
                    evidence.append(f"R2 integer atom {value} from constant "
                                    f"{node['name']}")
            for child in node.values():
                constants(child)
        elif isinstance(node, list):
            for child in node:
                constants(child)

    by_id, owner = {}, None

    def index_contracts(node):
        nonlocal owner
        if isinstance(node, dict):
            if node.get("nodeType") == "ContractDefinition":
                if node.get("id") is not None:
                    by_id[node["id"]] = node
                if node.get("name") == contract:
                    owner = node
            for child in node.values():
                index_contracts(child)
        elif isinstance(node, list):
            for child in node:
                index_contracts(child)

    index_contracts(ast)
    if owner is not None:
        chain = owner.get("linearizedBaseContracts") or [owner.get("id")]
        for node_id in chain:
            constants(by_id.get(node_id))
    return sorted(values, key=lambda value: (int(value), value)), evidence


def source_assignment_r2_specs(ast_path, contract, unit, params, layout,
                               rendered_coords, arity=None,
                               declaration_id=None, rettypes=None, maps=None,
                               log=print):
    """R2 specs for simple source assignments.

    This is deliberately narrower than general expression mining.  It only
    proposes a candidate when the target function body assigns one visible state
    variable or readable mapping slot directly from either one of the unit's
    rendered parameters, a source-level bool literal, or a source-level integer
    literal, or when it performs a simple unsigned self-update such as `x += p`,
    `m[k] += p`, or `x = x + p`.
    It also recognizes explicit single-value returns and direct assignments to
    a single named return parameter. The candidate is still proved by
    --path-cov-assert; the source only decides which small query to ask first.
    """
    try:
        target = _select_def(_function_defs(ast_path, contract, unit), arity,
                             declaration_id)
        ast = _load_ast(ast_path)
    except (OSError, ValueError):
        return [], ["R2 source assignments unavailable: AST is absent or "
                    "unreadable"]
    if target is None:
        return [], ["R2 source assignments unavailable: target declaration "
                    "missing"]
    rendered = {name for name, _kind, _width in (rendered_coords or [])}
    rendered_numeric = {name for name, kind, _width in (rendered_coords or [])
                        if kind == "num"}
    rendered_by_kind = {}
    for name, kind, _width in (rendered_coords or []):
        rendered_by_kind.setdefault(kind, set()).add(name)
    param_ids = {}
    param_tys = {}
    param_names = {name for name, _ty in (params or [])}
    for p in ((target.get("parameters") or {}).get("parameters") or []):
        name = p.get("name")
        if name and p.get("id") is not None:
            param_ids[p["id"]] = name
            param_tys[p["id"]] = _norm_ty(
                (p.get("typeDescriptions") or {}).get("typeString") or "")

    local_ids = set()
    local_aliases = {}
    local_storage_ids = set()
    local_storage_aliases = {}

    by_id, owner = {}, None

    def index(n):
        nonlocal owner
        if isinstance(n, dict):
            if n.get("nodeType") == "ContractDefinition":
                if n.get("id") is not None:
                    by_id[n["id"]] = n
                if n.get("name") == contract:
                    owner = n
            for child in n.values():
                index(child)
        elif isinstance(n, list):
            for child in n:
                index(child)

    index(ast)
    scopes = []
    if owner is not None:
        chain = owner.get("linearizedBaseContracts") or [owner.get("id")]
        scopes = [by_id[c] for c in reversed(chain) if c in by_id]
    if not scopes:
        scopes = [ast]
    state_ids = {}
    constant_ids = {}
    for scope in scopes:
        for n in (scope.get("nodes") or []):
            if (isinstance(n, dict) and
                    n.get("nodeType") == "VariableDeclaration" and
                    n.get("stateVariable") and n.get("name") and
                    n.get("id") is not None):
                ty = (n.get("typeDescriptions") or {}).get("typeString") or ""
                state_ids[n["id"]] = (n["name"], ty)
            if (isinstance(n, dict) and
                    n.get("nodeType") == "VariableDeclaration" and
                    n.get("constant") and n.get("name") and
                    n.get("id") is not None):
                ty = (n.get("typeDescriptions") or {}).get("typeString") or ""
                constant_ids[n["id"]] = (n["name"], ty, n.get("value"))

    entries, evidence, seen, by_name = [], [], set(), {}
    next_id = [0]

    def identifier_ref(n):
        if not isinstance(n, dict) or n.get("nodeType") != "Identifier":
            return None
        ref = n.get("referencedDeclaration")
        return ref if isinstance(ref, int) else None

    def local_alias_expr(n, seen=None):
        ref = identifier_ref(n)
        if ref is None or ref not in local_aliases:
            return None
        seen = set() if seen is None else set(seen)
        if ref in seen:
            return None
        seen.add(ref)
        expr = local_aliases[ref]
        nested = local_alias_expr(expr, seen)
        return nested if nested is not None else expr

    def local_storage_alias_expr(n, seen=None):
        ref = identifier_ref(n)
        if ref is None or ref not in local_storage_aliases:
            return None
        seen = set() if seen is None else set(seen)
        if ref in seen:
            return None
        seen.add(ref)
        expr = local_storage_aliases[ref]
        expr_ref = identifier_ref(expr)
        if expr_ref in seen:
            return None
        nested = local_storage_alias_expr(expr, seen)
        return nested if nested is not None else expr

    def is_storage_local_decl(n):
        if not isinstance(n, dict):
            return False
        if n.get("storageLocation") == "storage":
            return True
        ty = (n.get("typeDescriptions") or {}).get("typeString") or ""
        return " storage" in ty

    def unsigned_ty(t):
        return re.match(r"^uint(\d+)?$", t or "") is not None

    def unitless_number_term(n):
        alias = local_alias_expr(n)
        if alias is not None:
            return unitless_number_term(alias)
        if not isinstance(n, dict) or n.get("nodeType") != "Literal":
            return None
        if (n.get("kind") == "number" and not n.get("subdenomination")):
            value = str(n.get("value") or "")
            if value.isdigit():
                return {"kind": "literal", "value": value}, value
        return None

    def literal_term(n, state_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return literal_term(alias, state_ty)
        if not isinstance(n, dict) or n.get("nodeType") != "Literal":
            return None
        if n.get("kind") == "bool" and state_ty == "bool":
            value = n.get("value")
            if value is True or str(value).lower() == "true":
                return {"kind": "literal", "value": "1"}, "true"
            if value is False or str(value).lower() == "false":
                return {"kind": "literal", "value": "0"}, "false"
        if unsigned_ty(state_ty):
            return unitless_number_term(n)
        return None

    def zero_term(state_ty):
        state_ty = _norm_ty(state_ty)
        if state_ty == "bool":
            return {"kind": "literal", "value": "0"}, "false"
        if unsigned_ty(state_ty):
            return {"kind": "literal", "value": "0"}, "0"
        if state_ty == "address" or state_ty.startswith(("contract ",
                                                          "interface ")):
            return {"kind": "literal", "value": "0"}, "0"
        return None

    def address_zero_term(n, state_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return address_zero_term(alias, state_ty)
        if not (_norm_ty(state_ty) == "address"
                or _norm_ty(state_ty).startswith(("contract ", "interface "))):
            return None
        if not isinstance(n, dict) or n.get("nodeType") != "FunctionCall":
            return None
        args = n.get("arguments") or []
        if len(args) != 1:
            return None
        arg = unitless_number_term(args[0])
        if arg is None or arg[1] != "0":
            return None
        expr = n.get("expression")
        if not isinstance(expr, dict):
            return None
        if expr.get("nodeType") == "ElementaryTypeNameExpression":
            ty = expr.get("typeName") or {}
            if ty.get("name") == "address":
                return {"kind": "literal", "value": "0"}, "address(0)"
        return None

    def constant_term(n, state_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return constant_term(alias, state_ty)
        ref = identifier_ref(n)
        const = constant_ids.get(ref)
        if const is None:
            return None
        name, const_ty, value = const
        if type_coord_kind(const_ty) != type_coord_kind(state_ty):
            return None
        literal = literal_term(value, _norm_ty(state_ty))
        if literal is not None:
            return literal[0], name
        zero_addr = address_zero_term(value, state_ty)
        if zero_addr is not None:
            return zero_addr[0], name
        return None

    def address_literal_key(n):
        alias = local_alias_expr(n)
        if alias is not None:
            return address_literal_key(alias)
        literal = unitless_number_term(n)
        if literal is not None:
            return literal[1]
        if isinstance(n, dict) and n.get("nodeType") == "Literal":
            if n.get("kind") == "hexString":
                value = str(n.get("hexValue") or n.get("value") or "")
                if re.fullmatch(r"[0-9a-fA-F]{1,40}", value):
                    return "0x" + value
        if (isinstance(n, dict) and n.get("nodeType") == "FunctionCall"
                and n.get("kind") == "typeConversion"):
            args = n.get("arguments") or []
            expr = n.get("expression")
            if len(args) == 1 and isinstance(expr, dict):
                ty = expr.get("typeName") or {}
                if ty.get("name") == "address":
                    return address_literal_key(args[0])
        return None

    def constant_key_name(n, expected_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return constant_key_name(alias, expected_ty)
        ref = identifier_ref(n)
        const = constant_ids.get(ref)
        if const is None:
            return None
        _name, const_ty, value = const
        expected = _norm_ty(expected_ty)
        if type_coord_kind(const_ty) != type_coord_kind(expected):
            return None
        literal = unitless_number_term(value)
        if literal is not None and re.match(r"^u?int(\d+)?$", expected):
            return literal[1]
        if isinstance(value, dict) and value.get("nodeType") == "Literal":
            if value.get("kind") == "bool" and expected == "bool":
                v = value.get("value")
                if v is True or str(v).lower() == "true":
                    return "1"
                if v is False or str(v).lower() == "false":
                    return "0"
        if expected == "address":
            return address_literal_key(value)
        return None

    def env_coord_name(n):
        if not isinstance(n, dict) or n.get("nodeType") != "MemberAccess":
            return None
        member = n.get("memberName")
        if member not in ("sender", "value"):
            return None
        base = n.get("expression")
        if (isinstance(base, dict) and base.get("nodeType") == "Identifier"
                and base.get("name") == "msg"):
            return f"msg.{member}"
        return None

    def type_coord_kind(t):
        t = _norm_ty(t)
        if t == "bool":
            return "bool"
        if unsigned_ty(t):
            return "num"
        if t == "address" or t.startswith(("contract ", "interface ")):
            return "id"
        return None

    def type_conversion_arg(n, target_ty):
        if not isinstance(n, dict) or n.get("nodeType") != "FunctionCall":
            return None
        if n.get("kind") != "typeConversion":
            return None
        args = n.get("arguments") or []
        if len(args) != 1:
            return None
        target_ty = _norm_ty(target_ty)
        cast_ty = _norm_ty((n.get("typeDescriptions") or {}).get(
            "typeString") or "")
        if not cast_ty or not target_ty:
            return None
        if unsigned_ty(target_ty):
            return args[0] if cast_ty == target_ty else None
        target_kind = type_coord_kind(target_ty)
        cast_kind = type_coord_kind(cast_ty)
        if target_kind in ("id", "bool") and target_kind == cast_kind:
            return args[0]
        return None

    def coord_term(n, expected_kind, target_ty=None):
        alias = local_alias_expr(n)
        if alias is not None:
            return coord_term(alias, expected_kind, target_ty)
        if target_ty is not None:
            arg = type_conversion_arg(n, target_ty)
            if arg is not None:
                return coord_term(arg, expected_kind)
        if expected_kind is None:
            return None
        slot = slot_lhs(n)
        if slot is not None and type_coord_kind(slot[1]) == expected_kind:
            return {"kind": "coord", "name": "state." + slot[0]}, (
                "state." + slot[0])
        member = state_member_lhs(n)
        if (member is not None
                and type_coord_kind(member[1]) == expected_kind):
            return {"kind": "coord", "name": "state." + member[0]}, (
                "state." + member[0])
        ref = identifier_ref(n)
        name = param_ids.get(ref)
        if name and name in rendered_by_kind.get(expected_kind, set()):
            return {"kind": "coord", "name": name}, name
        state = state_ids.get(ref)
        if state is not None:
            state_name = f"state.{state[0]}"
            if state_name in rendered_by_kind.get(expected_kind, set()):
                return {"kind": "coord", "name": state_name}, state_name
        env_name = env_coord_name(n)
        if env_name and env_name in rendered_by_kind.get(expected_kind, set()):
            return {"kind": "coord", "name": env_name}, env_name
        return None

    def source_id():
        value = next_id[0]
        next_id[0] += 1
        return f"src{value}"

    def entry_for(state_name):
        entry = by_name.get(state_name)
        if entry is None:
            entry = {"name": state_name, "equals": [], "abs": [],
                     "deltas": []}
            by_name[state_name] = entry
            entries.append(entry)
        return entry

    def target_readable(name):
        if name == RETURN_VAR or name in (layout or {}):
            return True
        mname, _keys, tail = parse_slot_name(name)
        return bool(mname is not None and queryable_mapping(maps, mname + tail))

    def add_equals_candidate(state_name, term, reason, src):
        key = (state_name, "equals", r2_term_text(term))
        if target_readable(state_name) and key not in seen:
            seen.add(key)
            entry_for(state_name)["equals"].append({
                "id": source_id(),
                "term": term,
            })
            label = "return" if state_name == RETURN_VAR else "post"
            evidence.append(
                f"R2 source assignment candidate {state_name}: {label} == "
                f"{reason} from AST src {src or '?'}")

    def add_delta_candidate(state_name, direction, term, reason, src):
        key = (state_name, "deltas", direction, r2_term_text(term))
        if target_readable(state_name) and key not in seen:
            seen.add(key)
            entry_for(state_name)["deltas"].append({
                "id": source_id(),
                "dir": direction,
                "lo": term,
                "hi": term,
            })
            lhs, rhs = ("post", "pre") if direction == "inc" else ("pre",
                                                                    "post")
            evidence.append(
                f"R2 source assignment candidate {state_name}: {lhs} - {rhs} "
                f"== {reason} from AST src {src or '?'}")

    def delta_term(n, target_ty=None):
        alias = local_alias_expr(n)
        if alias is not None:
            return delta_term(alias, target_ty)
        if target_ty is not None:
            arg = type_conversion_arg(n, target_ty)
            if arg is not None:
                return delta_term(arg)
        slot = slot_lhs(n)
        if slot is not None and type_coord_kind(slot[1]) == "num":
            return {"kind": "coord", "name": "state." + slot[0]}, (
                "state." + slot[0])
        member = state_member_lhs(n)
        if member is not None and type_coord_kind(member[1]) == "num":
            return {"kind": "coord", "name": "state." + member[0]}, (
                "state." + member[0])
        ref = identifier_ref(n)
        param_name = param_ids.get(ref)
        if (param_name and param_name in param_names
                and param_name in rendered_numeric
                and unsigned_ty(param_tys.get(ref, ""))):
            return {"kind": "coord", "name": param_name}, param_name
        state = state_ids.get(ref)
        if state is not None:
            state_name = f"state.{state[0]}"
            if (state_name in rendered_numeric
                    and unsigned_ty(_norm_ty(state[1]))):
                return {"kind": "coord", "name": state_name}, state_name
        constant = constant_term(n, target_ty) if target_ty else None
        if constant is not None and constant[0].get("kind") == "literal":
            return constant
        env_name = env_coord_name(n)
        if env_name == "msg.value" and env_name in rendered_numeric:
            return {"kind": "coord", "name": env_name}, env_name
        return unitless_number_term(n)

    def numeric_endpoint_term(n, target_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return numeric_endpoint_term(alias, target_ty)
        direct = delta_term(n, target_ty)
        if direct is not None:
            return direct
        if not isinstance(n, dict) or n.get("nodeType") != "BinaryOperation":
            return None
        op = {"+": "add", "-": "sub", "*": "mul", "/": "div"}.get(
            n.get("operator"))
        if op is None:
            return None
        lhs = delta_term(n.get("leftExpression"), target_ty)
        rhs = delta_term(n.get("rightExpression"), target_ty)
        if lhs is None or rhs is None:
            return None
        if op == "div" and not (rhs[0].get("kind") == "literal"
                                and int(rhs[0].get("value", "0")) != 0):
            return None
        term = {"kind": "op", "op": op, "lhs": lhs[0], "rhs": rhs[0]}
        return term, r2_term_text(term)

    def self_ref(n, state_id):
        return identifier_ref(n) == state_id

    def self_update_delta(rhs, self_predicate, target_ty):
        alias = local_alias_expr(rhs)
        if alias is not None:
            return self_update_delta(alias, self_predicate, target_ty)
        if not isinstance(rhs, dict) or rhs.get("nodeType") != "BinaryOperation":
            return None
        op = rhs.get("operator")
        left = rhs.get("leftExpression")
        right = rhs.get("rightExpression")
        if op == "+":
            if self_predicate(left):
                term = numeric_endpoint_term(right, target_ty)
                return ("inc",) + term if term is not None else None
            if self_predicate(right):
                term = numeric_endpoint_term(left, target_ty)
                return ("inc",) + term if term is not None else None
        if op == "-" and self_predicate(left):
            term = numeric_endpoint_term(right, target_ty)
            return ("dec",) + term if term is not None else None
        return None

    def key_name(n, expected_ty):
        alias = local_alias_expr(n)
        if alias is not None:
            return key_name(alias, expected_ty)
        ref = identifier_ref(n)
        param_name = param_ids.get(ref)
        if (param_name and param_name in param_names and
                _norm_ty(param_tys.get(ref, "")) == _norm_ty(expected_ty)):
            return param_name
        expected = _norm_ty(expected_ty)
        constant_key = constant_key_name(n, expected)
        if constant_key is not None:
            return constant_key
        literal = unitless_number_term(n)
        if literal is not None and re.match(r"^u?int(\d+)?$", expected):
            return literal[1]
        if literal is not None and expected == "address":
            return literal[1]
        if isinstance(n, dict) and n.get("nodeType") == "Literal":
            if n.get("kind") == "bool" and expected == "bool":
                value = n.get("value")
                if value is True or str(value).lower() == "true":
                    return "1"
                if value is False or str(value).lower() == "false":
                    return "0"
            if n.get("kind") == "hexString" and expected == "address":
                return address_literal_key(n)
        if expected == "address":
            key = address_literal_key(n)
            if key is not None:
                return key
        zero_addr = address_zero_term(n, expected)
        if zero_addr is not None:
            return "0"
        if (isinstance(n, dict) and n.get("nodeType") == "MemberAccess" and
                n.get("memberName") == "sender"):
            base = n.get("expression")
            if (isinstance(base, dict) and base.get("nodeType") == "Identifier"
                    and base.get("name") == "msg"
                    and _norm_ty(expected_ty) == "address"):
                return "msg.sender"
        return None

    def state_path(n):
        ref = identifier_ref(n)
        state = state_ids.get(ref)
        if state is not None:
            return state[0]
        if isinstance(n, dict) and n.get("nodeType") == "MemberAccess":
            base = state_path(n.get("expression"))
            member = n.get("memberName")
            if base and member:
                return base + "." + member
        return None

    def slot_lhs(n):
        if not maps:
            return None
        cur = n
        tail = ""
        while isinstance(cur, dict) and cur.get("nodeType") == "MemberAccess":
            member = cur.get("memberName")
            if not member:
                return None
            tail = "." + member + tail
            cur = cur.get("expression")
        keys = []
        while isinstance(cur, dict) and cur.get("nodeType") == "IndexAccess":
            keys.append(cur.get("indexExpression"))
            cur = cur.get("baseExpression")
        alias = local_storage_alias_expr(cur)
        if alias is not None:
            expanded = alias
            final_ty = _norm_ty((n.get("typeDescriptions") or {}).get(
                "typeString") or "")
            for key in reversed(keys):
                expanded = {
                    "nodeType": "IndexAccess",
                    "baseExpression": expanded,
                    "indexExpression": key,
                    "typeDescriptions": {"typeString": final_ty},
                }
            members = tail.lstrip(".").split(".") if tail else []
            for member in members:
                expanded = {
                    "nodeType": "MemberAccess",
                    "memberName": member,
                    "expression": expanded,
                    "typeDescriptions": {"typeString": final_ty},
                }
            return slot_lhs(expanded)
        if not keys:
            return None
        base_name = state_path(cur)
        if base_name is None:
            return None
        mkey = base_name + tail
        if not queryable_mapping(maps, mkey):
            return None
        _slot, kty, _nbytes, _off, _base, _member = maps[mkey]
        ktypes = list(kty) if isinstance(kty, tuple) else [kty]
        if len(keys) != len(ktypes):
            return None
        names = []
        for key, expected_ty in zip(reversed(keys), ktypes):
            name = key_name(key, expected_ty)
            if name is None:
                return None
            names.append(name)
        ty = _norm_ty((n.get("typeDescriptions") or {}).get("typeString") or "")
        return base_name + "".join(f"[{name}]" for name in names) + tail, ty

    def state_member_lhs(n):
        if not layout:
            return None
        cur = n
        tail = ""
        while isinstance(cur, dict) and cur.get("nodeType") == "MemberAccess":
            member = cur.get("memberName")
            if not member:
                return None
            tail = "." + member + tail
            cur = cur.get("expression")
        if not tail:
            return None
        alias = local_storage_alias_expr(cur)
        if alias is not None:
            expanded = alias
            final_ty = _norm_ty((n.get("typeDescriptions") or {}).get(
                "typeString") or "")
            members = tail.lstrip(".").split(".")
            for member in members:
                expanded = {
                    "nodeType": "MemberAccess",
                    "memberName": member,
                    "expression": expanded,
                    "typeDescriptions": {"typeString": final_ty},
                }
            return state_member_lhs(expanded)
        ref = identifier_ref(cur)
        state = state_ids.get(ref)
        if state is None:
            return None
        name = state[0] + tail
        if name not in layout:
            return None
        ty = _norm_ty((n.get("typeDescriptions") or {}).get("typeString") or "")
        return name, ty

    return_target = None
    return_ty = None
    return_ids = set()
    if rettypes is not None and len(rettypes) == 1:
        return_ty = rettypes[0][1]
        return_target = endpoint_candidate(RETURN_VAR, rettypes[0][1])
        if return_target is not None:
            for p in ((target.get("returnParameters") or {}).get(
                    "parameters") or []):
                ref = p.get("id")
                if isinstance(ref, int):
                    return_ids.add(ref)

    def return_term(n, expected_kind, target_ty=None):
        alias = local_alias_expr(n)
        if alias is not None:
            return return_term(alias, expected_kind, target_ty)
        if expected_kind == "bool":
            literal = literal_term(n, "bool")
            if literal is not None:
                return literal
            return coord_term(n, "bool", target_ty)
        if expected_kind == "id":
            return coord_term(n, "id", target_ty)
        if expected_kind != "num":
            return None
        direct = delta_term(n, target_ty)
        if direct is not None:
            return direct
        if not isinstance(n, dict) or n.get("nodeType") != "BinaryOperation":
            return None
        op = {"+": "add", "-": "sub", "*": "mul", "/": "div"}.get(
            n.get("operator"))
        if op is None:
            return None
        lhs = return_term(n.get("leftExpression"), "num", target_ty)
        rhs = return_term(n.get("rightExpression"), "num", target_ty)
        if lhs is None or rhs is None:
            return None
        if op == "div" and not (rhs[0].get("kind") == "literal"
                                and int(rhs[0].get("value", "0")) != 0):
            return None
        term = {"kind": "op", "op": op, "lhs": lhs[0], "rhs": rhs[0]}
        return term, r2_term_text(term)

    def walk(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "VariableDeclarationStatement":
                decls = [d for d in (n.get("declarations") or [])
                         if isinstance(d, dict)]
                init = n.get("initialValue")
                if len(decls) == 1:
                    decl = decls[0]
                    ref = decl.get("id")
                    if isinstance(ref, int):
                        local_ids.add(ref)
                        if is_storage_local_decl(decl):
                            local_storage_ids.add(ref)
                        if init is not None:
                            local_aliases[ref] = init
                            if ref in local_storage_ids:
                                local_storage_aliases[ref] = init
                for child in n.values():
                    walk(child)
                return
            if n.get("nodeType") == "Assignment":
                operator = n.get("operator")
                lhs = n.get("leftHandSide")
                lhs_ref = identifier_ref(lhs)
                if operator == "=" and lhs_ref in local_ids:
                    rhs = n.get("rightHandSide")
                    local_aliases[lhs_ref] = rhs
                    if lhs_ref in local_storage_ids and rhs is not None:
                        local_storage_aliases[lhs_ref] = rhs
                    else:
                        local_storage_aliases.pop(lhs_ref, None)
                    for child in n.values():
                        walk(child)
                    return
                if lhs_ref in local_ids:
                    local_aliases.pop(lhs_ref, None)
                    local_storage_aliases.pop(lhs_ref, None)
                member = state_member_lhs(lhs)
                state = state_ids.get(lhs_ref)
                state_name = (member[0] if member is not None
                              else (state[0] if state else None))
                state_ty = _norm_ty(member[1] if member is not None
                                    else (state[1] if state else ""))
                slot = slot_lhs(lhs)
                slot_name = slot[0] if slot else None
                slot_ty = slot[1] if slot else ""
                rhs = n.get("rightHandSide")
                if slot_name and unsigned_ty(slot_ty) and operator in (
                        "+=", "-="):
                    delta = numeric_endpoint_term(rhs, slot_ty)
                    if delta is not None:
                        add_delta_candidate(
                            slot_name, "inc" if operator == "+=" else "dec",
                            delta[0], delta[1], n.get("src"))
                    for child in n.values():
                        walk(child)
                    return
                if state_name and unsigned_ty(state_ty) and operator in (
                        "+=", "-="):
                    delta = numeric_endpoint_term(rhs, state_ty)
                    if delta is not None:
                        add_delta_candidate(
                            state_name, "inc" if operator == "+=" else "dec",
                            delta[0], delta[1], n.get("src"))
                    for child in n.values():
                        walk(child)
                    return
                if operator != "=":
                    for child in n.values():
                        walk(child)
                    return
                rhs_ref = identifier_ref(rhs)
                param_name = param_ids.get(rhs_ref)
                if (slot_name and param_name and
                        param_name in param_names and param_name in rendered):
                    add_equals_candidate(
                        slot_name, {"kind": "coord", "name": param_name},
                        param_name, n.get("src"))
                coord = coord_term(rhs, type_coord_kind(slot_ty), slot_ty)
                if slot_name and coord is not None:
                    add_equals_candidate(slot_name, coord[0], coord[1],
                                         n.get("src"))
                literal = literal_term(rhs, slot_ty)
                if slot_name and literal is not None:
                    add_equals_candidate(slot_name, literal[0], literal[1],
                                         n.get("src"))
                zero_addr = address_zero_term(rhs, slot_ty)
                if slot_name and zero_addr is not None:
                    add_equals_candidate(slot_name, zero_addr[0],
                                         zero_addr[1], n.get("src"))
                constant = constant_term(rhs, slot_ty)
                if slot_name and constant is not None:
                    add_equals_candidate(slot_name, constant[0], constant[1],
                                         n.get("src"))
                endpoint = (numeric_endpoint_term(rhs, slot_ty)
                            if unsigned_ty(slot_ty) else None)
                if slot_name and endpoint is not None:
                    add_equals_candidate(slot_name, endpoint[0], endpoint[1],
                                         n.get("src"))
                if slot_name and unsigned_ty(slot_ty):
                    delta = self_update_delta(
                        rhs,
                        lambda candidate: (
                            slot_lhs(candidate) or (None,))[0] == slot_name,
                        slot_ty)
                    if delta is not None:
                        add_delta_candidate(slot_name, delta[0], delta[1],
                                            delta[2], n.get("src"))
                if (state_name and param_name and
                        param_name in param_names and param_name in rendered):
                    add_equals_candidate(
                        state_name, {"kind": "coord", "name": param_name},
                        param_name, n.get("src"))
                coord = coord_term(rhs, type_coord_kind(state_ty), state_ty)
                if state_name and coord is not None:
                    add_equals_candidate(state_name, coord[0], coord[1],
                                         n.get("src"))
                literal = literal_term(rhs, state_ty)
                if state_name and literal is not None:
                    add_equals_candidate(state_name, literal[0], literal[1],
                                         n.get("src"))
                zero_addr = address_zero_term(rhs, state_ty)
                if state_name and zero_addr is not None:
                    add_equals_candidate(state_name, zero_addr[0],
                                         zero_addr[1], n.get("src"))
                constant = constant_term(rhs, state_ty)
                if state_name and constant is not None:
                    add_equals_candidate(state_name, constant[0], constant[1],
                                         n.get("src"))
                endpoint = (numeric_endpoint_term(rhs, state_ty)
                            if unsigned_ty(state_ty) else None)
                if state_name and endpoint is not None:
                    add_equals_candidate(state_name, endpoint[0], endpoint[1],
                                         n.get("src"))
                if state_name and unsigned_ty(state_ty):
                    state_self = (
                        (lambda candidate:
                         (state_member_lhs(candidate) or (None,))[0]
                         == state_name)
                        if member is not None
                        else (lambda candidate: self_ref(candidate, lhs_ref)))
                    delta = self_update_delta(
                        rhs, state_self, state_ty)
                    if delta is not None:
                        add_delta_candidate(state_name, delta[0], delta[1],
                                            delta[2], n.get("src"))
                if lhs_ref in return_ids and return_target is not None:
                    term = return_term(rhs, return_target[1], return_ty)
                    if term is not None:
                        add_equals_candidate(RETURN_VAR, term[0], term[1],
                                             n.get("src"))
            elif n.get("nodeType") == "UnaryOperation" and n.get(
                    "operator") in ("++", "--"):
                direction = "inc" if n.get("operator") == "++" else "dec"
                one = {"kind": "literal", "value": "1"}
                sub = n.get("subExpression")
                sub_ref = identifier_ref(sub)
                if sub_ref in local_ids:
                    local_aliases.pop(sub_ref, None)
                    local_storage_aliases.pop(sub_ref, None)
                state = state_ids.get(sub_ref)
                if state is not None and unsigned_ty(_norm_ty(state[1])):
                    add_delta_candidate(state[0], direction, one, "1",
                                        n.get("src"))
                member = state_member_lhs(sub)
                if member is not None and unsigned_ty(member[1]):
                    add_delta_candidate(member[0], direction, one, "1",
                                        n.get("src"))
                slot = slot_lhs(sub)
                if slot is not None and unsigned_ty(slot[1]):
                    add_delta_candidate(slot[0], direction, one, "1",
                                        n.get("src"))
            elif n.get("nodeType") == "UnaryOperation" and n.get(
                    "operator") == "delete":
                sub = n.get("subExpression")
                sub_ref = identifier_ref(sub)
                if sub_ref in local_ids:
                    local_aliases.pop(sub_ref, None)
                    local_storage_aliases.pop(sub_ref, None)
                state = state_ids.get(sub_ref)
                if state is not None:
                    zero = zero_term(_norm_ty(state[1]))
                    if zero is not None:
                        add_equals_candidate(state[0], zero[0], zero[1],
                                             n.get("src"))
                member = state_member_lhs(sub)
                if member is not None:
                    zero = zero_term(member[1])
                    if zero is not None:
                        add_equals_candidate(member[0], zero[0], zero[1],
                                             n.get("src"))
                slot = slot_lhs(sub)
                if slot is not None:
                    zero = zero_term(slot[1])
                    if zero is not None:
                        add_equals_candidate(slot[0], zero[0], zero[1],
                                             n.get("src"))
            elif n.get("nodeType") == "Return" and return_target is not None:
                term = return_term(n.get("expression"), return_target[1],
                                   return_ty)
                if term is not None:
                    add_equals_candidate(RETURN_VAR, term[0], term[1],
                                         n.get("src"))
            for child in n.values():
                walk(child)
        elif isinstance(n, list):
            for child in n:
                walk(child)

    walk(target.get("body"))
    if not entries:
        return [], evidence
    for line in evidence:
        log(f"[put]   {line}")
    return [{
        "param": "source_assign",
        "stage": 1,
        "kind": "source-assign",
        "depth": 0,
        "candidate_count": sum(len(entry[kind]) for entry in entries
                               for kind in ("equals", "abs", "deltas")),
        "vars": entries,
    }], evidence


def _r2_direction(ladder_rows, log):
    verdicts = {}
    for var, text, verdict in ladder_rows or []:
        verdicts.setdefault(var, {})[text] = verdict
    direction = {}
    for var, rows in sorted(verdicts.items()):
        ge, le = rows.get("post >= pre"), rows.get("post <= pre")
        if ge == "HOLDS" and le == "REFUTED":
            direction[var] = "inc"
        elif le == "HOLDS" and ge == "REFUTED":
            direction[var] = "dec"
        elif ge == "REFUTED" and le == "REFUTED":
            log(f"[put]   typed R2 delta omitted for {var}: both directions "
                "occur in the certified region")
        elif ge != "HOLDS" or le != "HOLDS":
            log(f"[put]   typed R2 delta omitted for {var}: ordering did not "
                f"decide (ge={ge}, le={le})")
    return verdicts, direction


def propose_r2_batch(ladder_rows, params, source_literals=(), depth=1,
                     var_bytes=None, rendered_coords=None,
                     rettypes=None,
                     term_budget=R2_TERM_BUDGET,
                     candidate_budget=R2_CANDIDATE_BUDGET, log=print):
    """Build one typed depth-zero/one candidate batch for one certified path."""
    if depth not in (0, 1):
        raise ValueError("the implemented R2 grammar supports depth 0 or 1")
    if term_budget < 1:
        raise ValueError("R2 term budget must be positive")
    if candidate_budget < 1:
        raise ValueError("R2 candidate budget must be positive")
    verdicts, direction = _r2_direction(ladder_rows, log)
    target_bytes = dict(var_bytes or {})
    endpoint_kinds = {name: kind for name, kind, _width
                      in endpoint_candidates(params)}
    has_bool_endpoint = any(kind == "bool" for kind in endpoint_kinds.values())
    target_kinds = {}
    allvars = []
    for name in sorted(verdicts):
        rows = verdicts[name]
        if "post >= pre" not in rows or "post <= pre" not in rows:
            if (has_bool_endpoint and rows.get("post == pre") in
                    ("HOLDS", "REFUTED") and rows.get("post != pre") in
                    ("HOLDS", "REFUTED")):
                allvars.append(name)
                target_kinds[name] = "bool"
                continue
            log(f"[put]   typed R2 omitted for {name}: the R1 ladder emitted "
                "no ordering pair, so this is not an ordering-capable "
                "unsigned scalar")
            continue
        allvars.append(name)
        target_kinds[name] = "num"

    return_target = None
    if rettypes is not None and len(rettypes) == 1:
        return_target = endpoint_candidate(RETURN_VAR, rettypes[0][1])
    retlive_refuted = any(
        name == RETURN_VAR and text.startswith(RETLIVE_PREFIX)
        and verdict == "REFUTED"
        for name, text, verdict in ladder_rows)
    if return_target is not None and retlive_refuted:
        allvars.append(RETURN_VAR)
        if return_target[2] is not None:
            target_bytes[RETURN_VAR] = return_target[2]
    elif return_target is not None:
        log("[put]   typed R2 omitted for return: the retlive witness was not "
            "REFUTED, so return rungs would be vacuous")

    if var_bytes is not None:
        allvars = [name for name in allvars
                   if name == RETURN_VAR or name in target_bytes]
    if not allvars:
        log("[put]   typed R2 not proposed: no readable ladder candidate")
        return []

    if rendered_coords is None:
        rendered_coords = [(name, kind, width)
                           for name, kind, width in endpoint_candidates(params)]
    coords = []
    for name, kind, width in rendered_coords:
        if name and kind in ("num", "id", "bool"):
            coords.append((name, kind, width,
                           {"kind": "coord", "name": name}))
    literals = [{"kind": "literal", "value": str(value)}
                for value in source_literals if str(value).isdigit()]

    def dedup(terms):
        out, seen = [], set()
        for term in terms:
            text = r2_term_text(term)
            if text not in seen:
                seen.add(text)
                out.append(term)
        return out

    pre = {"kind": "pre"}
    numeric_atoms = dedup([pre] + [term for _n, kind, _w, term in coords
                                   if kind == "num"] + literals)
    terms = list(numeric_atoms)
    if depth == 1:
        for lhs in numeric_atoms:
            for rhs in numeric_atoms:
                for op in ("add", "sub", "mul"):
                    terms.append({"kind": "op", "op": op,
                                  "lhs": lhs, "rhs": rhs})
        nonzero_literals = [term for term in literals
                            if int(term["value"]) != 0]
        for lhs in numeric_atoms:
            for rhs in nonzero_literals:
                terms.append({"kind": "op", "op": "div",
                              "lhs": lhs, "rhs": rhs})
    terms = dedup(terms)
    dropped = max(0, len(terms) - term_budget)
    terms = terms[:term_budget]
    if dropped:
        log(f"[put]   typed R2 term budget kept {len(terms)} and named "
            f"{dropped} mechanically generated term(s) as NOT ASKED")

    zero = next((term for term in literals if term["value"] == "0"), None)
    entries = []
    for var in allvars:
        width = target_bytes.get(var)
        var_numeric_atoms = numeric_atoms
        var_terms = terms
        if var == RETURN_VAR:
            var_numeric_atoms = [
                term for term in numeric_atoms
                if not r2_term_mentions_pre(term)]
            var_terms = [
                term for term in terms if not r2_term_mentions_pre(term)]
        identity = [term for _name, kind, nbytes, term in coords
                    if kind == "id" and (width is None or nbytes is None
                                         or width == nbytes)]
        bool_terms = [term for _name, kind, _nbytes, term in coords
                      if kind == "bool"]
        if target_kinds.get(var) == "bool":
            equals = dedup(bool_terms)
            abs_ranges = []
            deltas = []
        else:
            equals = dedup(identity + [term for term in var_terms
                                       if r2_term_text(term) != "pre"])
            abs_ranges = [{"id": f"a{i}", "lo": term, "hi": term}
                          for i, term in enumerate(var_numeric_atoms)
                          if r2_term_text(term) != "pre"]
            if zero is not None:
                type_max = None if width is None else (1 << (8 * width)) - 1
                abs_ranges += [
                    {"id": f"ac{i}", "lo": zero, "hi": term}
                    for i, term in enumerate(var_terms)
                    if r2_term_text(term) != "0"]
                if type_max is not None:
                    abs_ranges = [
                        item for item in abs_ranges
                        if not (item["lo"] == zero
                                and item["hi"].get("kind") == "literal"
                                and int(item["hi"]["value"]) == type_max)]
            deltas = []
            if var != RETURN_VAR and var in direction:
                deltas = [{"id": f"d{i}", "dir": direction[var],
                           "lo": term, "hi": term}
                          for i, term in enumerate(terms)]
                if zero is not None:
                    deltas += [
                        {"id": f"dc{i}", "dir": direction[var],
                         "lo": zero, "hi": term}
                        for i, term in enumerate(terms)
                        if r2_term_text(term) != "0"]
        entry = {
            "name": var,
            "equals": [{"id": f"e{i}", "term": term}
                       for i, term in enumerate(equals)],
            "abs": abs_ranges,
            "deltas": deltas,
        }
        entries.append(entry)
    requested = sum(len(entry[kind]) for entry in entries
                    for kind in ("equals", "abs", "deltas"))
    if requested > candidate_budget:
        variable_queues = []
        for vi, entry in enumerate(entries):
            kind_queues = [(kind, list(entry[kind]))
                           for kind in ("equals", "abs", "deltas")
                           if entry[kind]]
            sequence = []
            while kind_queues:
                next_kind_queues = []
                for kind, queue in kind_queues:
                    sequence.append((kind, queue.pop(0)))
                    if queue:
                        next_kind_queues.append((kind, queue))
                kind_queues = next_kind_queues
            variable_queues.append((vi, sequence))
            for kind in ("equals", "abs", "deltas"):
                entry[kind] = []
        kept = 0
        while variable_queues and kept < candidate_budget:
            next_variable_queues = []
            for vi, queue in variable_queues:
                if kept >= candidate_budget:
                    next_variable_queues.append((vi, queue))
                    continue
                kind, candidate = queue.pop(0)
                entries[vi][kind].append(candidate)
                kept += 1
                if queue:
                    next_variable_queues.append((vi, queue))
            variable_queues = next_variable_queues
        starved = sum(not any(entry[kind]
                              for kind in ("equals", "abs", "deltas"))
                      for entry in entries)
        log(f"[put]   typed R2 candidate budget kept {kept} and named "
            f"{requested - kept} generated candidate(s) as NOT ASKED")
        if starved:
            log(f"[put]   typed R2 candidate budget is smaller than the "
                f"variable set: {starved} variable(s) received no candidate "
                "and are explicitly NOT ASKED")
    entries = [entry for entry in entries
               if any(entry[kind] for kind in ("equals", "abs", "deltas"))]
    candidate_count = sum(len(entry[kind]) for entry in entries
                          for kind in ("equals", "abs", "deltas"))
    log(f"[put]   typed R2 proposed ONE query with {candidate_count} "
        f"candidate(s), depth={depth}, over {len(entries)} variable(s)")
    return [{"param": "batch", "stage": 1, "kind": "typed",
             "depth": depth, "candidate_count": candidate_count,
             "vars": entries}]


def _r2_entry_count(entries):
    return sum(len(entry.get(kind, ())) for entry in entries or []
               for kind in ("equals", "abs", "deltas"))


def _r2_refresh_candidate_count(spec):
    spec["candidate_count"] = _r2_entry_count(spec.get("vars", ()))


def _r2_trim_mechanical_tail(spec, candidate_budget, log):
    """Keep source candidates in front and trim generated suffixes if needed."""
    if candidate_budget is None:
        return 0
    over = _r2_entry_count(spec.get("vars", ())) - candidate_budget
    if over <= 0:
        return 0
    dropped = 0
    for entry in reversed(spec.get("vars", ())):
        for kind in ("deltas", "abs", "equals"):
            queue = entry.get(kind, [])
            while queue and dropped < over:
                candidate = queue[-1]
                if str(candidate.get("id", "")).startswith("src"):
                    break
                queue.pop()
                dropped += 1
            if dropped >= over:
                break
        if dropped >= over:
            break
    spec["vars"] = [
        entry for entry in spec.get("vars", ())
        if any(entry.get(kind) for kind in ("equals", "abs", "deltas"))
    ]
    _r2_refresh_candidate_count(spec)
    if dropped:
        log(f"[put]   typed R2 candidate budget made room for source "
            f"assignment candidate(s) by naming {dropped} mechanical "
            "candidate(s) as NOT ASKED")
    return dropped


def merge_source_r2_specs(source_specs, typed_specs, candidate_budget=None,
                          log=print):
    """Merge source-prioritized candidates into one typed R2 verifier query."""
    source = json.loads(json.dumps(source_specs or []))
    typed = json.loads(json.dumps(typed_specs or []))
    if not source:
        return typed
    if not typed:
        return source

    target = typed[0]
    entries = target.setdefault("vars", [])
    by_name = {entry.get("name"): entry for entry in entries}
    new_entries = []
    inserted = 0

    for spec in source:
        for entry in spec.get("vars", []):
            name = entry.get("name")
            if not name:
                continue
            dest = by_name.get(name)
            if dest is None:
                dest = {"name": name, "equals": [], "abs": [], "deltas": []}
                by_name[name] = dest
                new_entries.append(dest)
            for kind in ("equals", "abs", "deltas"):
                dest.setdefault(kind, [])

            existing_equals = {r2_term_text(item["term"])
                               for item in dest.get("equals", [])}
            prepend_equals = []
            for candidate in entry.get("equals", []):
                text = r2_term_text(candidate["term"])
                if text in existing_equals:
                    continue
                existing_equals.add(text)
                prepend_equals.append(candidate)
                inserted += 1
            if prepend_equals:
                dest["equals"] = prepend_equals + dest.get("equals", [])

            existing_abs = {
                (r2_term_text(item["lo"]), r2_term_text(item["hi"]))
                for item in dest.get("abs", [])
            }
            prepend_abs = []
            for candidate in entry.get("abs", []):
                key = (r2_term_text(candidate["lo"]),
                       r2_term_text(candidate["hi"]))
                if key in existing_abs:
                    continue
                existing_abs.add(key)
                prepend_abs.append(candidate)
                inserted += 1
            if prepend_abs:
                dest["abs"] = prepend_abs + dest.get("abs", [])

            existing_deltas = {
                (item.get("dir"), r2_term_text(item["lo"]),
                 r2_term_text(item["hi"]))
                for item in dest.get("deltas", [])
            }
            prepend_deltas = []
            for candidate in entry.get("deltas", []):
                key = (candidate.get("dir"), r2_term_text(candidate["lo"]),
                       r2_term_text(candidate["hi"]))
                if key in existing_deltas:
                    continue
                existing_deltas.add(key)
                prepend_deltas.append(candidate)
                inserted += 1
            if prepend_deltas:
                dest["deltas"] = prepend_deltas + dest.get("deltas", [])

    if new_entries:
        target["vars"] = new_entries + entries
    if inserted:
        target["kind"] = "typed+source-assign"
        _r2_refresh_candidate_count(target)
        _r2_trim_mechanical_tail(target, candidate_budget, log)
        log(f"[put]   typed R2 source assignment merge kept {inserted} "
            "source candidate(s) in the same verifier query as the "
            "mechanical batch")
    return typed


def r2_terms_from_specs(specs):
    """Canonical term lookup consumed by the Foundry renderer."""
    out = {}

    def remember(term):
        text = r2_term_text(term)
        out[text] = term
        if term.get("kind") == "literal":
            if str(term.get("value")) == "0":
                out.setdefault("false", term)
            elif str(term.get("value")) == "1":
                out.setdefault("true", term)

    for spec in specs or []:
        for var in spec.get("vars", []):
            for item in var.get("equals", []):
                remember(item["term"])
            for key in ("abs", "deltas"):
                for item in var.get(key, []):
                    remember(item["lo"])
                    remember(item["hi"])
    return out


FORGE_RESULT_RE = re.compile(r"^\s*\[(PASS|FAIL[^\]]*)\]\s+(\w+)\s*\(")


def fuzz_prefilter_verdicts(labels, forge_out):
    """{rung label -> REFUTED | NOT-REFUTED | NOT-RUN} from one forge run.

    ---- WHY A FUZZ PASS BEFORE THE VERIFIER -------------------------------

    Measured on this corpus: of 1470 complementary ladder pairs, 1456 have one
    side REFUTED -- i.e. about HALF of every R1 rung the ladder emits is
    refutable. A refutation is a concrete counterexample, and a fuzzer finds
    those in milliseconds (a 256-draw forge run is 3-44 ms) where a ladder
    query costs 18-30 s. Asking forge first and the solver only about the
    survivors is therefore sound in ONE direction and cheap.

    ⛔ SOUND IN ONE DIRECTION ONLY. `forge found a failing draw` IS a
    refutation and may skip the solver. `forge found none in 256 draws` is NOT
    a proof and may never skip it. This function therefore never returns
    "HOLDS"; the strongest thing it says is NOT-REFUTED, which still costs a
    query.

    ⛔ AND ABSENCE IS ITS OWN ANSWER. A probe whose test name is not in the
    forge output DID NOT RUN -- the file failed to compile, the name was
    misspelled, the filter excluded it. Folding that into NOT-REFUTED would be
    the cheapest possible way to lose an oracle: every rung would "survive"
    and the prefilter would report a perfect pass rate while measuring
    nothing. This project has already shipped one always-true reader; this is
    where the next one would live.
    """
    seen = {}
    for ln in (forge_out or "").splitlines():
        m = FORGE_RESULT_RE.match(ln)
        if m:
            seen[m.group(2)] = m.group(1)
    out = {}
    for name, label in labels.items():
        r = seen.get(name)
        if r is None:
            out[label] = "NOT-RUN"
        elif r == "PASS":
            out[label] = "NOT-REFUTED"
        else:
            out[label] = "REFUTED"
    return out


def forge_json_test_results(forge_out):
    """Test-name keyed Forge JSON results, or an empty map on malformed data."""
    try:
        suites = json.loads(forge_out or "")
    except (TypeError, ValueError):
        suites = {}
    seen = {}
    if isinstance(suites, dict):
        for suite in suites.values():
            if not isinstance(suite, dict):
                continue
            for name, result in (suite.get("test_results") or {}).items():
                if isinstance(result, dict):
                    seen[name.split("(", 1)[0]] = result
    return seen


def fuzz_prefilter_json_verdicts(labels, forge_out):
    """Classify Forge JSON without mistaking an unrelated failure for a CE.

    ``labels`` maps each expected test function to its full unique assertion
    label (random marker plus variable and candidate text).
    A candidate is refuted only when Forge reports that test as a failure and
    the failure reason contains that marker.  A pass is still not a proof, and
    every absent, malformed, or unrelated failure remains NOT-RUN so it cannot
    remove a verifier candidate.
    """
    seen = forge_json_test_results(forge_out)
    out = {}
    for name, label in labels.items():
        result = seen.get(name)
        if result is None:
            out[name] = "NOT-RUN"
        elif result.get("status") == "Success":
            out[name] = "NOT-REFUTED"
        elif (result.get("status") == "Failure"
              and str(result.get("reason") or "").startswith(label + ":")):
            out[name] = "REFUTED"
        else:
            out[name] = "NOT-RUN"
    return out


def r2_candidates(specs):
    """Flatten specs fairly: one candidate per variable on each global lap."""
    variable_queues = []
    for si, spec in enumerate(specs or []):
        for vi, var in enumerate(spec.get("vars", [])):
            owner = var["name"]
            kind_queues = []
            for kind, state_prefix in (("equals", "post == "),
                                       ("abs", "post in ")):
                queue = []
                prefix = (state_prefix.replace("post", "return", 1)
                          if owner == RETURN_VAR else state_prefix)
                for candidate in var.get(kind, []):
                    if kind == "equals":
                        text_ = prefix + r2_term_text(candidate["term"])
                    else:
                        text_ = (prefix + "[" + r2_term_text(candidate["lo"])
                                 + ", " + r2_term_text(candidate["hi"]) + "]")
                    queue.append({
                        "key": f"s{si}:v{vi}:{kind}:{candidate['id']}",
                        "var": owner,
                        "text": text_,
                    })
                if queue:
                    kind_queues.append(queue)
            delta_queue = []
            for candidate in var.get("deltas", []):
                inc = candidate["dir"] == "inc"
                text_ = (("post - pre" if inc else "pre - post") + " in ["
                         + r2_term_text(candidate["lo"]) + ", "
                         + r2_term_text(candidate["hi"]) + "] with "
                         + ("post >= pre" if inc else "pre >= post"))
                delta_queue.append({
                    "key": f"s{si}:v{vi}:deltas:{candidate['id']}",
                    "var": owner,
                    "text": text_,
                })
            if delta_queue:
                kind_queues.append(delta_queue)
            queue = []
            while kind_queues:
                next_kind_queues = []
                for kind_queue in kind_queues:
                    queue.append(kind_queue.pop(0))
                    if kind_queue:
                        next_kind_queues.append(kind_queue)
                kind_queues = next_kind_queues
            if queue:
                variable_queues.append(queue)
    out = []
    while variable_queues:
        next_variable_queues = []
        for queue in variable_queues:
            out.append(queue.pop(0))
            if queue:
                next_variable_queues.append(queue)
        variable_queues = next_variable_queues
    return out


def skipped_forge_r2_evidence(specs, candidate_budget, reason, fuzz_runs):
    """Complete accounting for a Forge prefilter that issued no process."""
    candidates = r2_candidates(specs)
    return {
        "requested": len(candidates),
        "selected": min(len(candidates), candidate_budget),
        "candidate_budget": candidate_budget,
        "rendered": 0,
        "ran": 0,
        "refuted": 0,
        "not_refuted": 0,
        "not_run": len(candidates),
        "timed_out": False,
        "returncode": None,
        "fuzz_runs": fuzz_runs,
        "command": [],
        "reason": reason,
        "candidates": [{
            "key": candidate["key"],
            "var": candidate["var"],
            "text": candidate["text"],
            "verdict": "NOT-RUN",
            "reason": reason,
        } for candidate in candidates],
    }


def filter_r2_specs(specs, verdicts):
    """Return specs without candidates that have a concrete Forge CE."""
    filtered = json.loads(json.dumps(specs or []))
    for si, spec in enumerate(filtered):
        kept = 0
        for vi, var in enumerate(spec.get("vars", [])):
            for kind in ("equals", "abs", "deltas"):
                values = var.get(kind, [])
                var[kind] = [
                    candidate for candidate in values
                    if verdicts.get(
                        f"s{si}:v{vi}:{kind}:{candidate['id']}") != "REFUTED"
                ]
                kept += len(var[kind])
        spec["candidate_count"] = kept
        spec["vars"] = [
            var for var in spec.get("vars", [])
            if any(var.get(kind) for kind in ("equals", "abs", "deltas"))
        ]
    return [spec for spec in filtered if spec.get("vars")]


def run_r2_passes(specs, base_spec, write_spec, runner, parse, log=print):
    """Run one extra ladder query per proposed R2 spec; return the NEW rows.

    `write_spec(path_suffix, spec_dict) -> path`, `runner(spec_path) -> text`
    and `parse(text) -> (rows, summary, refusal, blocker)` are injected so the
    DECISION LOGIC is testable without esbmc: whether a pass runs at all, what
    goes into the spec, and -- the part that matters -- how a pass that comes
    back empty is treated.

    ⛔ AN EMPTY PASS IS REPORTED, NEVER ABSORBED. A query that produced no R2
    row means the request did not reach the ladder (a name the ladder does not
    carry, a refusal, a dead run). Merging silently would leave the PUT looking
    exactly like one where R2 was never asked for -- the whole reason R2 went
    unnoticed for this long.

    ⛔ AND IT NEVER OVERWRITES A ROW THE FIRST PASS ALREADY DECIDED. Only rows
    whose (var, text) is new are returned; a second pass disagreeing with the
    first about an R1 rung is a fact worth seeing, not a silent update.
    """
    seen = set()
    out = []
    # (var -> verdict) for the EXACT delta asked in stage 1. Stage 2's cap is
    # narrowed against this, so a variable whose exact bound already HOLDS does
    # not spend a whole query buying a strictly weaker rung, and one that came
    # back without a verdict does not spend it buying a second no-verdict.
    exact_delta = {}
    for i, s in enumerate(specs or []):
        stage, kind = s.get("stage", 1), s.get("kind", "num")
        entries = s["vars"]
        if stage == 2:
            entries = [e for e in entries
                       if exact_delta.get(e["name"]) == "REFUTED"]
            if not entries:
                log(f"[put]   R2 pass {i + 1}/{len(specs)} NOT RUN (cap on "
                    f"`{s['param']}`): stage 1 refuted the exact delta on no "
                    f"variable, so a cap would be asked about nothing")
                continue
        if not entries:
            log(f"[put]   R2 pass {i + 1}/{len(specs)} NOT RUN: every "
                "candidate in this batch was refuted by Forge")
            continue
        spec = dict(base_spec)
        spec["vars"] = entries
        path = write_spec(f".r2_{s['param']}_s{stage}", spec)
        log(f"[put]   R2 pass {i + 1}/{len(specs)}: stage {stage} {kind} "
            f"bound by `{s['param']}` on {len(entries)} variable(s)")
        text = runner(path)
        rows, _summary, refusal, _blocker = parse(text)
        # ⛔ `post in [` IS IN THIS LIST ON PURPOSE. The filter used to accept
        # the two delta shapes only, so an absolute row -- the entire point of
        # asking for one -- was parsed, matched nothing, and was dropped as
        # though the pass had come back empty. A request whose answer no reader
        # accepts is indistinguishable from a request never sent.
        def is_r2_row(text):
            if text.startswith(("post in [", "post - pre in [",
                                "pre - post in [", "return in [")):
                return True
            if text.startswith("post == ") and text != "post == pre":
                return True
            if text.startswith("return == "):
                return text not in ("return == 0", "return == false",
                                    "return == true")
            return False

        fresh = [(v, t, d) for v, t, d in rows
                 if is_r2_row(t) and (v, t) not in seen]
        for v, t, d in fresh:
            seen.add((v, t))
            if stage == 1 and t.startswith(("post - pre in [", "pre - post in [")):
                exact_delta[v] = d
        if not fresh:
            log(f"[put]     NO R2 ROW came back from this pass"
                + (f" (ladder refusal: {refusal})" if refusal else
                   " and the ladder reported no refusal, so the request "
                   "reached it and produced nothing -- that is a defect, not "
                   "a measurement"))
            continue
        for v, t, d in fresh:
            log(f"[put]     {v}: {t}  {d}")
        out += fresh
    return out


def maybe_run_r2_passes(specs, base_spec, write_spec, runner, parse,
                        rollback_here=False, revert_here=False, notes=None,
                        log=print):
    """Run R2 unless this path's post-state is hidden by a reverting exit.

    R2 rows are post-state or delta claims. On a reverting path they are proved
    about an intermediate state that no Foundry test can observe, and the
    emitter drops them before writing assertions. Skipping here saves the ESBMC
    query without weakening the emitted test.
    """
    if not (rollback_here or revert_here):
        return run_r2_passes(specs, base_spec, write_spec, runner, parse,
                             log=log)
    n_candidates = len(r2_candidates(specs))
    reason = ("this path rolls back" if rollback_here
              else "Stage-1 says this path exits through a revert")
    log(f"[put]   R2 ESBMC pass NOT RUN: {reason}, so its layer-2/3 "
        f"post-state is unobservable; {n_candidates} candidate(s) would be "
        "dropped before emit")
    if notes is not None:
        notes.append("R2 ESBMC skipped: reverting path has no observable "
                     "post-state")
    return []


def _norm_ty(t):
    """A Solidity type spelled the one way, for comparing a parameter's type
    against a mapping's key type. Module level, not nested in `main()`, so the
    proposer below is reachable from a test."""
    t = (t or "").strip()
    for suf in (" payable", " memory", " calldata", " storage"):
        t = t.replace(suf, "")
    return {"uint": "uint256", "int": "int256"}.get(t, t)


SLOT_VAR_BUDGET = 24


def propose_slot_vars(maps, params, budget=SLOT_VAR_BUDGET, log=print,
                      dependencies=None):
    """The mapping slot names to ASK THE LADDER ABOUT, one key per level.

    Lifted out of `main()` because it was inline there, which is precisely why
    the defect below survived: no test could reach it.

    ---- THE DEFECT THIS FUNCTION EXISTS TO PIN -------------------------------

    `maps[m]`'s key-type field is a STRING for a one-level store and a TUPLE of
    key types for a nested one. The inline version read it as a string
    unconditionally and died with `'tuple' object has no attribute 'strip'` the
    first time a nested mapping appeared -- farming's `_allowances`
    (`address => address => uint256`) and aqua's four-level store. Three PUTs
    that had been emitting for weeks stopped emitting.

    ⛔ THE ROOT CAUSE IS NOT THE CRASH. The key-type field gained a second
    shape and only TWO of its THREE readers were updated (`m_pin` and `m_slot`
    were; this one was not). Nothing pointed here because every one-level
    corpus row kept working, so the suite stayed green while the corpus broke.

    A nested name needs one parameter PER LEVEL, so the candidates are the
    cross product of the per-level type matches -- and one parameter may
    legitimately serve two levels: `bal[u][u]` is a real slot.

    ---- WHY `msg.sender` IS A CANDIDATE AND WHY IT GOES FIRST ----------------

    An address-keyed mapping in a real contract is overwhelmingly keyed by the
    CALLER, not by an argument: `_balances[msg.sender]`, `allowance[msg.sender]`
    and aqua's `_balances[msg.sender][app][...]` are all that shape. A proposer
    that draws keys only from the parameter list can never name any of them, so
    the unit's own storage effect is invisible to the ladder and the PUT comes
    back with an empty oracle -- which is exactly what the corpus was doing.

    ⛔ IT IS NOT A GUESS ON THE VERIFIER'S SIDE. `--path-cov-assert`'s key
    resolver already accepts it; its refusal text says so in as many words
    ("Name a parameter, an environment value as `msg.sender` / `msg.value`, or
    a state variable at entry as `state.<field>`"). And it is not a guess on the
    EMITTER's side either, but only conditionally: `slot_key_expr` still refuses
    the key unless `establish_env_sender` has rewritten the governing prank, so
    a proposal that survives the ladder and then cannot be pointed at a definite
    address is dropped with a reason rather than hashed at whatever the test's
    own sender happens to be.

    FIRST at each level, ahead of the sorted parameters, because the budget
    keeps a PREFIX. Sorting it in by name would put `msg.sender` behind every
    parameter spelled with an earlier letter, and on the four-level store where
    it matters most the cap would drop every candidate containing it -- the
    truncation would silently undo the change.
    """
    out = []
    if dependencies is None:
        map_names = sorted(maps)
    else:
        rank = {name: pos for pos, name in enumerate(dependencies)}
        map_names = sorted(
            (name for name, spec in maps.items() if spec[4] in rank),
            key=lambda name: (rank[maps[name][4]], name))
        excluded = sorted({spec[4] for spec in maps.values()
                           if spec[4] not in rank})
        if excluded:
            log(f"[put]   mapping candidates excluded by "
                f"{SLOT_DEPENDENCY_POLICY}: {', '.join(excluded)}")
    for mname in map_names:
        # A struct-valued mapping contributes one row PER FIELD, so the name
        # the ladder is asked about is `<map>[<param>].<field>` while the row
        # is keyed `<map>.<field>`. A scalar-valued one has member None and the
        # name is unchanged, byte for byte.
        _s, ktype, _n, _o, base, member = maps[mname]
        if not map_esbmc_certifiable(base):
            log(f"[put]   mapping candidate {base} skipped: ESBMC's "
                "--path-cov-assert ladder currently resolves only "
                "contract-scope mapping stores, not struct-contained "
                "mapping_t fields")
            continue
        ktypes = list(ktype) if isinstance(ktype, tuple) else [ktype]
        per_level = []
        for kt in ktypes:
            # SORTED HERE rather than by sorting the product afterwards. The
            # two were the same thing while every level held only parameters;
            # they stop being the same once a level is deliberately NOT in name
            # order, and the product of sorted lists is already in lexicographic
            # tuple order, so the old output is reproduced byte for byte.
            cands = sorted(pn for pn, pt in (params or [])
                           if pn and _norm_ty(pt) == _norm_ty(kt))
            if _norm_ty(kt) == "address":
                cands = ["msg.sender"] + cands
            per_level.append(cands)
        # EVERY level needs a key. A four-level store with a match on three of
        # them yields NO name -- a partially-keyed name would address a word
        # nothing wrote, which the depth check refuses downstream anyway.
        if not all(per_level):
            continue
        combos = list(itertools.product(*per_level))
        # NO SILENT CAP. Four levels against four address parameters is 256
        # candidate names, each costing a ladder query. What the budget drops
        # is printed, because a truncated candidate set that says nothing reads
        # exactly like "the tool found only these".
        if len(combos) > budget:
            log(f"[put]   {base}: {len(ktypes)} level(s) x per-level matches "
                f"{[len(x) for x in per_level]} = {len(combos)} candidate slot "
                f"name(s); keeping the first {budget} in sorted order, "
                f"DROPPING {len(combos) - budget}. A rung this cap removed is "
                f"not a rung that was refused.")
            combos = combos[:budget]
        for keys in combos:
            out.append(base + "".join(f"[{k}]" for k in keys)
                       + (f".{member}" if member else ""))
    return out


# A rung that HOLDS makes weaker rungs on the SAME variable follow for free.
# `post > pre` gives `post >= pre` and `post != pre` with no further evidence,
# and the delta rung gives its own direction conjunct back.
IMPLIED_BY = {
    "post > pre": ("post >= pre", "post != pre"),
    "post < pre": ("post <= pre", "post != pre"),
    "post == pre": ("post >= pre", "post <= pre"),
}


def antichain(rows, revert_tolerant=False):
    """(kept, implied) -- drop every HOLDS rung another HOLDS rung entails.

    ⛔ THIS REMOVES NO ORACLE. `assertGe(post, pre)` beside `assertGt(post,
    pre)` cannot fail on any execution the second one passes, so the pair
    detects exactly what the strict one detects alone. What it does change is
    the NUMBER, and the number is read as strength: a PUT reporting six
    assertions of which three are entailed by the other three is claiming an
    oracle twice as sharp as the one it has.

    ⛔ AND IT IS `implied`, NOT `skipped`. The two are different facts with
    different repairs -- a skipped rung is oracle that was LOST and wants
    fixing, an implied one is oracle that is still fully present in a stronger
    form. Filing the second under the first is how a healthy pipeline comes to
    look broken; filing the first under the second is how a broken one comes
    to look healthy.

    Only rows whose verdict is HOLDS take part: a REFUTED `post > pre` entails
    nothing at all, and using it to drop `post >= pre` would delete a rung
    that HOLDS on the strength of one that does not.

    ⛔ ONLY THE ORDERING FAMILY DOMINATES, AND THAT IS A SAFETY RULE, NOT A
    SIMPLIFICATION. This filter runs BEFORE rendering, so a rung it removes is
    gone whether or not the rung that dominated it turns out to be
    renderable. The six ordering rungs all render through the same branch and
    the same slot lookup, so within that family they succeed or fail together
    and nothing can be lost. A DELTA rung is different: its endpoints can be
    unspellable (`bound_term` returns None and the whole rung is dropped),
    and letting it dominate `post >= pre` would trade a rung that renders for
    one that does not -- losing the oracle outright in exactly the case the
    domination was supposed to be free. The redundancy it leaves behind is one
    duplicated `assertGe` line, which is the cheaper mistake.

    ⛔ AND DOMINATION MAY NOT CROSS THE GUARD BOUNDARY. On a revert-tolerant
    call the CHANGE rungs are emitted inside `if (_put_ok)` and the rest are
    emitted unconditionally, so they are not assertions of equal standing:

        assertGe(post, pre, ...)                 <- always runs
        if (_put_ok) { assertGt(post, pre, ...) } <- skipped on a revert

    `post > pre` entails `post >= pre` as a proposition, but the ASSERTION it
    renders is skipped exactly when the call reverts -- and a revert is
    precisely when the unconditional one still has something to say. Dropping
    the outer rung because the inner one dominates it leaves that execution
    asserting NOTHING, which is oracle destroyed by a filter whose whole
    premise is that it destroys none. MEASURED on the shape this is written
    against: farming setDistributor enc=13 emits `_distributor: post >= pre`
    unconditionally and `post != pre` / `post > pre` under the guard.

    Under a BARE call every rung is unconditional and the boundary does not
    exist, so `revert_tolerant=False` keeps the full table.
    """
    holds = {}
    for var, text, verdict in rows:
        if verdict == "HOLDS":
            holds.setdefault(var, set()).add(text)
    dominated = {}
    for var, texts in holds.items():
        d = set()
        for text in texts:
            if text.startswith("post == "):
                term = text[len("post == "):]
                exact = f"post in [{term}, {term}]"
                if exact in texts:
                    d.add(exact)
        for t in texts:
            for weaker in IMPLIED_BY.get(t, ()):
                if weaker not in texts:
                    continue
                if (revert_tolerant
                        and rung_asserts_a_change(t)
                        and not rung_asserts_a_change(weaker)):
                    continue
                d.add(weaker)
        dominated[var] = d
    kept, implied = [], []
    for row in rows:
        var, text, verdict = row
        if verdict == "HOLDS" and text in dominated.get(var, ()):
            implied.append(row)
        else:
            kept.append(row)
    return kept, implied


def render_r2_term(term, pre, idents):
    """Render a verifier-certified term into Solidity, or return None."""
    kind = term.get("kind")
    if kind == "pre":
        return pre
    if kind == "coord":
        return (idents or {}).get(term.get("name"))
    if kind == "literal":
        value = str(term.get("value", ""))
        return value if value.isdigit() else None
    if kind == "op":
        lhs = render_r2_term(term.get("lhs", {}), pre, idents)
        rhs = render_r2_term(term.get("rhs", {}), pre, idents)
        op = {"add": "+", "sub": "-", "mul": "*", "div": "/"}.get(
            term.get("op"))
        if lhs is None or rhs is None or op is None:
            return None
        return f"({lhs} {op} {rhs})"
    return None


def r2_term_coord_names(term):
    """Coordinate names mentioned by a structured R2 term."""
    if not isinstance(term, dict):
        return []
    kind = term.get("kind")
    if kind == "coord":
        name = term.get("name")
        return [name] if name else []
    if kind == "op":
        return (r2_term_coord_names(term.get("lhs")) +
                r2_term_coord_names(term.get("rhs")))
    return []


def return_rung_term_spellings(text):
    """Structured endpoint spellings a return rung may contain."""
    if text.startswith("return == "):
        return [text[len("return == "):]]
    m = re.match(r"^return in \[(.*), (.*)\]$", text)
    if m:
        return [m.group(1), m.group(2)]
    return []


def post_rung_term_spellings(text):
    """Structured endpoint spellings a post-state rung may contain."""
    if text.startswith("post == ") and text != "post == pre":
        return [text[len("post == "):]]
    m = re.match(r"^post in \[(.*), (.*)\]$", text)
    if m:
        return [m.group(1), m.group(2)]
    m = re.match(r"^(?:post - pre|pre - post) in \[(.*), (.*)\] with "
                 r"(?:post >= pre|pre >= post)$", text)
    if m:
        return [m.group(1), m.group(2)]
    return []


def rung_assertions(text, pre, post, label, idents=None, idents_abs=None,
                    r2_terms=None):
    """Forge assertion lines for one rung, or None if it cannot be spelled.

    `idents` are the endpoints a DELTA bound may name -- arithmetic
    coordinates only. `idents_abs` are the ones an ABSOLUTE bound may name,
    which additionally includes the address coordinates: `post == the address
    you passed in` is the property of a setter, while `post - pre == an
    address` is meaningless. Defaults to `idents` so every existing caller
    keeps its exact behaviour.
    """
    if idents_abs is None:
        idents_abs = idents
    lit = json.dumps(label)

    def structured(spelling, coord_table):
        term = (r2_terms or {}).get(spelling)
        return None if term is None else render_r2_term(
            term, pre, coord_table)

    if text.startswith("post == ") and text != "post == pre":
        expr = structured(text[len("post == "):], idents_abs)
        if expr is not None:
            return [f"    assertEq({post}, {expr}, {lit});"]
    m = re.match(r"^post in \[(.*), (.*)\]$", text)
    if m:
        lo = structured(m.group(1), idents_abs)
        hi = structured(m.group(2), idents_abs)
        if lo is not None and hi is not None:
            return [f"    assertGe({post}, {lo}, {lit});",
                    f"    assertLe({post}, {hi}, {lit});"]
    m = re.match(r"^(post - pre|pre - post) in \[(.*), (.*)\] with "
                 r"(post >= pre|pre >= post)$", text)
    if m:
        lo = structured(m.group(2), idents)
        hi = structured(m.group(3), idents)
        if lo is not None and hi is not None:
            lhs, rhs = ((post, pre) if m.group(1) == "post - pre"
                        else (pre, post))
            return [f"    assertGe({lhs}, {rhs}, {lit});",
                    f"    assertGe({lhs} - {rhs}, {lo}, {lit});",
                    f"    assertLe({lhs} - {rhs}, {hi}, {lit});"]
    m = re.match(r"^post (==|!=|>=|<=|>|<) pre$", text)
    if m:
        op = m.group(1)
        fn = {"==": "assertEq", ">=": "assertGe", "<=": "assertLe",
              ">": "assertGt", "<": "assertLt"}.get(op)
        if fn:
            return [f"    {fn}({post}, {pre}, {lit});"]
        return [f"    assertTrue({post} != {pre}, {lit});"]

    def ends(m):
        """(lo, hi) as Solidity text, or None if either endpoint is unspellable."""
        lo = bound_term(m.group(1), idents)
        hi = bound_term(m.group(2), idents)
        return None if lo is None or hi is None else (lo, hi)

    m = re.match(r"^post in \[%s, %s\]$" % (_BND, _BND), text)
    if m:
        # THE ABSOLUTE BOUND, and the only shape allowed to name an address.
        lo = bound_term(m.group(1), idents_abs)
        hi = bound_term(m.group(2), idents_abs)
        e = None if lo is None or hi is None else (lo, hi)
        if e is None:
            return None
        return [f"    assertGe({post}, {e[0]}, {lit});",
                f"    assertLe({post}, {e[1]}, {lit});"]
    m = re.match(r"^post - pre in \[%s, %s\] with post >= pre$" % (_BND, _BND),
                 text)
    if m:
        e = ends(m)
        if e is None:
            return None
        return [f"    assertGe({post}, {pre}, {lit});",
                f"    assertGe({post} - {pre}, {e[0]}, {lit});",
                f"    assertLe({post} - {pre}, {e[1]}, {lit});"]
    m = re.match(r"^pre - post in \[%s, %s\] with pre >= post$" % (_BND, _BND),
                 text)
    if m:
        e = ends(m)
        if e is None:
            return None
        return [f"    assertGe({pre}, {post}, {lit});",
                f"    assertGe({pre} - {post}, {e[0]}, {lit});",
                f"    assertLe({pre} - {post}, {e[1]}, {lit});"]
    return None


# ---------------------------------------------------------------------------
# The unit's OWN RETURN VALUE as an oracle
# ---------------------------------------------------------------------------
#
# WHY IT COMES THROUGH THE LADDER AND NOT OUT OF THE REPORT. The counterexample
# payload carries `return_value`, and asserting THAT would be wrong: it is the
# value at ONE point, while this PUT `bound()`s its parameters across the whole
# region and runs 256 of them. On aqua the PUT fuzzes `maker`/`app`/`token`
# across the entire address space and the payload names one triple, so a point
# value asserted here is RED. Only a rung the verifier judged HOLDS over the
# ASSUMED region may become an assertion -- the same contract the state rungs
# satisfy, which is why the return value is a rung (`--path-cov-assert`,
# `<rung>_return`) rather than a field copied across.
#
# THE `retlive` GATE IS NOT OPTIONAL. Every return rung carries `|| !retset` so
# that it reads "IF a value was returned, THEN ...", which means the whole
# family can HOLD for want of a returned value. `retlive` asserts `!retset` and
# is REFUTED exactly when some execution of this path does return one. Anything
# other than REFUTED there and the other rungs say nothing -- so they are
# dropped, by name, rather than rendered.
def return_kind(sol_type):
    """(declared type for the local, uint256-cast template) or None.

    A WHITELIST, for the same reason `coord_expressible` is one on the tool
    side: the assertion is built by casting the bound value to uint256, and a
    type this does not know either fails to compile or -- worse -- compiles
    with a different meaning. `bool` has no cast at all and is asserted with
    assertTrue/assertFalse; that is why the second element is None for it.
    """
    t = (sol_type or "").strip()
    if t == "bool":
        return ("bool", None)
    m = re.match(r"^uint(\d+)?$", t)
    if m:
        return (f"uint{m.group(1) or 256}", "uint256({v})")
    m = re.match(r"^bytes(\d+)$", t)
    if m:
        return (t, "uint256({v})")
    if t in ("address", "address payable"):
        return (t, "uint256(uint160({v}))")
    return None


def return_rung_assertions(text, kind, var, label, idents_abs=None,
                           r2_terms=None):
    """forge-std lines for one HOLDS return rung, or None if not renderable.

    A text whose family does not match the declared type is NOT rendered.
    That case means the tool typed the ghost from the goto model and this
    script typed the local from the AST and the two disagree; rendering
    anyway would assert something neither of them said.
    """
    decl_t, tou = kind
    lit = json.dumps(label)

    def structured(spelling):
        term = (r2_terms or {}).get(spelling)
        return None if term is None else render_r2_term(term, None, idents_abs)

    if decl_t == "bool":
        if text == "return == false":
            return [f"    assertFalse({var}, {lit});"]
        if text == "return == true":
            return [f"    assertTrue({var}, {lit});"]
        if text.startswith("return == "):
            expr = structured(text[len("return == "):])
            if expr is not None:
                if expr == "0":
                    return [f"    assertFalse({var}, {lit});"]
                if expr == "1":
                    return [f"    assertTrue({var}, {lit});"]
                return [f"    assertEq({var}, {expr}, {lit});"]
        return None
    v = tou.format(v=var)
    if text.startswith("return == ") and text != "return == 0":
        expr = structured(text[len("return == "):])
        if expr is not None:
            return [f"    assertEq({v}, {expr}, {lit});"]
    if text == "return == 0":
        return [f"    assertEq({v}, 0, {lit});"]
    if text == "return != 0":
        return [f"    assertTrue({v} != 0, {lit});"]
    m = re.match(r"^return in \[(.*), (.*)\]$", text)
    if m:
        lo = structured(m.group(1))
        hi = structured(m.group(2))
        if lo is not None and hi is not None:
            return [f"    assertGe({v}, {lo}, {lit});",
                    f"    assertLe({v}, {hi}, {lit});"]
    m = re.match(r"^return in \[(\d+), (\d+)\]$", text)
    if m:
        return [f"    assertGe({v}, {m.group(1)}, {lit});",
                f"    assertLe({v}, {m.group(2)}, {lit});"]
    return None


def bind_return_lhs(call_line, unit, lhs):
    """(rewritten call line, None) or (None, reason it may not be bound).

    `lhs` is the whole left-hand side -- `uint256 _put_ret` for a scalar, or a
    destructuring pattern `(uint248 _put_ret0, )` for a tuple. ONE
    implementation for both shapes: the guards below are about the STATEMENT,
    not about what is being bound, and a second copy would be a second place
    for the try/catch refusal to be forgotten.

    ONLY a bare asserted call statement is bound. The other shapes the emitter
    writes are the R0 EXIT-KIND EXPECTATION itself -- `try`/`catch` for a
    rollback revert, a bare call under `vm.expectRevert()` for a custom-error
    one -- and that expectation is preserved here BY CONSTRUCTION, by touching
    only the argument list. Splicing a binding into either would replace an
    assertion about how the transaction ends with a different statement, which
    is exactly the failure this whole lifting route exists to avoid.
    """
    stripped = call_line.lstrip()
    indent = call_line[:len(call_line) - len(stripped)]
    if stripped.startswith("try "):
        return None, ("the emitted call is a `try`/`catch` statement -- that "
                      "shape IS this path's exit-kind expectation, and a "
                      "reverting execution has no return value to assert")
    key = "." + unit + "("
    k = stripped.find(key)
    if k < 0:
        return None, "the call statement could not be located for binding"
    if "=" in stripped[:k]:
        return None, "the emitted call already binds its result"
    if not stripped.rstrip().endswith(";"):
        return None, ("the emitted call is not a simple statement, so a "
                      "binding cannot be placed in front of it")
    return indent + lhs + " = " + stripped, None


def bind_return(call_line, unit, decl_type, var):
    """The scalar case of `bind_return_lhs`."""
    return bind_return_lhs(call_line, unit, f"{decl_type} {var}")


# ---------------------------------------------------------------------------
# Storage layout (from solc, via forge) -- never guessed
# ---------------------------------------------------------------------------

# Key types whose mapping slot is `keccak256(abi.encode(key, slot))`.
#
# A WHITELIST, for the reason every other whitelist in this pipeline is one.
# Solidity computes a mapping slot as `keccak256(h(k) . p)` where h PADS a
# VALUE type to 32 bytes -- which is exactly what `abi.encode` does -- but
# CONCATENATES a `string`/`bytes` key unpadded, which is `abi.encodePacked`.
# Using the wrong one produces a perfectly well-formed read of a slot nothing
# wrote, i.e. a green assertion about an unrelated quantity. Rather than encode
# both rules, the dynamic-key case is refused by name.
MAP_KEY_OK = re.compile(
    r"^(?:u?int(?:\d+)?|address|bool|bytes(?:[1-9]|[12]\d|3[0-2])|"
    r"enum\s+.+)$")


def map_key_type_ok(label):
    return MAP_KEY_OK.match((label or "").strip()) is not None

# ---- ONE SLOT-NAME PARSER, USED BY BOTH SITES -----------------------------
#
# `bal[k]`, `bal[a][b][c]`, `pack[k].tag` -- a mapping name, one or more keys,
# an optional member tail. There are two places in `build_put` that take a
# slot name apart (the entry-state PIN and the oracle READ), and they used to
# carry the same regex twice. Both spelled the key as `(.+?)`, which on a
# nested name matches the whole run of brackets: `bal[a][b]` came out as the
# single key `a][b`, which resolves to nothing. Two copies also means a fix to
# one is invisible in the other -- the divergence this file has already paid
# for elsewhere.
#
# `[^\[\]]+` rather than `.+?` on purpose: a key is an identifier, a numeric
# or hex literal, or `msg.sender`, never something containing a bracket, so
# forbidding brackets inside a key makes the split unambiguous instead of
# backtracking into a wrong one.
SLOT_NAME_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)((?:\[[^\[\]]+\])+)"
    r"((?:\.[A-Za-z_]\w*)*)$")
SLOT_KEY_RE = re.compile(r"\[([^\[\]]+)\]")


def parse_slot_name(s):
    """(mapping, [key, ...], tail) -- or (None, [], "") if it is not a slot.

    The tail keeps its leading dot, exactly as the old group(3) did, because
    `maps` is keyed by `<label>` and `<label>.<member>` and the lookup is
    built by concatenation.
    """
    m = SLOT_NAME_RE.match(s)
    if not m:
        return None, [], ""
    return m.group(1), SLOT_KEY_RE.findall(m.group(2)), m.group(3)


ESBMC_MAP_BASE_RE = re.compile(r"^[A-Za-z_]\w*$")


def map_esbmc_certifiable(base):
    """Whether --path-cov-assert can resolve this mapping base today."""
    return ESBMC_MAP_BASE_RE.match(base or "") is not None


def map_entry_esbmc_certifiable(spec):
    return bool(spec and len(spec) >= 5 and map_esbmc_certifiable(spec[4]))


def queryable_mapping(maps, key):
    return bool(maps and key in maps and map_entry_esbmc_certifiable(maps[key]))


def esbmc_certifiable_maps(maps):
    return {name: spec for name, spec in (maps or {}).items()
            if map_entry_esbmc_certifiable(spec)}


def region_slot_vars(region, maps):
    """Mapping-member coordinates already present in the certified region.

    Stage 2 has already paid to certify these exact source dependency slots,
    including any literal key that was needed to make a bytesN aggregate
    expressible. Reusing them for the assertion ladder is both cheaper and more
    faithful than regenerating a cross product of same-typed keys.
    """
    out = []
    for name in region or {}:
        if not name.startswith("state."):
            continue
        v = name[6:]
        mname, _keys, tail = parse_slot_name(v)
        if mname is None:
            continue
        if not queryable_mapping(maps, mname + tail):
            continue
        if v not in out:
            out.append(v)
    return out


def assert_query_pins(pins, layout, maps):
    """Pins that the ESBMC assertion query can resolve, plus skipped reasons."""
    keep, skipped = {}, []
    for name, value in sorted((pins or {}).items()):
        if not name.startswith("state."):
            keep[name] = value
            continue
        v = name[6:]
        mname, _keys, tail = parse_slot_name(v)
        if mname is not None:
            if queryable_mapping(maps, mname + tail):
                keep[name] = value
            else:
                skipped.append(
                    f"{name} (not passed to --path-cov-assert: `{mname}` is "
                    "not a queryable mapping member in solc's layout)")
            continue
        if layout and v in layout:
            keep[name] = value
        else:
            skipped.append(
                f"{name} (not passed to --path-cov-assert: solc's layout "
                "does not list it, so it is a semantic constant/immutable pin)")
    return keep, skipped


def assert_query_region_entries(region, holes, layout, maps):
    """Certified region entries that the ESBMC assertion query can resolve."""
    entries, skipped = [], []
    for name, (lo, hi) in (region or {}).items():
        if name.startswith("state."):
            v = name[6:]
            mname, _keys, tail = parse_slot_name(v)
            if mname is not None:
                if not queryable_mapping(maps, mname + tail):
                    skipped.append(
                        f"{name} (not passed to --path-cov-assert: `{mname}` "
                        "is not a queryable mapping member in solc's layout)")
                    continue
            elif not (layout and v in layout):
                skipped.append(
                    f"{name} (not passed to --path-cov-assert: solc's layout "
                    "does not list it, so it is a semantic constant/immutable "
                    "pin)")
                continue
        entry = {"name": name, "lo": str(lo), "hi": str(hi)}
        if holes.get(name):
            entry["holes"] = [str(h) for h in holes[name]]
        entries.append(entry)
    return entries, skipped


def _storage_layout_struct_members(label, base_slot, members, types):
    out = {}
    if not label:
        return out
    try:
        slot = int(base_slot)
    except (TypeError, ValueError):
        return out
    for mem in members or []:
        try:
            mty = types.get(mem.get("type")) or {}
            if (mty.get("encoding") == "inplace"
                    and mty.get("members") is not None):
                out.update(_storage_layout_struct_members(
                    "%s.%s" % (label, mem["label"]),
                    slot + int(mem.get("slot", 0)),
                    mty.get("members"), types))
                continue
            if (mty.get("encoding") != "inplace"
                    or mty.get("members") is not None
                    or mty.get("numberOfBytes") is None):
                continue
            out["%s.%s" % (label, mem["label"])] = (
                slot + int(mem.get("slot", 0)),
                int(mem.get("offset", 0)),
                int(mty["numberOfBytes"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _storage_layout_mapping_entries(label, base_slot, key_type, value_type,
                                    types):
    out = {}
    try:
        mslot = int(base_slot)
    except (TypeError, ValueError):
        return out
    vt = value_type or {}
    kts = [key_type]
    depth_guard = 0
    while vt.get("encoding") == "mapping":
        depth_guard += 1
        if depth_guard > 16:
            return {}
        kts.append((types.get(vt.get("key")) or {}).get("label") or "")
        vt = types.get(vt.get("value")) or {}
    if (vt.get("encoding") != "inplace"
            or vt.get("numberOfBytes") is None
            or not all(map_key_type_ok(k) for k in kts)):
        return out
    ktxt = (kts[0].strip() if len(kts) == 1
            else tuple(k.strip() for k in kts))
    if vt.get("members") is None:
        try:
            out[label] = (mslot, ktxt, int(vt["numberOfBytes"]), 0,
                          label, None)
        except (TypeError, ValueError):
            return {}
        return out
    for mem in vt["members"]:
        try:
            mty = types.get(mem.get("type")) or {}
            if (mty.get("encoding") != "inplace"
                    or mty.get("members") is not None
                    or mty.get("numberOfBytes") is None):
                continue
            if int(mem.get("slot", 0)) != 0:
                continue
            out["%s.%s" % (label, mem["label"])] = (
                mslot, ktxt, int(mty["numberOfBytes"]),
                int(mem.get("offset", 0)), label, mem["label"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _storage_layout_struct_mappings(label, base_slot, members, types):
    out = {}
    if not label:
        return out
    try:
        slot = int(base_slot)
    except (TypeError, ValueError):
        return out
    for mem in members or []:
        try:
            mty = types.get(mem.get("type")) or {}
            name = "%s.%s" % (label, mem["label"])
            mslot = slot + int(mem.get("slot", 0))
            if mty.get("encoding") == "mapping":
                kt = (types.get(mty.get("key")) or {}).get("label") or ""
                vt = types.get(mty.get("value")) or {}
                out.update(_storage_layout_mapping_entries(
                    name, mslot, kt, vt, types))
            elif (mty.get("encoding") == "inplace"
                  and mty.get("members") is not None):
                out.update(_storage_layout_struct_mappings(
                    name, mslot, mty.get("members"), types))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def storage_layout(project, contract):
    """({var: (slot, off, size)}, {map: (slot, key_type, value_size)}, err).

    Read from `forge inspect <C> storageLayout --json`, i.e. from solc.  A
    variable in NEITHER dict has NO storage slot: it is a `constant` (baked
    into the code) or an `immutable` (baked into the deployed bytecode).
    Returning it absent rather than guessing a slot is what lets the caller
    DROP its rungs with a reason instead of emitting a read of the wrong slot
    -- which would be a green-looking assertion about a quantity nothing wrote.

    THE SECOND DICT IS NOT THE FIRST ONE WIDENED. A mapping has no readable
    slot OF ITS OWN -- the number solc reports for it is the `p` that goes into
    the hash, not a word holding a value -- so putting it in `out` would let
    every existing caller `vm.load` it and get the zero word back. It is a
    different KIND of address and it gets a different table.
    """
    p = subprocess.run(["forge", "inspect", contract, "storageLayout",
                        "--json"], cwd=project, capture_output=True, text=True)
    if p.returncode != 0:
        return None, None, (f"forge inspect failed (rc={p.returncode}): "
                            f"{p.stdout + p.stderr}")
    try:
        j = json.loads(p.stdout)
    except ValueError as e:
        return None, None, f"forge inspect produced no JSON: {e}"
    types = j.get("types") or {}
    out, maps = {}, {}
    for e in j.get("storage") or []:
        ty = types.get(e.get("type")) or {}
        enc = ty.get("encoding")
        if enc == "mapping":
            kt = (types.get(ty.get("key")) or {}).get("label") or ""
            vt = types.get(ty.get("value")) or {}
            # The VALUE must itself be a plain inplace scalar. A nested mapping
            # (`encoding == "mapping"`) or a struct value needs a second hash
            # or a member offset, and guessing either reads the wrong word.
            #
            # ---- WHAT THIS `continue` COSTS, AND WHY LIFTING IT BUYS NOTHING --
            #
            # MEASURED on aqua, whose ONE state variable hits both arms at once
            # (`forge inspect Aqua storageLayout --json`):
            #
            #   _balances: mapping(address => mapping(address =>
            #                mapping(bytes32 => mapping(address => Balance))))
            #   struct Balance { uint248 amount; uint8 tokensCount; }
            #                                 -- both in slot 0, offset 0 / 31
            #
            # so every aqua unit reports, in its own emit log:
            #
            #   step 2a: storage layout -- 0 readable scalar slot(s): none;
            #                              0 mapping(s) with a value-type key: none
            #
            # and the arm's four units yield ONE PUT with an oracle: rawBalances,
            # on its RETURN VALUE. push and dock declare no return, safeBalances'
            # certified path never reaches one, and none of the three has a
            # scalar slot to read. That is the whole of aqua's oracle ceiling.
            #
            # ⛔ A MAPPING-ENTRY ORACLE IS NOT A MISSING CAPABILITY. It works
            # end to end for the shape this table admits, and the emitted proof
            # is on disk -- P28_MapMin.take path 15, `mapping(uint256 =>
            # uint256)`, four POST-STATE assertions read through `vm.load` at
            # the slot this file computes:
            #
            #   uint256 _pre_bal_k  = uint256(vm.load(address(c0),
            #       keccak256(abi.encode(<key>, uint256(0)))));
            #   c0.take(<key>, v);
            #   uint256 _post_bal_k = ...same...;
            #   assertTrue(_post_bal_k != _pre_bal_k, "bal[k]: post != pre");
            #   assertLe (_post_bal_k,  _pre_bal_k,   "bal[k]: post <= pre");
            #   assertLt (_post_bal_k,  _pre_bal_k,   "bal[k]: post < pre");
            #   assertLe (_post_bal_v,  _pre_bal_v,   "bal[v]: post <= pre");
            #
            # So naming, solving and rendering all exist for a ONE-level mapping
            # with a scalar value. What aqua asks for differs from P28 by TWO
            # changes at once: FOUR levels of nesting, and a packed STRUCT value
            # (`Balance{uint248 amount; uint8 tokensCount}`, offsets 0 and 31).
            #
            # BOTH SIDES REFUSE IT, AND THE STRUCT ALONE IS ENOUGH. Measured on
            # D44_MapStructValue, which is P28's shape with a matched pair of
            # units over two mappings differing ONLY in the value type --
            # `mapping(uint256 => uint256)` and `mapping(uint256 => Bal)` with
            # `Bal { uint248 amount; uint8 tag; }`. One contract, one run, so
            # cell, bound and entry state cannot explain a difference.
            #
            # SIDE 1, HERE. All eight emitted PUTs report the same step 2a line:
            #     storage layout -- 0 readable scalar slot(s): none;
            #                       1 mapping(s) with a value-type key: balScalar
            #     mapping slots proposed to the ladder: balScalar[k], balScalar[v]
            # `balStruct` never appears, not even for `takeStruct`, the unit that
            # touches nothing else. The `continue` below is why: the SCRIPT picks
            # which mapping slots go into the spec's `vars`, so this table is
            # upstream of the ladder for mappings (it is NOT upstream for scalar
            # state variables -- those the verifier names itself, which is why
            # aqua's ladder still finds the immutable `_DOCKED`).
            #
            # SIDE 2, THE VERIFIER, probed directly by hand-writing the `vars`
            # this table would have to produce. `balScalar[k]` is the control and
            # it returns six judged rows in the same harness. All three spellings
            # of the struct member are REFUSED, and the middle one says what is
            # actually missing:
            #     balStruct[k].amount -> "not a scalar component of this
            #         contract's instance object"
            #     balStruct[k]        -> "the mapping's VALUE type cannot carry a
            #         candidate: it resolves to an AGGREGATE (struct / contract
            #         instance) -- bounding it would need a COORDINATE PER FIELD,
            #         which is a different coordinate kind, not a wider interval"
            #     balStruct.amount[k] -> "'balStruct.amount' is not a
            #         contract-scope store ... available are: balScalar, balStruct"
            #
            # SO THE ORDER IS FIXED: a per-field coordinate kind has to exist in
            # the verifier BEFORE widening the `continue` below buys anything.
            # Widening it first proposes names that come back refused, and the
            # emitted PUT looks exactly as oracle-free as it does today.
            #
            # aqua's `_balances` is blocked on this arm AND on nesting, and only
            # this arm has been isolated. Whether four levels with a SCALAR value
            # would be named is untested here; note P16_Mapping is the two-level
            # scalar shape and returns `solver-unknown` for every path, so that
            # arm may fail at the solver rather than at the naming.
            #
            # Meanwhile this table has a SECOND consumer that does fire on the
            # one-level shape: the mapping-slot PIN `state.<m>[<key>]` in
            # `build_put`, which establishes an entry value with `vm.store`.
            # ---- ⚠ THE LONG NOTE ABOVE IS PARTLY STALE, and here is what ----
            #
            # It concludes "a per-field coordinate kind has to exist in the
            # verifier BEFORE widening the `continue` below buys anything".
            # That prerequisite has since been MET: D44's emitted PUTs carry
            # `balStruct[k].amount` and `.tag` assertions, read with the right
            # mask and shift, so the per-field kind exists end to end. The
            # struct arm of the note is history; the NESTING arm is what is
            # handled here.
            #
            # ---- PEEL EVERY MAPPING LEVEL, COLLECTING THE KEY TYPES --------
            #
            # A nested mapping's value is itself `encoding == "mapping"`, so
            # the old guard dropped the whole variable at level 0 and aqua's
            # `_balances` never entered this table at all. Peeling reaches the
            # scalar (or packed-struct) leaf and records what each level's key
            # must be; `map_slot_expr` hashes them in the same order.
            maps.update(_storage_layout_mapping_entries(e["label"], e["slot"],
                                                        kt, vt, types))
            continue
        # Only INPLACE value types can be read/written as a masked slot word.
        # A `bytes`/`string` slot holds a length-or-payload encoding, not the
        # value.
        if enc != "inplace":
            continue
        nb = ty.get("numberOfBytes")
        if ty.get("members") is not None:
            out.update(_storage_layout_struct_members(
                e.get("label"), e.get("slot"), ty["members"], types))
            maps.update(_storage_layout_struct_mappings(
                e.get("label"), e.get("slot"), ty["members"], types))
            continue
        if nb is None:
            continue
        try:
            out[e["label"]] = (int(e["slot"]), int(e["offset"]), int(nb))
        except (KeyError, TypeError, ValueError):
            continue
    return out, maps, None


# MASKS ARE DECIMAL, NEVER HEX, and that is not a style choice.
#
# `0xffffffffffffffffffffffffffffffffffffffff` is 40 hex digits, and solc
# parses a 40-hex-digit literal as an ADDRESS, rejecting it unless it is
# EIP-55 checksummed:
#
#   Error (9429): This looks like an address but has an invalid checksum.
#   Error (2271): Built-in binary operator & cannot be applied to types
#                 uint256 and address.
#
# MEASURED: every farming PUT failed to compile on exactly this, for a mask
# over a 20-byte `address` state variable. The emitter had already learned the
# same lesson from the other side -- foundry.cpp:370-371 renders an address
# value as `address(uint160(<decimal>))` "so we never emit a 40-hex-digit
# literal (which Solidity rejects unless EIP-55 checksummed)". A decimal
# literal has no such second reading at any width.
def _mask_lit(v):
    return str(v)


def slot_read_expr_at(addr, slot_expr, off, nbytes):
    """A uint256-valued read of one packed variable at a slot EXPRESSION.

    Split out from `slot_read_expr` so a mapping slot -- whose address is a
    hash, not a literal -- reuses the SAME masking. Two copies of the shift and
    mask arithmetic is how the packed case comes to be right in one place and
    wrong in the other.
    """
    mask = (1 << (8 * nbytes)) - 1
    inner = f"uint256(vm.load({addr}, {slot_expr}))"
    if off:
        inner = f"({inner} >> {8 * off})"
    if nbytes < 32:
        inner = f"({inner} & {_mask_lit(mask)})"
    return inner


def slot_read_expr(addr, slot, off, nbytes):
    """A uint256-valued expression reading one packed storage variable."""
    return slot_read_expr_at(addr, f"bytes32(uint256({slot}))", off, nbytes)


def map_slot_expr(key_exprs, slot):
    """The bytes32 storage slot of `m[k1][k2]...` for a mapping at `slot`.

    Solidity: `keccak256(h(k) . p)` with h padding a VALUE-type key to 32
    bytes, which is what `abi.encode` does. `abi.encodePacked` would NOT --
    it is the rule for a dynamic key -- and those are refused in
    `storage_layout` rather than encoded here.

    NESTED IS THE SAME RULE APPLIED AGAIN, with the previous level's hash
    taking the place of `p`. The keys are applied LEFT TO RIGHT, outermost
    first, because `m[a][b]` is `(m[a])[b]`: the slot of `m[a]` is the `p` the
    second hash consumes. Applying them in the other order produces a
    perfectly well-formed address of a word nothing ever wrote, and a
    `post == pre` rung over it would stay GREEN -- the silent-wrong-quantity
    failure, in its most convincing costume.

    A bare string is still accepted, so every existing caller is unchanged and
    the one-level output is byte for byte what it was.
    """
    if isinstance(key_exprs, str):
        key_exprs = [key_exprs]
    acc = f"uint256({slot})"
    for k in key_exprs:
        acc = f"keccak256(abi.encode({k}, {acc}))"
    return acc


# `bal[k]` is not an identifier, and a local named after it would not compile.
def _slot_ident(var):
    return re.sub(r"[^0-9A-Za-z_]", "_", var).strip("_")


def slot_write_lines_at(addr, slot_expr, off, nbytes, value_expr,
                        indent="    "):
    """Read-modify-write of one packed variable at a slot EXPRESSION.

    Split out from `slot_write_lines` for the same reason `slot_read_expr_at`
    was: a MAPPING slot's address is a keccak hash rather than a literal, and
    two copies of the shift/mask arithmetic is how the packed case comes to be
    right in one place and wrong in the other.

    RMW rather than a whole-word store: several state variables share a slot
    whenever they pack (solc's layout reports offset/numberOfBytes precisely
    so this is decidable, not guessed), and a whole-word store would silently
    zero its neighbours -- which is a change to the entry state nobody asked
    for and which the region says nothing about. A mapping value occupies the
    whole word, so there the RMW degenerates to a plain store; keeping ONE code
    path is worth more than saving those two lines.
    """
    mask = (1 << (8 * nbytes)) - 1
    s = _mask_lit(mask << (8 * off))
    return [
        f"{indent}{{",
        f"{indent}  uint256 _w = uint256(vm.load({addr}, {slot_expr}));",
        f"{indent}  _w = (_w & ~uint256({s})) | "
        f"((uint256({value_expr}) & {_mask_lit(mask)}) << {8 * off});",
        f"{indent}  vm.store({addr}, {slot_expr}, bytes32(_w));",
        f"{indent}}}",
    ]


def slot_write_lines(addr, slot, off, nbytes, value_expr, indent="    "):
    """Read-modify-write of one packed storage variable at a literal slot."""
    return slot_write_lines_at(
        addr, f"bytes32(uint256({slot}))", off, nbytes, value_expr, indent)


def slot_landing_check_at(addr, slot_expr, off, nbytes, value_expr, what,
                          indent="    "):
    """Read the word back and assert the establishment LANDED.

    ⛔ WHY A WRITE NEEDS A CHECK AT ALL. `vm.store` cannot fail. Hand it a slot
    address the contract never reads -- a mapping hashed with the wrong key
    order, a packed field whose offset was mis-taken, a name that moved when
    the contract was recompiled -- and it writes the word, returns, and the
    PUT runs green. The region is a statement about the slice `owner == 0`;
    every rung then holds of a slice the test never entered, and the whole
    file is 256 green runs standing for a different execution.

    That is not hypothetical bookkeeping: this emitter has already shipped a
    pin that was "satisfied by coincidence and reported as unestablishable"
    (see the mapping-pin comment in `build_put`), and the only reason it was
    caught was that a human read the preamble. A read-back turns the whole
    class into a RED test with the coordinate's name on it.

    The check is emitted right after the write, before the call, so a failure
    names the establishment rather than the oracle.
    """
    rd = slot_read_expr_at(addr, slot_expr, off, nbytes)
    msg = json.dumps(
        f"entry pin {what} did NOT land: vm.store wrote a word the contract "
        f"does not read back at this slot, so the test is not inside the "
        f"certified region and every rung below is about a different state")
    return [f"{indent}assertEq({rd}, uint256({value_expr}), {msg});"]


def slot_landing_check(addr, slot, off, nbytes, value_expr, what,
                       indent="    "):
    """`slot_landing_check_at` for a literal slot number."""
    return slot_landing_check_at(
        addr, f"bytes32(uint256({slot}))", off, nbytes, value_expr, what,
        indent)


def slot_inside_region_check_at(addr, slot_expr, off, nbytes, lo, hi, what,
                                indent="    "):
    """Assert the ENTRY value already lies inside a bound the test cannot set.

    A wide `state.<v>` bound is not established -- the entry state is never
    havoc'd, so storing a fuzz-chosen value would explore entry states the
    proof never saw, and doing it is what turned three PoC PUTs RED. But
    DROPPING it silently leaves the other half unchecked: the query ASSUMED
    the entry value is in `[lo, hi]`, and if the constructor's actual value is
    outside, that assumption was vacuous and the certificate is about no
    execution at all.

    So the bound becomes a READ-ONLY check. Nothing is written, the entry
    state the proof was about is kept exactly, and the one thing the drop used
    to hide -- a region that is vacuous at this entry state -- becomes a RED
    test naming the coordinate instead of a comment nobody reads.

    ⛔ AN ENDPOINT AT THE TYPE'S OWN LIMIT IS NOT EMITTED. `state.tag in
    [0, 2^256-1]` constrains nothing, so `assertLe(tag, 2^256-1)` is a
    compile-time tautology -- an assertion that cannot fail, which is the one
    shape this file must never add: it makes the checked count go up while the
    oracle stays where it was. Both endpoints trivial means an EMPTY list, and
    the caller reports the coordinate as unchecked rather than as checked.
    """
    rd = slot_read_expr_at(addr, slot_expr, off, nbytes)
    msg = json.dumps(
        f"the entry state is OUTSIDE the certified region: {what} was assumed "
        f"in [{lo}, {hi}] when the path was certified, so a value outside it "
        f"means the assumption was vacuous and the rungs below were proved "
        f"about no execution this test can reach")
    tmax = (1 << (8 * nbytes)) - 1
    out = []
    if int(lo) > 0:
        out.append(f"{indent}assertGe({rd}, uint256({lo}), {msg});")
    if int(hi) < tmax:
        out.append(f"{indent}assertLe({rd}, uint256({hi}), {msg});")
    return out


def slot_inside_region_check(addr, slot, off, nbytes, lo, hi, what,
                             indent="    "):
    """`slot_inside_region_check_at` for a literal slot number."""
    return slot_inside_region_check_at(
        addr, f"bytes32(uint256({slot}))", off, nbytes, lo, hi, what, indent)


# A key written as a LITERAL has no Solidity type until it is given one, and
# `abi.encode(0xFF..FF)` does not compile ("Cannot perform ABI encoding for
# type rational_const"). A key that is a declared PARAMETER already has one and
# must NOT be re-cast: `uint256(someAddress)` is a compile error, while
# `abi.encode(someAddress)` is exactly the 32-byte padding Solidity hashes.
_KEY_LIT_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)$")


def key_expr_typed(text):
    return f"uint256({text})" if _KEY_LIT_RE.match(text.strip()) else text


# ---------------------------------------------------------------------------
# The unit's declared parameters, from the solc AST
# ---------------------------------------------------------------------------

def _load_ast(ast_path):
    txt = open(ast_path).read()
    return json.loads(txt[txt.index("{"):])


def _decl_list(node, key):
    """[(name, solidity_type)] for a FunctionDefinition's `parameters` or
    `returnParameters`, in SOURCE ORDER."""
    out = []
    for p in ((node.get(key) or {}).get("parameters") or []):
        ty = ((p.get("typeDescriptions") or {}).get("typeString") or "")
        out.append((p.get("name") or "", ty))
    return out


def _function_defs(ast_path, contract, unit):
    """The FunctionDefinition nodes named `unit` visible in `contract`,
    BASE-FIRST, so the LAST one is the most-derived declaration.

    INHERITANCE IS NOT OPTIONAL: a unit of the contract under test is
    routinely DECLARED on a base (`BaseEscrow.rescueFunds` under
    `--contract EscrowSrc`).  The C3 linearisation is walked in reverse so the
    most-derived declaration wins, exactly as the compiler resolves it.
    """
    ast = _load_ast(ast_path)
    by_id, target = {}, None

    def index(n):
        nonlocal target
        if isinstance(n, dict):
            if n.get("nodeType") == "ContractDefinition":
                if n.get("id") is not None:
                    by_id[n["id"]] = n
                if n.get("name") == contract:
                    target = n
            for v in n.values():
                index(v)
        elif isinstance(n, list):
            for v in n:
                index(v)

    index(ast)
    scopes = []
    if target is not None:
        chain = target.get("linearizedBaseContracts") or [target.get("id")]
        scopes = [by_id[c] for c in reversed(chain) if c in by_id]
    if not scopes:
        scopes = [ast]

    defs = []
    for sc in scopes:
        for n in sc.get("nodes", []) or []:
            if (isinstance(n, dict) and n.get("nodeType") == "FunctionDefinition"
                    and n.get("name") == unit):
                defs.append(n)
    return defs


def _select_def(defs, arity, declaration_id=None):
    """The one declaration an `arity`-argument call resolves to, or None.

    SHARED between the parameter reader and the return-type reader ON PURPOSE.
    Two overloads of one name are two units with two signatures AND two return
    types; a second copy of this selection could pick a different one, and the
    symptom would be a return value bound at the wrong type on a call whose
    arguments were rewritten from the right one -- i.e. one fact kept in two
    ledgers, which this project has already paid for once.
    """
    if not defs:
        return None
    if declaration_id is not None:
        exact = [declaration for declaration in defs
                 if declaration.get("id") == declaration_id]
        return exact[0] if len(exact) == 1 else None
    if len(defs) == 1:
        return defs[-1]
    if arity is not None:
        fit = [d for d in defs if len(_decl_list(d, "parameters")) == arity]
        if len(fit) == 1:
            return fit[0]
        # Same arity twice: most-derived wins (scopes walked base-first).
        if fit:
            return fit[-1]
    return defs[-1]


def function_params(ast_path, contract, unit, arity=None,
                    declaration_id=None):
    """[(name, solidity_type)] in SOURCE ORDER for `contract.unit`.

    Source order is what makes a positional rewrite of the emitted call legal,
    and it is the same order the emitter itself fills arguments in
    (foundry.cpp:1288 iterates the declared parameters).  Read from the AST
    rather than from the emitted text, so the two agree by construction on the
    only fact they share.

    `arity` disambiguates overloads: two functions of one name are two units,
    and picking the wrong one would rename arguments across signatures.
    """
    d = _select_def(_function_defs(ast_path, contract, unit), arity,
                    declaration_id)
    return None if d is None else _decl_list(d, "parameters")


def function_returns(ast_path, contract, unit, arity=None,
                     declaration_id=None):
    """[(name, solidity_type)] of the DECLARED return parameters, or None.

    An empty list is a real answer -- the unit returns nothing -- and is not
    the same as None, which means the declaration could not be read at all.
    The caller must not collapse them: "returns nothing" drops the return
    rungs silently and correctly, "could not read" has to be reported.
    """
    d = _select_def(_function_defs(ast_path, contract, unit), arity,
                    declaration_id)
    return None if d is None else _decl_list(d, "returnParameters")


def overload_artifact_label(ast_path, contract, unit, declaration_id):
    """Disambiguate artifacts only when the source name is truly overloaded."""
    if not ast_path or declaration_id is None:
        return ""
    try:
        declarations = _function_defs(ast_path, contract, unit)
    except (OSError, ValueError):
        return ""
    signatures = {
        tuple(sol_type for _name, sol_type
              in _decl_list(declaration, "parameters"))
        for declaration in declarations
    }
    return f"_pf{declaration_id}" if len(signatures) > 1 else ""


def select_path_claim(report, unit, enc, path_function=None):
    """Select one complete-path claim, refusing ambiguous legacy identities."""
    claims = [claim for claim in report.get("claims", [])
              if str(claim.get("path_id")) == str(enc)]
    if path_function:
        claims = [claim for claim in claims
                  if claim.get("path_function") == path_function]
    else:
        claims = [claim for claim in claims
                  if ((claim.get("condition") or "").split(":", 1)[0]
                      == unit)]
    if not claims:
        identity = path_function or unit
        return None, f"no report claim matches {identity} enc={enc}"
    path_functions = {claim.get("path_function") for claim in claims}
    if len(path_functions) != 1 or None in path_functions:
        return None, (f"enc={enc} under simple unit {unit!r} matches "
                      f"multiple path functions: "
                      f"{', '.join(sorted(str(p) for p in path_functions))}")
    if len(claims) != 1:
        return None, (f"{next(iter(path_functions))} enc={enc} appears "
                      f"{len(claims)} times in the report")
    return claims[0], None


# `bound()` on a coordinate needs a type this script can cast in both
# directions.  Anything else is NOT lifted -- reported, never silently kept as
# a concrete literal that would read like a generalised one.
def lift_kind(sol_type):
    t = (sol_type or "").strip()
    if t == "bool":
        return ("bool", 1)
    if t in ("address", "address payable"):
        return ("address", 160)
    m = re.match(r"^uint(\d+)?$", t)
    if m:
        return ("uint", int(m.group(1) or 256))
    return None


def full_lift_bounds(sol_type):
    """Full scalar domain for a type this emitter can place in a PUT signature."""
    lk = lift_kind(sol_type)
    if lk is None:
        return None
    kind, width = lk
    if kind == "bool":
        return (0, 1)
    return (0, (1 << width) - 1)


def default_call_arg(sol_type):
    """Concrete placeholder used before a missing emitted arg is fuzz-lifted."""
    lk = lift_kind(sol_type)
    if lk is None:
        return None
    kind, _width = lk
    if kind == "bool":
        return "false"
    if kind == "address":
        return "address(uint160(0))"
    return "0"


def signature_type(sol_type):
    """ABI signature spelling for a type whose omitted argument can be rebuilt."""
    t = _norm_ty(sol_type)
    if t == "address payable":
        return "address"
    if t in ("bool", "address"):
        return t
    m = re.match(r"^uint(\d+)?$", t)
    if m:
        return f"uint{m.group(1) or 256}"
    return None


def named_params(params):
    """Stable names for anonymous or duplicate Solidity parameters."""
    out, used = [], set()
    for idx, (name, ty) in enumerate(params or []):
        base = (name or "").strip() or f"arg{idx}"
        candidate = base
        if candidate in used:
            candidate = f"{base}_{idx}"
        while candidate in used:
            candidate += "_"
        used.add(candidate)
        out.append((candidate, ty))
    return out


# ---------------------------------------------------------------------------
# Reading the emitter's own output
# ---------------------------------------------------------------------------

def split_top_level(s):
    """Split an argument list on top-level commas.

    Scanned with a depth counter rather than `s.split(",")`: an emitted
    argument is routinely `address(uint160(0))` or a struct literal
    `IBaseEscrow.Immutables(a, b, c)`, and splitting on every comma cuts those
    in half -- silently, producing a wrong-arity call that still looks like a
    call.
    """
    out, depth, cur, instr = [], 0, "", False
    i = 0
    while i < len(s):
        ch = s[i]
        if instr:
            cur += ch
            if ch == "\\" and i + 1 < len(s):
                cur += s[i + 1]
                i += 2
                continue
            if ch == '"':
                instr = False
            i += 1
            continue
        if ch == '"':
            instr = True
            cur += ch
        elif ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip() or out:
        out.append(cur.strip())
    return out


class EmittedFile:
    """The `<Primary>.cov.t.sol` the emitter wrote, parsed just enough to
    append a function into the right test contract and to find the concrete
    case for one path."""

    CONTRACT_RE = re.compile(r"^contract (\w+) is Test \{")
    CLAIM_RE = re.compile(r"^\s*// claim: (.*)$")
    FN_RE = re.compile(r"^\s*function (test_cov_\d+)\(\) public \{")

    def __init__(self, path):
        self.path = path
        self.lines = open(path).read().splitlines()
        self.blocks = []          # (contract_name, start_idx, end_idx)
        self.cases = []           # (contract_idx, name, claims, body_slice)
        cur_c, cur_start = None, None
        depth = 0
        i = 0
        pending_claim = ""
        while i < len(self.lines):
            ln = self.lines[i]
            m = self.CONTRACT_RE.match(ln)
            if m and cur_c is None:
                cur_c, cur_start, depth = m.group(1), i, 1
                i += 1
                continue
            if cur_c is not None:
                mc = self.CLAIM_RE.match(ln)
                if mc:
                    pending_claim = mc.group(1)
                mf = self.FN_RE.match(ln)
                # A function is consumed WHOLE, opening and closing brace
                # together, and its braces are never fed to `depth`. Counting
                # the `function ... {` line and then jumping past its `}` left
                # `depth` permanently one too deep, so the contract's own
                # closing brace never brought it to zero and NO block was ever
                # recorded -- an empty `blocks` list that reads, three steps
                # later, as "the emitted file has no test contract".
                if mf:
                    j, d2 = i + 1, 1
                    while j < len(self.lines) and d2 > 0:
                        d2 += self.lines[j].count("{") - self.lines[j].count("}")
                        if d2 == 0:
                            break
                        j += 1
                    self.cases.append((len(self.blocks), mf.group(1),
                                       pending_claim, (i, j)))
                    pending_claim = ""
                    i = j + 1
                    continue
                depth += ln.count("{") - ln.count("}")
                if depth == 0:
                    self.blocks.append((cur_c, cur_start, i))
                    cur_c = None
            i += 1

    def case_for(self, path_function, enc):
        """The concrete case whose `// claim:` names this path, or None.

        Matched on the FULL mangled identity `<path_function>:path:<enc>`, not
        on `:path:<enc>` alone: two units of one contract have independent
        path-id spaces, so a bare enc matches the wrong unit's case as readily
        as the right one.
        """
        want = f"{path_function}:path:{enc}"
        for c in self.cases:
            ids = [x.strip() for x in c[2].split(",")]
            if want in ids:
                return c
        return None


# ---------------------------------------------------------------------------
# The ENVIRONMENT the emitted preamble actually sets, versus the one certified
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. A region coordinate is one of three kinds and the driver had
# only two answers: a declared PARAMETER is lifted, a `state.` coordinate is
# established with `vm.store`, and an ENVIRONMENT coordinate (`msg.`/`tx.`/
# `block.`) fell through BOTH loops and was dropped without a word.
#
# MEASURED, on FeeVault.setDiscount enc=7: the certified region is
# `msg.sender in [0, 0]`, and the emitter's kept preamble happens to carry
# `vm.prank(address(uint160(0)))`. The emitted PUT is therefore inside its own
# certified slice BY LUCK. Had the two disagreed, the test would have run under
# a caller the certification never spoke about, silently -- which is the same
# defect as the dropped `state.` pin, found a second time in the same function.
#
# Environment quantities are checked unless a dedicated mechanism below can
# establish them. A disagreement on every other quantity refuses.
#
# ---- ...AND FOR `msg.sender` THAT IS NOW TOO WEAK, MEASURED -----------------
#
# The paragraph above is right about `block.timestamp` and about `msg.value`,
# and it was right about `msg.sender` for exactly as long as no region could
# carry a WIDE one. `--env-coord msg.sender` makes that reachable, and the
# first two regions it produced were both REFUSED here:
#
#   farming.setDistributor enc=12
#     msg.sender is certified over [821886974, 821919743] but the emitted case
#     sets it to 2147483649 (`vm.prank(address(uint160(2147483649)));`), which
#     is OUTSIDE that range
#   farming.setDistributor enc=13
#     msg.sender is certified at 821886973 but the emitted case sets it to
#     2147483649
#
# The refusal is CORRECT -- the test really would walk an execution the region
# never spoke about -- and it is also terminal: the preamble's sender comes
# from the concrete counterexample of a DIFFERENT query, so it will essentially
# never land inside a certified interval by chance. Checking alone therefore
# converts every wide-sender region into nothing.
#
# `msg.sender` is an environment quantity a Foundry test can CHOOSE. The
# comment further down this file that says an environment quantity "cannot be
# bound() into the signature" is true only of the CALL's argument list; the
# test FUNCTION's signature is a different list, and `vm.prank` accepts an
# arbitrary expression -- including a fuzz parameter. So for `msg.sender`, and
# it, the driver ESTABLISHES rather than checks:
#
#   width > 1  -> a fuzz parameter, bound() into the certified interval, and
#                 the governing prank rewritten to use it. The PUT is then a
#                 fuzz test OVER the certified sender range instead of one
#                 point of it.
#   width == 1 -> the governing prank rewritten to the certified value.
#
# A certified region already proves that every admitted msg.value walks the
# target path, including its exit kind. Therefore an EXISTING low-level
# `.call{value: ...}` may also take a bound fuzz parameter. The rewrite is kept
# narrow: this driver does not invent a value option or change another call
# shape. `tx.`/`block.` stay CHECKED because a test cannot set them at all.
ENV_PREFIXES = ("msg.", "tx.", "block.")
# Auto-derived environment coordinates must be realizable by the emitted test.
# msg.value remains conditional on an existing value-bearing call; that final
# shape check lives in planned_env_value and fails closed in env_disagreements.
ESTABLISHABLE_ENV_COORDS = frozenset(("msg.sender", "msg.value"))

_PRANK_RE = re.compile(r"vm\.(?:start)?[Pp]rank\(")
_VALUE_RE = re.compile(r"\{\s*value\s*:\s*([^},]+?)\s*\}")


def _lit_int(expr):
    """The integer a rendered Solidity literal denotes, or None if it is not one.

    Handles the emitter's own renderings -- `address(uint160(<dec>))`
    (foundry.cpp:370-371 renders an address that way precisely so it never emits
    a 40-hex-digit literal), `uint256(<dec>)`, a bare decimal, and hex. Anything
    else returns None, which the caller reports as UNCHECKABLE rather than as
    agreement: an expression this cannot read is not a value it may assume.
    """
    if expr is None:
        return None
    s = expr.strip()
    while True:
        m = re.match(r"^(?:address|payable|u?int\d*)\s*\(\s*(.*)\s*\)$", s)
        if not m:
            break
        s = m.group(1).strip()
    try:
        return int(s, 0)
    except ValueError:
        return None


def _arg0(line, open_idx):
    """The first argument of a call whose `(` is at `open_idx`, as text."""
    depth, i = 1, open_idx + 1
    start = i
    while i < len(line) and depth:
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth:
        return None
    return split_top_level(line[start:i])[0] if line[start:i].strip() else None


def _strip_strings(s):
    """`s` with every double-quoted literal emptied.

    Parens INSIDE a string are not grouping. The emitter's own value-gate line
    carries `"setDistributor(address)"`, whose two parens happen to balance --
    so a naive count is right there BY LUCK and would be wrong the moment a
    signature took two arguments, or a revert string contained one bracket.
    """
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', s)


def statement_start(lines, i):
    """Index of the FIRST line of the statement whose LAST line is `i`.

    ---- WHY A STATEMENT IS NOT A LINE, MEASURED --------------------------------
    `find_unit_call` returns the line that NAMES the unit, and for the low-level
    value-gate shape the emitter puts that name on the SECOND line:

        (bool ok5, ) = address(c0).call{value: 1}(
            abi.encodeWithSignature("setDistributor(address)", address(uint160(0))));

    Every consumer that treated that index as "the call statement" was then
    reading, splicing or inserting in the middle of a statement:

      * `observed_env` searched `{value: ...}` on the returned line, did not
        find it, and reported `msg.value == 0` -- which REFUSED the PUT for
        farming/setDistributor enc=2, whose certified region is
        `msg.value in [1, 2^256-1]`, on the grounds that the emitted case ran
        outside it. The emitted case does not; the reader could not see it.
      * `establish_env_sender` would have inserted its `vm.prank(...)` at that
        index, i.e. BETWEEN the two halves of the statement.
      * `build_put`'s head/tail split would have spliced the entry-state
        `vm.store`s and the oracle's pre-reads there too.

    The first produced a wrong REFUSAL; the other two would have produced a
    file that does not compile. Paren depth over the lines, with string literals
    emptied first, is what tells the three of them the same answer.
    """
    j = i
    t = _strip_strings(lines[i])
    depth = t.count(")") - t.count("(")
    while depth > 0 and j > 0:
        j -= 1
        t = _strip_strings(lines[j])
        depth += t.count(")") - t.count("(")
    return j


def observed_env(body, call_i, call_line):
    """What the emitted case sets for `msg.sender` / `msg.value` at THIS call.

    The LAST prank above the call wins, because that is the semantics forge
    gives it -- `vm.prank` sets the sender for the next call only. `msg.value`
    comes from the call's own `{value: ...}` option; its ABSENCE is 0, which is
    a fact about the EVM and not a guess.

    The `{value: ...}` is looked for over the WHOLE STATEMENT, not over
    `call_line` alone: see `statement_start` for the shape that breaks it across
    two lines and for the wrong refusal that cost.

    Returns {name: (value_or_None, evidence_text)}. A None value with evidence
    means "found something and could not read it"; a None value with no
    evidence means "the preamble says nothing about this".
    """
    start, text = call_i, call_line
    if 0 <= call_i < len(body):
        start = statement_start(body, call_i)
        text = "\n".join(body[start:call_i + 1])
    sender, sender_ev = None, None
    for ln in body[:start]:
        m = _PRANK_RE.search(ln)
        if m:
            sender_ev = ln.strip()
            sender = _lit_int(_arg0(ln, m.end() - 1))
    value, value_ev = 0, "no {value:} option on the call, so msg.value is 0"
    m = _VALUE_RE.search(text)
    if m:
        value_ev = m.group(0)
        value = _lit_int(m.group(1))
    return {"msg.sender": (sender, sender_ev), "msg.value": (value, value_ev)}


def low_level_value_gate_asserts_exit(body, call_i, call_line):
    """Whether the emitted low-level value-gate assertion survived the rewrite.

    This intentionally recognizes only the shape the cov emitter writes for a
    non-payable ABI value gate:

        (bool ok5, ) = address(c0).call{value: ...}(...);
        assertFalse(ok5, ...);

    A user assertion such as `assertFalse(c0.flag())` is a functional oracle,
    not an exit-kind assertion, and must not be counted in this ledger.
    """
    if not (0 <= call_i < len(body)):
        return False
    stmt_i = statement_start(body, call_i)
    stmt = "\n".join(body[stmt_i:call_i + 1])
    if ".call" not in stmt or _VALUE_RE.search(stmt) is None:
        return False
    m = re.search(r"\(\s*bool\s+([A-Za-z_][A-Za-z0-9_]*)\s*,[^)]*\)\s*=",
                  stmt)
    if not m:
        return False
    ok_name = m.group(1)
    if call_i + 1 >= len(body):
        return False
    return bool(re.match(r"\s*assertFalse\s*\(\s*" + re.escape(ok_name)
                         + r"\s*(?:,|\))", body[call_i + 1]))


def establish_env_sender(body, call_i, region, holes, pins, used,
                         call_value_expr=None):
    """Rewrite the governing `vm.prank` so the test runs inside the certified
    `msg.sender` slice, instead of refusing because it does not.

    Returns `(body, call_i, established, sig_add, pre_add, note, sender_expr)`.
    `body` and `call_i` come back unchanged, and `established` is None, when
    the region says nothing about `msg.sender` -- the caller then behaves
    exactly as it did before, so every existing PUT is reproduced verbatim.

    `sender_expr` IS THE SOLIDITY TEXT THIS FUNCTION PUT INSIDE THE PRANK, and
    it is returned so that a storage slot keyed by `msg.sender` can be read at
    the SAME address the call will run as. Before it was returned, the caller
    only got the marker string `"msg.sender"` and had no way to name the
    address, so `slot_key_expr` refused every such key -- correctly, because
    guessing would have produced a vacuously green `post == pre` over a slot
    the unit never touches. It is None on the early return (the region says
    nothing about the sender, so this function chose no address and the
    refusal must stay), and non-None on BOTH other paths: the fuzz-parameter
    branch sets it to the parameter name, the point branch to the literal cast.

    WHICH PRANK IS "GOVERNING". The LAST one above the call, which is the same
    rule `observed_env` reads by and the same one forge implements
    (`vm.prank` sets the sender for the next call only). Deliberately identical
    to the checker's rule rather than better than it: if the two disagreed
    about which line matters, this function could rewrite one line while
    `env_disagreements` cleared a different one, and the emitted test would be
    refused-or-accepted on evidence about the wrong statement.

    IF THERE IS NO PRANK AT ALL the sender is the test contract's own address,
    which is a value nothing here chose and which the certified interval will
    not contain except by accident -- so one is INSERTED. That shifts the call,
    hence `call_i` is returned rather than assumed unchanged; forgetting it
    would splice the new statement into the middle of the caller's later
    `body[head_end:call_i]` slice.

    ⛔ `holes` IS NOT OPTIONAL AND MUST COME FROM THE CALLER'S OWN DICT. The
    certified region on this coordinate can be an interval MINUS a set, and a
    value in the hole is NOT in the region: the certification query was never
    asked about it. Rendering the interval and dropping the punch produces a
    test whose header claims the certified region and whose body ranges over a
    strictly larger one -- green on draws the proof does not cover. This
    argument used to be a literal `()` here while the PARAMETER path four lines
    below passed `holes.get(pname, ())`, which is the one-fact-two-readers shape
    the rest of this file is full of warnings about.
    """
    lo = hi = None
    if "msg.sender" in region:
        lo, hi = region["msg.sender"]
    elif "msg.sender" in pins:
        lo = hi = pins["msg.sender"]
    if lo is None:
        return body, call_i, None, None, [], None, None

    sig_add, pre_add = None, []
    if hi > lo:
        var = "p_msg_sender"
        while var in used:
            var += "_"
        sig_add = ("address", var)
        # Reuses the parameter path's own bounder, so a sender interval and an
        # address ARGUMENT interval are rendered by one piece of code. 160 is
        # the address width; a certified bound outside it would be a bound the
        # coordinate's own type cannot hold, and bound_lines clamps in uint256
        # before the cast exactly as it does for an address parameter.
        # Same call the parameter path makes, holes included: `bound_lines`
        # renders each as `vm.assume(x != h)`, and a hole removes one value out
        # of an interval so rejection stays rare by construction.
        sender_holes = sorted(holes.get("msg.sender", ()))
        pre_add = bound_lines(var, "address", 160, lo, hi, sender_holes)
        sender_expr = var
        prank = f"    vm.prank({var});"
        note = (f"msg.sender in [{lo}, {hi}]"
                + ("  \\ {" + ", ".join(str(h) for h in sender_holes) + "}"
                   if sender_holes else "")
                + f" is ESTABLISHED and FUZZED: the "
                  f"governing vm.prank now takes the bound() fuzz parameter "
                  f"`{var}`, so this PUT ranges over the certified sender "
                  f"interval rather than over one point of it"
                + (f", and the {len(sender_holes)} punched value(s) are "
                   f"excluded by vm.assume" if sender_holes else ""))
    else:
        sender_expr = f"address(uint160({lo}))"
        prank = f"    vm.prank({sender_expr});"
        note = (f"msg.sender == {lo} is ESTABLISHED: the governing vm.prank "
                f"was rewritten to the certified value. The emitted case's own "
                f"sender came from a different query's counterexample and is "
                f"not the value this region is a statement about")

    new_body = list(body)
    # The prank goes above the STATEMENT, not above the line that names the
    # unit: for the low-level value-gate shape those are two different indices
    # and the second one is inside the statement. See `statement_start`.
    stmt_i = (statement_start(new_body, call_i)
              if 0 <= call_i < len(new_body) else call_i)

    # ---- FUNDING THE SENDER THIS DRIVER JUST CHOSE ---------------------------
    #
    # A `{value: v}` call is paid for BY THE SENDER, and after the rewrite the
    # sender is a value this driver chose rather than the one the emitter
    # funded. The emitter writes `vm.deal(address(this), v)` because ITS
    # counterexample ran the call from the test contract; once the prank moves
    # msg.sender, that account is no longer the one paying and the call fails
    # for INSUFFICIENT FUNDS.
    #
    # ⛔ AND THE TEST WOULD STILL BE GREEN, which is why this is not cosmetic.
    # The value-gate case's own assertion is
    #     assertFalse(ok, "value sent to a non-payable entry must revert")
    # and an out-of-funds failure satisfies it WITHOUT the value ever reaching
    # the entry -- so the assertion passes while the path it is named after is
    # never walked. Every `post == pre` rung passes too, for the same wrong
    # reason. That is a test that is green while standing for something else,
    # which is the outcome this pipeline exists never to produce.
    fund = None
    if call_value_expr is not None:
        fund = f"    vm.deal({sender_expr}, {call_value_expr});"
        note += (f" (and funded with `vm.deal({sender_expr}, "
                 f"{call_value_expr})`, because the call sends a fuzzed value "
                 f"and the sender pays)")
    elif 0 <= call_i < len(new_body):
        mv = _VALUE_RE.search("\n".join(new_body[stmt_i:call_i + 1]))
        if mv:
            v = _lit_int(mv.group(1))
            if v is None:
                note += (f" (⚠ the call carries `{mv.group(0)}`, whose amount "
                         f"this driver cannot read, so the chosen sender was "
                         f"NOT funded -- the call may fail for lack of funds "
                         f"rather than at the value gate)")
            elif v > 0:
                fund = f"    vm.deal({sender_expr}, {v});"
                note += (f" (and funded with `vm.deal({sender_expr}, {v})`, "
                         f"because the call sends value and the sender pays)")

    last = None
    for i in range(min(stmt_i, len(new_body))):
        if _PRANK_RE.search(new_body[i]):
            last = i
    if last is None:
        add = ([fund] if fund else []) + [prank]
        new_body[stmt_i:stmt_i] = add
        call_i += len(add)
        note += " (no prank was present, so one was inserted)"
    else:
        note += f" (replacing `{new_body[last].strip()}`)"
        new_body[last] = prank
        # BEFORE the prank, never after: `vm.prank` binds to the NEXT call, and
        # foundry.cpp's own comment states it "must be the last cheatcode
        # before the call".
        if fund:
            new_body.insert(last, fund)
            call_i += 1
    return new_body, call_i, "msg.sender", sig_add, pre_add, note, sender_expr


def planned_env_value(body, call_i, region, used):
    """Return the fuzz variable for a renderable wide msg.value interval.

    This intentionally recognises only an existing low-level `.call{value:}`.
    Changing another call shape into a value-bearing call would be a separate
    semantic transformation; leaving it unhandled keeps env_disagreements as
    the fail-closed gate.
    """
    if "msg.value" not in region:
        return None
    lo, hi = region["msg.value"]
    if hi <= lo or not (0 <= call_i < len(body)):
        return None
    stmt_i = statement_start(body, call_i)
    statement = "\n".join(body[stmt_i:call_i + 1])
    if ".call" not in statement or not _VALUE_RE.search(statement):
        return None
    var = "p_msg_value"
    while var in used:
        var += "_"
    return var


def establish_env_value(body, call_i, region, holes, value_var,
                        sender_expr=None):
    """Fuzz an existing low-level call's certified msg.value interval.

    Returns `(body, call_i, established, sig_add, pre_add, note)`.  A missing
    plan is a no-op; the caller then checks the concrete value exactly as it did
    before this mechanism existed.
    """
    if value_var is None:
        return body, call_i, None, None, [], None

    lo, hi = region["msg.value"]
    value_holes = sorted(holes.get("msg.value", ()))
    new_body = list(body)
    stmt_i = statement_start(new_body, call_i)
    replaced = False
    for i in range(stmt_i, call_i + 1):
        if not replaced and _VALUE_RE.search(new_body[i]):
            new_body[i] = _VALUE_RE.sub(
                "{value: " + value_var + "}", new_body[i], count=1)
            replaced = True
    if not replaced:
        return body, call_i, None, None, [], None

    # establish_env_sender already funds the rewritten sender when it chose
    # one. Otherwise recover the actual payer from the governing prank, or use
    # the test contract for an ordinary unpranked call.
    if sender_expr is None:
        payer = "address(this)"
        prank_i = None
        for i in range(stmt_i):
            m = _PRANK_RE.search(new_body[i])
            if m:
                prank_i = i
                payer = _arg0(new_body[i], m.end() - 1).strip()
        fund = f"    vm.deal({payer}, {value_var});"
        insert_at = prank_i if prank_i is not None else stmt_i
        new_body.insert(insert_at, fund)
        call_i += 1

    note = (f"msg.value in [{lo}, {hi}]"
            + ("  \\ {" + ", ".join(str(h) for h in value_holes) + "}"
               if value_holes else "")
            + f" is ESTABLISHED and FUZZED: the existing low-level call now "
              f"takes the bound() fuzz parameter `{value_var}`"
            + (f", and the {len(value_holes)} punched value(s) are excluded "
               f"by vm.assume" if value_holes else ""))
    return (new_body, call_i, "msg.value", ("uint256", value_var),
            bound_lines(value_var, "uint", 256, lo, hi, value_holes), note)


def env_disagreements(body, call_i, call_line, region, pins, established=()):
    """(refusals, unchecked) for every width-1 environment quantity certified.

    `refusals` is non-empty exactly when the emitted case is KNOWN to run
    outside the certified slice, or when it cannot be shown to run inside it.
    An environment quantity this driver cannot establish or observe is a
    refusal. A test of unknown membership in the certified slice is not a
    weaker PUT; it is not justified by that certificate at all.
    """
    want, ranged = {}, {}
    for n, (lo, hi) in region.items():
        if not n.startswith(ENV_PREFIXES):
            continue
        # ESTABLISHED, so there is nothing left to check: the caller rewrote
        # the statement that decides this quantity, and comparing the rewritten
        # line against the value it was rewritten to would be a tautology
        # dressed as a verification. Skipped HERE rather than by deleting the
        # coordinate from `region`, because `region` is also what the PUT's
        # header prints and the reader must still see the certified bound.
        if n in established:
            continue
        if lo == hi:
            want[n] = lo
        else:
            # ---- A WIDE ENVIRONMENT COORDINATE MUST NOT VANISH -------------
            #
            # This function only ever looked at `lo == hi`, and an environment
            # coordinate is in NEITHER of build_put's other two loops: the
            # parameter loop iterates the unit's DECLARED parameters (msg.sender
            # is not one) and the entry-state loop requires a `state.` prefix.
            # So a wide one fell through all three and was dropped without a
            # word -- which is verbatim the defect the comment above this
            # function describes, fixed at the time only for the width-one case.
            #
            # It was unreachable until now: a region can only carry a wide
            # environment coordinate if one was promoted with `--env-coord`,
            # and nothing in this pipeline passed that flag. The two undriven
            # farming units measured today are refused on exactly `msg.sender`
            # and `block.timestamp` -- "[NOT a bounded coordinate]" on every
            # path -- so promoting them is the named repair, and it opens this.
            ranged[n] = (lo, hi)
    for n, v in pins.items():
        if n.startswith(ENV_PREFIXES) and n not in want and n not in established:
            want[n] = v
    obs = observed_env(body, call_i, call_line)
    refusals, unchecked = [], []
    # A RANGE IS NOT ESTABLISHED, IT IS CHECKED FOR MEMBERSHIP. The test runs
    # under whatever single value the emitter's preamble sets; the region says
    # the path holds across an interval. Those are compatible exactly when that
    # one value lies inside the interval -- then the test is one point of a
    # wider certified claim, which is weaker than the region and is SAID so.
    # Outside it, the test walks an execution the region never spoke about, and
    # that is the same refusal a width-one disagreement gets.
    for n, (lo, hi) in sorted(ranged.items()):
        if n not in obs:
            refusals.append(
                f"{n} is certified over [{lo}, {hi}], but this emitter cannot "
                f"establish or observe it. The test is not known to run inside "
                f"the certified range")
            continue
        got, ev = obs[n]
        if got is None:
            refusals.append(
                f"{n} is certified over [{lo}, {hi}] and the emitted case "
                + (f"sets it with `{ev}`, which this driver cannot read as a "
                   f"value" if ev else "never sets it")
                + ", so the test is not known to run inside the certified range")
            continue
        if got < lo or got > hi:
            refusals.append(
                f"{n} is certified over [{lo}, {hi}] but the emitted case sets "
                f"it to {got} (`{ev}`), which is OUTSIDE that range. The test "
                f"would walk an execution the region never spoke about")
            continue
        unchecked.append(
            f"{n} is certified over [{lo}, {hi}] and this test exercises the "
            f"single value {got} (`{ev}`), which is inside it. The PUT is "
            f"therefore ONE POINT of that part of the region, not a fuzz over "
            f"it -- an environment quantity is not a call argument, so it "
            f"cannot be bound() into the signature")
    for n, v in sorted(want.items()):
        if n not in obs:
            refusals.append(
                f"{n} is certified at {v}, but this emitter cannot establish "
                f"or observe it. The test is not known to run inside that "
                f"certified slice")
            continue
        got, ev = obs[n]
        if got is None:
            refusals.append(
                f"{n} is certified at {v}, and the emitted case "
                + (f"sets it with `{ev}`, which this driver cannot read as a "
                   f"value" if ev else
                   "never sets it, so it takes forge's default rather than the "
                   "certified value")
                + ". Emitting anyway would produce a test that is not known to "
                  "run inside the region it quotes")
            continue
        if got != v:
            refusals.append(
                f"{n} is certified at {v} but the emitted case sets it to "
                f"{got} (`{ev}`). The test would walk a different execution "
                f"from the one the region is a statement about")
    return refusals, unchecked


def slot_key_expr(kname, key_expr_of):
    """(expression, None) or (None, refusal) for a mapping-slot KEY.

    ONE implementation for BOTH sides, and that is the whole reason it exists.
    The oracle side refused a key that is not a declared parameter; the ENTRY-
    STATE PIN side fell back to the raw text. Two answers to one question, and
    the permissive one was on the side that WRITES.

    WHY THE PERMISSIVE SIDE IS WRONG, specifically for `msg.sender`. Inside a
    Foundry test function `msg.sender` is whoever called the TEST -- forge's
    default sender -- while the contract under test sees `address(this)` as its
    caller unless a `vm.prank` sits immediately above the call. So a pin on
    `state.<m>[msg.sender]` would `vm.store` at
    `keccak256(abi.encode(<the test's sender>, p))` while the unit reads
    `keccak256(abi.encode(<the caller>, p))`: a perfectly well-formed write to a
    slot the contract never reads.

    AND IT WOULD NOT SHOW UP AS A FAILURE. The rungs a frame-condition slot
    produces are `post == pre`, which hold trivially when nothing writes either
    word -- so the emitted test is GREEN while establishing none of the entry
    state its own header claims. That is the exact shape this file already
    carries three comments about, and it became REACHABLE the moment the stage-2
    driver learned to propose `state.<m>[msg.sender]` as a coordinate.

    Refusing is not a yield loss that could have been avoided by being cleverer:
    the address that will be `msg.sender` during the call is decided by the
    emitter's own preamble (a prank, or the test contract itself), and the
    region names a quantity in the VERIFIER's namespace. Making the two agree is
    a separate change with its own evidence; guessing here is not.
    """
    k = (kname or "").strip()
    if k in key_expr_of:
        return key_expr_typed(key_expr_of[k]), None
    if _KEY_LIT_RE.match(k):
        return key_expr_typed(k), None
    if k.startswith(ENV_PREFIXES):
        return None, (
            f"the key `{k}` is an ENVIRONMENT quantity, not a declared "
            f"parameter. Inside a Foundry test `msg.sender` is whoever called "
            f"the test, while the unit sees the test contract (or the pranked "
            f"address) as its caller -- so the slot written here and the slot "
            f"the unit reads would be different words, and a `post == pre` "
            f"rung over an untouched slot would stay GREEN while establishing "
            f"nothing")
    return None, (f"the key `{k}` is not a declared parameter of this unit, so "
                  f"the PUT has no expression for it")


CALL_LINE_RE_TMPL = r"^(\s*)(try )?(\w+)\.{unit}\("

# ---- THE SECOND SHAPE THE EMITTER WRITES, WHICH THE LIFTER COULD NOT SEE ----
#
# A path whose exit is the ABI VALUE GATE cannot be replayed as
# `c0.f(args)`: Solidity refuses to attach `{value: v}` to a call on a
# non-payable function at COMPILE time, so the emitter writes the only form
# that compiles --
#
#     (bool ok5, ) = address(c0).call{value: 1}(
#         abi.encodeWithSignature("setDistributor(address)", address(uint160(0))));
#     assertFalse(ok5, "value sent to a non-payable entry must revert");
#
# -- which is a CORRECT, ASSERTED concrete test. The lifter matched only the
# member-call shape, so it reported `no call to setDistributor found in
# test_cov_4; nothing to lift` and the path produced no PUT.
#
# MEASURED end to end on farming/setDistributor: with msg.value probed instead
# of auto-pinned, enc=2 CERTIFIES (`msg.value in [1, 2^256-1]`, `distributor_`
# and `msg.sender` both wide) and its ladder comes back 30 rows, 15 HOLDS --
# every state variable `post == pre`, which is exactly the oracle a value-gate
# path should have. Everything was in hand except the ability to read the
# emitter's own line back.
#
# The unit's arguments live inside `abi.encodeWithSignature`, one place to the
# right: element 0 of that call is the SIGNATURE STRING and the unit's argument
# k is element k+1. Nothing else about the statement is touched -- the
# `{value: ...}`, the tuple destructuring and the following `assertFalse` stay
# the emitter's, which is what keeps the R0 exit-kind expectation true by
# construction rather than re-derived here.
LOWLEVEL_CALL_RE_TMPL = r'abi\.encodeWithSignature\(\s*"{unit}\('


def call_arg_span(line, unit):
    """Argument-list span and ABI-signature offset for a supported unit call."""
    key = "." + unit + "("
    k = line.find(key)
    sig_offset = 0
    if k < 0:
        m = re.search(LOWLEVEL_CALL_RE_TMPL.format(unit=re.escape(unit)), line)
        if not m:
            return None
        # Start of `abi.encodeWithSignature(`'s argument list.
        k = line.find("(", m.start())
        key = "("
        sig_offset = 1
    start = k + len(key)
    depth, i = 1, start
    while i < len(line) and depth:
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth:
        return None
    args = split_top_level(line[start:i])
    if len(args) == 1 and args[0] == "":
        args = []
    return start, i, sig_offset, args


def find_unit_call(lines, unit):
    """Index of the LAST line in `lines` that calls `unit` on an instance.

    The last one, because a reconstructed case can replay several transactions
    (measured on farming `decimals`: three revert-tolerant calls followed by
    the asserted one) and the path being generalised is the one the case's
    final, exit-classified call walks.  Its preceding statements are the
    sequence that establishes the entry state and are kept verbatim.
    """
    rx = re.compile(CALL_LINE_RE_TMPL.format(unit=re.escape(unit)))
    # The low-level shape is SEARCHED, not matched at the line start: the
    # emitter breaks that statement across two lines and the signature sits on
    # the second, indented and inside a string.
    rx_low = re.compile(LOWLEVEL_CALL_RE_TMPL.format(unit=re.escape(unit)))
    hit = None
    for i, ln in enumerate(lines):
        if rx.match(ln) or rx_low.search(ln):
            hit = i
    return hit


def rewrite_call_args(line, unit, replacements):
    """Replace argument k of the call to `unit` with `replacements[k]`.

    Only the argument list is touched.  The receiver, the `try`/`{value:}`
    decoration and the statement shape are the emitter's and stay the
    emitter's -- that is what makes requirement 5 (the R0 exit-kind
    expectation) hold by construction instead of being re-derived here.
    """
    # ---- TWO SHAPES, ONE REWRITER ----
    #
    # Member call:      c0.setDistributor(a0, a1)
    # Low-level call:   abi.encodeWithSignature("setDistributor(address)", a0)
    #
    # In the second, element 0 of the argument list is the SIGNATURE STRING and
    # the unit's argument k is element k+1. The offset is applied here, in the
    # one place that knows which shape it is looking at -- a caller that had to
    # know would be a second reader of the same fact.
    span = call_arg_span(line, unit)
    if span is None:
        return None, None
    start, i, sig_offset, args = span
    new = list(args)
    for idx, txt in replacements.items():
        if idx + sig_offset < len(new):
            new[idx + sig_offset] = txt
    # The UNIT's arguments are returned, never the signature string: every
    # caller counts them against the unit's declared parameter list, and an
    # extra leading element would shift every index by one silently.
    return line[:start] + ", ".join(new) + line[i:], args[sig_offset:]


def complete_missing_call_args(line, unit, params, args):
    """Fill omitted concrete calldata args from the AST declaration.

    The coverage emitter can omit arguments that do not affect a revert-only
    path, leaving `try c.f() {}` or `abi.encodeWithSignature("f()")` for a
    function whose declaration has parameters.  A PUT can still fuzz those
    parameters over their full type domain: the certified region did not bound
    them precisely because the path predicate ignored them.  Unsupported types
    remain refused.
    """
    if len(args) >= len(params):
        return line, args, [], None
    span = call_arg_span(line, unit)
    if span is None:
        return None, None, [], "could not parse the emitted call's argument list"
    start, i, sig_offset, full_args = span
    if full_args[sig_offset:] != args:
        return None, None, [], "internal argument parser disagreement"
    completed = list(full_args)
    implicit = []
    for idx in range(len(args), len(params)):
        name, ty = params[idx]
        default = default_call_arg(ty)
        bounds = full_lift_bounds(ty)
        if default is None or bounds is None:
            return None, None, [], (
                f"emitted call omits parameter {idx} `{name}` of type `{ty}`, "
                "which this emitter cannot synthesize as a full-domain fuzz input")
        completed.append(default)
        implicit.append(idx)
    if sig_offset:
        sig_types = []
        for _name, ty in params:
            sty = signature_type(ty)
            if sty is None:
                return None, None, [], (
                    f"emitted low-level call omits a `{ty}` parameter whose ABI "
                    "signature spelling this emitter cannot render")
            sig_types.append(sty)
        if not completed:
            return None, None, [], "low-level call has no signature string"
        sig = completed[0].strip()
        if not re.match(rf'^"{re.escape(unit)}\([^"]*\)"$', sig):
            return None, None, [], (
                "low-level call's signature string could not be recognized")
        completed[0] = f'"{unit}({",".join(sig_types)})"'
    new_line = line[:start] + ", ".join(completed) + line[i:]
    return new_line, completed[sig_offset:], implicit, None


def target_instance_for_call(lines, call_i, unit):
    """Contract instance variable whose unit call is lifted, e.g. `c1`."""
    if not (0 <= call_i < len(lines)):
        return None
    start = statement_start(lines, call_i)
    stmt = "\n".join(lines[start:call_i + 1])
    m = re.search(r"address\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\.call",
                  stmt)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|[\s({;])(?:try\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*"
                  + re.escape(unit) + r"\s*\(", stmt)
    if m:
        return m.group(1)
    return None


def target_address_expr_for_call(lines, call_i, unit):
    """Address expression for the contract instance whose unit call is lifted.

    The concrete Foundry preamble names deployed contracts `c<N>`, but the
    target is not always `c0`: interface mocks may be deployed first. Storage
    slot oracles must read and write the same instance the lifted call targets.
    """
    inst = target_instance_for_call(lines, call_i, unit)
    return f"address({inst})" if inst is not None else None


# ---------------------------------------------------------------------------
# Building the PUT
# ---------------------------------------------------------------------------

def bound_lines(pname, kind, width, lo, hi, holes):
    """`bound()` + `vm.assume()` for one lifted coordinate.

    `bound` rather than `vm.assume(lo <= x && x <= hi)`: an assume-based range
    over a 160-bit coordinate rejects essentially every fuzz input and forge
    fails the run for too many rejections.  `bound` maps the whole input space
    into the interval, which is what makes 256 fuzz runs 256 MEASUREMENTS of
    the region rather than 256 rejections.  Holes stay assumes -- a hole
    removes one value out of an interval, so rejection is rare by
    construction.
    """
    out = []
    if kind == "bool":
        if lo == hi:
            out.append(f"    {pname} = {'true' if int(lo) else 'false'};")
        for h in holes:
            out.append(
                f"    vm.assume({pname} != {'true' if int(h) else 'false'});")
    elif kind == "address":
        out.append(f"    {pname} = address(uint160(bound("
                   f"uint256(uint160({pname})), {lo}, {hi})));")
        for h in holes:
            out.append(f"    vm.assume(uint256(uint160({pname})) != {h});")
    else:
        if width == 256:
            out.append(f"    {pname} = bound({pname}, {lo}, {hi});")
        else:
            out.append(f"    {pname} = uint{width}(bound("
                       f"uint256({pname}), {lo}, {hi}));")
        for h in holes:
            out.append(f"    vm.assume({pname} != {h});")
    return out


# ---- DID THIS PATH EXIT THROUGH A ROLLBACK REVERT? -------------------------
#
# The tool says so in the ladder run, in its own words:
#
#   WARNING: --path-cov-assert: unit '<uid>' path enc=13 exits through a
#   ROLLBACK revert. ...
#
# ⛔ WHY THE EMITTER HAS TO READ IT. The literal `ROLLBACK` appeared ZERO times
# in this file before this block: the fact existed in the log and NO reader
# consumed it, so layer-2/3 rungs were emitted on reverting paths as though the
# state they compare were observable. It is not.
#
# A reverting path's assertion is planted BEFORE the operation that fails, and
# that placement is not negotiable -- put after it, the assertion is unreachable
# on every input, the verifier reports it as holding, and that reads exactly
# like a proof that the path cannot be taken. The consequence is that the value
# the ladder compares is the one BETWEEN the write and the rollback, a moment no
# test and no chain can observe.
#
# MEASURED, farming setDistributor enc=13: the same run printed the ROLLBACK
# warning AND `✗ FAILED: eq__distributor` / `✓ PASSED: ne__distributor` with the
# region pinning state._distributor to [0, 0]. Restored state cannot refute
# `post == pre`; pre-rollback state can.
#
# §oracle already settles what to do: "A test for a reverting path carries the
# first layer alone, since a revert undoes what the other two would have
# compared." This is the wiring for that sentence.
ROLLBACK_EXIT_RE = re.compile(
    r"--path-cov-assert: unit '([^']*)' path enc=(\d+) exits through a "
    r"ROLLBACK revert")

ROLLBACK_UNOBSERVABLE = (
    "this path exits through a ROLLBACK revert, so the value the ladder "
    "compared is the one between the write and the rollback -- a moment no "
    "test and no chain can read. A revert restores storage, so the only "
    "post-state a test can observe on this path is the pre-state, and the "
    "only layer left with anything to say is the exit kind")

REVERT_UNOBSERVABLE = (
    "this path exits through a revert according to the Stage-1 path report, "
    "so post-state and return-value rungs are not observable on the chain; "
    "the layer left with anything to say is the exit kind")


def rollback_exit_paths(log):
    """{(unit_id, enc)} the ladder run reported as leaving through a rollback.

    Keyed on BOTH names the line carries rather than on `enc` alone: an enc is
    unique only within its unit, and reading one unit's rollback onto another's
    path would drop an oracle that was fine.
    """
    return {(m.group(1), int(m.group(2)))
            for m in ROLLBACK_EXIT_RE.finditer(log or "")}


def build_put(contract, unit, enc, depth_, path_function, region, holes, pins,
              params, emitted, case, layout, ladder_rows, notes, cell=None,
              unwind=None, rettypes=None, maps=None, piece_label="",
              derived_by=None, rollback_exit=False, r2_terms=None,
              oracle_label_prefix="", exit_kind=None):
    """The PUT function text, plus a per-part accounting for the report."""
    c_idx, cname, claims, (fs, fe) = case
    body = emitted.lines[fs + 1:fe]

    call_i = find_unit_call(body, unit)
    if call_i is None:
        notes.append(f"no call to `{unit}` found in {cname}; nothing to lift")
        return None, None
    call_line = body[call_i]

    # Which declared parameters the region actually bounds, and can be lifted.
    _new, args = rewrite_call_args(call_line, unit, {})
    if args is None:
        notes.append("could not parse the emitted call's argument list")
        return None, None
    if params is None:
        notes.append("the unit's declared parameters could not be read "
                     "from the AST")
        return None, None
    params = named_params(params)
    completed, completed_args, implicit_full, cerr = complete_missing_call_args(
        call_line, unit, params, args)
    if cerr is not None:
        notes.append(cerr)
        return None, None
    if implicit_full:
        body = list(body)
        body[call_i] = completed
        call_line = completed
        args = completed_args
        notes.append(
            "emitted replay omitted "
            + ", ".join(params[i][0] for i in implicit_full)
            + "; lifting them as full-domain calldata fuzz inputs because the "
              "certified region leaves them unconstrained")
    if len(params) != len(args):
        notes.append(f"declared arity {len(params)} != emitted arity "
                     f"{len(args)}; refusing to rewrite positionally")
        return None, None

    lifted, repl, sig, pre_lines = [], {}, [], []
    implicit_full = set(implicit_full)
    # coordinate -> how many values the EMITTED test leaves it. See the floor
    # test below; `lifted` alone cannot answer it.
    rendered_width = {}
    # WHAT AN R2 BOUND IS ALLOWED TO NAME, and it is a much smaller set than
    # "the unit's parameters". A rung may now carry a NAMED endpoint --
    # `post - pre in [amount, amount]`, the property a deposit is actually
    # about -- but the test can only spell a coordinate that this emitter
    # LIFTED into its own signature. Three kinds of coordinate look nameable
    # and are not:
    #   * a PINNED one -- not in the region, so it never becomes a parameter
    #     and keeps the emitter's literal;
    #   * one whose type `lift_kind` refuses -- also never a parameter;
    #   * one the emitter RENAMED to `p_<name>` to dodge a collision.
    # Rendering any of those produces a test that does not COMPILE, which is
    # strictly worse than dropping the rung. So the identifier is recorded
    # here, at the one place that knows it, and a bound naming anything absent
    # from this table is refused by `rung_assertions`.
    coord_ident = {}
    # The same table PLUS the address coordinates, spelled with the cast the
    # uint256 slot read needs. Only the ABSOLUTE bound may draw on it; see the
    # comment at the assignment below.
    coord_ident_abs = {}
    used = {b[0] for b in emitted.blocks}

    # The environment the emitted case runs under must be the one certified.
    # Foundry can establish msg.sender with prank and can establish msg.value
    # when the emitted statement already carries a low-level `{value: ...}`
    # option. Everything else is checked and a disagreement still refuses.
    #
    # ORDER MATTERS: establishment rewrites the very line the check reads, so
    # it has to happen first, and the rewritten body is what everything below
    # -- the call rewrite, the head/pre-state slices, the emitted text -- must
    # use. `call_i` can move, because a missing prank is inserted.
    env_value_var = planned_env_value(body, call_i, region, used)
    (body, call_i, env_est, sig_add, pre_add, env_note,
     env_sender_expr) = establish_env_sender(
        body, call_i, region, holes, pins, used,
        call_value_expr=env_value_var)
    env_established = []
    if env_est is not None:
        env_established.append(env_note)
        if sig_add is not None:
            sig.append(sig_add)
            pre_lines += pre_add
            lifted.append("msg.sender")
            used.add(sig_add[1])
            # `sig_add` is produced only on the `hi > lo` branch of
            # establish_env_sender, so this is the fuzzed sender; the width-one
            # branch rewrites the prank to a constant and renders one value.
            if "msg.sender" in region:
                _slo, _shi = region["msg.sender"]
                # MINUS THE PUNCHED VALUES, exactly as the parameter loop below
                # does. This number is what the floor test reads, so counting a
                # hole as a value the test may take would let a coordinate whose
                # interval is entirely punched out stand as the reason a PUT is
                # parameterized.
                # A SET, not a list: a hole repeated in the spec would be
                # subtracted twice and the interval would be reported narrower
                # than it is -- narrow enough, on a width-2 coordinate, to fail
                # the floor test and silently cost the whole PUT.
                rendered_width["msg.sender"] = (_shi - _slo + 1) - len(
                    {h for h in holes.get("msg.sender", ())
                     if _slo <= h <= _shi})
            coord_ident_abs["msg.sender"] = (
                f"uint256(uint160({env_sender_expr}))")

    (body, call_i, value_est, value_sig, value_pre,
     value_note) = establish_env_value(
        body, call_i, region, holes, env_value_var, env_sender_expr)
    if value_est is not None:
        env_established.append(value_note)
        sig.append(value_sig)
        pre_lines += value_pre
        lifted.append("msg.value")
        used.add(value_sig[1])
        _vlo, _vhi = region["msg.value"]
        rendered_width["msg.value"] = (_vhi - _vlo + 1) - len(
            {h for h in holes.get("msg.value", ())
             if _vlo <= h <= _vhi})
        coord_ident["msg.value"] = value_sig[1]
        coord_ident_abs["msg.value"] = value_sig[1]

    call_line = body[call_i]

    env_refusals, env_unchecked = env_disagreements(
        body, call_i, call_line, region, pins,
        established={e for e in (env_est, value_est) if e})
    if env_refusals:
        notes.append("the emitted case does not run in the certified "
                     "environment slice: " + "; ".join(env_refusals))
        return None, None
    for idx, (pname, ptype) in enumerate(params):
        if pname in region:
            lo, hi = region[pname]
            param_holes = sorted(holes.get(pname, ()))
        elif idx in implicit_full:
            lo, hi = full_lift_bounds(ptype)
            param_holes = []
        else:
            continue                       # pinned: keep the emitter's literal
        lk = lift_kind(ptype)
        if lk is None:
            notes.append(f"coordinate `{pname}` has type `{ptype}`, which "
                         f"this emitter cannot bound; kept PINNED at the "
                         f"emitter's literal")
            continue
        kind, width = lk
        var = pname if pname not in used and pname != "c0" else "p_" + pname
        sig_ty = "address" if kind == "address" else (
            "bool" if kind == "bool" else f"uint{width}")
        sig.append((sig_ty, var))
        pre_lines += bound_lines(var, kind, width, lo, hi, param_holes)
        repl[idx] = var
        lifted.append(pname)
        # ---- AN ADDRESS BOUNDS AN ABSOLUTE VALUE, NEVER A DELTA ------------
        #
        # What stood here dropped the address coordinate from the table
        # entirely, on the argument that `assertGe(post - pre, someAddress)`
        # is a question nobody asked. That argument is right about a DELTA and
        # wrong about an ABSOLUTE bound, and the two were being decided by one
        # flag -- the same conflation the R2 proposer had on the request side.
        #
        # MEASURED, and it cost the strongest oracle in the corpus: on
        # farming setDistributor the ladder answered
        #     _distributor: post in [distributor_, distributor_]   HOLDS
        # -- `the state ends equal to the argument`, which IS the property of
        # a setter -- and the emitter then dropped it with `rung shape not
        # rendered`, because `bound_term` could not spell `distributor_`.
        # Proven and thrown away at the last step.
        #
        # The slot read is a uint256, so the endpoint carries the cast that
        # makes the comparison well typed. It goes in a SEPARATE table:
        # `coord_ident` still holds only the arithmetic coordinates, so the
        # delta shapes cannot reach an address and the original rule stands
        # exactly where it was right.
        if kind == "address":
            coord_ident_abs[pname] = f"uint256(uint160({var}))"
        elif kind == "bool":
            bit = f"({var} ? uint256(1) : uint256(0))"
            coord_ident[pname] = bit
            coord_ident_abs[pname] = bit
        else:
            coord_ident[pname] = var
            coord_ident_abs[pname] = var
        # HOW MANY VALUES THIS RENDERED COORDINATE MAY TAKE. Recorded per
        # coordinate rather than inferred from `lifted` being non-empty, because
        # a coordinate whose region is a POINT is still rendered -- `bound(x, 0,
        # 0)` is a real line in the emitted tests -- and "rendered" and "free to
        # vary" are the two things the floor test below must not confuse.
        # A SET, and only the holes INSIDE the interval. A repeated hole
        # subtracted twice understates the width; a hole outside the interval
        # subtracted at all understates it for a value the bound already
        # excludes. Both directions cost a PUT that should have been emitted.
        rendered_width[pname] = (hi - lo + 1) - len(
            {h for h in param_holes if lo <= h <= hi})

    # ---- §From a Region to a Test: THE FLOOR TEST IS ON THE RENDERED SET ----
    #
    # Verbatim from the method:
    #
    #     A test is parameterized when at least one coordinate IT RENDERS is
    #     left more than one value to take. A region wider than a point does not
    #     settle this on its own, since the coordinates the omission rule leaves
    #     out are not rendered and a region can be wide only on those. Where no
    #     rendered coordinate takes more than one value, what the path receives
    #     is the concrete replay test of Section enum, whatever the width of
    #     R_pi.
    #
    # WHAT THIS FILE TESTED INSTEAD: whether `lifted` is non-empty, i.e. whether
    # ANY coordinate was rendered at all. Those differ exactly where it matters,
    # because a coordinate whose region is a POINT is still rendered -- the
    # emitted tests carry `bound(uint256(uint160(distributor_)), 0, 0)` as a
    # real line -- and nothing in the parameter loop above ever looked at the
    # width. So a region wide ONLY on coordinates the emitter drops (every
    # `state.<v>` bound of width > 1 is dropped by the entry-state branch below,
    # and says so) would be emitted as a PUT whose every fuzz parameter is
    # pinned to one value: a concrete replay with `bound()` syntax over it,
    # counted as a parameterized test.
    #
    # NOT HYPOTHETICAL. farming.setDistributor enc=12 renders `distributor_`
    # over [0, 0] and DROPS `state._owner in [1, 821886975]` as too wide to
    # establish. It survives only because `msg.sender` is also rendered and IS
    # wide. Remove the sender coordinate -- which is what every arm without
    # `--env-coord msg.sender` does -- and the same region renders one value on
    # everything while the box stays wide.
    #
    # The concrete replay test the method points to is not built here: the
    # emitter has already written it into this same file (`test_cov_*`), so the
    # correct action is to emit no PUT and say why.
    # ---- A RENDERED COORDINATE WITH NO VALUE LEFT REFUSES THE WHOLE PUT ----
    #
    # The holes are emitted as `vm.assume`, which is REJECTION SAMPLING. A
    # coordinate whose interval is entirely holed rejects every fuzz input, and
    # forge then fails the run for too many rejections -- a RED test, on the
    # unmodified contract, for a reason that has nothing to do with the
    # contract. Worse, it only happens when some OTHER coordinate is wide
    # enough to pass the floor test below, so the failure appears on exactly
    # the PUTs that looked healthiest.
    #
    # The driver is not supposed to produce this. That is the reason to CHECK
    # it here rather than to assume it: a proposition the method rests on is
    # worth a runtime check, and the ones this project has been bitten by were
    # all "cannot happen" until they did.
    _empty = sorted(n for n, w in rendered_width.items() if w < 1)
    if _empty:
        notes.append(
            "REFUSED: the certified region leaves NO value for "
            + ", ".join(f"{n} (rendered width {rendered_width[n]})"
                        for n in _empty)
            + ". Holes are emitted as `vm.assume`, i.e. rejection sampling, so "
              "an empty coordinate rejects every fuzz input and forge fails "
              "the run for too many rejections -- a RED test on the unmodified "
              "contract, for a reason that is not about the contract. Emitting "
              "nothing is the correct outcome; the region and its holes "
              "disagree and that is a fact about the region")
        return None, None
    if not any(w > 1 for w in rendered_width.values()):
        widths = ", ".join(f"{n}={w}" for n, w in sorted(rendered_width.items()))
        notes.append(
            "NOT PARAMETERIZED, per §From a Region to a Test: no coordinate "
            "this test RENDERS is left more than one value to take"
            + (f" (rendered widths: {widths})" if widths
               else " (no coordinate is rendered at all)")
            + ". A region wider than a point does not settle this on its own -- "
              "the coordinates the omission rule leaves out are not rendered, "
              "and a region can be wide only on those. What this path receives "
              "is the concrete replay test the emitter already wrote into this "
              "file; a PUT here would be that same replay with bound() syntax "
              "over it, counted as a parameterized test")
        return None, None

    new_call, _ = rewrite_call_args(call_line, unit, repl)
    target_addr = target_address_expr_for_call(body, call_i, unit)
    if target_addr is None:
        notes.append("could not identify the contract instance targeted by the "
                     "emitted call; refusing to guess storage oracle address")
        return None, None

    # What each declared parameter is called IN THE PUT. A lifted coordinate is
    # the fuzz local, a pinned one keeps the emitter's own literal -- and a
    # mapping-slot oracle has to index with whichever it is, or the pre-read and
    # the call disagree about which entry they are talking about.
    key_expr_of = {}
    for idx, (pname, _pt) in enumerate(params):
        key_expr_of[pname] = repl.get(idx, args[idx].strip())
    # ---- msg.sender IS NAMEABLE ONLY WHERE THIS PUT DECIDED IT ---------------
    #
    # `slot_key_expr` refuses `msg.sender` as a mapping key, and its docstring
    # says exactly why: the address the unit will see as its caller is chosen by
    # THIS emitter's preamble, while the region names a quantity in the
    # verifier's namespace, so hashing the wrong one writes/reads a word the
    # contract never touches and every `post == pre` rung over it stays GREEN
    # while establishing nothing. That docstring also names the fix -- "making
    # the two agree is a separate change with its own evidence" -- and this is
    # it: `establish_env_sender` has just rewritten the governing `vm.prank`, so
    # the address is no longer a guess, it is a string this file emitted.
    #
    # ⛔ THE REFUSAL IS NOT REMOVED, it is given an antecedent. When the region
    # says nothing about `msg.sender` this driver chose no address,
    # `env_sender_expr` is None, nothing is added here, and `slot_key_expr`
    # refuses with its wording unchanged. That branch is the negative control
    # and it must keep firing.
    #
    # ORDERING, which is what makes the fuzzed branch sound: the prank may take
    # the bound() parameter `p_msg_sender`, whose `bound()` lines go into
    # `pre_lines`. `out += pre_lines` runs BEFORE `out += store_lines` and
    # before the `pre_reads`, so every slot address computed from this
    # expression is computed from the value the call will actually run as, not
    # from the raw draw.
    if env_sender_expr is not None:
        key_expr_of["msg.sender"] = env_sender_expr

    # --- entry-state coordinates: ESTABLISHED with vm.store -----------------
    #
    # A PINNED state coordinate is established here too, and it is not a
    # refinement -- leaving it out emitted a test that is not in the slice its
    # own header claims. `state.<v> == k` is `[k, k]`: the same statement about
    # the entry state a width-one region bound makes, arrived at by a different
    # route (the operator named it rather than the ladder measuring it), and
    # certification treats the two identically -- `main()` builds the assert
    # spec by concatenating the region bounds with `{lo: v, hi: v}` rows for the
    # pins, so ESBMC has already been answering about the pinned value.
    #
    # MEASURED, on FeeVault.setDiscount: the guard is `msg.sender == owner`, and
    # the emitter's concrete case for the success path pranks `msg.sender = 0`
    # while `owner` keeps the value the CONSTRUCTOR gave it (the test contract's
    # own address, since `owner = msg.sender` at deployment). That case is
    # `[FAIL: EvmError: Revert]` under forge -- the require it was generated to
    # walk past rejects it. Pinning owner is what makes the path certifiable at
    # all (it turns a cross-coordinate relation, out of scope by Definition 6,
    # into coordinate-equals-constant), so the pin is not decoration: the region
    # is a statement about the slice `owner == 0`, and a test that never puts
    # the contract in that slice is evidence about a different execution.
    #
    # A pin the layout cannot reach is reported through the SAME `state_skipped`
    # channel as an unreachable region bound, because the consequence is the
    # same one: the emitted test is not known to be inside the certified slice,
    # and that has to be visible on the test rather than inferred from silence.
    store_lines, stored, state_skipped = [], [], []
    state_items = [(n, b) for n, b in region.items()]
    state_items += [(n, (v, v)) for n, v in pins.items() if n not in region]
    for name, (lo, hi) in sorted(state_items):
        if not name.startswith("state."):
            continue
        v = name[6:]
        # ---- A MAPPING SLOT PIN, `state.<m>[<key>]` -------------------------
        #
        # ESBMC resolves this shape as a certification COORDINATE, so a region
        # can now be a statement about one slot's entry value -- which is what
        # makes a mapping-guarded path (`require(bal[k] >= v)`) certifiable at
        # all. The test therefore has to ESTABLISH it, exactly as it does for a
        # scalar `state.<v>` pin.
        #
        # MEASURED, and it is why this branch exists rather than the pin simply
        # being dropped: on P28_MapMin.take the pin fell through to the
        # `v not in layout` arm below and was reported
        #     "no storage slot: ... it is a constant/immutable"
        # -- a sentence that is FALSE for a mapping (solc reports its slot; what
        # it does not report is a slot holding a value). The PUT happened to be
        # green anyway, because the emitted preamble's own `put(...)` call had
        # established the same value by luck. A pin that is satisfied by
        # coincidence and reported as unestablishable is the shape of defect
        # this file already carries three comments about.
        # The optional `.field` tail is a scalar member of a struct-valued
        # element; `mkey` is how that row is named in `maps`, which keys by
        # `<map>.<field>` so one mapping can contribute several coordinates.
        mname, pin_keys, pin_tail = parse_slot_name(v)
        if mname is not None:
            kname = ", ".join(pin_keys)
            mkey = mname + pin_tail
            if not maps or mkey not in maps:
                state_skipped.append(
                    f"{name} (`{mname}` is not a mapping solc's layout reports "
                    f"with a value-type key and a scalar value, so its slot "
                    f"address cannot be computed; a guessed one would write a "
                    f"word the contract never reads)")
                continue
            if lo != hi:
                # Same rule as a wide scalar state bound: the entry state is not
                # havoc'd, so a wide bound constrained nothing in the query and
                # the rungs were proved about ONE entry value. Establishing a
                # fuzz-chosen one would test entry states the proof never saw.
                # Same two halves as the scalar case below: the WRITE stays
                # forbidden, the CHECK does not. A mapping slot's entry value
                # is read with the same hash the write would have used, so a
                # vacuous assumption here is caught by the same line that
                # would catch a wrong key order.
                mslot, _kt2, vnb2, voff2, _mb2, _mm2 = maps[mkey]
                kx, kerr2 = [], None
                for kn in pin_keys:
                    ke2, err2 = slot_key_expr(kn, key_expr_of)
                    if err2 is not None:
                        kerr2 = err2
                        break
                    kx.append(ke2)
                chk = ([] if kerr2 is not None else
                       slot_inside_region_check_at(
                           target_addr, map_slot_expr(kx, mslot),
                           voff2, vnb2, lo, hi, name))
                if chk:
                    store_lines += chk
                    stored.append(f"{name} in [{lo}, {hi}] (checked, not set)")
                    continue
                state_skipped.append(
                    f"{name} in [{lo}, {hi}] (width > 1, DROPPED: the entry "
                    f"state is not havoc'd, so this bound constrained nothing "
                    f"in the query"
                    + (f"; and its key is not spellable either: {kerr2}"
                       if kerr2 is not None else
                       "; both endpoints are the type's own limits, so there "
                       "is not even an in-region check to make") + ")")
                continue
            mslot, _kt, vnb, voff, _mb, _mm = maps[mkey]
            # ---- THE KEY COUNT IS CHECKED, NOT ASSUMED ----
            #
            # `_kt` is the key type for one level and a TUPLE of them for a
            # nested store, so it carries the depth. A name with the wrong
            # number of keys still hashes to a perfectly well-formed address --
            # of a word nothing ever wrote -- and every rung over it would hold
            # trivially. Refused with both numbers named.
            nlev = 1 if isinstance(_kt, str) else len(_kt)
            if len(pin_keys) != nlev:
                state_skipped.append(
                    f"{name} (`{mname}` is a {nlev}-level store but the name "
                    f"gives {len(pin_keys)} key(s); a name with the wrong "
                    f"depth addresses a word nothing wrote)")
                continue
            kexprs, kerr = [], None
            for kn in pin_keys:
                ke, err = slot_key_expr(kn, key_expr_of)
                if err is not None:
                    kerr = err
                    break
                kexprs.append(ke)
            if kerr is not None:
                state_skipped.append(f"{name} ({kerr})")
                continue
            kexpr = kexprs
            # `voff`, not 0. A packed field does not start at bit 0 of the word,
            # and the read-modify-write is the only reason its neighbour survives
            # being established.
            store_lines += slot_write_lines_at(
                target_addr, map_slot_expr(kexpr, mslot), voff, vnb, str(lo))
            # READ IT BACK. A mapping address is a keccak of key and slot, so
            # a wrong key order, a wrong level count or a stale slot number
            # all produce a perfectly well-formed write to a word the contract
            # never reads -- and `vm.store` cannot fail. See
            # `slot_landing_check_at`.
            store_lines += slot_landing_check_at(
                target_addr, map_slot_expr(kexpr, mslot), voff, vnb,
                str(lo), name)
            stored.append(f"{name} := {lo}")
            continue
        if v not in layout:
            state_skipped.append(
                f"{name} (no storage slot: solc's layout does not list it, so "
                f"it is a constant/immutable and no test can set it)")
            continue
        slot, off, nb = layout[v]
        if lo != hi:
            # ---- A WIDE `state.<v>` BOUND MAY NOT BECOME A FUZZ COORDINATE ----
            #
            # THE ENTRY STATE IS NEVER HAVOC'D, and the tool says so in its own
            # words on every run: the ladder's summary line ends "HOLDS is
            # BOUNDED-holds: true for every input of the region under THIS
            # exploration (tx/unwind bound, POST-CONSTRUCTOR ENTRY STATE)". So a
            # region bound on a state variable is ASSUMED against a value the
            # constructor already fixed. When the bound spans the whole type --
            # and `state.tag in [0, 2^256-1]` is exactly that -- the assume
            # constrains NOTHING, and the rung was proved about ONE entry state.
            #
            # Lifting it into a fuzz parameter and establishing it with
            # `vm.store` explores 2^256 entry states the query never saw. That
            # is not a weaker test, it is a WRONG one, and it produced the
            # outcome this pipeline exists never to produce -- a PUT that is RED
            # on the unmodified contract:
            #
            #   D11_Bytes32Equality.takeBytes32 path 7
            #     ladder:  tag: post > pre   HOLDS      (pre = ctor's 0, post = 12)
            #     emitted: s_tag = bound(s_tag, 0, 2^256-1); vm.store(tag, s_tag)
            #     forge:   FAIL  tag: post >= pre: 12 < 75354922222753616806807...
            #
            # Three of the seven PoC PUTs failed this way, all with the same
            # shape. So the bound is DROPPED and reported: the contract keeps the
            # entry state the proof was about, and the PUT says out loud that the
            # region claimed more than the test establishes. Yield, not
            # soundness, is what is given up.
            #
            # Lifting it becomes correct the day the query havocs entry state --
            # then the proof and the test would be about the same set. Until
            # then, `lo == hi` is the only state bound a test may establish,
            # because that is the one case where storing the value cannot move
            # the entry state away from the one that was checked.
            #
            # NOT ESTABLISHED, BUT NO LONGER UNCHECKED. The write stays
            # forbidden for the reason above; what the drop used to also throw
            # away is the other half of the bound -- whether the entry value
            # the proof assumed is where the proof assumed it. That costs
            # nothing to check and is RED exactly when the assumption was
            # vacuous. See `slot_inside_region_check`.
            chk = slot_inside_region_check(target_addr, slot, off, nb,
                                           lo, hi, name)
            if chk:
                store_lines += chk
                stored.append(f"{name} in [{lo}, {hi}] (checked, not set)")
                continue
            state_skipped.append(
                f"{name} in [{lo}, {hi}] (width > 1, DROPPED: the entry state "
                f"is not havoc'd, so this bound constrained nothing in the "
                f"query -- the rungs were proved about the constructor's own "
                f"value. Establishing a fuzz-chosen value here would test "
                f"entry states the proof never covered, which is how this PUT "
                f"came back RED on the unmodified contract. Both endpoints "
                f"are the type's own limits, so there is not even an "
                f"in-region check to make: the bound says nothing at all)")
            continue
        val = str(lo)
        store_lines += slot_write_lines(target_addr, slot, off, nb, val)
        # READ IT BACK -- see `slot_landing_check`. A packed field whose
        # offset was mis-taken lands in its neighbour's bits and the PUT is
        # green about a state nobody set.
        store_lines += slot_landing_check(target_addr, slot, off, nb, val,
                                          name)
        stored.append(f"{name} := {val}")

    # --- the oracle: the unit's OWN RETURN VALUE ----------------------------
    #
    # Whether THIS path's call was emitted revert-tolerant. Read off the call
    # the PUT will actually contain (`new_call`), not off the fixture's
    # original line: the return-binding step below rewrites it, and a flag
    # taken from the wrong one of the two would gate the rungs on a statement
    # the test does not carry.
    call_is_revert_tolerant = new_call.strip().startswith("try ")

    # Done BEFORE the state rungs because it can rewrite `new_call`, and after
    # the state stores because it does not depend on them.
    # TWO SHAPES, ONE TABLE. A scalar return is the candidate `return`; a tuple
    # is one candidate PER MEMBER, `return.0`, `return.1`, ... in declaration
    # order -- which is the order solc gives them and therefore the order a
    # destructuring `(a, b) = f(...)` binds them in. The `retlive` witness is
    # shared and always sits on the bare `return`.
    ret_rows_all = [(var, t, v) for var, t, v in ladder_rows
                    if var == RETURN_VAR or var.startswith(RETURN_VAR + ".")]
    ret_asserts, ret_skipped, ret_pre_reads = [], [], []
    if ret_rows_all:
        live = [v for var, t, v in ret_rows_all
                if var == RETURN_VAR and t.startswith(RETLIVE_PREFIX)]
        # member index (None == the whole value) -> the texts that HOLD
        holds = {}
        for var, t, v in ret_rows_all:
            if t.startswith(RETLIVE_PREFIX) or v != "HOLDS":
                continue
            idx = None if var == RETURN_VAR else int(var.split(".", 1)[1])
            holds.setdefault(idx, []).append(t)
        lhs, plan = None, []
        why = None
        if not live:
            why = ("this ladder carries return rungs but NO `retlive` witness, "
                   "so a HOLDS among them cannot be told apart from holding "
                   "for want of a returned value")
        elif any(v != "REFUTED" for v in live):
            why = (f"the `retlive` witness came back {live[0]}, not REFUTED -- "
                   f"no execution of this path was shown to reach a return, so "
                   f"every other return rung holds VACUOUSLY")
        elif not holds:
            why = ("no return rung HOLDS over the certified region (the "
                   "value varies across it), which is a measurement, not a "
                   "failure")
        elif rettypes is None:
            why = ("the unit's declared return type could not be read from "
                   "the AST, so no local can be declared to bind the value")
        elif any("vm.expectRevert" in ln for ln in body[:call_i]):
            why = ("the emitted case arms `vm.expectRevert()` before this "
                   "call -- the transaction is expected to revert, so there "
                   "is no returned value for the test to read")
        elif None in holds and len(holds) > 1:
            why = ("the table carries BOTH a whole-value rung and per-member "
                   "rungs for one unit. A return is one shape or the other, "
                   "and guessing which the table means is how a value gets "
                   "bound at the wrong arity")
        elif None in holds and len(rettypes) != 1:
            why = (f"the ladder reports a whole-value rung but the unit "
                   f"declares {len(rettypes)} return value(s)")
        elif None not in holds and max(holds) >= len(rettypes):
            why = (f"the ladder names member {max(holds)} but the unit "
                   f"declares only {len(rettypes)} return value(s)")

        if why is None:
            base = "_put_ret"
            while any(base in ln for ln in body):
                base += "_"
            if None in holds:
                rk = return_kind(rettypes[0][1])
                if rk is None:
                    why = (f"the declared return type `{rettypes[0][1]}` is "
                           f"not one this emitter can bind and cast")
                else:
                    lhs = f"{rk[0]} {base}"
                    plan = [(None, rk, base)]
            else:
                # A member with no HOLDS rung, or one this emitter cannot cast,
                # gets an EMPTY slot rather than a named local: `(uint248 a, )`
                # is a legal destructuring and an unused named local is a solc
                # warning on a test nobody asked to be noisy.
                slots = []
                for i, (_nm, ty) in enumerate(rettypes):
                    rk = return_kind(ty)
                    if i in holds and rk is not None:
                        vn = f"{base}{i}"
                        slots.append(f"{rk[0]} {vn}")
                        plan.append((i, rk, vn))
                        continue
                    if i in holds and rk is None:
                        ret_skipped.append(
                            f"return.{i} (declared `{ty}`, which this emitter "
                            f"cannot bind and cast; its rungs are dropped and "
                            f"the OTHER members are still asserted)")
                    slots.append("")
                if not plan:
                    why = ("no member carrying a HOLDS rung has a type this "
                           "emitter can bind")
                else:
                    lhs = "(" + ", ".join(slots) + ")"

        if why is None:
            bound_call, berr = bind_return_lhs(new_call, unit, lhs)
            if berr is not None:
                why = berr
            else:
                new_call = bound_call
                ret_coord_ident_abs = dict(coord_ident_abs)
                planned_ret_pre_reads = []
                planned_ret_pre_names = set()
                for idx, _rk, _vn in plan:
                    for t in holds[idx]:
                        for spelling in return_rung_term_spellings(t):
                            term = (r2_terms or {}).get(spelling)
                            for cname in r2_term_coord_names(term):
                                if not cname.startswith("state."):
                                    continue
                                svar = cname[len("state."):]
                                if cname in ret_coord_ident_abs:
                                    continue
                                mname, slot_keys, slot_tail = parse_slot_name(
                                    svar)
                                if mname is not None:
                                    mkey = mname + slot_tail
                                    if not maps or mkey not in maps:
                                        ret_skipped.append(
                                            f"{cname} (`{mname}` is not a "
                                            "mapping solc's layout reports "
                                            "with a scalar value, so this "
                                            "return coordinate cannot be "
                                            "read before the call)")
                                        continue
                                    mslot, _ktype, vnb, voff, _mb, _mm = maps[mkey]
                                    nlev = (1 if isinstance(_ktype, str)
                                            else len(_ktype))
                                    if len(slot_keys) != nlev:
                                        ret_skipped.append(
                                            f"{cname} (`{mname}` is a "
                                            f"{nlev}-level store but the name "
                                            f"gives {len(slot_keys)} key(s))")
                                        continue
                                    kexprs, kerr = [], None
                                    for kn in slot_keys:
                                        ke, err = slot_key_expr(kn, key_expr_of)
                                        if err is not None:
                                            kerr = err
                                            break
                                        kexprs.append(ke)
                                    if kerr is not None:
                                        ret_skipped.append(f"{cname} ({kerr})")
                                        continue
                                    ident = "_ret_pre_" + _slot_ident(svar)
                                    if ident in planned_ret_pre_names:
                                        continue
                                    planned_ret_pre_names.add(ident)
                                    rd = slot_read_expr_at(
                                        target_addr,
                                        map_slot_expr(kexprs, mslot),
                                        voff, vnb)
                                    planned_ret_pre_reads.append(
                                        f"    uint256 {ident} = {rd};")
                                    ret_coord_ident_abs[cname] = (
                                        f"({ident} != 0)"
                                        if _rk[0] == "bool" else ident)
                                    continue
                                if svar not in layout:
                                    continue
                                slot, off, nb = layout[svar]
                                ident = "_ret_pre_" + _slot_ident(svar)
                                if ident in planned_ret_pre_names:
                                    continue
                                planned_ret_pre_names.add(ident)
                                rd = slot_read_expr(target_addr, slot, off, nb)
                                planned_ret_pre_reads.append(
                                    f"    uint256 {ident} = {rd};")
                                ret_coord_ident_abs[cname] = (
                                    f"({ident} != 0)"
                                    if _rk[0] == "bool" else ident)
                planned_ret_asserts = []
                for idx, rk, vn in plan:
                    label_var = (RETURN_VAR if idx is None
                                 else f"{RETURN_VAR}.{idx}")
                    for t in holds[idx]:
                        a = return_rung_assertions(
                            t, rk, vn, f"{label_var}: {t}",
                            ret_coord_ident_abs, r2_terms)
                        if a is None:
                            ret_skipped.append(
                                f"{label_var}: {t} (rung shape not renderable "
                                f"for its declared type)")
                            continue
                        planned_ret_asserts += a
                ret_asserts += planned_ret_asserts
                if planned_ret_asserts:
                    ret_pre_reads += planned_ret_pre_reads
                if not ret_asserts:
                    # Every HOLDS rung was unrenderable, so the binding buys
                    # nothing and would leave an unused local (a solc warning
                    # on a test nobody asked to be noisy). Put the call back.
                    new_call, _ = rewrite_call_args(call_line, unit, repl)
        if why is not None:
            ret_skipped.append(f"all return rungs DROPPED: {why}")

    # --- the oracle: post-state --------------------------------------------
    pre_reads, post_reads = list(ret_pre_reads), []
    asserts, oracle_skipped = [], []
    # RUNGS THAT SAY THE STATE CHANGED, kept apart from the rest because they
    # are emitted under a condition and the header has to report them as such.
    guarded, guard_notes = [], []
    okvar = "_put_ok"
    while any(okvar in ln for ln in body):
        okvar += "_"
    seen_vars = []
    r2_state_pre_names = set()

    def materialize_r2_state_coord(cname):
        if cname in coord_ident_abs:
            return True
        if not cname.startswith("state."):
            return True
        svar = cname[len("state."):]
        mname, slot_keys, slot_tail = parse_slot_name(svar)
        if mname is not None:
            mkey = mname + slot_tail
            if not maps or mkey not in maps:
                oracle_skipped.append(
                    f"{cname} (`{mname}` is not a mapping solc's layout "
                    "reports with a scalar value, so the R2 endpoint cannot "
                    "be read before the call)")
                return False
            mslot, _ktype, vnb, voff, _mb, _mm = maps[mkey]
            nlev = 1 if isinstance(_ktype, str) else len(_ktype)
            if len(slot_keys) != nlev:
                oracle_skipped.append(
                    f"{cname} (`{mname}` is a {nlev}-level store but the "
                    f"name gives {len(slot_keys)} key(s))")
                return False
            kexprs, kerr = [], None
            for kn in slot_keys:
                ke, err = slot_key_expr(kn, key_expr_of)
                if err is not None:
                    kerr = err
                    break
                kexprs.append(ke)
            if kerr is not None:
                oracle_skipped.append(f"{cname} ({kerr})")
                return False
            ident = "_pre_" + _slot_ident(svar)
            if ident not in r2_state_pre_names:
                r2_state_pre_names.add(ident)
                rd = slot_read_expr_at(
                    target_addr, map_slot_expr(kexprs, mslot), voff, vnb)
                pre_reads.append(f"    uint256 {ident} = {rd};")
            coord_ident[cname] = ident
            coord_ident_abs[cname] = ident
            return True
        if svar not in layout:
            oracle_skipped.append(
                f"{cname} (no storage slot: solc's layout does not list it, "
                "so the R2 endpoint cannot be read before the call)")
            return False
        slot, off, nb = layout[svar]
        ident = "_pre_" + _slot_ident(svar)
        if ident not in r2_state_pre_names:
            r2_state_pre_names.add(ident)
            rd = slot_read_expr(target_addr, slot, off, nb)
            pre_reads.append(f"    uint256 {ident} = {rd};")
        coord_ident[cname] = ident
        coord_ident_abs[cname] = ident
        return True

    def materialize_r2_state_terms(text):
        ok = True
        for spelling in post_rung_term_spellings(text):
            term = (r2_terms or {}).get(spelling)
            for cname in r2_term_coord_names(term):
                ok = materialize_r2_state_coord(cname) and ok
        return ok

    # ---- THE ANTICHAIN. Only the rungs nothing else entails are rendered ----
    #
    # `assertGe` beside `assertGt` on the same pair detects exactly what the
    # `assertGt` detects alone, so the pair is one oracle reported as two. The
    # dropped rows are recorded as IMPLIED, never as SKIPPED: one of those two
    # words means oracle was lost and wants fixing, the other means it is still
    # there in a sharper form, and swapping them makes a healthy pipeline read
    # as broken or a broken one as healthy.
    ladder_rows, implied_rows = antichain(ladder_rows, call_is_revert_tolerant)
    oracle_implied = [f"{v}: {t} (entailed by a stronger rung that also HOLDS "
                      f"on {v}, so asserting it detects nothing the stronger "
                      f"one misses)" for v, t, _d in implied_rows]
    for var, text, verdict in ladder_rows:
        # `return` AND `return.<k>`. Skipping only the bare name filed every
        # tuple MEMBER row as a state variable with no storage slot -- a wrong
        # LABEL on a right value, and the same class of defect as publishing a
        # tuple member as `state_written_value_unavailable`. Neither has a slot
        # BY DESIGN; they are not state at all.
        if var == RETURN_VAR or var.startswith(RETURN_VAR + "."):
            continue
        if verdict != "HOLDS":
            continue
        # ---- A MAPPING SLOT, `m[k]` ----------------------------------------
        #
        # Not in `layout` and never will be: the number solc reports for a
        # mapping is the `p` that goes into the hash, not a word holding a
        # value. It gets its own table and its own address arithmetic.
        #
        # WHAT A `post == pre` ON A SLOT IS. When the unit does not touch that
        # entry the rung holds, and the assertion it renders is a FRAME
        # CONDITION -- "this call leaves that balance alone" -- which is a real
        # post-condition rather than a restatement of the input. It is weaker
        # than a delta rung and the header below says which is which, so a
        # reader is never left to infer strength from the fact that something
        # was asserted.
        mname, slot_keys, slot_tail = parse_slot_name(var)
        if mname is not None:
            kname = ", ".join(slot_keys)
            mkey = mname + slot_tail
            if not maps or mkey not in maps:
                oracle_skipped.append(
                    f"{var} (`{mname}` is not a mapping solc's layout reports "
                    f"with a value-type key and a scalar value, so the slot "
                    f"address cannot be computed; a guessed one would read a "
                    f"word nothing wrote)")
                continue
            mslot, _ktype, vnb, voff, _mb, _mm = maps[mkey]
            # Same depth check as the entry-state pin above, for the same
            # reason: a name with the wrong number of keys reads a word nothing
            # wrote, and `post == pre` over it is green and meaningless.
            nlev = 1 if isinstance(_ktype, str) else len(_ktype)
            if len(slot_keys) != nlev:
                oracle_skipped.append(
                    f"{var} (`{mname}` is a {nlev}-level store but the name "
                    f"gives {len(slot_keys)} key(s); a name with the wrong "
                    f"depth reads a word nothing wrote)")
                continue
            # SAME decision as the entry-state pin above, through the same
            # function: the two used to answer differently, and the WRITING side
            # was the permissive one. See `slot_key_expr`.
            kexprs, kerr = [], None
            for kn in slot_keys:
                ke, err = slot_key_expr(kn, key_expr_of)
                if err is not None:
                    kerr = err
                    break
                kexprs.append(ke)
            if kerr is not None:
                oracle_skipped.append(f"{var} ({kerr})")
                continue
            kexpr = kexprs
            ident = _slot_ident(var)
            if var not in seen_vars:
                seen_vars.append(var)
                rd = slot_read_expr_at(
                    target_addr, map_slot_expr(kexpr, mslot), voff, vnb)
                pre_reads.append(f"    uint256 _pre_{ident} = {rd};")
                post_reads.append(f"    uint256 _post_{ident} = {rd};")
            coord_ident["state." + var] = "_pre_" + ident
            coord_ident_abs["state." + var] = "_pre_" + ident
            if not materialize_r2_state_terms(text):
                continue
            # GUARDED, not dropped. See the block comment at `okvar`.
            _chg = call_is_revert_tolerant and rung_asserts_a_change(text)
            a = rung_assertions(text, f"_pre_{ident}", f"_post_{ident}",
                                oracle_label_prefix + f"{var}: {text}",
                                coord_ident,
                                coord_ident_abs, r2_terms)
            if a is None:
                oracle_skipped.append(f"{var}: {text} (rung shape not rendered)")
                continue
            if _chg:
                guarded += a
                guard_notes.append(f"{var}: {text}")
            else:
                asserts += a
            continue
        if var not in layout:
            msg = (f"{var} (no storage slot: solc's layout does not list it, "
                   f"so it is a constant/immutable -- a rung over it is a "
                   f"compile-time tautology, not an oracle)")
            if msg not in oracle_skipped:
                oracle_skipped.append(msg)
            continue
        slot, off, nb = layout[var]
        if var not in seen_vars:
            seen_vars.append(var)
            rd = slot_read_expr(target_addr, slot, off, nb)
            pre_reads.append(f"    uint256 _pre_{var.lstrip('_')} = {rd};")
            post_reads.append(f"    uint256 _post_{var.lstrip('_')} = {rd};")
        coord_ident["state." + var] = "_pre_" + _slot_ident(var)
        coord_ident_abs["state." + var] = "_pre_" + _slot_ident(var)
        if not materialize_r2_state_terms(text):
            continue
        # GUARDED, not dropped. See the block comment at `okvar`.
        _chg = call_is_revert_tolerant and rung_asserts_a_change(text)
        a = rung_assertions(text, f"_pre_{var.lstrip('_')}",
                            f"_post_{var.lstrip('_')}",
                            oracle_label_prefix + f"{var}: {text}",
                            coord_ident, coord_ident_abs, r2_terms)
        if a is None:
            oracle_skipped.append(f"{var}: {text} (rung shape not rendered)")
            continue
        if _chg:
            guarded += a
            guard_notes.append(f"{var}: {text}")
        else:
            asserts += a
    # ---- THE CALL HAS TO CARRY THE FLAG, OR THE GUARD IS NOT A GUARD -------
    #
    # Only a call ending in `catch {}` is rewritten. Anything else -- a catch
    # with a body, a shape this emitter did not write -- keeps the OLD DROP and
    # says so: a flag left permanently true would make every guarded assertion
    # unconditional, which is the red test the drop rule exists to prevent.
    # ---- A REVERTING PATH CARRIES THE FIRST LAYER ALONE (§oracle) ----
    #
    # Every layer-2/3 rung goes, and it goes NAMED: dropping them silently would
    # turn "this oracle was about an unobservable moment" into "this path has
    # nothing assertable", which are different facts with different repairs.
    # What replaces them is not nothing -- see the `assertFalse` below. Today a
    # reverting path is emitted as `try c0.f() {} catch {}`, an oracle that
    # cannot fail whatever the contract does; asserting the revert turns a
    # mutant that stops reverting from invisible into RED.
    existing_expect_revert = any("vm.expectRevert()" in ln
                                 for ln in body[:call_i])
    low_level_exit_asserted = low_level_value_gate_asserts_exit(
        list(body[:call_i]) + [new_call] + list(body[call_i + 1:]),
        call_i, new_call)
    revert_layer1 = bool(rollback_exit) or exit_kind == "revert"
    catch_assert_revert = (
        revert_layer1 and new_call.rstrip().endswith("catch {}"))
    insert_expect_revert = (
        revert_layer1 and not catch_assert_revert and
        not existing_expect_revert and not low_level_exit_asserted)
    if revert_layer1:
        n_dropped = len(asserts) + len(guarded) + len(ret_asserts)
        if n_dropped:
            why_drop = ROLLBACK_UNOBSERVABLE if rollback_exit else REVERT_UNOBSERVABLE
            oracle_skipped.append(
                f"{n_dropped} layer-2/3 rung(s) DROPPED ({why_drop})")
        asserts, guarded, guard_notes, ret_asserts = [], [], [], []
    if guarded or catch_assert_revert:
        if new_call.rstrip().endswith("catch {}"):
            new_call = (new_call.rstrip()[:-len("catch {}")]
                        + "catch { " + okvar + " = false; }")
        else:
            for n in guard_notes:
                oracle_skipped.append(
                    f"{n} ({CHANGE_UNDER_CATCH}; and the guard could NOT be "
                    f"applied: this call does not end in `catch {{}}`, so there "
                    f"is nowhere to clear the flag, and an always-true flag "
                    f"would make the assertion unconditional)")
            guarded, guard_notes = [], []
    oracle_skipped += ret_skipped

    # ---- ONE PATH, SEVERAL CERTIFIED BOXES: THE NAME HAS TO SAY WHICH -------
    #
    # `--max-region-pieces > 1` lets stage 2 certify a path as a UNION of boxes,
    # each by its own query. Those are several regions and therefore several
    # PUTs, and every one of them is about the SAME enc -- so a name built from
    # the enc alone collides. The file name collides hardest: `newc` decides
    # `test/<newc>.t.sol`, so piece 4 would OVERWRITE piece 3 and the sweep
    # would report two emissions and leave one file.
    #
    # ⚠ The function name is NOT a Solidity-level collision -- two different
    # test contracts may each declare `test_put_X_u_path12` and forge compiles
    # both. It is an ACCOUNTING collision: put_all's B gate keys its verdict
    # table on the test name across every suite, so the two pieces would share
    # one cell and whichever forge reported last would decide both.
    #
    # So the label goes on BOTH, from ONE variable. Empty by default, which
    # reproduces every existing name byte for byte.
    fname = f"test_put_{contract}_{unit}_path{enc}{piece_label}"
    sig_txt = ", ".join(f"{t} {n}" for t, n in sig)

    out = []
    out.append("")
    out.append(f"  // ===================== PUT (stage 4) "
               f"=====================")
    out.append(f"  // claim: {path_function}:path:{enc}   depth={depth_}")
    if cell:
        out.append(f"  // CELL {cell[0]} -- {cell[1]}")
    if unwind:
        out.append(f"  // LADDER WIDENED: {' '.join(unwind)}")
        out.append(f"  // These apply to the ASSERTION LADDER run only. The "
                   f"emit run that")
        out.append(f"  // produced the preamble and the concrete case below ran "
                   f"BEFORE any")
        out.append(f"  // loop had been named, so it did not carry them -- the "
                   f"oracle and the")
        out.append(f"  // body come from two runs at different symex bounds.")
    out.append(f"  // CERTIFIED REGION (stage 2), certified by an independent")
    out.append(f"  // `assume(box); assert(tr == pi)` query, not by the "
               f"subtraction:")
    for n, (lo, hi) in sorted(region.items()):
        hs = sorted(holes.get(n, ()))
        out.append(f"  //   {n} in [{lo}, {hi}]"
                   + ("  \\ {" + ", ".join(str(h) for h in hs) + "}"
                      if hs else ""))
    # A pin is printed with WHETHER THE TEST ESTABLISHES IT, because those are
    # two different tests. `PIN state.owner == 0` alone reads as a precondition
    # the test satisfies; it only is one if a `vm.store` above put the contract
    # in that state. The unestablished ones are the interesting line -- they say
    # the test runs beside the certified slice rather than inside it.
    established = {s.split(" := ", 1)[0] for s in stored}
    for n, v in sorted(pins.items()):
        if n in established:
            out.append(f"  //   PIN {n} == {v}   [ESTABLISHED by vm.store "
                       f"below]")
        elif n.startswith("state."):
            out.append(f"  //   PIN {n} == {v}   [NOT ESTABLISHED -- see the "
                       f"dropped-bound line]")
        else:
            out.append(f"  //   PIN {n} == {v}")
    # ---- WHERE THE WIDTH CAME FROM -----------------------------------------
    #
    # The work order: a width must come from a boundary the LADDER measured or
    # from what is left after subtracting the sibling paths, and a width that
    # rests only on a neighbourhood probe (±1) does not count. The region
    # string cannot say which -- it is `name in [lo, hi]` -- so the arm's
    # switches are printed here.
    #
    # ⛔ ROW GRANULARITY, SAID OUT LOUD. These switches describe the whole
    # region of this row, not one coordinate. A reader who took them as a
    # per-bound trace would be reading a precision that was never measured,
    # and that misreading is likelier than no information at all.
    if derived_by:
        _ladder = bool(derived_by.get("geometric_bracket"))
        _subtract = bool(derived_by.get("sibling_subtraction"))
        _probe_only = (not _ladder) and (not _subtract) and bool(
            derived_by.get("level0") or derived_by.get("level0_perturb")
            or derived_by.get("level0_points"))
        out.append("  // WIDTH PROVENANCE (stage-2 switches for this ROW, not "
                   "per coordinate):")
        for k in sorted(derived_by):
            out.append(f"  //   {k} = {derived_by[k]}")
        if _probe_only:
            out.append("  // ⚠ NO LADDER AND NO SUBTRACTION RAN FOR THIS ROW. "
                       "Every interval below rests on")
            out.append("  // a neighbourhood probe, which the work order does "
                       "NOT accept as a width: a")
            out.append("  // probe shows some nearby value also walks the "
                       "path, not where the path stops.")
            out.append("  // The region is still CERTIFIED -- an independent "
                       "query admitted it -- but its")
            out.append("  // WIDTH is not evidence of a measured boundary.")
        elif _ladder or _subtract:
            out.append("  // Width sources that ran: "
                       + ", ".join(s for s, on in
                                   (("the stage-2 geometric ladder's boundary "
                                     "probes",
                                     _ladder),
                                    ("subtraction of the sibling paths",
                                     _subtract))
                                   if on))
    out.append(f"  // Arguments the region does NOT bound keep the "
               f"counterexample's own")
    out.append(f"  // literal: the region is a statement about THAT slice, "
               f"and generalising")
    out.append(f"  // over them would be a claim the certification never "
               f"made.")
    if lifted:
        out.append(f"  // FUZZ COORDINATES: {', '.join(lifted)}")
    else:
        out.append(f"  // NO FUZZ COORDINATE: every certified coordinate is "
                   f"entry state or an")
        out.append(f"  // unliftable type, so this PUT is a single "
                   f"deterministic point of the")
        out.append(f"  // region rather than a fuzz test over it.")
    if revert_layer1:
        out.append(f"  // ORACLE: the FIRST LAYER ONLY, and that is the rule "
                   f"rather than a shortfall.")
        if rollback_exit:
            out.append(f"  // This path exits through a ROLLBACK revert. A "
                       f"revert restores storage, so")
            out.append(f"  // every before/after comparison is `post == pre` "
                       f"on the chain whatever the")
            out.append(f"  // contract does, and the ladder's own verdicts "
                       f"were read at the moment")
            out.append(f"  // BETWEEN the write and the rollback -- which no "
                       f"test can observe. They")
        else:
            out.append(f"  // Stage 1 reports this complete path exits through "
                       f"a revert. Post-state")
            out.append(f"  // and return-value rungs are not observable on the "
                       f"chain for this path. They")
        out.append(f"  // are dropped and counted below. What is asserted "
                   f"instead is the exit")
        out.append(f"  // itself: the call MUST fail. That is a real oracle -- "
                   f"it goes RED on a")
        out.append(f"  // contract that stops reverting -- and it replaces a "
                   f"`try {{}} catch {{}}`")
        out.append(f"  // body that could not fail whatever happened.")
    elif asserts or ret_asserts:
        out.append(f"  // ORACLE: {len(asserts) + len(ret_asserts)} "
                   f"assertion(s) from the surviving (HOLDS) rungs of")
        out.append(f"  // --path-cov-assert -- {len(asserts)} over POST-STATE, "
                   f"read through vm.load at")
        out.append(f"  // the slot solc reports, and {len(ret_asserts)} over "
                   f"the unit's OWN RETURN")
        out.append(f"  // VALUE, bound from the call below. A return rung is "
                   f"emitted only when the")
        out.append(f"  // ladder's `retlive` witness was REFUTED, i.e. only "
                   f"when this path was")
        out.append(f"  // shown to reach a return at all.")
    else:
        # ---- DO NOT PROMISE AN ASSERTION THE BODY DOES NOT CARRY ----
        #
        # This line used to be unconditional, and it was FALSE exactly when it
        # mattered. "the exit-kind expectation below is still an assertion" is
        # only true when the emitter wrote the call BARE (`[asserted] path exits
        # normally`), armed `vm.expectRevert()`, or emitted the non-payable
        # value-gate's `assertFalse`. When the exit was not confirmed the
        # emitter writes `[revert-tolerant] outcome not asserted` and wraps the
        # call in `try {} catch {}` -- which asserts nothing at all.
        #
        # MEASURED on the aqua round-trip, three files for three:
        #   dock_put12, push_put14, safeBalances_put14
        # each carry "ORACLE: none emitted ... still an assertion" over a
        # try/catch body with ZERO assert statements. The one file that does not
        # carry the line (rawBalances_put7) is the one that does assert. The
        # correlation is exact, which is what makes it a wrong claim rather than
        # a stale comment.
        #
        # This matters more than a red test would. A red test announces itself;
        # a GREEN test whose header says it asserts the exit kind, over a body
        # that tolerates any outcome, is a gate that can never fire while
        # reading exactly like one that can.
        headline, why = no_oracle_reason(ladder_rows)
        if insert_expect_revert or exit_kind_asserted(
                list(body[:call_i]) + [new_call] + list(body[call_i + 1:])):
            out.append(f"  // ORACLE: none emitted -- {headline}:")
            out.append(f"  // {why}.")
            out.append(f"  // The exit-kind expectation below is still an "
                       f"assertion.")
        else:
            out.append(f"  // ORACLE: NONE, AND NEITHER IS THE EXIT KIND. "
                       f"{headline}:")
            out.append(f"  // {why},")
            out.append(f"  // and the emitter could not confirm this path's "
                       f"exit, so the call below")
            out.append(f"  // is REVERT-TOLERANT (`try {{}} catch {{}}`). This "
                       f"PUT walks the path and")
            out.append(f"  // checks NOTHING: it is GREEN whatever the call "
                       f"does, including")
            out.append(f"  // reverting. It is a reachability witness, not a "
                       f"test.")
    if guarded:
        out.append(f"  // CONDITIONAL: {len(guarded)} further assertion(s) say "
                   f"the state CHANGED.")
        out.append(f"  // A revert leaves storage untouched, and this call is "
                   f"revert-tolerant because")
        out.append(f"  // the exit kind could not be confirmed -- so they are "
                   f"emitted under `if ({okvar})`,")
        out.append(f"  // which is false exactly when the call reverted. Sound "
                   f"because every input of")
        out.append(f"  // the certified region walks this path: one that did "
                   f"not revert walked it.")
        out.append(f"  // ⚠ WHETHER THE GUARD'S TRUE BRANCH IS EVER TAKEN IS "
                   f"NOT MEASURED HERE. If it")
        out.append(f"  // never is, these assertions are green and say "
                   f"nothing.")
        for s in guard_notes:
            out.append(f"  //   rung CONDITIONAL: {s}")
    for s in oracle_skipped:
        out.append(f"  //   rung DROPPED: {s}")
    for s in oracle_implied:
        out.append(f"  //   rung IMPLIED (not lost, not asserted twice): {s}")
    for s in state_skipped:
        out.append(f"  //   entry-state bound DROPPED: {s}")
    for s in env_unchecked:
        out.append(f"  //   environment NOT CHECKED: {s}")
    for s in env_established:
        # Printed on the test itself, not only in the driver log. A reader of
        # the emitted file has to be able to see that the sender it runs under
        # was CHOSEN to satisfy the region -- otherwise the rewritten prank
        # looks like part of the reconstructed counterexample, which is the one
        # thing it is not.
        out.append(f"  //   environment ESTABLISHED: {s}")
    out.append(f"  function {fname}({sig_txt}) public {{")
    out += pre_lines
    # ---- WHAT MAY BE INSERTED BEFORE THE CALL, AND WHAT MAY NOT -----------
    #
    # `body[:call_i]` is the reconstructed sequence that establishes the entry
    # state, and it is kept verbatim -- but it must NOT be treated as one
    # block. Its TAIL is the emitter's own per-call decoration, and two of
    # those cheatcodes bind to THE NEXT CALL specifically:
    # `vm.expectRevert()` (foundry.cpp:3030) and `vm.prank(...)`
    # (foundry.cpp:3011-3016, whose own comment says it "sets the sender for
    # the NEXT call ONLY, so it must be the last cheatcode before the call").
    # Splicing the entry-state stores and the oracle's pre-reads between them
    # and the call would retarget an expectRevert at a cheatcode and silently
    # drop the sender pin -- turning the R0 exit-kind expectation this route
    # exists to preserve into a different assertion.
    #
    # So the tail (the contiguous run of comments and `vm.*` statements
    # directly above the call) is re-attached immediately above the call, and
    # everything the PUT adds goes in front of it.
    #
    # The walk starts at the statement's FIRST line, not at the line that names
    # the unit. For the low-level value-gate shape those differ, and starting at
    # the second one puts the stores and the pre-reads BETWEEN the two halves of
    # one statement -- a file that does not compile. See `statement_start`.
    head_end = statement_start(body, call_i)
    while head_end > 0:
        prev = body[head_end - 1].strip()
        if prev.startswith("//") or prev.startswith("vm."):
            head_end -= 1
            continue
        break
    for ln in body[:head_end]:
        out.append(ln)
    if store_lines:
        out.append("    // entry state the certified region names, "
                   "ESTABLISHED (not assumed):")
        out.append("    //   " + "; ".join(stored))
        out += store_lines
    if pre_reads:
        out.append("    // pre-state for the oracle, at this path's own entry")
        out += pre_reads
    for ln in body[head_end:call_i]:
        if insert_expect_revert and "[asserted] path exits normally" in ln:
            out.append("    // [asserted] path exits through revert; "
                       "vm.expectRevert arms the call")
            continue
        out.append(ln)
    if guarded or catch_assert_revert:
        out.append(f"    bool {okvar} = true;")
    if insert_expect_revert:
        out.append("    vm.expectRevert();")
    out.append(new_call)
    # NOT `if post_reads:`. That guard was equivalent while every assertion
    # came from a state rung -- a var with no post-read produced no assert
    # either -- and it stops being equivalent the moment the RETURN VALUE can
    # be an oracle on its own: a unit whose only surviving rung is a return
    # rung has no post-read at all, and the guard would have dropped its
    # assertions while the header above still announced them.
    out += post_reads
    out += asserts
    if guarded:
        out.append(f"    if ({okvar}) {{")
        out += guarded
        out.append("    }")
    if catch_assert_revert:
        # LAYER 1, and it is the whole oracle for this path. `okvar` is false
        # exactly when the call reverted, and the certified region says every
        # input of it walks THIS path, whose exit is a revert -- so a call that
        # succeeds is a contract that no longer does what was certified.
        out.append(
            f'    assertFalse({okvar}, "path enc={enc}{piece_label} exits '
            f'through a REVERT: the call must fail on the unmodified '
            f'contract");')
    out += ret_asserts
    for ln in body[call_i + 1:]:
        out.append(ln)
    out.append("  }")
    exit_kind_asserts = 1 if (
        catch_assert_revert or insert_expect_revert or
        (revert_layer1 and existing_expect_revert)) else 0
    original_call_body = list(body[:call_i]) + [new_call] + list(
        body[call_i + 1:])
    if low_level_exit_asserted:
        exit_kind_asserts += 1
    stats = {"fuzz_params": len(sig), "lifted": lifted,
             # COUNTED, and counted separately. A conditional assertion is an
             # assertion the test carries, so it belongs in the total; it is a
             # WEAKER one, so a reader who cannot see how many are conditional
             # is reading a strength the file does not have.
             # ⛔ THE ROLLBACK PATH'S ONE ASSERTION IS AN ORACLE AND IS
             # COUNTED. It reported `oracle asserts : 0` on a PUT whose body
             # carries `assertFalse(_put_ok, "... must fail ...")` -- an
             # assertion a mutant that stops reverting turns RED. A zero there
             # reads as "this PUT checks nothing", which is exactly the
             # conclusion the layer-1 rule exists to make false, and it would
             # have sent the next reader looking for a bug that is not there.
             "asserts": (len(asserts) + len(ret_asserts) + len(guarded)
                         + exit_kind_asserts),
             "state_asserts": len(asserts) + len(guarded),
             "guarded_asserts": len(guarded),
             "exit_kind_asserts": exit_kind_asserts,
             # Recorded so the B table can say WHY a row's oracle is one line:
             # a rollback path is a measurement, not a missing feature, and the
             # two must not read alike.
             "rollback_exit": bool(rollback_exit),
             "exit_kind": exit_kind,
             "return_asserts": len(ret_asserts),
             "oracle_skipped": oracle_skipped,
             # SEPARATE KEY, not folded into `oracle_skipped`. An implied rung
             # is oracle still fully present in a stronger form; a skipped one
             # is oracle that was lost. One number wants investigating and the
             # other does not.
             "oracle_implied": oracle_implied,
             "state_stored": stored, "state_skipped": state_skipped,
             "env_unchecked": env_unchecked}
    return out, stats


def no_oracle_reason(ladder_rows):
    """(headline, detail) for a PUT that renders no assertion.

    TWO REASONS, AND THEY WERE BEING REPORTED AS ONE. The header said "Not one
    rung HOLDS over the certified region" whenever zero assertions were
    RENDERED, and rendering is downstream of holding: a rung that HOLDS is
    dropped when its variable has no storage slot (a constant/immutable, where
    the assertion would be a compile-time tautology), when its shape has no
    renderer, or when a mapping key cannot be expressed in the test.

    MEASURED on aqua `dock` enc=12, which is what exposed it. The ladder came
    back 3 HOLDS / 3 REFUTED --

      _DOCKED: post == pre  HOLDS      _DOCKED: post != pre  REFUTED
      _DOCKED: post >= pre  HOLDS      _DOCKED: post > pre   REFUTED
      _DOCKED: post <= pre  HOLDS      _DOCKED: post < pre   REFUTED

    -- and all three HOLDS were then dropped because solc's layout does not
    list `_DOCKED`. The emitted file nonetheless said not one rung holds, which
    is false, and it is false in the direction that HIDES WORK THAT SUCCEEDED:
    "the prover found nothing" and "the prover found three things this emitter
    cannot render" call for opposite next actions -- more budget in the first
    case, a renderer in the second.

    That is the same defect this file just removed one layer up (a header
    claiming an assertion over a body that asserts nothing), found while
    verifying that fix. The `retlive` witness is excluded from the count on
    purpose: a HOLDS there means NO execution reached a return, so counting it
    as a rung that held would report the absence of a return value as a
    rendering failure.
    """
    held = [(v, t) for v, t, d in ladder_rows
            if d == "HOLDS"
            and not (v == RETURN_VAR and t.startswith(RETLIVE_PREFIX))]
    if held:
        # ⛔ THIS LINE DOES NOT SAY WHY, and an earlier draft did. It ended
        # "This is NOT 'the region supports no oracle'", which is a claim about
        # the REGION made from a count of RENDERINGS, and it is false whenever
        # the rungs were dropped for VACUITY rather than for a rendering gap.
        #
        # MEASURED on aqua safeBalances enc=14, in the run that emitted it:
        # `return.0: return == 0 HOLDS` AND `return.0: return != 0 HOLDS` --
        # both, which no real execution admits -- with the `retlive` witness
        # HOLDS, i.e. no execution of that path reaches a return at all. For
        # that file the region really does support no oracle, and the line
        # would have denied it. The per-rung reasons below distinguish the two
        # cases; a summary line cannot, so it does not try.
        return ("EVERY RUNG THAT HOLDS WAS DROPPED",
                f"{len(held)} rung(s) HOLD over the certified region and not "
                f"one could be rendered as an assertion. WHY differs per rung "
                f"and is on the `rung DROPPED` line(s) below -- read those "
                f"rather than this count")
    return ("NOT ONE RUNG HOLDS",
            "no candidate held over the certified region, so there is nothing "
            "to render")


def exit_kind_asserted(body_lines):
    """Does this PUT body actually assert anything about how the call exits?

    The emitter marks its own decision in the text it writes, and there are
    exactly three shapes that carry an assertion:

      * `// [asserted] path exits normally; a revert fails the test` -- the call
        is BARE, and its bareness IS the assertion;
      * `// [asserted] value sent to a NON-PAYABLE entry: the call must fail`,
        which is followed by an `assertFalse`;
      * `vm.expectRevert();` armed before the call.

    and exactly one that carries none:

      * `// [revert-tolerant] outcome not asserted` with the call wrapped in
        `try {} catch {}`.

    A separate function, and not two lines inlined at the one call site, because
    it has TWO answers and both have to be pinned. A predicate whose false arm
    is never exercised is indistinguishable from one that is hard-wired -- this
    workspace has shipped that shape before -- so `test_solidity_path_put.py`
    drives both directions rather than the one the current corpus happens to
    produce.
    """
    txt = "\n".join(body_lines)
    return "[asserted]" in txt or "vm.expectRevert()" in txt


def fixture_from_esbmc_args(extra):
    """The JSON passed through `--path-cov-fixture`, or None."""
    i = 0
    while i < len(extra):
        if extra[i] == "--path-cov-fixture" and i + 1 < len(extra):
            try:
                with open(extra[i + 1]) as f:
                    return json.load(f)
            except (OSError, ValueError):
                return None
            break
        i += 1
    return None


def _fixture_value(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    s = str(v).strip()
    if s.lower() == "true":
        return "1"
    if s.lower() == "false":
        return "0"
    return s


def _statement_end(lines, start):
    depth = 0
    for i in range(start, len(lines)):
        t = _strip_strings(lines[i])
        depth += t.count("(") - t.count(")")
        if ";" in t and depth <= 0:
            return i
    return start


def _replace_constructor_args(lines, start, args):
    end = _statement_end(lines, start)
    stmt = "\n".join(lines[start:end + 1])
    open_i = stmt.find("(")
    close_i = stmt.rfind(")")
    if open_i < 0 or close_i < open_i:
        return lines[start:end + 1]
    prefix = stmt[:open_i + 1]
    suffix = stmt[close_i:]
    return [prefix + ", ".join(args) + suffix]


def _fixture_foundry_args(fixture):
    foundry = fixture.get("foundry") or {}
    args = foundry.get("constructor_args")
    if args is None:
        return None
    if not isinstance(args, list):
        return None
    return [str(a) for a in args]


def _fixture_foundry_skip(fixture):
    foundry = fixture.get("foundry") or {}
    return bool(foundry.get("skip_constructor"))


def apply_foundry_fixture(lines, emitted, case, unit, contract, fixture, layout):
    """Mirror a path-cov fixture in the Foundry preamble.

    ESBMC's `--path-cov-fixture` may skip a constructor and install scalar
    state. If Stage 4 keeps Foundry's constructor-based `setUp`, the generated
    PUT is checked from a different entry state than the one ESBMC certified.
    """
    if not fixture or not fixture.get("skip_constructor"):
        return lines
    if fixture.get("contract") and fixture.get("contract") != contract:
        return lines
    body = emitted.lines[case[3][0] + 1:case[3][1]]
    call_i = find_unit_call(body, unit)
    inst = target_instance_for_call(body, call_i, unit)
    if inst is None:
        return lines
    rx = re.compile(r"^(\s*)" + re.escape(inst) + r"\s*=\s*new\s+"
                    + re.escape(contract) + r"\s*\(")
    out, i, replaced = [], 0, False
    while i < len(lines):
        m = rx.match(lines[i])
        if not replaced and m:
            indent = m.group(1)
            end = _statement_end(lines, i)
            replay_args = _fixture_foundry_args(fixture)
            if replay_args is not None:
                out.append(f"{indent}// path-cov fixture: ESBMC skipped the "
                           "constructor; Foundry replays a legal deployment")
                out += _replace_constructor_args(lines, i, replay_args)
            elif _fixture_foundry_skip(fixture):
                out.append(f"{indent}// path-cov fixture: constructor skipped "
                           "by ESBMC")
                out.append(f"{indent}address _esbmc_fixture_{inst} = "
                           "address(uint160(1337));")
                out.append(f"{indent}vm.etch(_esbmc_fixture_{inst}, "
                           f"type({contract}).runtimeCode);")
                out.append(f"{indent}{inst} = {contract}(_esbmc_fixture_{inst});")
            else:
                out.extend(lines[i:end + 1])
            for name, value in sorted((fixture.get("state") or {}).items()):
                v = name[6:] if name.startswith("state.") else name
                if not layout or v not in layout:
                    out.append(f"{indent}// path-cov fixture state `{name}` "
                               "not established: no scalar storage slot")
                    continue
                slot, off, nb = layout[v]
                val = _fixture_value(value)
                out += slot_write_lines(f"address({inst})", slot, off, nb, val,
                                        indent)
                out += slot_landing_check(f"address({inst})", slot, off, nb,
                                          val, f"path-cov fixture {name}",
                                          indent)
            i = end + 1
            replaced = True
            continue
        out.append(lines[i])
        i += 1
    return out


def assemble_put_source(emitted, case, puts, new_contract, fixture=None,
                        layout=None, contract=None, unit=None):
    """Insert PUT functions into the emitter's contract and rename safely."""
    cname, _cstart, cend = emitted.blocks[case[0]]
    lines = list(emitted.lines)
    inserted = []
    for put in puts:
        inserted += put
    lines[cend:cend] = inserted
    # The `test_cov_*` cases are the concrete replay source of truth, but they
    # are not part of the PUT deliverable. Keeping them in the assembled project
    # lets stale replay details fail compilation before forge can measure the
    # generated `test_put_*` row. Measured on st1inch disabled ERC20 entries:
    # the PUT call was repaired to `transfer(arg0,arg1)`, while the retained
    # concrete case still contained `transfer()` and killed the whole project.
    for _ci, _name, _claims, (fs, fe) in sorted(
            emitted.cases, key=lambda item: item[3][0], reverse=True):
        del lines[fs:fe + 1]
    if fixture is not None and contract is not None and unit is not None:
        lines = apply_foundry_fixture(lines, emitted, case, unit, contract,
                                      fixture, layout)
    source = "\n".join(lines) + "\n"
    source = source.replace(
        f"contract {cname} is Test", f"contract {new_contract} is Test")
    source = re.sub(r'from "\./', 'from "../src/', source)
    # Longest first because IERC20 is a prefix of IERC20Metadata.
    for mock in sorted(set(re.findall(r"ESBMCMock_(\w+)", source)),
                       key=len, reverse=True):
        source = re.sub(
            r"ESBMCMock_" + re.escape(mock) + r"\b",
            f"ESBMCMock_{mock}_{new_contract}", source)
    return source


def run_forge_r2_prefilter(project, workdir, emitted, case, contract, unit,
                           enc, depth_, path_function, region, holes, pins,
                           params, layout, maps, specs, r2_terms, cell,
                           derived_by, timeout, fuzz_runs, candidate_budget,
                           fixture=None, log=print):
    """Refute R2 candidates with one Forge run; never produce proof verdicts."""
    candidates = r2_candidates(specs)
    verdicts = {candidate["key"]: "NOT-RUN" for candidate in candidates}
    evidence = {
        candidate["key"]: {
            "key": candidate["key"], "var": candidate["var"],
            "text": candidate["text"], "verdict": "NOT-RUN",
            "reason": "candidate probe was not renderable",
        }
        for candidate in candidates
    }
    if not candidates:
        return verdicts, skipped_forge_r2_evidence(
            specs, candidate_budget, "no R2 candidate was proposed", fuzz_runs)

    puts = []
    labels = {}
    keys_by_test = {}
    selected = candidates[:candidate_budget]
    marker_namespace = secrets.token_hex(16)
    for candidate in candidates[candidate_budget:]:
        evidence[candidate["key"]]["reason"] = (
            f"outside the {candidate_budget}-candidate Forge budget; retained "
            "for ESBMC")
    for index, candidate in enumerate(selected):
        marker = f"VERIPUT_CANDIDATE_{marker_namespace}_{index}"
        piece = f"fz{index}"
        probe, stats = build_put(
            contract, unit, enc, depth_, path_function, region, holes, pins,
            params, emitted, case, layout,
            [(candidate["var"], candidate["text"], "HOLDS")], [],
            cell=cell, rettypes=None, maps=maps, piece_label=piece,
            derived_by=derived_by, rollback_exit=False, r2_terms=r2_terms,
            oracle_label_prefix=marker + " ")
        if probe is None or not stats or stats.get("state_asserts", 0) == 0:
            continue
        if marker not in "\n".join(probe):
            continue
        test = f"test_put_{contract}_{unit}_path{enc}{piece}"
        puts.append(probe)
        labels[test] = f"{marker} {candidate['var']}: {candidate['text']}"
        keys_by_test[test] = candidate["key"]
        evidence[candidate["key"]].update({
            "test": test, "marker": marker,
            "reason": "expected Forge test was absent",
        })

    if not puts:
        skipped = skipped_forge_r2_evidence(
            specs, candidate_budget,
            "no candidate could be rendered as a labeled probe", fuzz_runs)
        skipped["candidates"] = [evidence[candidate["key"]]
                                 for candidate in candidates]
        return verdicts, skipped

    base_contract = emitted.blocks[case[0]][0]
    probe_contract = f"{base_contract}_{contract}_{unit}_put{enc}_FuzzR2"
    source = assemble_put_source(emitted, case, puts, probe_contract, fixture,
                                 layout, contract, unit)
    artifact = os.path.join(workdir, "fuzz-r2-prefilter.t.sol")
    with open(artifact, "w") as stream:
        stream.write(source)
    dest = os.path.join(
        project, "test",
        f"{probe_contract}_{os.getpid()}_{time.time_ns()}.t.sol")
    with open(dest, "w") as stream:
        stream.write(source)

    match_test = f"^test_put_{re.escape(contract)}_{re.escape(unit)}_path{enc}fz"
    command = [
        "forge", "test", "--json", "--match-contract",
        f"^{re.escape(probe_contract)}$", "--match-test", match_test,
        "--fuzz-runs", str(fuzz_runs),
    ]
    stdout, stderr, timed_out, returncode = "", "", False, None
    try:
        proc = subprocess.Popen(
            command, cwd=project, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            stdout, stderr = proc.communicate()
        returncode = proc.returncode
    except OSError as exc:
        stderr = str(exc)
    finally:
        try:
            os.unlink(dest)
        except FileNotFoundError:
            pass

    with open(os.path.join(workdir, "fuzz-r2-prefilter.json"), "w") as stream:
        stream.write(stdout)
    with open(os.path.join(workdir, "fuzz-r2-prefilter.stderr"), "w") as stream:
        stream.write(stderr)

    raw_results = forge_json_test_results(stdout)
    classified = ({} if timed_out else
                  fuzz_prefilter_json_verdicts(labels, stdout))
    for test, verdict in classified.items():
        key = keys_by_test[test]
        verdicts[key] = verdict
        result = raw_results.get(test)
        if result is None:
            reason = "expected Forge test was absent"
        elif verdict == "NOT-REFUTED":
            reason = "Forge reported Success; this is not proof"
        else:
            reason = str(result.get("reason") or result.get("status") or "")
        evidence[key].update({"verdict": verdict, "reason": reason})
    if timed_out:
        for test, key in keys_by_test.items():
            evidence[key]["reason"] = (
                f"Forge timed out after {timeout}s before a candidate verdict")
    refuted = sum(verdict == "REFUTED" for verdict in verdicts.values())
    not_run = sum(verdict == "NOT-RUN" for verdict in verdicts.values())
    executed = len(verdicts) - not_run
    log(f"[put]   Forge R2 prefilter: {len(candidates)} candidate(s), "
        f"{len(labels)} rendered, {refuted} REFUTED by a labeled concrete "
        f"failure, {not_run} NOT-RUN; passes prove nothing")
    return verdicts, {
        "requested": len(candidates), "selected": len(selected),
        "candidate_budget": candidate_budget, "rendered": len(labels),
        "ran": executed,
        "refuted": refuted, "not_run": not_run,
        "not_refuted": sum(v == "NOT-REFUTED" for v in verdicts.values()),
        "timed_out": timed_out, "returncode": returncode,
        "fuzz_runs": fuzz_runs, "command": command,
        "candidates": [evidence[candidate["key"]]
                       for candidate in candidates],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def binary_identity(esbmc_path):
    """Who produced this put.json. Three fields, and each is load-bearing.

    Byte-for-byte the shape `pathcov_collect.py::binary_identity()` records,
    so one reader can check a runs.jsonl row and a put.json with one rule.
    `head` is the commit the tree was on; `binaryMtime` is the executable's own
    timestamp and is the ONLY field that distinguishes two builds sharing a
    HEAD; `srcDirty` says the commit named does not identify the binary at all.

    MEASURED, and the reason this exists: the pieces_corpus arm reported
    `B = 7 of 10` where one of the seven was emitted on Aug 3 by a build the
    current tree no longer reproduces -- the emit run finds no claim for that
    path. Nothing in put.json could have shown that.
    """
    def _sh(args):
        try:
            return subprocess.run(
                args, capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": (int(os.stat(esbmc_path).st_mtime)
                        if os.path.exists(esbmc_path) else 0),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esbmc", default="esbmc")
    ap.add_argument("--sol", required=True)
    ap.add_argument("--ast", default=None)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--path-function", default=None,
                    help="exact mangled path_function certified by stage 2. "
                         "Legacy callers may omit it only when unit+enc "
                         "selects one unique path function in the fresh "
                         "emission report")
    ap.add_argument("--exit-kind", default=None,
                    choices=("normal", "revert", "unknown"),
                    help="Stage-1 path report exit_kind for this path. A "
                         "revert path has no observable post-state/return "
                         "oracle on chain, so the PUT asserts the exit kind "
                         "instead of shipping a try/catch that can never fail")
    ap.add_argument("--enc", type=int, required=True)
    ap.add_argument("--depth", type=int, default=None,
                    help="the path's decision depth. Omit to read it from the "
                         "step-1 report. It is NOT optional information: the "
                         "ladder's antecedent is `tr != enc || cnt != depth`, "
                         "so a wrong depth is true on every execution and "
                         "every rung would hold VACUOUSLY -- a report "
                         "indistinguishable from a fully successful ladder. "
                         "The tool refuses a mismatch (N3) rather than warn, "
                         "and reading it from the same run that supplied the "
                         "case is what stops a hand-typed one from ever "
                         "disagreeing.")
    ap.add_argument("--region", required=True,
                    help="JSON: {\"<coord>\": [lo, hi], ...} -- the CERTIFIED "
                         "region, decimal strings or ints")
    ap.add_argument("--holes", default="{}",
                    help="JSON: {\"<coord>\": [v, ...]} -- Definition 5")
    ap.add_argument("--pin", action="append", default=[],
                    help="coord=value, recorded on the PUT as the slice it is "
                         "a statement about")
    ap.add_argument("--forge-project", required=True,
                    help="a Foundry project whose src/ holds this flat; used "
                         "for `forge inspect <C> storageLayout` and to run "
                         "the emitted test")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--memlimit", default="8g")
    ap.add_argument("--test-suffix", default="")
    ap.add_argument("--piece", default=None, metavar="K",
                    help="which PIECE of a split certified region this is. "
                         "stage 2 may certify one path as a UNION of boxes "
                         "(--max-region-pieces > 1), each by its own query; "
                         "they are several regions about ONE enc, so without "
                         "this the second one OVERWRITES the first's "
                         "test/<contract>.t.sol and put_all's B gate keys both "
                         "on the same test name. Appended as `p<K>` to the test "
                         "function, the test contract and the file. Omit for an "
                         "unsplit region -- every existing name is then "
                         "reproduced byte for byte.")
    ap.add_argument("--propose-r2", action="store_true",
                    help="after the first ladder pass, issue ONE additional "
                         "ESBMC query containing typed R2 terms")
    ap.add_argument("--r2-depth", type=int, choices=(0, 1), default=1,
                    help="structured R2 expression depth")
    ap.add_argument("--r2-term-budget", type=int, default=R2_TERM_BUDGET,
                    help="per-variable term prefix kept in the R2 query")
    ap.add_argument("--r2-candidate-budget", type=int,
                    default=R2_CANDIDATE_BUDGET,
                    help="global typed R2 solver-claim cap across all state "
                         "variables; the omitted suffix is NOT ASKED")
    ap.add_argument("--fuzz-r2-prefilter", action="store_true",
                    dest="fuzz_r2_prefilter",
                    help="before the typed R2 ESBMC batch, run one Foundry "
                         "suite with one labeled test per candidate and drop "
                         "only candidates with a matching assertion failure; "
                         "passes never prove a candidate")
    ap.add_argument("--fuzz-runs", type=int, default=256,
                    help="Foundry draws per candidate in --fuzz-r2-prefilter")
    ap.add_argument("--fuzz-r2-candidate-budget", type=int, default=128,
                    help="prefix of typed candidates rendered into the one "
                         "Forge suite; the remainder still goes to ESBMC")
    ap.add_argument("--fuzz-r2-prefilter-timeout", type=int, default=300,
                    dest="fuzz_r2_prefilter_timeout",
                    help="hard timeout in seconds for the single R2 Forge run")
    ap.add_argument("--derived-by", default="{}", metavar="JSON",
                    help="the stage-2 switches this region was derived under, "
                         "as a JSON object, printed on the emitted test. The "
                         "work order requires a rendered width to say WHICH "
                         "STEP produced it and forbids one that rests on a "
                         "neighbourhood probe alone; the certified region "
                         "string carries no such information, so it is passed "
                         "here. ⚠ ROW granularity, not per coordinate -- the "
                         "test says so in as many words rather than letting "
                         "the reader assume each bound was traced.")
    ap.add_argument("--auto-unwind", type=int, default=0, metavar="N",
                    help="on an UNDECIDED-TRUNCATED ladder, RE-RUN it up to N "
                         "times, widening every loop the tool NAMED with "
                         "--unwindset <loop>:<k> and doubling k each attempt "
                         "(8, 16, 32, ...). This is the missing answer to 'how "
                         "many unwinds does this unit need': the tool already "
                         "names the loop it cut, and nothing was reading it. "
                         "--unwindset moves only the symex side, so each "
                         "attempt explores a SUPERSET -- it cannot make a "
                         "feasible path look infeasible. OFF by default, "
                         "because it costs a run per attempt and changes what "
                         "a default invocation does.")
    ap.add_argument("--scope", default="focus",
                    help="focus passes --focus-function <unit> (the GATE "
                         "cell); whole drops it; a comma-separated list passes "
                         "that dispatcher alphabet (the target plus recorded "
                         "state writers for an ARTEFACT cell). The choice is RECORDED "
                         "on the emitted test and in put.json, because "
                         "INVOCATION_DECISIONS.md forbids quoting one cell's "
                         "run into the other's table.")
    ap.add_argument("--esbmc-arg", action="append", default=[], dest="esbmc_arg",
                    help="passed verbatim to BOTH esbmc runs, once per token: "
                         "`--esbmc-arg --unwindset --esbmc-arg 64:512`. It "
                         "exists because the ladder's own UNDECIDED-TRUNCATED "
                         "refusal NAMES the loop to widen and this driver had "
                         "no way to act on it. Strategy flags are REFUSED here "
                         "-- see STRATEGY_FLAGS_REFUSED, which is measured, not "
                         "cautious. Whatever is passed is recorded in put.json, "
                         "because a region certified under one set of flags and "
                         "a test emitted under another is two measurements.")
    a = ap.parse_args()

    refusal = check_esbmc_args(a.esbmc_arg)
    if refusal:
        print(f"[put] REFUSED: {refusal}")
        return 1
    foundry_fixture = fixture_from_esbmc_args(a.esbmc_arg)
    if (a.r2_term_budget <= 0 or a.r2_candidate_budget <= 0
            or a.fuzz_runs <= 0 or a.fuzz_r2_prefilter_timeout <= 0
            or a.fuzz_r2_candidate_budget <= 0):
        print("[put] REFUSED: R2 term/candidate budgets, --fuzz-runs, "
              "--fuzz-r2-candidate-budget and "
              "--fuzz-r2-prefilter-timeout must all be positive")
        return 1
    if a.fuzz_r2_prefilter and not a.propose_r2:
        print("[put] REFUSED: --fuzz-r2-prefilter needs --propose-r2; there "
              "are otherwise no typed candidates to filter")
        return 1

    region = {k: (int(str(v[0])), int(str(v[1])))
              for k, v in json.loads(a.region).items()}
    holes = {k: [int(str(x)) for x in v]
             for k, v in json.loads(a.holes).items()}
    pins = {}
    for p in a.pin:
        n, _, v = p.partition("=")
        pins[n] = int(v, 0)

    os.makedirs(a.workdir, exist_ok=True)
    emit_dir = os.path.join(a.workdir, "emit")
    # ---- ABSOLUTE, BECAUSE ESBMC DOES NOT RUN IN THIS PROCESS'S CWD ---------
    #
    # Every esbmc child below is started with `cwd=` set to a run directory, so
    # a RELATIVE --workdir makes `--path-cov-assert <workdir>/assert/spec.json`
    # resolve against the wrong directory. esbmc then prints
    #     ERROR: --path-cov-assert: cannot open '<path>'
    # and ABORTS -- the driver sees `exit=-6 rows=0` and reports "the ladder
    # produced no rows", which reads as a fact about the region and is a fact
    # about a path string. This exact failure has already cost one session on
    # `spec_dup_probe.py`, whose fix was the same one line; the fix reached that
    # script and not this one, which is the two-readers-of-one-fact shape.
    a.workdir = os.path.abspath(a.workdir)
    assert_dir = os.path.join(a.workdir, "assert")
    os.makedirs(emit_dir, exist_ok=True)
    os.makedirs(assert_dir, exist_ok=True)

    notes = []

    # ---- 1. the emitter's own output: preamble + concrete case ------------
    cell_name, cell_rule = cell_of(a.scope, a.max_tx)
    print(f"[put] {a.contract}.{a.unit} enc={a.enc} depth={a.depth}")
    print(f"[put] CELL {cell_name}: {cell_rule}")
    print("[put] step 1: emit the concrete suite (preamble source of truth)")
    # ---- THE THREE FLAGS AN EMITTING RUN MUST CARRY ----
    #
    # `notes/coverage/INVOCATION_DECISIONS.md` (row 6, amended) states it and
    # this driver did not do it:
    #
    #   "Add THREE flags when the run is meant to EMIT TESTS:
    #    --overflow-check --div-by-zero-check --path-cov-arith-resolve.
    #    Without them a witnessed path whose counterexample wraps or divides by
    #    zero is rendered as a bare call asserting a NORMAL exit, and is RED on
    #    the unmodified contract -- measured three times across the PoC set."
    #
    # MEASURED AGAIN HERE, which is why the flags are now unconditional. Two of
    # the three PUTs still failing after the entry-state fix are exactly this:
    #
    #   D10_WrapNotPanic.add path 7   FAIL panic 0x11 (0x11 = arithmetic
    #   D20_FalseRevertOnly.addGate 6 FAIL panic 0x11  overflow)
    #
    # Both are paths the PoC exists to isolate: reachable ONLY by wrapping, so
    # on chain the transaction reverts with Panic(0x11) and a bare call
    # asserting a normal exit cannot be green. With the checks enabled the
    # re-solve either finds a NON-wrapping witness for the same path (and the
    # case becomes green) or proves there is none -- `arith_revert_only` -- and
    # the emitter REFUSES the case, so this driver finds no concrete case to
    # lift and refuses the PUT rather than shipping a red one. Both outcomes are
    # correct; emitting a red test is the only one that is not.
    #
    # `--path-cov-arith-resolve` refuses to run unless a check is enabled, so
    # the three travel together and cannot be half-copied into a silent no-op.
    #
    # ⚠ THE LADDER RUN DELIBERATELY DOES NOT GET THEM. Its result is a
    # HOLDS/REFUTED table over the certified region, and adding goto_check
    # claims there would put non-path claims into a solve loop whose whole
    # output is per-candidate verdicts. The emission run and the assertion run
    # answer different questions and carry different flags; that asymmetry is
    # the point, not an oversight.
    out1, rc1, w1 = run_esbmc(
        a.esbmc, a.sol, a.ast, a.contract, a.unit,
        ["--generate-foundry-testcase", "--cov-report-json",
         "--overflow-check", "--div-by-zero-check",
         "--path-cov-arith-resolve"] + a.esbmc_arg,
        emit_dir, a.max_tx, a.timeout, a.memlimit, a.scope)
    produced = sorted(f for f in os.listdir(emit_dir)
                      if f.endswith(".cov.t.sol"))
    print(f"[put]   exit={rc1} {w1:.1f}s  emitted={produced}")
    if not produced:
        print("[put] REFUSED: the emitter produced no .cov.t.sol, so there is "
              "no preamble to reuse and no concrete case to lift. This is an "
              "EMISSION outcome, not a property of the region")
        return 1
    emitted = EmittedFile(os.path.join(emit_dir, produced[0]))

    # The path identity the claim comment carries is the MANGLED id; read it
    # from this run's own report so the match cannot be against another run's
    # numbering.
    rep = json.load(open(os.path.join(emit_dir, "cov-report.json")))
    claim, claim_error = select_path_claim(
        rep, a.unit, a.enc, path_function=a.path_function)
    if claim is None:
        print(f"[put] REFUSED: {claim_error}, so the concrete case cannot be "
              "identified. Nothing was lifted")
        return 1
    pf = claim.get("path_function")
    rd = claim.get("path_depth")
    if a.depth is None:
        a.depth = int(rd)
        print(f"[put]   depth read from this run's own report: {a.depth}")
    elif str(rd) != str(a.depth):
        notes.append(f"depth mismatch: report says {rd}, spec says {a.depth}")
    declaration_id = path_function_declaration_id(pf)
    if declaration_id is None:
        print(f"[put] REFUSED: malformed path_function {pf!r}; expected a "
              "trailing #<solc-node-id>. Declaration facts cannot be selected "
              "without guessing")
        return 1
    case = emitted.case_for(pf, a.enc)
    if case is None:
        print(f"[put] REFUSED: no emitted case names {pf}:path:{a.enc}. The "
              f"path was witnessed but its counterexample produced no test "
              f"(refused as an obstacle, an empty body, or an unrenderable "
              f"argument) -- so there is no concrete case to generalise")
        return 1
    print(f"[put]   concrete case: {case[1]} in contract {emitted.blocks[case[0]][0]}")

    # ---- 2a. storage layout and declared parameters ------------------------
    #
    # READ BEFORE THE LADDER, not after it as they used to be. The ladder now
    # has to be TOLD which mapping slots to judge -- the tool deliberately
    # refuses to sweep every mapping with every parameter, because a rung about
    # a slot the unit never touches holds and would be rendered as an oracle
    # nobody chose -- and the two facts needed to name a slot are exactly these:
    # which mappings solc reports, and what this unit's parameters are called.
    # Neither depends on the ladder, so the move changes nothing else.
    layout, maps, err = storage_layout(a.forge_project, a.contract)
    if layout is None:
        print(f"[put] REFUSED: {err}. Without solc's storage layout a state "
              f"read would be a GUESSED slot, and a green assertion about the "
              f"wrong slot is worse than no assertion")
        return 1
    query_maps = esbmc_certifiable_maps(maps)
    skipped_maps = sorted(set(maps or {}) - set(query_maps))
    print(f"[put] step 2a: storage layout — {len(layout)} readable scalar "
          f"slot(s): {', '.join(sorted(layout)) or 'none'}; "
          f"{len(query_maps)} ESBMC-queryable mapping(s) with a value-type "
          f"key: {', '.join(sorted(query_maps)) or 'none'}")
    if skipped_maps:
        print("[put]   mapping(s) present in solc layout but not sent to "
              "--path-cov-assert yet: " + ", ".join(skipped_maps) +
              " (struct-contained mapping_t fields need internal verifier "
              "support before they are safe ladder variables)")

    # Both come from _select_def with the SAME arity, so an overload cannot be
    # resolved one way for the arguments and another way for the return value.
    params, rettypes, arity = None, None, None
    if a.ast:
        _n, args0 = rewrite_call_args(
            emitted.lines[case[3][0] + 1:case[3][1]][
                find_unit_call(emitted.lines[case[3][0] + 1:case[3][1]],
                               a.unit) or 0],
            a.unit, {})
        arity = len(args0) if args0 is not None else None
        params = function_params(a.ast, a.contract, a.unit, arity,
                                 declaration_id)
        rettypes = function_returns(a.ast, a.contract, a.unit, arity,
                                    declaration_id)
    if params is None:
        print("[put] WARNING: declared parameters unavailable (no --ast, or "
              "the name did not resolve); no argument can be lifted")
    if rettypes is None:
        print("[put] WARNING: the declared return type is unavailable, so no "
              "return-value rung can be bound even if one HOLDS")
    else:
        print(f"[put]   declared return: "
              f"{', '.join(t for _n2, t in rettypes) or '(none)'}")

    # ---- WHICH SLOTS TO ASK ABOUT, and why the DRIVER chooses --------------
    #
    # `m[p]` for every mapping whose KEY TYPE matches a declared parameter's
    # type. That is a policy, it is the driver's to make, and it is recorded on
    # the emitted test rather than buried: the tool refuses to make it, because
    # a default sweep inside the verifier would emit rungs about slots the unit
    # never touches with nothing naming the choice.
    #
    # The proposal is CHEAP AND JUDGED. A slot the unit does not write yields
    # `post == pre`, which is a frame condition rather than a strong oracle;
    # one it does write yields the sign and delta rungs. Which of the two a
    # given slot produced is visible in the table, so over-proposing costs
    # candidates, never correctness.

    slot_vars = []
    slot_dependencies, slot_dependency_evidence = unit_state_dependencies(
        a.ast, a.contract, a.unit, arity=arity,
        declaration_id=declaration_id)
    if slot_dependencies is None:
        print("[put]   mapping dependency closure unavailable; failing closed "
              "with no proposed mapping slot: "
              + "; ".join(slot_dependency_evidence))
    else:
        for evidence in slot_dependency_evidence:
            print(f"[put]   {evidence}")
        direct_slot_vars = region_slot_vars(region, query_maps)
        if direct_slot_vars:
            print("[put]   certified-region mapping slots sent to the "
                  "assertion ladder first: " + ", ".join(direct_slot_vars))
            slot_vars += direct_slot_vars
        direct_mkeys = set()
        for v in direct_slot_vars:
            mname, _keys, tail = parse_slot_name(v)
            if mname is not None:
                direct_mkeys.add(mname + tail)
        remaining_maps = {
            name: spec for name, spec in (query_maps or {}).items()
            if name not in direct_mkeys
        }
        slot_vars += propose_slot_vars(
            remaining_maps, params, dependencies=slot_dependencies)
    scalar_vars = [name for name in (slot_dependencies or ())
                   if name in (layout or {})]
    oracle_vars = scalar_vars + slot_vars
    if oracle_vars:
        print(f"[put]   dependency-selected assertion candidates: "
              f"{', '.join(oracle_vars)}")
    if slot_vars:
        print(f"[put]   mapping slots proposed to the ladder: "
              f"{', '.join(slot_vars)}")

    # ---- 2b. the assertion ladder -----------------------------------------
    print("[put] step 2b: post-state assertion ladder over the certified region")
    query_pins, skipped_query_pins = assert_query_pins(pins, layout,
                                                       query_maps)
    query_region, skipped_query_region = assert_query_region_entries(
        region, holes, layout, query_maps)
    for s in skipped_query_region:
        print(f"[put]   {s}")
    for s in skipped_query_pins:
        print(f"[put]   {s}")
    spec = {"unit": pf, "enc": a.enc, "depth": a.depth,
            "region": query_region
                      + [{"name": n, "lo": str(v), "hi": str(v)}
                         for n, v in query_pins.items()]}
    # Exact means exact even when the closure is empty or names only mappings.
    # Omitting vars requests the legacy all-state scan, which would turn "this
    # unit has no state dependency" into unrelated frame conditions. Return
    # rungs remain independently enabled by the verifier under this policy.
    spec["vars_policy"] = "state-exact"
    spec["vars"] = [{"name": name} for name in oracle_vars]
    with open(os.path.join(assert_dir, "spec.json"), "w") as f:
        json.dump(spec, f)
    out2, rc2, w2 = run_esbmc(
        a.esbmc, a.sol, a.ast, a.contract, a.unit,
        ["--path-cov-assert", os.path.join(assert_dir, "spec.json"),
         "--cov-report-json"] + a.esbmc_arg,
        assert_dir, a.max_tx, a.timeout, a.memlimit, a.scope)
    rows, summary, refusal, blocker = parse_ladder(out2)
    # The ladder run is asked about exactly ONE (unit, enc), so any rollback
    # line in ITS log is about this path -- but the pair is still matched rather
    # than assumed, because a log that ever covers two paths would otherwise
    # silently apply one path's rollback to the other.
    rollback_here = any(e == int(a.enc) for _u, e in rollback_exit_paths(out2))

    # ---- THE UNWIND LADDER: widen the loops the tool NAMED, and say so -----
    unwind_attempts, unwind_applied = [], []
    k = 8
    for attempt in range(1, a.auto_unwind + 1):
        if blocker != "truncated":
            break
        loops, shapes = truncated_loops(out2)
        if not loops:
            print("[put]   auto-unwind: the run answered UNDECIDED-TRUNCATED "
                  "but NAMED NO LOOP that this parser can read "
                  f"(shape counts {shapes}). Refusing to widen a loop nobody "
                  "identified -- teach the parser the new wording instead")
            break
        named = ", ".join(f"{lid} ({fn}, {f}:{ln})"
                          for lid, f, ln, fn in sorted(loops))
        print(f"[put]   auto-unwind {attempt}/{a.auto_unwind}: the tool named "
              f"loop(s) {named}; re-running with --unwindset at {k}")
        extra = unwindset_args(loops, k)
        out2b, rc2b, w2b = run_esbmc(
            a.esbmc, a.sol, a.ast, a.contract, a.unit,
            ["--path-cov-assert", os.path.join(assert_dir, "spec.json"),
             "--cov-report-json"] + a.esbmc_arg + extra,
            assert_dir, a.max_tx, a.timeout, a.memlimit, a.scope)
        rows_b, summary_b, refusal_b, blocker_b = parse_ladder(out2b)
        # ---- AN ATTEMPT THAT PRODUCED NO LADDER MAY NOT REPLACE THE STATE ---
        #
        # See `attempt_is_usable`. Adopting unconditionally is what let a
        # crashed retry (exit 64 on a rejected command line) overwrite
        # blocker="truncated" with None, walk straight past the gate below, and
        # ship an oracle-free PUT with exit 0. The refusal the retry was trying
        # to LIFT is the thing it deleted.
        usable = attempt_is_usable(rows_b, blocker_b)
        unwind_attempts.append({"attempt": attempt, "k": k,
                                "loops": [list(x) for x in sorted(loops)],
                                "shapes": shapes, "exit": rc2b,
                                "wall_s": round(w2b, 1),
                                "adopted": usable,
                                "blocker_after": blocker_b if usable else None,
                                "rows_after": len(rows_b) if usable else 0})
        if not usable:
            print(f"[put]     exit={rc2b} {w2b:.1f}s  NO LADDER: this attempt "
                  f"produced neither a candidate row nor a RESULT token, so it "
                  f"is not a measurement of anything. The PREVIOUS verdict "
                  f"stands and the widening is abandoned -- re-running at "
                  f"{k * 2} would fail the same way. See "
                  f"{os.path.join(assert_dir, 'run.log')}")
            break
        out2, rows, summary, refusal, blocker = (
            out2b, rows_b, summary_b, refusal_b, blocker_b)
        print(f"[put]     exit={rc2b} {w2b:.1f}s  blocker={blocker} "
              f"rows={len(rows)}")
        # ⛔ NOT folded into `a.esbmc_arg`. Doing that was the first version
        # and it wrote a FALSE record: step 1 -- the emit run that supplies the
        # preamble and the concrete case -- already ran WITHOUT the widening,
        # so listing the widened flags as the run's configuration claims both
        # runs used them. They are kept in their own field, and the emitted test
        # says which of its two halves the widening applies to.
        if blocker != "truncated":
            unwind_applied.extend(extra)
        k *= 2
    if refusal:
        print(f"[put]   ladder REFUSED: {refusal}")
        notes.append(f"ladder refused: {refusal}")
    print(f"[put]   exit={rc2} {w2:.1f}s  rows={len(rows)} summary={summary}")

    # ---- WHAT WAS ASKED MINUS WHAT WAS ANSWERED. See ladder_answer_gap. -----
    #
    # Printed BEFORE R2 and BEFORE the emit, because every number after this
    # point is computed from `rows`, and a `rows` that answers none of the
    # questions asked reads exactly like a unit with nothing to assert.
    unanswered, unasked = ladder_answer_gap(slot_vars, rows)
    if slot_vars:
        print(f"[put]   slot candidates: {len(slot_vars)} asked, "
              f"{len(slot_vars) - len(unanswered)} answered, "
              f"{len(unanswered)} came back with NO ROW; "
              f"{len(unasked)} row(s) name a variable the spec never asked "
              f"about (the component loop is not whitelisted by a slot-only "
              f"spec, so those are expected)")
        if unanswered and len(unanswered) == len(slot_vars):
            print(f"[put]   ⛔ ZERO of {len(slot_vars)} slot candidate(s) came "
                  f"back with a verdict. THE EMPTY SLOT ORACLE BELOW IS A "
                  f"REFUSAL, NOT A MEASUREMENT -- this unit was never told to "
                  f"have no assertable slot, it was never answered about any. "
                  f"The per-candidate reason is in the run log for this "
                  f"invocation ({os.path.join(assert_dir, 'run.1.log')}); "
                  f"esbmc prints one line per dropped candidate")
        elif unanswered:
            print(f"[put]     unanswered: {', '.join(unanswered)}")
    notes.append(f"slot candidates asked={len(slot_vars)} "
                 f"unanswered={len(unanswered)}")

    # ---- R2: ASK FOR THE DELTA BOUND THE FIRST PASS MADE ANSWERABLE --------
    #
    # OPT-IN, and it stays opt-in. Each proposed spec is one more esbmc
    # invocation; a flag that silently multiplies the run count of a serial
    # sweep is not a feature. The direction comes from the ordering rungs
    # `rows` already carries, so this cannot run before the first pass and
    # never guesses `delta_dir`.
    r2_term_lookup = {}
    r2_fuzz_prefilter = {"enabled": bool(a.fuzz_r2_prefilter)}
    path_reverts = a.exit_kind == "revert"
    if a.propose_r2:
        # ---- THE CANDIDATE WIDTH TABLE, FROM solc's OWN LAYOUT --------------
        #
        # Built here rather than inside the proposer because this is where the
        # layout lives, and guessed nowhere: a candidate absent from BOTH
        # dicts has no storage slot at all (a constant/immutable), and it is
        # left out so the proposer excludes it rather than spending a solver
        # query on a row that is discarded downstream anyway.
        _var_bytes = {}
        for _v, (_slot, _off, _nb) in (layout or {}).items():
            _var_bytes[_v] = _nb
        for _v, _t, _d in rows:
            _mn, _keys, _tail = parse_slot_name(_v)
            if _mn is None:
                continue
            _mk = _mn + _tail
            if query_maps and _mk in query_maps:
                _var_bytes[_v] = query_maps[_mk][2]
        _source_literals, _source_evidence = source_r2_literals(
            a.ast, a.contract, a.unit, arity=len(params or []),
            declaration_id=declaration_id)
        for _evidence in _source_evidence:
            print(f"[put]   {_evidence}")
        _rendered_coords = []
        for _pn, _pt in params or []:
            if _pn not in region:
                continue
            _kind = lift_kind(_pt)
            if _kind is None:
                continue
            _coord_kind = {"address": "id", "bool": "bool"}.get(_kind[0],
                                                                "num")
            _rendered_coords.append(
                (_pn, _coord_kind,
                 20 if _kind[0] == "address" else None))
        if "msg.sender" in region:
            _rendered_coords.append(("msg.sender", "id", 20))
        if "msg.value" in region:
            _rendered_coords.append(("msg.value", "num", None))
        for _sn in sorted({n for n in list(region) + list(pins)
                           if n.startswith("state.")}):
            _sv = _sn[len("state."):]
            if parse_slot_name(_sv)[0] is not None:
                continue
            if _sv not in layout:
                continue
            _nb = layout[_sv][2]
            _rendered_coords.append(
                (_sn, "id" if _nb == 20 else "num",
                 _nb if _nb == 20 else None))
        _r2, _source_assignment_evidence = source_assignment_r2_specs(
            a.ast, a.contract, a.unit, params, layout, _rendered_coords,
            arity=len(params or []), declaration_id=declaration_id,
            rettypes=rettypes, maps=query_maps, log=print)
        _typed_r2 = propose_r2_batch(
            rows, params, source_literals=_source_literals,
            depth=a.r2_depth, var_bytes=_var_bytes, rettypes=rettypes,
            rendered_coords=_rendered_coords,
            term_budget=a.r2_term_budget,
            candidate_budget=a.r2_candidate_budget, log=print)
        _r2 = merge_source_r2_specs(
            _r2, _typed_r2, candidate_budget=a.r2_candidate_budget, log=print)
        r2_term_lookup = r2_terms_from_specs(_r2)
        r2_requested = True

        if rollback_here or path_reverts:
            reason = ("rollback path has no observable R2 post-state"
                      if rollback_here else
                      "revert path has no observable R2 post-state")
            if a.fuzz_r2_prefilter:
                r2_fuzz_prefilter.update(skipped_forge_r2_evidence(
                    _r2, a.fuzz_r2_candidate_budget, reason,
                    a.fuzz_runs))
                print(f"[put]   Forge R2 prefilter NOT RUN: {reason}")
        elif a.fuzz_r2_prefilter:
            _fuzz_verdicts, r2_fuzz_prefilter = run_forge_r2_prefilter(
                a.forge_project, a.workdir, emitted, case, a.contract,
                a.unit, a.enc, a.depth, pf, region, holes, pins, params,
                layout, maps, _r2, r2_term_lookup,
                (cell_name, cell_rule), json.loads(a.derived_by or "{}"),
                a.fuzz_r2_prefilter_timeout, a.fuzz_runs,
                a.fuzz_r2_candidate_budget, foundry_fixture)
            r2_fuzz_prefilter["enabled"] = True
            _r2 = filter_r2_specs(_r2, _fuzz_verdicts)
            survivors = len(r2_candidates(_r2))
            print(f"[put]   Forge R2 survivors sent to ESBMC: "
                  f"{survivors}; a Forge pass was NOT counted as proof")

        def _write_r2(suffix, spec_dict):
            p = os.path.join(assert_dir, "spec" + suffix + ".json")
            with open(p, "w") as f:
                json.dump(spec_dict, f)
            return p

        def _run_r2(spec_path):
            o, _rc, _w = run_esbmc(
                a.esbmc, a.sol, a.ast, a.contract, a.unit,
                ["--path-cov-assert", spec_path, "--cov-report-json"]
                + a.esbmc_arg,
                assert_dir, a.max_tx, a.timeout, a.memlimit, a.scope)
            return o

        rows += maybe_run_r2_passes(
            _r2, spec, _write_r2, _run_r2, parse_ladder,
            rollback_here=rollback_here, revert_here=path_reverts,
            notes=notes)
    else:
        # ---- AN R2 CLASS THAT WAS NEVER ASKED FOR MUST NOT READ AS ONE ------
        #
        # THAT ABSENCE IS THE CORPUS'S ACTUAL STATE. Of the 218 put.json files
        # on disk, 201 carry a ladder with no R2 row of either kind, and NOT
        # ONE corpus unit carries an R2 row at all -- because `--propose-r2` is
        # opt-in and the corpus runs did not pass it. The single delta row that
        # has ever HELD is on a hand-written fixture.
        #
        # Without this line, a `put.json` whose ladder holds no delta row is
        # indistinguishable from one where the delta was asked for and came
        # back refuted. Reading the first as the second turns "we never asked"
        # into "the class does not work on this corpus", which is a conclusion
        # about the method drawn from a missing command-line flag.
        r2_requested = False
        print("[put]   R2 NOT REQUESTED (`--propose-r2` is off). The absolute "
              "and delta bounds were not asked for on this run, so their "
              "absence below is a fact about this INVOCATION and says nothing "
              "about whether they hold. Do not read it as a measurement.")
    for v, t, verdict in rows:
        print(f"[put]     {v}: {t}  {verdict}")

    # ---- THE VACUITY GATE, AND WHY IT IS FATAL RATHER THAN A WARNING ------
    #
    # A ladder refusal about the VARIABLES (no scalar state, a bool-only
    # variable, a `vars` name that is a mapping) costs the oracle and nothing
    # else -- the region and the exit-kind expectation are still worth
    # shipping. VACUOUS is not that kind of refusal. It says no execution the
    # region admits walks this path at all, so a PUT built on it would `bound`
    # every fuzz input into a set from which the path is never taken: 256
    # green runs of a test that stands for nothing, which is precisely the
    # outcome this pipeline exists to never produce (the emitter already
    # refuses empty-bodied cases for the same reason, foundry.cpp:2519-2545).
    #
    # MEASURED, and it is a DISAGREEMENT rather than a mere refusal. On
    # aqua `dock` enc=12 depth=3 with the identical region, identical flags:
    #
    #   --path-cov-certify : `dock:path:12#nonvacuous` FAILED (refuted, i.e.
    #                        witnessed) -> RESULT: CERTIFIED, "NON-VACUITY was
    #                        witnessed"
    #   --path-cov-assert  : `dock:path:12#nonvacuous` PASSED -> VACUOUS, and
    #                        all six MUTUALLY CONTRADICTORY rungs PASSED
    #                        alongside it (post == pre and post != pre both
    #                        holding is impossible for any real execution)
    #
    # Both cannot be right, and the two gates had never been run against one
    # another before this file existed -- which is itself the argument for
    # wiring them together.
    #
    # IT IS NOW SETTLED, AND THE DISAGREEMENT HAD ONE CAUSE. `--path-cov-assert`
    # is the only one of the three sub-modes that forces `--no-simplify`
    # (esbmc_parseoptions.cpp:4223), which stops `do_simplify` folding loop
    # guards, which lets a library loop be ENTERED, truncated at the coverage
    # unwind bound of 4, and -- because the pass also forces
    # `no-unwinding-assertions` (:4305) -- its remaining executions ASSUMED
    # AWAY. On aqua that loop was `__memset_impl`
    # (src/c2goto/library/string.c:298) and `--unwindset 64:512` brought the two
    # witnesses back (F 0 -> F 2). The assert side was wrong, and the refusal
    # recorded at notes/coverage/put_roundtrip/_wd/aqua_Aqua__dock__12/put.json
    # was a LOST PUT rather than a property of the region.
    #
    # The tool no longer answers VACUOUS in that situation: it answers
    # UNDECIDED-TRUNCATED, a distinct token, and the two are refused with
    # DIFFERENT words below. Both still refuse -- a PUT that bounds every fuzz
    # input into a set the path may never be taken from is 256 green runs
    # standing for nothing, and that is the outcome this pipeline exists never
    # to produce (the emitter refuses empty-bodied cases for the same reason,
    # foundry.cpp:2519-2545). What changes is that "the region admits nothing"
    # and "we could not tell, and here is the loop to raise the bound on" stop
    # being the same message.
    if blocker == "truncated":
        print("[put] REFUSED: the assertion ladder returned "
              "UNDECIDED-TRUNCATED -- a loop was cut at the unwind bound "
              "while unwinding assertions were disabled, so the executions "
              "that would walk this path may have been ASSUMED AWAY rather "
              "than shown not to exist. This is NOT a vacuous region and must "
              "not be recorded as one: the region may be perfectly good and "
              "the BOUND is what could not see it. Re-run with a larger "
              "--unwind, or --unwindset/--unwindsetname on the loop(s) the "
              "tool named, to get a verdict")
        print(f"[put]   tool line: {refusal}")
        with open(os.path.join(a.workdir, "put.json"), "w") as f:
            json.dump({"contract": a.contract, "unit": a.unit, "enc": a.enc,
                       "depth": a.depth, "refused": "ladder-undecided-truncated",
                       "ladder_refusal": refusal,
                       # What was TRIED, so "still truncated" is separable from
                       # "nobody widened anything". An empty list here with
                       # --auto-unwind 0 is the second case and says so.
                       "unwind_attempts": unwind_attempts, "notes": notes},
                      f, indent=2)
        return 3
    if blocker == "vacuous":
        print("[put] REFUSED: the assertion ladder reports the certified "
              "region VACUOUS for this path, i.e. its non-vacuity witness "
              "held where certification's was refuted, and NO loop was "
              "truncated in this run -- so the bound cannot explain it and "
              "the region really does admit no input that walks this path. A "
              "PUT built on it would bound every fuzz input into a set the "
              "path is never taken from. Refusing rather than shipping a test "
              "that could be green while standing for nothing")
        with open(os.path.join(a.workdir, "put.json"), "w") as f:
            json.dump({"contract": a.contract, "unit": a.unit, "enc": a.enc,
                       "depth": a.depth, "refused": "ladder-vacuous",
                       "ladder_refusal": refusal, "notes": notes}, f, indent=2)
        return 2

    # ---- 3. build ---------------------------------------------------------
    #
    # The storage layout and the declared parameters were read at step 2a, in
    # front of the ladder, because the ladder has to be told which mapping
    # slots to judge.
    # ONE variable for the label, used by the function name, the test contract
    # name and put.json's `test` field. Three call sites deriving it separately
    # is how the emitted function and the name the B gate looks up come to
    # disagree -- and a lookup that cannot match is a gate that never fires.
    overload_label = overload_artifact_label(
        a.ast, a.contract, a.unit, declaration_id)
    plabel = overload_label + (f"p{a.piece}" if a.piece else "")
    put, stats = build_put(a.contract, a.unit, a.enc, a.depth, pf,
                           region, holes, pins, params, emitted, case,
                           layout, rows, notes,
                           cell=(cell_name, cell_rule),
                           unwind=unwind_applied, rettypes=rettypes,
                           maps=maps, piece_label=plabel,
                           derived_by=json.loads(a.derived_by or "{}"),
                           rollback_exit=rollback_here,
                           r2_terms=r2_term_lookup,
                           exit_kind=a.exit_kind)
    if put is None:
        print("[put] REFUSED: " + "; ".join(notes))
        return 1

    # Insert into the SAME test contract, so the PUT shares the deploy the
    # concrete tests use rather than carrying a second copy of it.
    cname, _cstart, _cend = emitted.blocks[case[0]]
    newc = f"{cname}_{a.contract}_{a.unit}_put{a.enc}{plabel}{a.test_suffix}"
    txt = assemble_put_source(emitted, case, [put], newc, foundry_fixture,
                              layout, a.contract, a.unit)
    dest = os.path.join(a.forge_project, "test", f"{newc}.t.sol")
    with open(dest, "w") as f:
        f.write(txt)
    print(f"[put] WROTE {dest}")
    print(f"[put]   fuzz parameters : {stats['fuzz_params']} "
          f"({', '.join(stats['lifted']) or 'none'})")
    print(f"[put]   oracle asserts  : {stats['asserts']} "
          f"({stats['state_asserts']} post-state, "
          f"{stats['return_asserts']} return value, "
          f"{stats.get('exit_kind_asserts', 0)} exit-kind)")
    for s in stats["oracle_skipped"]:
        print(f"[put]     rung dropped: {s}")
    for s in stats.get("oracle_implied", []):
        print(f"[put]     rung implied: {s}")
    for s in stats["state_stored"]:
        print(f"[put]     entry state stored: {s}")
    for s in stats["state_skipped"]:
        print(f"[put]     entry state dropped: {s}")
    for s in stats.get("env_unchecked") or []:
        print(f"[put]     environment NOT CHECKED: {s}")
    for n in notes:
        print(f"[put]   note: {n}")

    with open(os.path.join(a.workdir, "put.json"), "w") as f:
        json.dump({"contract": a.contract, "unit": a.unit, "enc": a.enc,
                   "depth": a.depth, "path_function": pf,
                   "artifact_identity": overload_label,
                   "file": dest,
                   # The SAME `plabel` build_put used. A second derivation here
                   # is how put.json comes to name a function the file does not
                   # contain.
                   "test": f"test_put_{a.contract}_{a.unit}"
                           f"_path{a.enc}{plabel}",
                   "piece": a.piece,
                   "region": {k: [str(v[0]), str(v[1])]
                              for k, v in region.items()},
                   "holes": {k: [str(x) for x in v] for k, v in holes.items()},
                   "pins": {k: str(v) for k, v in pins.items()},
                   "ladder": [{"var": v, "text": t, "verdict": d}
                              for v, t, d in rows],
                   "ladder_summary": summary, "ladder_refusal": refusal,
                   # ⛔ WITHOUT THIS FIELD, "no R2 row" IS UNREADABLE. A ladder
                   # that was never asked for an absolute or delta bound and
                   # one that was asked and got nothing produce byte-identical
                   # `ladder` arrays. 201 of the 218 put.json files on disk are
                   # the first kind, and reading them as the second would turn
                   # a missing command-line flag into a conclusion about the
                   # method.
                   "r2_requested": r2_requested,
                   "r2_depth": a.r2_depth if r2_requested else None,
                   "r2_term_budget": (a.r2_term_budget
                                      if r2_requested else None),
                   "r2_candidate_budget": (a.r2_candidate_budget
                                            if r2_requested else None),
                   "r2_fuzz_prefilter": r2_fuzz_prefilter,
                   "oracle_dependency_policy": SLOT_DEPENDENCY_POLICY,
                   "oracle_dependency_state": list(slot_dependencies or ()),
                   "oracle_vars": list(oracle_vars),
                   # ⛔ ASKED, AND ANSWERED, AS TWO NUMBERS. A ladder that was
                   # asked about 48 slots and answered about none produces the
                   # same `ladder` array as a unit with no slot to ask about.
                   # aqua `push` enc=6 is the first kind and was read as the
                   # second for as long as this field did not exist.
                   "slot_candidates": {
                       "asked": list(slot_vars),
                       "unanswered": unanswered,
                       "rows_for_unasked_names": unasked},
                   # A region certified under one set of flags and a test
                   # emitted under another are two measurements; the artefact
                   # has to say which one it is.
                   "esbmc_extra_args": a.esbmc_arg,
                   # The widening applies to the LADDER run only. The emit run
                   # that produced the preamble and the concrete case ran before
                   # any loop was named, so it did not carry these.
                   "unwind_applied_to_ladder_only": unwind_applied,
                   "unwind_attempts": unwind_attempts,
                   "cell": {"name": cell_name, "scope": a.scope,
                            "max_tx": a.max_tx, "rule": cell_rule},
                   # WHICH EXECUTABLE PRODUCED THIS. Without it a reader
                   # that re-reads put.json (put_all --forge-only) cannot tell
                   # a row emitted by this tree from one left behind by a build
                   # that no longer exists, and it counted one of each toward
                   # the same B.
                   "binary": binary_identity(a.esbmc),
                   "stats": stats, "notes": notes}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
