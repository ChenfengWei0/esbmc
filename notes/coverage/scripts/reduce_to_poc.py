#!/usr/bin/env python3
"""Shrink a contract that will not run until the failure fits on a screen.

THE RULE THIS ENFORCES: when a real contract does not produce a result -- killed,
out of memory, no report, everything bounded-holds -- the next action is to
REDUCE IT, not to raise a limit. Raising `--memlimit` and lengthening timeouts
is explicitly out of bounds; a failure with no minimal reproduction is not a
finding, it is a debt.

Why it is worth automating rather than doing by eye: a 30-line hand-written
contract with a nested mapping reproduced st1inch's death (`std::bad_alloc` on
8 paths) AND exposed a second defect that the real benchmark could never have
shown -- a claim that had already been REFUTED came back as `U` with no reason
token. On the big contract that would have been absorbed into "too slow".

HOW IT REDUCES. Syntactic units, largest first, each removal kept only if the
failure SURVIVES:

    PASS 1  whole function-like blocks (function / modifier / constructor /
            receive / fallback) -- the biggest win, usually most of the file
    PASS 2  single statements inside whatever blocks remain
    PASS 3  whole TYPE-level blocks: contract / library / interface / struct /
            enum
    PASS 4  single state-variable declarations at contract scope

PASS 3 and 4 exist because of a measurement, not a hunch: reducing st1inch
against `z3-not-well-founded` deleted the focused function's ENTIRE body and
the failure survived, then stalled at 2830 lines -- everything left was type
structure, which passes 1 and 2 cannot touch. Four hand-written single-factor
candidates were refuted before adding them (struct-in-struct-with-mapping
through a mapping; a string state variable; a base-contract chain plus an
interface; an immutable of interface type), which is when guessing shapes stops
paying.

Dropping a contract that something else inherits from simply fails to compile,
and a candidate that does not compile is already rejected by the predicate, so
these passes need no dependency analysis -- only the willingness to try.

After each candidate removal the contract must still COMPILE and must still
FAIL THE SAME WAY. A candidate that changes the failure mode is rejected and
the element is put back -- that element is part of the cause, which is itself
the answer.

THE PREDICATE IS EXPLICIT, NEVER "it broke". `--fail-on` takes one of:

    no-report        the run produced no cov-report.json
    partial          the report exists and is marked partial
    crash            non-zero exit that is not the coverage FAILED signal
    budget           at least one claim carries `claim-budget-exceeded`
    bounded-holds    at least N paths are bounded-holds (with --min-bh)
    internal-defect  the run printed its own INTERNAL DEFECT invariant
    never-returns    still alive at --timeout and had to be killed
    z3-not-well-founded
                     z3 refused AT ENCODING TIME with `datatype is not
                     well-founded`
    solver-oom       the backend died allocating (`std::bad_alloc` / `Out of
                     memory`) -- NOT the same failure as never returning
    solver-unknown   the run FINISHED and wrote a report, and at least
                     --min-unknown claim(s) came back neither sat nor unsat.
                     Nothing died, so `crash` / `never-returns` / `no-report`
                     are all FALSE here. This is st1inch's actual blocker (59
                     of its 128 claims) and the one the branch gate turns into
                     a 0 that reads like zero coverage

"The same way" means the same predicate is still true. Reducing against "it
exits non-zero" is how a memory bug turns into a syntax error and the reduction
reports success. That is also why the last three are separate predicates rather
than `crash`: on st1inch the SAME query failed three different ways under three
backends -- bitwuzla never returned, cvc5 raised std::bad_alloc at 4 GB with
0.000 s of decision-procedure time, and z3 refused before solving at all -- and
`crash` would happily accept a reduction that swapped one for another.

THE BACKEND IS PART OF THE FAILURE, so `--solver-flags` is passed through
verbatim, printed before any reduction starts, and written into the reduced
file's banner. A reducer that always runs the default backend cannot reduce a
defect the default backend alone has, and cannot reduce a defect that only
appears under the backend actually in use: the path-coverage collector runs
st1inch with `--z3 --tuple-node-flattener`, so a reduction made without those
flags is a reduction of a different run.

AND SO IS THE UNIT. `--focus-function` is passed through for the same reason:
the st1inch encoder failure was observed under `--focus-function
setFeeReceiver`, and the whole contract is a different query. Reducing without
it would either make every candidate "fail" by timing out (so nothing is ever
removed) or reproduce some other unit's failure under the same name.

THE COMPILER IS PART OF THE ARTIFACT TOO. The benchmarks pin an exact pragma
-- st1inch pins 0.8.23, and its committed .solast was built by that compiler --
while the solc on PATH here is 0.8.34. Compiling a candidate with the wrong
front end makes the reduced file a reproduction of a run nobody measured, and
`predicate` short-circuits to False whenever a candidate does not compile, so
the reducer refuses before ESBMC is invoked at all and the refusal is
indistinguishable from "the failure went away". `--solc` names the binary to
use. It is deliberately not `solc-select use`: that switches the compiler for
the regression suite as well, which currently passes under 0.8.34.

Usage:
    reduce_to_poc.py --sol X.sol --contract C --fail-on no-report
                     [--tx 1] [--timeout 120] [--memlimit 4g] [--min-bh 1]
                     [--solver-flags="--z3 --tuple-node-flattener"]
                     [--focus-function setFeeReceiver]
                     [--solc ~/.solc-select/artifacts/solc-0.8.23/solc-0.8.23]
                     [--out notes/coverage/poc/R_X.sol]

Note the `=` in --solver-flags: argparse reads a separate value that begins
with `-` as another option and refuses the command outright.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"

COV_FAILED_EXIT = 1  # coverage witnessed something; NOT a crash


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


_BIN_FP = None


def _binary_fingerprint():
    st = ESBMC.stat()
    return (st.st_mtime_ns, st.st_size)


def _assert_same_binary():
    """A reduction is a CHAIN of comparisons, and every link must be judged by
    the same binary.

    This is not hypothetical. On 2026-08-01 a reduction of st1inch against
    `solver-unknown` was started, and esbmc was rebuilt TWICE while it ran --
    once to a pristine baseline and once back to a patched build whose whole
    subject was ARITHMETIC ENCODING. Every candidate after the first rebuild was
    judged by a different tool than the one that verified the original, and the
    predicate is exactly the kind that an encoding change can flip. The run had
    to be discarded at 4573 lines, having cost sixteen minutes.

    Nothing reported it. The reducer kept printing `dropped ... -> N lines` and
    the checkpoints kept landing on disk, so the output was indistinguishable
    from a sound reduction -- which is why this is a hard failure rather than a
    warning. A reduced file whose provenance spans two binaries is worse than no
    file: it looks like evidence.
    """
    global _BIN_FP
    fp = _binary_fingerprint()
    if _BIN_FP is None:
        _BIN_FP = fp
        return
    if fp != _BIN_FP:
        raise SystemExit(
            "\n*** ESBMC WAS REBUILT MID-REDUCTION -- STOPPING ***\n"
            f"    started with mtime_ns={_BIN_FP[0]} size={_BIN_FP[1]}\n"
            f"    now         mtime_ns={fp[0]} size={fp[1]}\n"
            "    Every candidate judged after the rebuild was compared against a\n"
            "    different tool than the one that verified the original, so the\n"
            "    reduction chain is not sound and its output must not be used.\n"
            "    Re-run against a fixed binary. Do not 'continue from' the\n"
            "    checkpoint on disk: it carries removals from both builds.")


def run_once(src_text, contract, tx, timeout, memlimit, solver_flags=(),
             focus=None, solc="solc"):
    """Compile and run one candidate in its own directory. Returns
    (compiles, rc, stdout, report_or_None). rc == -1 means it was killed at
    `timeout`, kept distinct from every real exit code because "never
    returned" is a failure mode in its own right here."""
    _assert_same_binary()
    d = Path(tempfile.mkdtemp(prefix="reduce_"))
    try:
        sol = d / "C.sol"
        sol.write_text(src_text)
        rc, out = sh([solc, "--ast-compact-json", str(sol)], timeout=120)
        if rc != 0:
            return False, rc, out, None
        ast = d / "C.solast"
        ast.write_text(out)
        cmd = [str(ESBMC), str(ast), "--sol", str(sol),
               "--solidity-path-coverage", "--cov-report-json",
               "--memlimit", memlimit, "--contract", contract,
               "--solidity-max-tx", str(tx)] + list(solver_flags)
        if focus:
            cmd += ["--focus-function", focus]
        rc, out = sh(cmd, cwd=str(d), timeout=timeout)
        rep = d / "cov-report.json"
        data = None
        if rep.exists():
            try:
                data = json.loads(rep.read_text())
            except ValueError:
                data = None
        return True, rc, out, data
    finally:
        shutil.rmtree(d, ignore_errors=True)


