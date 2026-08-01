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
     `--path-cov-assert`, read through `vm.load` at the slot solc itself
     reports.

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
import json
import os
import re
import subprocess
import sys
import time

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
    cmd += extra
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = p.stdout + p.stderr
    with open(os.path.join(cwd, "run.log"), "w") as f:
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
                               r"REFUSING THE LADDER[:,] (.*)$")
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


# Rung text -> a renderer producing forge-std assertion lines.  `post`/`pre`
# are the expression texts the caller has already built.
def rung_assertions(text, pre, post, label):
    lit = json.dumps(label)
    m = re.match(r"^post (==|!=|>=|<=|>|<) pre$", text)
    if m:
        op = m.group(1)
        fn = {"==": "assertEq", ">=": "assertGe", "<=": "assertLe",
              ">": "assertGt", "<": "assertLt"}.get(op)
        if fn:
            return [f"    {fn}({post}, {pre}, {lit});"]
        return [f"    assertTrue({post} != {pre}, {lit});"]
    m = re.match(r"^post in \[(\d+), (\d+)\]$", text)
    if m:
        return [f"    assertGe({post}, {m.group(1)}, {lit});",
                f"    assertLe({post}, {m.group(2)}, {lit});"]
    m = re.match(r"^post - pre in \[(\d+), (\d+)\] with post >= pre$", text)
    if m:
        return [f"    assertGe({post}, {pre}, {lit});",
                f"    assertGe({post} - {pre}, {m.group(1)}, {lit});",
                f"    assertLe({post} - {pre}, {m.group(2)}, {lit});"]
    m = re.match(r"^pre - post in \[(\d+), (\d+)\] with pre >= post$", text)
    if m:
        return [f"    assertGe({pre}, {post}, {lit});",
                f"    assertGe({pre} - {post}, {m.group(1)}, {lit});",
                f"    assertLe({pre} - {post}, {m.group(2)}, {lit});"]
    return None


# ---------------------------------------------------------------------------
# Storage layout (from solc, via forge) -- never guessed
# ---------------------------------------------------------------------------

def storage_layout(project, contract):
    """{var: (slot, offset_bytes, size_bytes)} for the contract's own storage.

    Read from `forge inspect <C> storageLayout --json`, i.e. from solc.  A
    variable the layout does not mention has NO storage slot: it is a
    `constant` (baked into the code) or an `immutable` (baked into the
    deployed bytecode).  Returning it absent rather than guessing a slot is
    what lets the caller DROP its rungs with a reason instead of emitting a
    read of the wrong slot -- which would be a green-looking assertion about
    a quantity nothing wrote.
    """
    p = subprocess.run(["forge", "inspect", contract, "storageLayout",
                        "--json"], cwd=project, capture_output=True, text=True)
    if p.returncode != 0:
        return None, (f"forge inspect failed (rc={p.returncode}): "
                      f"{p.stdout + p.stderr}")
    try:
        j = json.loads(p.stdout)
    except ValueError as e:
        return None, f"forge inspect produced no JSON: {e}"
    types = j.get("types") or {}
    out = {}
    for e in j.get("storage") or []:
        ty = types.get(e.get("type")) or {}
        # Only INPLACE value types can be read/written as a masked slot word.
        # A mapping has no slot of its own to read; a `bytes`/`string` slot
        # holds a length-or-payload encoding, not the value.
        if ty.get("encoding") != "inplace":
            continue
        nb = ty.get("numberOfBytes")
        if nb is None or ty.get("members") is not None:
            continue
        try:
            out[e["label"]] = (int(e["slot"]), int(e["offset"]), int(nb))
        except (KeyError, TypeError, ValueError):
            continue
    return out, None


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


def slot_read_expr(addr, slot, off, nbytes):
    """A uint256-valued expression reading one packed storage variable."""
    mask = (1 << (8 * nbytes)) - 1
    inner = f"uint256(vm.load({addr}, bytes32(uint256({slot}))))"
    if off:
        inner = f"({inner} >> {8 * off})"
    if nbytes < 32:
        inner = f"({inner} & {_mask_lit(mask)})"
    return inner