def predicate(kind, min_bh, compiles, rc, out, data, min_unknown=1):
    if not compiles:
        return False
    if kind == "no-report":
        return data is None
    if kind == "partial":
        return bool(data and (data.get("partial")
                              or data.get("summary", {}).get("partial")))
    if kind == "crash":
        return rc not in (0, COV_FAILED_EXIT)
    if kind == "budget":
        if not data:
            return False
        ur = data.get("summary", {}).get("U_reasons", {})
        return ur.get("claim-budget-exceeded", 0) > 0
    if kind == "bounded-holds":
        if not data:
            return False
        ur = data.get("summary", {}).get("U_reasons", {})
        return ur.get("bounded-holds", 0) >= min_bh
    if kind == "internal-defect":
        return "INTERNAL DEFECT" in (out or "")
    if kind == "never-returns":
        # run_once yields -1 only on the kill-at-timeout path.
        return rc == -1
    if kind == "z3-not-well-founded":
        return "datatype is not well-founded" in (out or "")
    if kind == "solver-oom":
        o = out or ""
        return "std::bad_alloc" in o or "Out of memory" in o
    if kind == "solver-unknown":
        # The report exists and the run finished; the solver simply returned
        # neither sat nor unsat for at least N claims. Distinct from every
        # failure above: nothing died, nothing was killed, nothing refused --
        # which is why `crash`, `never-returns` and `no-report` are all FALSE
        # here and reducing against any of them would reduce a different thing.
        #
        # This is st1inch's actual blocker: 59 of its 128 claims come back
        # solver-unknown, and the branch gate turns that into a 0 that reads
        # like zero coverage. A minimal contract that reproduces ONE such claim
        # is what turns it back into a finding.
        if not data:
            return False
        ur = data.get("summary", {}).get("U_reasons", {})
        return ur.get("solver-unknown", 0) >= min_unknown
    raise SystemExit(f"unknown --fail-on: {kind}")