def slot_write_lines(addr, slot, off, nbytes, value_expr, indent="    "):
    """Read-modify-write of one packed storage variable.

    RMW rather than a whole-word store: several state variables share a slot
    whenever they pack (solc's layout reports offset/numberOfBytes precisely
    so this is decidable, not guessed), and a whole-word store would silently
    zero its neighbours -- which is a change to the entry state nobody asked
    for and which the region says nothing about.
    """
    mask = (1 << (8 * nbytes)) - 1
    s = _mask_lit(mask << (8 * off))
    return [
        f"{indent}{{",
        f"{indent}  uint256 _w = uint256(vm.load({addr}, "
        f"bytes32(uint256({slot}))));",
        f"{indent}  _w = (_w & ~uint256({s})) | "
        f"((uint256({value_expr}) & {_mask_lit(mask)}) << {8 * off});",
        f"{indent}  vm.store({addr}, bytes32(uint256({slot})), bytes32(_w));",
        f"{indent}}}",
    ]


# ---------------------------------------------------------------------------
# The unit's declared parameters, from the solc AST
# ---------------------------------------------------------------------------

def _load_ast(ast_path):
    txt = open(ast_path).read()
    return json.loads(txt[txt.index("{"):])


def function_params(ast_path, contract, unit, arity=None):
    """[(name, solidity_type)] in SOURCE ORDER for `contract.unit`.

    Source order is what makes a positional rewrite of the emitted call legal,
    and it is the same order the emitter itself fills arguments in
    (foundry.cpp:1288 iterates the declared parameters).  Read from the AST
    rather than from the emitted text, so the two agree by construction on the
    only fact they share.

    INHERITANCE IS NOT OPTIONAL: a unit of the contract under test is
    routinely DECLARED on a base (`BaseEscrow.rescueFunds` under
    `--contract EscrowSrc`).  The C3 linearisation is walked in reverse so the
    most-derived declaration wins, exactly as the compiler resolves it.

    `arity` disambiguates overloads: two functions of one name are two units,
    and picking the wrong one would rename arguments across signatures.
    Returns None when the name is ambiguous and `arity` does not separate it.
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

    cands = []
    for sc in scopes:
        for n in sc.get("nodes", []) or []:
            if (isinstance(n, dict) and n.get("nodeType") == "FunctionDefinition"
                    and n.get("name") == unit):
                ps = []
                for p in ((n.get("parameters") or {}).get("parameters") or []):
                    ty = ((p.get("typeDescriptions") or {}).get("typeString")
                          or "")
                    ps.append((p.get("name") or "", ty))
                cands.append(ps)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[-1]
    if arity is not None:
        fit = [c for c in cands if len(c) == arity]
        if len(fit) == 1:
            return fit[0]
        # Same arity twice: most-derived wins (scopes walked base-first).
        if fit:
            return fit[-1]
    return cands[-1]


# `bound()` on a coordinate needs a type this script can cast in both
# directions.  Anything else is NOT lifted -- reported, never silently kept as
# a concrete literal that would read like a generalised one.
def lift_kind(sol_type):
    t = (sol_type or "").strip()
    if t in ("address", "address payable"):
        return ("address", 160)
    m = re.match(r"^uint(\d+)?$", t)
    if m:
        return ("uint", int(m.group(1) or 256))
    return None


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
# The environment is not ESTABLISHED here, it is CHECKED. The emitter already
# writes `vm.prank(...)` and `{value: ...}` from the counterexample and that
# statement shape is kept verbatim (it is what makes the R0 exit-kind
# expectation hold by construction); re-deriving it would put the same fact in
# two places. So the question is only whether the two agree, and a disagreement
# REFUSES rather than annotates.
ENV_PREFIXES = ("msg.", "tx.", "block.")

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


def observed_env(body, call_i, call_line):
    """What the emitted case sets for `msg.sender` / `msg.value` at THIS call.

    The LAST prank above the call wins, because that is the semantics forge
    gives it -- `vm.prank` sets the sender for the next call only. `msg.value`
    comes from the call's own `{value: ...}` option; its ABSENCE is 0, which is
    a fact about the EVM and not a guess.

    Returns {name: (value_or_None, evidence_text)}. A None value with evidence
    means "found something and could not read it"; a None value with no
    evidence means "the preamble says nothing about this".
    """
    sender, sender_ev = None, None
    for ln in body[:call_i]:
        m = _PRANK_RE.search(ln)
        if m:
            sender_ev = ln.strip()
            sender = _lit_int(_arg0(ln, m.end() - 1))
    value, value_ev = 0, "no {value:} option on the call, so msg.value is 0"
    m = _VALUE_RE.search(call_line)
    if m:
        value_ev = m.group(0)
        value = _lit_int(m.group(1))
    return {"msg.sender": (sender, sender_ev), "msg.value": (value, value_ev)}


def env_disagreements(body, call_i, call_line, region, pins):
    """(refusals, unchecked) for every width-1 environment quantity certified.

    `refusals` is non-empty exactly when the emitted case is KNOWN to run
    outside the certified slice, or when it cannot be shown to run inside it.
    `unchecked` names the environment quantities this driver has no way to
    compare -- block.timestamp and friends -- so they appear on the emitted PUT
    instead of being invisible, which is the failure this whole block exists to
    stop repeating.
    """
    want = {}
    for n, (lo, hi) in region.items():
        if n.startswith(ENV_PREFIXES) and lo == hi:
            want[n] = lo
    for n, v in pins.items():
        if n.startswith(ENV_PREFIXES) and n not in want:
            want[n] = v
    obs = observed_env(body, call_i, call_line)
    refusals, unchecked = [], []
    for n, v in sorted(want.items()):
        if n not in obs:
            unchecked.append(
                f"{n} == {v} is NOT CHECKED: this driver can compare only "
                f"msg.sender and msg.value against the emitted preamble, so "
                f"whether the test runs in that part of the slice is unknown")
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


CALL_LINE_RE_TMPL = r"^(\s*)(try )?(\w+)\.{unit}\("


def find_unit_call(lines, unit):
    """Index of the LAST line in `lines` that calls `unit` on an instance.

    The last one, because a reconstructed case can replay several transactions
    (measured on farming `decimals`: three revert-tolerant calls followed by
    the asserted one) and the path being generalised is the one the case's
    final, exit-classified call walks.  Its preceding statements are the
    sequence that establishes the entry state and are kept verbatim.
    """
    rx = re.compile(CALL_LINE_RE_TMPL.format(unit=re.escape(unit)))
    hit = None
    for i, ln in enumerate(lines):
        if rx.match(ln):
            hit = i
    return hit


def rewrite_call_args(line, unit, replacements):
    """Replace argument k of the call to `unit` with `replacements[k]`.

    Only the argument list is touched.  The receiver, the `try`/`{value:}`
    decoration and the statement shape are the emitter's and stay the
    emitter's -- that is what makes requirement 5 (the R0 exit-kind
    expectation) hold by construction instead of being re-derived here.
    """
    key = "." + unit + "("
    k = line.find(key)
    if k < 0:
        return None, None
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
        return None, None
    args = split_top_level(line[start:i])
    if len(args) == 1 and args[0] == "":
        args = []
    new = list(args)
    for idx, txt in replacements.items():
        if idx < len(new):
            new[idx] = txt
    return line[:start] + ", ".join(new) + line[i:], args


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
    if kind == "address":
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


def build_put(contract, unit, enc, depth_, path_function, region, holes, pins,
              params, emitted, case, layout, ladder_rows, notes, cell=None):
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
    if len(params) != len(args):
        notes.append(f"declared arity {len(params)} != emitted arity "
                     f"{len(args)}; refusing to rewrite positionally")
        return None, None

    # The environment the emitted case runs under must be the one certified.
    # Checked, not established -- see `env_disagreements`.
    env_refusals, env_unchecked = env_disagreements(
        body, call_i, call_line, region, pins)
    if env_refusals:
        notes.append("the emitted case does not run in the certified "
                     "environment slice: " + "; ".join(env_refusals))
        return None, None

    lifted, repl, sig, pre_lines = [], {}, [], []
    used = {b[0] for b in emitted.blocks}
    for idx, (pname, ptype) in enumerate(params):
        if pname not in region:
            continue                       # pinned: keep the emitter's literal
        lk = lift_kind(ptype)
        if lk is None:
            notes.append(f"coordinate `{pname}` has type `{ptype}`, which "
                         f"this emitter cannot bound; kept PINNED at the "
                         f"emitter's literal")
            continue
        kind, width = lk
        var = pname if pname not in used and pname != "c0" else "p_" + pname
        lo, hi = region[pname]
        sig.append((("address" if kind == "address" else f"uint{width}"), var))
        pre_lines += bound_lines(var, kind, width, lo, hi,
                                 sorted(holes.get(pname, ())))
        repl[idx] = var
        lifted.append(pname)

    new_call, _ = rewrite_call_args(call_line, unit, repl)

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
        if v not in layout:
            state_skipped.append(
                f"{name} (no storage slot: solc's layout does not list it, so "
                f"it is a constant/immutable and no test can set it)")
            continue
        slot, off, nb = layout[v]
        if lo == hi:
            val = str(lo)
        else:
            var = "s_" + v.lstrip("_")
            sig.append(("uint256", var))
            pre_lines += bound_lines(var, "uint", 256, lo, hi,
                                     sorted(holes.get(name, ())))
            val = var
        store_lines += slot_write_lines("address(c0)", slot, off, nb, val)
        stored.append(f"{name} := {val}")

    # --- the oracle --------------------------------------------------------
    pre_reads, post_reads, asserts, oracle_skipped = [], [], [], []
    seen_vars = []
    for var, text, verdict in ladder_rows:
        if verdict != "HOLDS":
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
            rd = slot_read_expr("address(c0)", slot, off, nb)
            pre_reads.append(f"    uint256 _pre_{var.lstrip('_')} = {rd};")
            post_reads.append(f"    uint256 _post_{var.lstrip('_')} = {rd};")
        a = rung_assertions(text, f"_pre_{var.lstrip('_')}",
                            f"_post_{var.lstrip('_')}", f"{var}: {text}")
        if a is None:
            oracle_skipped.append(f"{var}: {text} (rung shape not rendered)")
            continue
        asserts += a

    fname = f"test_put_{contract}_{unit}_path{enc}"
    sig_txt = ", ".join(f"{t} {n}" for t, n in sig)

    out = []
    out.append("")
    out.append(f"  // ===================== PUT (stage 4) "
               f"=====================")
    out.append(f"  // claim: {path_function}:path:{enc}   depth={depth_}")
    if cell:
        out.append(f"  // CELL {cell[0]} -- {cell[1]}")
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
    if asserts:
        out.append(f"  // ORACLE: {len(asserts)} assertion(s) from the "
                   f"surviving rungs of")
        out.append(f"  // --path-cov-assert, read through vm.load at the slot "
                   f"solc reports.")
    else:
        out.append(f"  // ORACLE: none emitted (see the run's report); the "
                   f"exit-kind")
        out.append(f"  // expectation below is still an assertion.")
    for s in oracle_skipped:
        out.append(f"  //   rung DROPPED: {s}")
    for s in state_skipped:
        out.append(f"  //   entry-state bound DROPPED: {s}")
    for s in env_unchecked:
        out.append(f"  //   environment NOT CHECKED: {s}")
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
    head_end = call_i
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
        out.append(ln)
    out.append(new_call)
    if post_reads:
        out += post_reads
        out += asserts
    for ln in body[call_i + 1:]:
        out.append(ln)
    out.append("  }")
    stats = {"fuzz_params": len(sig), "lifted": lifted, "asserts": len(asserts),
             "oracle_skipped": oracle_skipped,
             "state_stored": stored, "state_skipped": state_skipped,
             "env_unchecked": env_unchecked}
    return out, stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esbmc", default="esbmc")
    ap.add_argument("--sol", required=True)
    ap.add_argument("--ast", default=None)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
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
    ap.add_argument("--scope", choices=("focus", "whole"), default="focus",
                    help="focus passes --focus-function <unit> (the GATE "
                         "cell); whole drops it and lets every entry be "
                         "dispatched (the ARTEFACT cell, which with "
                         "--max-tx 2 is the only configuration measured to "
                         "reach cross-function state). The choice is RECORDED "
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
    assert_dir = os.path.join(a.workdir, "assert")
    os.makedirs(emit_dir, exist_ok=True)
    os.makedirs(assert_dir, exist_ok=True)

    notes = []

    # ---- 1. the emitter's own output: preamble + concrete case ------------
    cell_name, cell_rule = cell_of(a.scope, a.max_tx)
    print(f"[put] {a.contract}.{a.unit} enc={a.enc} depth={a.depth}")
    print(f"[put] CELL {cell_name}: {cell_rule}")
    print("[put] step 1: emit the concrete suite (preamble source of truth)")
    out1, rc1, w1 = run_esbmc(
        a.esbmc, a.sol, a.ast, a.contract, a.unit,
        ["--generate-foundry-testcase", "--cov-report-json"] + a.esbmc_arg,
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
    pf = None
    for c in rep.get("claims", []):
        cond = c.get("condition") or ""
        if (cond.split(":", 1)[0] if ":" in cond else "") != a.unit:
            continue
        if str(c.get("path_id")) == str(a.enc):
            pf = c.get("path_function")
            rd = c.get("path_depth")
            if a.depth is None:
                a.depth = int(rd)
                print(f"[put]   depth read from this run's own report: "
                      f"{a.depth}")
            elif str(rd) != str(a.depth):
                notes.append(f"depth mismatch: report says {rd}, spec says "
                             f"{a.depth}")
    if pf is None:
        print(f"[put] REFUSED: this run's report holds no claim for "
              f"{a.unit} enc={a.enc}, so the concrete case cannot be "
              f"identified. Nothing was lifted")
        return 1
    case = emitted.case_for(pf, a.enc)
    if case is None:
        print(f"[put] REFUSED: no emitted case names {pf}:path:{a.enc}. The "
              f"path was witnessed but its counterexample produced no test "
              f"(refused as an obstacle, an empty body, or an unrenderable "
              f"argument) -- so there is no concrete case to generalise")
        return 1
    print(f"[put]   concrete case: {case[1]} in contract {emitted.blocks[case[0]][0]}")

    # ---- 2. the assertion ladder -----------------------------------------
    print("[put] step 2: post-state assertion ladder over the certified region")
    spec = {"unit": a.unit, "enc": a.enc, "depth": a.depth,
            "region": [{"name": n, "lo": str(lo), "hi": str(hi)}
                       | ({"holes": [str(h) for h in holes[n]]}
                          if holes.get(n) else {})
                       for n, (lo, hi) in region.items()]
                      + [{"name": n, "lo": str(v), "hi": str(v)}
                         for n, v in pins.items()]}
    with open(os.path.join(assert_dir, "spec.json"), "w") as f:
        json.dump(spec, f)
    out2, rc2, w2 = run_esbmc(
        a.esbmc, a.sol, a.ast, a.contract, a.unit,
        ["--path-cov-assert", os.path.join(assert_dir, "spec.json"),
         "--cov-report-json"] + a.esbmc_arg,
        assert_dir, a.max_tx, a.timeout, a.memlimit, a.scope)
    rows, summary, refusal, blocker = parse_ladder(out2)
    if refusal:
        print(f"[put]   ladder REFUSED: {refusal}")
        notes.append(f"ladder refused: {refusal}")
    print(f"[put]   exit={rc2} {w2:.1f}s  rows={len(rows)} summary={summary}")
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
                       "ladder_refusal": refusal, "notes": notes}, f, indent=2)
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

    # ---- 3. storage layout, from solc ------------------------------------
    layout, err = storage_layout(a.forge_project, a.contract)
    if layout is None:
        print(f"[put] REFUSED: {err}. Without solc's storage layout a state "
              f"read would be a GUESSED slot, and a green assertion about the "
              f"wrong slot is worse than no assertion")
        return 1
    print(f"[put] step 3: storage layout — {len(layout)} readable scalar "
          f"slot(s): {', '.join(sorted(layout))}")

    # ---- 4. declared parameters ------------------------------------------
    params = None
    if a.ast:
        _n, args0 = rewrite_call_args(
            emitted.lines[case[3][0] + 1:case[3][1]][
                find_unit_call(emitted.lines[case[3][0] + 1:case[3][1]],
                               a.unit) or 0],
            a.unit, {})
        params = function_params(a.ast, a.contract, a.unit,
                                 len(args0) if args0 is not None else None)
    if params is None:
        print("[put] WARNING: declared parameters unavailable (no --ast, or "
              "the name did not resolve); no argument can be lifted")

    # ---- 5. build ---------------------------------------------------------
    put, stats = build_put(a.contract, a.unit, a.enc, a.depth, pf,
                           region, holes, pins, params, emitted, case,
                           layout, rows, notes,
                           cell=(cell_name, cell_rule))
    if put is None:
        print("[put] REFUSED: " + "; ".join(notes))
        return 1

    # Insert into the SAME test contract, so the PUT shares the deploy the
    # concrete tests use rather than carrying a second copy of it.
    cname, cstart, cend = emitted.blocks[case[0]]
    lines = list(emitted.lines)
    lines[cend:cend] = put
    txt = "\n".join(lines) + "\n"
    newc = f"{cname}_{a.contract}_{a.unit}_put{a.enc}{a.test_suffix}"
    txt = txt.replace(f"contract {cname} is Test", f"contract {newc} is Test")
    txt = re.sub(r'from "\./', 'from "../src/', txt)
    # Mocks are file-level contracts; two PUT files in one project would
    # redeclare them, so every `ESBMCMock_*` is suffixed per file.
    #
    # LONGEST NAME FIRST. `ESBMCMock_IERC20` is a strict prefix of
    # `ESBMCMock_IERC20Metadata`, so renaming the short one first rewrites the
    # long one's prefix and yields `ESBMCMock_IERC20_<suffix>Metadata` -- which
    # happens to compile, because the rewrite is uniform, and is exactly the
    # kind of name nobody can read back to its interface. MEASURED on farming,
    # which declares both.
    for m in sorted(set(re.findall(r"ESBMCMock_(\w+)", txt)),
                    key=len, reverse=True):
        txt = re.sub(r"ESBMCMock_" + re.escape(m) + r"\b",
                     f"ESBMCMock_{m}_{newc}", txt)
    dest = os.path.join(a.forge_project, "test", f"{newc}.t.sol")
    with open(dest, "w") as f:
        f.write(txt)
    print(f"[put] WROTE {dest}")
    print(f"[put]   fuzz parameters : {stats['fuzz_params']} "
          f"({', '.join(stats['lifted']) or 'none'})")
    print(f"[put]   oracle asserts  : {stats['asserts']}")
    for s in stats["oracle_skipped"]:
        print(f"[put]     rung dropped: {s}")
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
                   "file": dest, "test": f"test_put_{a.contract}_{a.unit}"
                                         f"_path{a.enc}",
                   "region": {k: [str(v[0]), str(v[1])]
                              for k, v in region.items()},
                   "holes": {k: [str(x) for x in v] for k, v in holes.items()},
                   "pins": {k: str(v) for k, v in pins.items()},
                   "ladder": [{"var": v, "text": t, "verdict": d}
                              for v, t, d in rows],
                   "ladder_summary": summary, "ladder_refusal": refusal,
                   # A region certified under one set of flags and a test
                   # emitted under another are two measurements; the artefact
                   # has to say which one it is.
                   "esbmc_extra_args": a.esbmc_arg,
                   "cell": {"name": cell_name, "scope": a.scope,
                            "max_tx": a.max_tx, "rule": cell_rule},
                   "stats": stats, "notes": notes}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