# --------------------------------------------------------------------------
# Candidate removals, largest first.
# --------------------------------------------------------------------------
FUNC_RE = re.compile(
    r"^[ \t]*(function|modifier|constructor|receive|fallback)\b[^\n]*$")

# Type-level blocks. `abstract contract` is covered because the keyword
# `contract` is matched anywhere the line starts with an optional `abstract`.
TYPE_RE = re.compile(
    r"^[ \t]*(abstract[ \t]+)?(contract|library|interface|struct|enum)\b"
    r"[^\n]*$")

# A state-variable declaration at contract scope: an indented line ending in
# `;` that is not a statement inside a body. The caller only offers lines that
# are NOT inside a function-like block, so this stays a cheap shape test.
STATE_RE = re.compile(r"^[ \t]+[A-Za-z_][^;{}]*;[ \t]*$")


def blocks_matching(text, header_re):
    """(start, end, header) for every block whose header line matches, closed by
    brace depth rather than by a regex spanning the body -- a regex that tries
    to span a body stops at the first nested `}` and silently truncates."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if header_re.match(lines[i]):
            depth = 0
            started = False
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
                if not started and lines[j].rstrip().endswith(";"):
                    break  # a declaration, not a definition
                j += 1
            out.append((i, min(j, len(lines) - 1), lines[i].strip()))
            i = j + 1
        else:
            i += 1
    return out


def state_var_lines(text):
    """Line indices that look like contract-scope state variables: they match
    STATE_RE and are NOT inside any function-like block."""
    inside = set()
    for lo, hi, _ in blocks_matching(text, FUNC_RE):
        inside.update(range(lo, hi + 1))
    out = []
    for k, ln in enumerate(text.splitlines()):
        if k in inside:
            continue
        if STATE_RE.match(ln) and not ln.strip().startswith("//"):
            out.append(k)
    return out


def top_level_blocks(text):
    """(start_line, end_line, header) for every function-like block, matched by
    brace depth rather than by a regex over the whole body -- a regex that tries
    to span a body stops at the first nested `}` and silently truncates."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if FUNC_RE.match(lines[i]):
            depth = 0
            started = False
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
                if not started and lines[j].rstrip().endswith(";"):
                    break  # an abstract/interface declaration
                j += 1
            out.append((i, min(j, len(lines) - 1), lines[i].strip()))
            i = j + 1
        else:
            i += 1
    return out


def drop_lines(text, lo, hi):
    lines = text.splitlines()
    return "\n".join(lines[:lo] + lines[hi + 1:]) + "\n"


def statements_of(text, lo, hi):
    """Line indices inside a block that are plausibly removable statements:
    a single line ending in `;` that is not a declaration of something used
    later. Conservative on purpose -- a wrong removal is rejected by the
    predicate anyway, and being conservative keeps the search short."""
    lines = text.splitlines()
    out = []
    for k in range(lo + 1, hi):
        s = lines[k].strip()
        if s.endswith(";") and not s.startswith("//"):
            out.append(k)
    return out


def banner_for(a, shown):
    """The provenance header. Factored out because it is now written on EVERY
    checkpoint as well as at the end, and two copies of a provenance string is
    two things that can drift -- the reduced file would then claim a
    configuration the run did not use."""
    return (
        f"// MINIMAL REPRODUCTION, reduced automatically from {a.sol}\n"
        f"// against the predicate `{a.fail_on}` at "
        f"--solidity-max-tx {a.tx}, --memlimit {a.memlimit},\n"
        f"// timeout {a.timeout}s, backend flags {shown},\n"
        f"// focus-function "
        f"{a.focus_function or '(whole contract)'}, solc {a.solc}.\n"
        f"// The backend flags are part of the reproduction: the same query "
        f"fails\n"
        f"// differently under different backends, so a run without them is a "
        f"run of\n"
        f"// something else.\n"
        f"// Every element still here is one whose removal made the failure "
        f"GO AWAY,\n"
        f"// so this file is not merely smaller -- each remaining part is "
        f"load-bearing.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sol", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--fail-on", required=True)
    ap.add_argument("--tx", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memlimit", default="4g")
    ap.add_argument("--min-bh", type=int, default=1,
                    help="threshold for --fail-on bounded-holds ONLY")
    ap.add_argument("--min-unknown", type=int, default=1,
                    help="threshold for --fail-on solver-unknown ONLY. A "
                         "separate knob rather than a reuse of --min-bh: one "
                         "flag standing for two different counts is a name "
                         "that stops describing its contents")
    ap.add_argument("--solver-flags", default="",
                    help="extra backend flags. MUST use the `=` form -- "
                         "--solver-flags=\"--z3 --tuple-node-flattener\" -- "
                         "because argparse reads a separate value beginning "
                         "with `-` as another option and refuses")
    ap.add_argument("--focus-function", default=None,
                    help="restrict to one unit, as the observed failure was")
    ap.add_argument("--solc", default="solc",
                    help="solc binary to compile candidates with; must satisfy "
                         "the source's pragma or nothing compiles and the "
                         "reducer refuses without ever running ESBMC")
    ap.add_argument("--types-first", action="store_true",
                    help="run the TYPE-level pass (whole contract / library / "
                         "interface / struct / enum) BEFORE the function and "
                         "statement passes. Every candidate costs one solc "
                         "compile plus one ESBMC run, so the candidate COUNT is "
                         "the cost, and on a large flat the two differ by an "
                         "order of magnitude: st1inch's 4874-line flat offers "
                         "242 function-like blocks against 30 type-level ones, "
                         "and one type-level try can remove a 1144-line library "
                         "outright. Not the default, because on a single-contract "
                         "PoC the type pass has nothing to remove and costs a "
                         "wasted run per round")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    solver_flags = a.solver_flags.split()
    shown = " ".join(solver_flags) if solver_flags else "(default)"
    text = Path(a.sol).read_text()

    last = {}

    def fails(t):
        c, rc, out, data = run_once(t, a.contract, a.tx, a.timeout, a.memlimit,
                                    solver_flags, a.focus_function, a.solc)
        last.update(compiles=c, rc=rc, out=out, data=data)
        return predicate(a.fail_on, a.min_bh, c, rc, out, data,
                         a.min_unknown)

    print(f"# reducing {a.sol} against `{a.fail_on}`", flush=True)
    print(f"#   backend flags: {shown}", flush=True)
    print(f"#   focus-function: {a.focus_function or '(whole contract)'}",
          flush=True)
    print(f"#   solc           : {a.solc}", flush=True)
    print(f"#   timeout {a.timeout}s, memlimit {a.memlimit}, "
          f"tx {a.tx}\n", flush=True)

    # A reduction that removes the focused unit would be reducing a different
    # query, and PASS 1 removes whole functions -- so the unit is protected by
    # the predicate rather than by a rule: dropping it changes the failure and
    # the candidate is rejected. Said here because "the reducer deleted the
    # function I was focusing on" reads as a bug in the reducer if it happens.
    t0 = time.time()
    if not fails(text):
        # A REFUSAL MUST CARRY WHAT ACTUALLY HAPPENED. "Does not satisfy the
        # predicate" is a negative with no content: it does not separate "the
        # failure is gone" from "it failed a different way", "it was killed at
        # the timeout", "solc rejected it" or "it simply succeeded" -- and
        # those call for different next actions. Guessing between them is how a
        # defect that was FIXED and a defect that was RENAMED become the same
        # event in the notes.
        where = Path(a.out).with_suffix(".refused.log") if a.out else \
            Path("reduce_refused.log")
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(last.get("out") or "")
        print(f"REFUSING TO REDUCE: the ORIGINAL does not satisfy "
              f"`{a.fail_on}`.")
        print(f"  compiles           : {last.get('compiles')}"
              f"{'   <- solc rejected it; check --solc against the pragma' if last.get('compiles') is False else ''}")
        print(f"  exit code          : {last.get('rc')}"
              f"{'   (-1 = killed at the timeout)' if last.get('rc') == -1 else ''}")
        print(f"  cov-report.json    : "
              f"{'written' if last.get('data') is not None else 'absent'}")
        print(f"  full output        : {where}   "
              f"({len((last.get('out') or '').splitlines())} lines)")
        print("Reducing against a predicate the input does not meet produces a "
              "minimal contract for a different failure, and it looks like a "
              "success. Read the output above before choosing another "
              "predicate.")
        return 1
    print(f"  original reproduces `{a.fail_on}`   "
          f"({len(text.splitlines())} lines)\n", flush=True)

    # ---- CHECKPOINT AFTER EVERY ACCEPTED REMOVAL ----
    #
    # This used to write `--out` ONCE, at the very end. A reduction of a real
    # benchmark runs for hours, and the expected way a run that long ends on this
    # machine is a KILL -- so the single write at the end is the one moment that
    # may never arrive.
    #
    # MEASURED: the st1inch `solver-unknown` reduction ran ~45 minutes, got from
    # 4874 lines to 4016, and then the process was gone with NO output file. The
    # only thing that survived was the progress log, and only because that run
    # was launched with `python3 -u`. 858 lines of accepted removals -- every one
    # of them paid for with a compile and an ESBMC run -- were thrown away.
    #
    # Same shape, and the same fix, as the counterexample journal: the end of the
    # run may not happen, so write when the fact is established rather than when
    # the run concludes. Each write is atomic (.tmp then rename), so a kill
    # during the write cannot leave a half-file that still parses as Solidity.
    def checkpoint(t, note):
        if not a.out:
            return
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        tmp = outp.with_suffix(outp.suffix + ".tmp")
        tmp.write_text(banner_for(a, shown) +
                       f"// CHECKPOINT: {note}. This file is the reduction AS OF\n"
                       f"// THAT POINT, written after every accepted removal "
                       f"because a run\n"
                       f"// this long is expected to be killed -- a single write "
                       f"at the end is\n"
                       f"// the one moment that may never arrive. It reproduces "
                       f"the predicate;\n"
                       f"// it is simply not known to be MINIMAL.\n" + t)
        os.replace(str(tmp), str(outp))

    checkpoint(text, "original, before any removal")

    # The four passes, each a fixpoint loop over one kind of syntactic unit.
    # Extracted from the straight-line sequence they used to be so the ORDER can
    # be chosen -- see --types-first below.
    def pass_functions():
        nonlocal text
        changed = True
        while changed:
            changed = False
            blocks = top_level_blocks(text)
            blocks.sort(key=lambda b: b[0] - b[1])  # largest first
            for lo, hi, header in blocks:
                cand = drop_lines(text, lo, hi)
                if fails(cand):
                    print(f"  dropped  {header[:70]}   "
                          f"-> {len(cand.splitlines())} lines", flush=True)
                    text = cand
                    checkpoint(text, f"PASS fn, {len(text.splitlines())} lines")
                    changed = True
                    break

    def pass_statements():
        nonlocal text
        changed = True
        while changed:
            changed = False
            for lo, hi, header in top_level_blocks(text):
                for k in statements_of(text, lo, hi):
                    cand = drop_lines(text, k, k)
                    if fails(cand):
                        print(f"  dropped  stmt: "
                              f"{text.splitlines()[k].strip()[:60]}", flush=True)
                        text = cand
                        checkpoint(text,
                                   f"PASS stmt, {len(text.splitlines())} lines")
                        changed = True
                        break
                if changed:
                    break

    def pass_types():
        nonlocal text
        changed = True
        while changed:
            changed = False
            blocks = blocks_matching(text, TYPE_RE)
            blocks.sort(key=lambda b: b[0] - b[1])  # largest first
            for lo, hi, header in blocks:
                cand = drop_lines(text, lo, hi)
                if fails(cand):
                    print(f"  dropped  TYPE {header[:65]}   "
                          f"-> {len(cand.splitlines())} lines", flush=True)
                    text = cand
                    checkpoint(text,
                               f"PASS type, {len(text.splitlines())} lines")
                    changed = True
                    break

    def pass_state_vars():
        nonlocal text
        changed = True
        while changed:
            changed = False
            for k in state_var_lines(text):
                cand = drop_lines(text, k, k)
                if fails(cand):
                    print(f"  dropped  state: "
                          f"{text.splitlines()[k].strip()[:60]}", flush=True)
                    text = cand
                    checkpoint(text,
                               f"PASS state, {len(text.splitlines())} lines")
                    changed = True
                    break

    # THE ORDER IS A COST DECISION, AND IT IS NOW MEASURED RATHER THAN ARGUED.
    #
    # The default runs functions and statements first. The reason written here
    # was that "with the bodies gone a contract or library is far more likely to
    # be removable whole" -- a plausible argument, and it was never checked
    # against what the passes actually COST. Every candidate costs the same, one
    # solc compile plus one ESBMC run, so the number of candidates IS the cost:
    #
    #     st1inch__St1inch.flat.sol, 4874 lines, measured 2.4 min per candidate
    #       function-like blocks : 242  ->  9.7 h
    #       type-level blocks    :  30  ->  1.2 h
    #     and one type-level try can remove `library SafeCast` (1144 lines),
    #     `library Math` (674) or `library SafeERC20` (473) outright, taking
    #     dozens of function candidates with it.
    #
    # Both orders can be right for different inputs -- on a single-contract PoC
    # the type pass has nothing to remove and costs a wasted run each round. So
    # this is a FLAG rather than a new default, the default is unchanged, and the
    # order used is printed with the run so a reduction's provenance says which
    # one produced it.
    order = ([("types", pass_types), ("functions", pass_functions),
              ("statements", pass_statements), ("state-vars", pass_state_vars)]
             if a.types_first else
             [("functions", pass_functions), ("statements", pass_statements),
              ("types", pass_types), ("state-vars", pass_state_vars)])
    print(f"  pass order: {' -> '.join(n for n, _ in order)}"
          f"{'   (--types-first)' if a.types_first else '   (default)'}\n",
          flush=True)
    for _name, fn in order:
        fn()

    print(f"\n  reduced to {len(text.splitlines())} lines in "
          f"{round(time.time() - t0)}s\n", flush=True)
    print(text)

    if a.out:
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        # The FINAL write replaces the last checkpoint's caveat: every pass has
        # now run to fixpoint, so this file really is minimal under the four
        # passes -- which the checkpoints deliberately never claimed.
        outp.write_text(banner_for(a, shown) + text)
        print(f"  written to {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
