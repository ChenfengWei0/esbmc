#!/usr/bin/env python3
"""Drive ESBMC's Solidity complete-path generalisation loop.

The tool measures; this script decides. That split is deliberate and is kept
everywhere below: ESBMC never parses its own report, never chooses a ladder, and
never applies a shrink -- it answers exactly the query it is handed. Every policy
decision (which ladder, which span, when to stop) lives here, so changing policy
never touches the verifier.

The loop, and why each step is the shape it is:

  1. ENUMERATE.  `--solidity-path-coverage --cov-report-json` gives, per complete
     path, its identity (enc, depth) and a counterexample. `depth` matters as
     much as `enc`: a stage-2 query identifies a path by `tr == enc && cnt ==
     depth`, and the `cnt` conjunct is what stops a longer path whose 64-bit `tr`
     wrapped from answering for a shorter one.

  2. BRACKET (geometric).  A first linear ladder cannot work on a 256-bit input:
     any span wide enough to contain the boundary makes the resolution useless.
     So round 1 probes at 0, 1, 2, 4, ... 2^k. That brackets the bound within a
     factor of two whatever its magnitude, in ONE run.

     This replaces the rule the design originally recorded -- "take the span from
     the nearest sibling counterexample". That rule was measured NOT to work: a
     solver counterexample can sit arbitrarily far from the boundary, and on the
     first contract tried it sat at 2^256-1, which is the whole type. The bracket
     uses only the path's OWN verdicts, so it does not depend on where some other
     path's counterexample happened to land.

  3. REFINE (linear, inside the bracket).  Each further round divides the
     resolution by (probes+1) again, so precision is logarithmic in ROUNDS while
     every round stays a single batch. It never becomes an adaptive
     query-per-step search, which is what sank the withdrawn widening route.

  4. SUBTRACT.  Zero queries: path domains partition the input space, so an input
     in this path's outer box and in no sibling's must walk this path. ESBMC does
     this and prints a candidate region per path.

  5. CERTIFY.  `assume(box); assert(tr == pi)`. SUCCESSFUL means the region is
     certified. FAILED comes with a witness input inside the box that leaves the
     path, and ESBMC prints the exact cut that excludes it while keeping the
     path's own counterexample -- one refutation, one cut, no bisection. This
     script applies that cut and retries.

A candidate region is NEVER trusted because it was subtracted. Subtraction is
sound only if path enumeration is complete for the unit, so every region goes
through the independent certification query before it is reported as certified.
"""

import argparse
import copy
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# ONE LIST, NOT TWO. `solidity_path_put.py` already decides which extra ESBMC
# flags a driver may forward and which it refuses, with the reason per flag and
# a written argument for why the refusal is a refusal-to-guess rather than a
# current measurement. Copying that list here would be the same fact in two
# ledgers -- the defect this project keeps paying for -- so it is imported.
# No cycle: that module imports only the standard library.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solidity_path_put import (
    CHAIN_ID_MAX,
    ESTABLISHABLE_ENV_COORDS,  # noqa: E402,F401
    STRATEGY_FLAGS_REFUSED,
    check_esbmc_args,
    contract_state_types)
from solidity_ast_dependencies import (  # noqa: E402,F401
    SLOT_DEPENDENCY_POLICY, contract_state_esbmc_store_names, path_function_declaration_id,
    unit_mapping_slot_accesses, unit_contains_inline_assembly, unit_state_dependencies)

UINT256_MAX = (1 << 256) - 1
SIZE_T_MAX = (1 << 64) - 1
ADDRESS_MAX = (1 << 160) - 1
PATH_PROBE_EARLY_STOP_CLAIMS = int(os.environ.get("VERIPUT_PATH_PROBE_EARLY_STOP_CLAIMS", "128"))
PATH_PROBE_ENUM_FRACTION = float(os.environ.get("VERIPUT_PATH_PROBE_ENUM_FRACTION", "0.25"))
PATH_PROBE_ENUM_CAP_S = int(os.environ.get("VERIPUT_PATH_PROBE_ENUM_CAP_S", "90"))
PATH_PROBE_ENUM_MIN_S = int(os.environ.get("VERIPUT_PATH_PROBE_ENUM_MIN_S", "30"))
RE_PATH_PROBE_ADDED = re.compile(r"--path-cov-probe: unit '([^']+)' added ([0-9]+) "
                                 r"exit-latched claim\(s\)")
RE_PATH_COV_NO_CLAIMS_REACHED = re.compile(
    r"INTERNAL DEFECT .*?instrumented path claim\(s\) reached the solver.*?"
    r"The harness never entered any unit", re.DOTALL)


def _kill_process_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def _timeout_output(exc):

    def _txt(value):
        if value is None:
            return ""
        return value.decode(errors="replace") \
            if isinstance(value, bytes) else value

    return _txt(exc.stdout) + _txt(exc.stderr)


def run(esbmc,
        sol,
        contract,
        extra,
        max_tx,
        timeout,
        cwd,
        ast=None,
        focus=None,
        memlimit="8g",
        esbmc_args=(),
        result_only=True,
        probe_claim_stop=None):
    """One ESBMC invocation. Returns its combined output.

    `ast` names a PREBUILT .solast, passed positionally with --sol still naming
    the source so locations resolve. Without it the driver can only handle
    sources the locally installed solc happens to accept -- and every flattened
    benchmark input pins an exact `pragma solidity =X.Y.Z`, so on a machine
    carrying any other solc the driver could not run on them at all. Measured:
    that is the FIRST of three things that stopped this script from ever
    completing a run.

    `focus` narrows the harness dispatcher to one entry. It does NOT change the
    enumeration -- that was verified by comparing the content-addressed path key
    sets of both configurations, not merely their counts -- so it is a pure
    scope control. It is what makes a contract like EscrowSrc tractable at all:
    whole-contract it exceeds a 900s budget and produces nothing, focused on one
    method it finishes in seconds.
    """
    # ABSOLUTE, like every other path argument here. This runs with `cwd` set
    # to the workdir -- the emitted report's filename is fixed, so it must --
    # and a relative `--esbmc build/src/esbmc/esbmc` therefore resolves against
    # the workdir and raises FileNotFoundError before a single query is issued.
    # `--sol` and the AST were already absolutised; the binary was the one path
    # that was not, and it is the one the caller is most likely to type
    # relatively.
    cmd = [os.path.abspath(esbmc) if os.sep in esbmc else esbmc]
    if ast:
        cmd.append(os.path.abspath(ast))
    cmd += [
        "--sol",
        os.path.abspath(sol), "--contract", contract, "--solidity-path-coverage",
        "--solidity-max-tx",
        str(max_tx), "--memlimit", memlimit
    ]
    # ---- --result-only SUPPRESSES THE COUNTEREXAMPLE, AND ONE QUERY NEEDS IT
    #
    # Every round here judges on the RESULT line alone, so the trace is noise
    # and the flag stays on -- that is why it is the default of this parameter
    # and why no other call site passes it.
    #
    # The single-point check of §Certification is the exception. Its FAILED
    # verdict has TWO possible causes -- the witness trips a compiler-inserted
    # check, or a quantity outside the coordinate set is still free -- and this
    # driver has been REPORTING THEM UNSEPARATED, in those words, for want of
    # the one thing that names which: ESBMC's own `Violated property:` block.
    # That block is part of the counterexample, so asking for the verdict while
    # suppressing the trace was asking a question whose answer was thrown away
    # before it could be read.
    #
    # It is an OUTPUT flag. The query put to the solver is the same either way;
    # only what gets printed changes. The certified region of a path that does
    # NOT reach this branch must therefore come back byte-identical, which is
    # the control on this change.
    if result_only:
        cmd.append("--result-only")
    if focus:
        cmd += ["--focus-function", focus]
    cmd += extra
    # ---- THE CALLER'S OWN FLAGS, LAST ----
    #
    # This exists because the tool's OWN refusal names a repair this driver had
    # no way to apply. MEASURED on farming/approve: the certification query came
    # back UNDECIDED-TRUNCATED and said
    #
    #   "Re-run this path with a larger --unwind, or --unwindset/--unwindsetname
    #    on the loop(s) named, to get a verdict"
    #
    # and then named them (loop 55 and loop 56, both `_str_assign`,
    # src/c2goto/library/solidity/solidity_string.c). Without a passthrough the
    # only possible response to a named one-line repair was to record the
    # refusal -- which is exactly the gap `solidity_path_put.py` closed at
    # stage 4 and stage 2 still had.
    #
    # LAST on the command line on purpose, so a caller can override a default
    # this driver set; and applied to EVERY invocation (enumeration, every
    # outer-box round, every certification query), because a bound that differs
    # between the round that measured a region and the query that certifies it
    # is two measurements wearing one name.
    cmd += list(esbmc_args)
    cmd_line = " ".join(shlex.quote(part) for part in cmd)
    if probe_claim_stop is None:
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # A timeout is an OUTCOME of this pipeline, not a crash of it.
            # Measured: one outer-box round on a real contract unit (5 paths,
            # 2 coordinates, 4 probes) does not finish in 540s, so on real
            # input this is the common case rather than the exceptional one --
            # and it used to surface as a Python traceback in the middle of a
            # benchmark. Return the partial output with an explicit marker;
            # since it carries NEITHER verdict line, every caller reads it as
            # UNKNOWN, which is what it is.
            # `text=True` does NOT apply to the exception's captured output:
            # both attributes come back as bytes. Decode each side BEFORE
            # concatenating, or the handler itself raises -- which is what it
            # did on its first real use, turning a handled timeout into a
            # TypeError inside the handler.
            out = _timeout_output(e)
            return (out + f"\n[run] CMD {cmd_line}\n"
                    f"[run] TIMEOUT after {timeout}s: {cmd_line}\n")
        # RECORD THE EXIT CODE, and let callers judge on it rather than on
        # message text. ESBMC uses 0 for SUCCESSFUL and 1 for FAILED; anything
        # else means it did not finish -- 6 for a conversion error, 134 for an
        # abort, and so on.
        #
        # This replaces a whitelist of two known failure messages, which was
        # wrong in the way this file keeps being wrong: a THIRD cause (an abort
        # on a string-typed state coordinate, `Projecting from non-tuple based
        # AST`) matched neither pattern, so the round came back with no regions
        # and was reported downstream as "no fully bounded region was measured"
        # -- a property of the path, for what was a crash. A whitelist of
        # failures is open at the bottom; an exit code is not.
        return (p.stdout + p.stderr + f"\n[run] CMD {cmd_line}\n[run] EXIT {p.returncode}\n")

    output = []
    proc = subprocess.Popen(cmd,
                            cwd=cwd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            start_new_session=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    early_stop_reason = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process_group(proc)
                return ("".join(output) + f"\n[run] CMD {cmd_line}\n"
                        f"[run] TIMEOUT after {timeout}s: {cmd_line}\n")
            events = sel.select(timeout=min(0.2, remaining))
            for key, _mask in events:
                line = key.fileobj.readline()
                if not line:
                    continue
                output.append(line)
                m = RE_PATH_PROBE_ADDED.search(line)
                if m and int(m.group(2)) > probe_claim_stop:
                    early_stop_reason = (f"--path-cov-probe added {m.group(2)} "
                                         f"exit-latched claim(s) for {m.group(1)}, over "
                                         f"the fallback threshold {probe_claim_stop}")
                    _kill_process_group(proc)
                    return ("".join(output) + f"\n[run] CMD {cmd_line}\n"
                            f"[run] EARLY STOP: {early_stop_reason}\n")
            if proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    output.append(rest)
                break
    finally:
        try:
            sel.unregister(proc.stdout)
        except Exception:
            pass
        sel.close()
        if proc.poll() is None and early_stop_reason is not None:
            _kill_process_group(proc)

    return ("".join(output) + f"\n[run] CMD {cmd_line}\n[run] EXIT {proc.returncode}\n")


def save_failed_round(cwd, kind, spec, log, failure, wall_seconds):
    """Persist the exact failed outer-round query for later ESBMC diagnosis."""
    fail_dir = os.path.join(cwd, "failed-rounds")
    os.makedirs(fail_dir, exist_ok=True)
    existing = [
        name for name in os.listdir(fail_dir)
        if name.startswith(f"{kind}-") and name.endswith(".meta.json")
    ]
    prefix = os.path.join(fail_dir, f"{kind}-{len(existing) + 1:03d}")
    with open(prefix + ".outer.json", "w", encoding="utf-8") as stream:
        json.dump(spec, stream, indent=2, sort_keys=True)
    with open(prefix + ".log", "w", encoding="utf-8") as stream:
        stream.write(log)
    cmd_line = None
    for line in log.splitlines():
        if line.startswith("[run] CMD "):
            cmd_line = line[len("[run] CMD "):]
    meta = {
        "kind": kind,
        "failure": failure,
        "wallSeconds": round(wall_seconds, 3),
        "outerSpec": os.path.basename(prefix + ".outer.json"),
        "log": os.path.basename(prefix + ".log"),
        "cmd": cmd_line
    }
    with open(prefix + ".meta.json", "w", encoding="utf-8") as stream:
        json.dump(meta, stream, indent=2, sort_keys=True)
    return prefix + ".meta.json"


def parse_int(s):
    if isinstance(s, int):
        return s
    s = str(s).strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def claim_unit(c):
    """The unit a claim belongs to, spelled the way the user names it.

    NOT `c["function"]`. That field exists, is spelled exactly right, and is
    EMPTY on every complete-path claim -- measured on a toy contract and on
    EscrowSrc alike. Filtering on it matched ZERO paths on every input ever
    tried, which is why the stage-2 loop below had never once run to completion.

    The unit's plain name does reach the report: it is the prefix of the claim
    identity `<unit>:path:<id>`. A Solidity identifier cannot contain ':', so
    splitting on the first one is exact rather than heuristic.
    """
    cond = c.get("condition") or ""
    return cond.split(":", 1)[0] if ":" in cond else ""


def same_path_function(actual, expected):
    if not expected:
        return True
    if actual == expected:
        return True
    actual_id = path_function_declaration_id(actual)
    expected_id = path_function_declaration_id(expected)
    return actual_id is not None and actual_id == expected_id


def path_function_instrument_args(path_function):
    """Exact Stage-1 denominator selection for an AST-resolved unit."""
    return (["--path-cov-instrument-only", path_function] if path_function else [])


def _journal_env_name(name):
    for prefix in ("msg_", "tx_", "block_"):
        if name.startswith(prefix):
            return prefix[:-1] + "." + name[len(prefix):]
    return name


def named_value_items(values, *, env=False):
    """Return (name, value) pairs from report dicts or CE-journal lists."""
    if isinstance(values, dict):
        items = values.items()
    else:
        items = ((v.get("name"), v.get("value")) for v in values or [] if isinstance(v, dict))
    out = []
    for name, value in items:
        if not name:
            continue
        out.append((_journal_env_name(name) if env else name, value))
    return out


K_INDUCTION_PROOF_FLAGS_WITH_VALUE = {
    "--base-k-step",
    "--k-step",
    "--max-inductive-step",
    "--unwind",
    "--unwindset",
    "--unwindsetname",
    "--max-k-step",
}
K_INDUCTION_PROOF_FLAGS = {
    "--base-case",
    "--enable-forward-condition",
    "--disable-forward-condition",
    "--disable-inductive-step",
    "--falsification",
    "--forward-condition",
    "--partial-loops",
    "--incremental-bmc",
    "--overflow-check",
    "--div-by-zero-check",
    "--path-cov-arith-resolve",
    "--k-induction",
    "--k-induction-parallel",
    "--inductive-step",
    "--termination",
    "--unlimited-k-steps",
}


def k_induction_proof_args(existing):
    """Build the clean unbounded proof strategy for region certification."""
    out = []
    skip_value = False
    for raw in existing or ():
        arg = str(raw)
        if skip_value:
            skip_value = False
            continue
        name = arg.split("=", 1)[0]
        if name in K_INDUCTION_PROOF_FLAGS_WITH_VALUE:
            skip_value = "=" not in arg
            continue
        if name in K_INDUCTION_PROOF_FLAGS:
            continue
        out.append(arg)
    return out + [
        "--k-induction", "--enable-forward-condition", "--max-k-step", "30"
    ]


def named_values_dict(values, *, env=False):
    return {name: value for name, value in named_value_items(values, env=env)}


ENV_PREFIXES = ("msg.", "tx.", "block.")


def is_env(name):
    """Is this coordinate an EVM environment quantity rather than an input?

    Same namespace rule the tool uses to resolve coordinate names. It matters
    for POLICY, not just naming: environment quantities are pinned only when
    that is an explicit slice decision, and promoted only when the PUT emitter
    can establish them.
    """
    return name.startswith(ENV_PREFIXES)


def derive_env_coord_disagreed(paths, env_names, pins):
    """Split env names into PUT-establishable disagreements and explanations."""
    promoted, kept = [], []
    for n in list(env_names):
        if n in pins:
            kept.append(f"{n} (already pinned at {pins[n]})")
            continue
        vals = {ce.get(n) for _, _, ce in paths}
        if len(vals) == 1 and None not in vals:
            kept.append(f"{n} (all {len(paths)} paths agree)")
            continue
        if n not in ESTABLISHABLE_ENV_COORDS:
            kept.append(f"{n} (paths disagree, but the PUT emitter cannot "
                        "establish this environment quantity)")
            continue
        promoted.append(n)
    return promoted, kept


def derive_agreed_establishable_env_pins(paths, env_names, pins):
    """Environment pins that preserve PUT generation."""

    agreed, kept = {}, []
    for n in list(env_names):
        if n in pins:
            kept.append(f"{n} (already pinned at {pins[n]})")
            continue
        vals = {ce.get(n) for _, _, ce in paths}
        if len(vals) != 1 or None in vals:
            kept.append(f"{n} (paths disagree)")
            continue
        if n not in ESTABLISHABLE_ENV_COORDS:
            kept.append(f"{n} (all paths agree, but the PUT emitter cannot "
                        "establish this environment quantity)")
            continue
        value = vals.pop()
        if n == "msg.sender" and value == 0:
            kept.append("msg.sender (all paths agree at 0, but Foundry cannot establish "
                        "address(0) with vm.prank; leave it quantified for ESBMC "
                        "certification instead)")
            continue
        if n == "block.chainid" and not 0 <= value <= CHAIN_ID_MAX:
            kept.append(f"block.chainid (all paths agree at {value}, but Foundry "
                        "vm.chainId requires a value below 2^64; leave it quantified "
                        "for ESBMC certification instead)")
            continue
        agreed[n] = value
    return agreed, kept


def decision_read_env_coords(paths, path_decisions, pins, env_names):
    """Environment coordinates actually read by complete-path decisions."""

    env_set = set(env_names or [])
    if not env_set:
        return set()
    read = set()
    coord_set = env_set | set(pins or {})
    for enc, _depth, ce in paths:
        for d in (path_decisions or {}).get(enc) or []:
            rel = _decision_relation(d.get("branch_claim"), d.get("arm"))
            if rel is None:
                continue
            lhs, _op, rhs = rel
            for term in (lhs, rhs):
                got = _decision_term(term, ce, pins, constants=None, coord_set=coord_set)
                if got and got[0] == "coord" and got[1] in env_set:
                    read.add(got[1])
    return read


def decision_read_slot_coords(paths, path_decisions, slot_coords):
    """Proposed mapping slots that participate in complete-path identity."""
    spellings = {}
    for coord in slot_coords or ():
        spelling = re.sub(r"\$\d+(?=\[)", "", coord)
        if spelling.startswith("state."):
            spelling = spelling[len("state."):]
        spellings[coord] = spelling
    read = set()
    for enc, _depth, _ce in paths:
        for decision in (path_decisions or {}).get(enc) or ():
            claim = str(decision.get("branch_claim") or "")
            read.update(coord for coord, spelling in spellings.items() if spelling in claim)
    return read


def derive_agreed_unpinned_establishable_env_coords(paths,
                                                    env_names,
                                                    pins,
                                                    decision_env_names=None):
    """Agreed environment coordinates that must stay quantified.

    The main case is msg.sender == 0. Foundry cannot establish address(0) with
    vm.prank, so pinning is impossible; keeping it in env_names is worse,
    because then it is neither pinned nor a free coordinate. Leaving it free
    lets ESBMC decide whether the path really holds for an executable sender
    range. The PUT emitter already punches address(0) out of wide sender
    intervals before replay.
    """

    decision_env_names = (set(decision_env_names) if decision_env_names is not None else None)
    promoted = set()
    for n in list(env_names):
        if n in pins:
            continue
        vals = {ce.get(n) for _, _, ce in paths}
        if len(vals) != 1 or None in vals:
            continue
        if decision_env_names is not None and n not in decision_env_names:
            continue
        if n == "msg.sender" and vals.pop() == 0:
            promoted.add(n)
    return promoted


def live_witness_vectors(paths, members, pins):
    """Witness vectors that are known members of the currently pinned slice."""
    live_by_enc = {}
    n_violate = n_missing = 0
    for enc, _depth, ce in paths:
        live = []
        for v in (members or {}).get(enc) or [ce]:
            bad = [n for n, pv in (pins or {}).items() if n in v and v[n] != pv]
            gone = [n for n in (pins or {}) if n not in v]
            if bad:
                n_violate += 1
                continue
            if gone:
                n_missing += 1
                continue
            live.append(v)
        live_by_enc[enc] = live
    return live_by_enc, n_violate, n_missing


def pinned_slice_exclusions(paths, pins):
    """Return witnessed paths whose own CE is outside the pinned slice."""
    excluded = {}
    for enc, _depth, ce in paths:
        violations = []
        for name, value in sorted((pins or {}).items()):
            if name in ce and ce[name] != value:
                violations.append(f"{name}: CE {ce[name]} outside [{value}, {value}]")
        if violations:
            excluded[enc] = ("EXCLUDED FROM THE SLICE by the pins (" + "; ".join(violations) +
                             "), so this path is not an input to region search")
    return excluded


def struct_fields(text, nested=False):
    """Top-level scalar fields of a rendered struct, as {field: int}.

    `nested=True` additionally DESCENDS into aggregate-valued fields and returns
    their scalar leaves under dotted names (`farmInfo.finished`). OFF by default,
    so every recorded number reproduces byte for byte -- the paragraph below
    argues the depth-1 rule and that argument is not withdrawn, it is made
    optional. WHY IT HAS TO BE AVAILABLE AT ALL, measured on farming/deposit:
    the whole payload for `_farm` is

        { .farmInfo = { .finished = 0 } }

    whose only depth-1 field is an aggregate, so the depth-1 rule returns NOTHING
    and the coordinate set is empty for that state variable however it is wired.

    The report renders an aggregate as one string:

        { .orderHash={ .data=nil }, .taker=0, .amount=0 }

    Until the tool could resolve `immutables.taker` there was nothing to do with
    that but refuse the whole parameter -- and refusing it meant a unit whose
    ONLY argument is a struct had nothing generalisable at all. Measured across
    all five EscrowSrc units: every one reported zero coordinates, 23 witnessed
    paths between them, none of it a search failure.

    CONSERVATIVE ON PURPOSE, and it is a parse of a real format rather than a
    guess about one. Only DEPTH-1 fields whose value is a plain integer are
    returned:

      * a nested aggregate (`.orderHash={...}`) is skipped -- its own fields are
        reachable as `immutables.orderHash.data` once something asks for them,
        but flattening it here would invent coordinate names the caller did not
        request;
      * `nil` and any other non-integer is skipped, for the same reason
        coord_values refuses a symbolic slot: a coordinate must have a concrete
        value to be a known member of the domain.

    Skipping is silent HERE and reported by the caller, which already has the
    refusal channel for exactly this.
    """
    out = {}
    if not text.startswith("{"):
        return out
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "." and depth == 1:
            j = text.find("=", i)
            if j < 0:
                break
            name = text[i + 1:j].strip()
            k = j + 1
            while k < n and text[k] not in ",}":
                k += 1
            val = text[j + 1:k].strip()
            if name and val and not val.startswith("{"):
                try:
                    out[name] = parse_int(val)
                except ValueError:
                    pass
            elif nested and name and val.startswith("{"):
                # DESCEND. The scanner above stops at the first ',' or '}',
                # which is inside the nested aggregate, so the substring has to
                # be re-cut by BRACE BALANCE rather than by that index -- using
                # `val` here would hand the recursion a truncated string and it
                # would silently return nothing, which is the always-empty
                # channel this project keeps building by accident.
                b, d2 = j + 1, 0
                while b < n and text[b] != "{":
                    b += 1
                e = b
                while e < n:
                    if text[e] == "{":
                        d2 += 1
                    elif text[e] == "}":
                        d2 -= 1
                        if d2 == 0:
                            break
                    e += 1
                for sub, sv in struct_fields(text[b:e + 1], nested=True).items():
                    out[f"{name}.{sub}"] = sv
                # RESUME AFTER the nested aggregate's closing brace, not ON it.
                # ⛔ `i = e` loses every field that follows: the recursion
                # consumed the nested `{` without the outer scanner counting it,
                # so letting the loop see the matching `}` drops `depth` to 0 and
                # every later `.field` is then read at the wrong depth and
                # skipped. MEASURED the moment it was written -- the EscrowSrc
                # rendering `{ .orderHash={ .data=nil }, .taker=0, .amount=0 }`
                # came back EMPTY under nested=True while the depth-1 rule found
                # taker and amount. The control that caught it is case 3 of
                # check_state_struct_fields.py, which exists precisely because a
                # richer parse that silently returns LESS is the shape this
                # project keeps shipping.
                i = e + 1
                continue
            i = j
        i += 1
    return out


def coord_values(c,
                 state_structs=False,
                 param_types=None,
                 state_types=None,
                 extcall_coord_specs=None):
    """This claim's counterexample as {coordinate: int}, plus what was refused.

    A coordinate must be a quantity a generated test can SET, and it can only be
    set if it has a concrete scalar value. Two kinds of entry do not:

      * struct- and bytes-typed parameters, which the report renders as a
        pretty-printed aggregate (`{ .orderHash={ ... }, .taker=0, ... }`);
      * entry-storage slots whose model value is symbolic
        (`_ESBMC_aux_Escrow.PROXY_BYTECODE_HASH`).

    The settled rule is that such coordinates are UNSUPPORTED and must be
    REFUSED. Before this they were neither: `int()` was called on them and the
    driver died with a ValueError halfway through a benchmark. Refusing is not
    the same as ignoring -- every refused name is returned and printed, because
    a coordinate that silently vanishes turns a region measured over a SLICE
    into one that reads as a statement about the whole input space.
    """
    ce, refused = {}, []
    param_types = dict(param_types or {})
    state_types = dict(state_types or {})
    extcall_coord_specs = {
        str(spec.get("coord")): spec
        for spec in (extcall_coord_specs or []) if isinstance(spec, dict) and spec.get("coord")
    }
    for n, v in named_value_items(c.get("env"), env=True):
        # EVM environment (msg.*/tx.*/block.*). The tool resolves these names as
        # coordinates already -- what was missing is that the driver never read
        # them out of the report, and that omission is a systematic yield
        # killer rather than a detail: a NON-payable function carries an
        # ABI-level decision on msg.value, so a box that leaves msg.value
        # unconstrained always contains an input that reverts at the gate and
        # certification is refused however far the box is shrunk. Measured by
        # intervention on one contract and one path: box unchanged on
        # (a, state.s), certification FAILED with "no single-coordinate
        # shrink"; add `msg.value` pinned to 0 and the SAME query returns
        # VERIFICATION SUCCESSFUL. Non-payable is most real code, so this is
        # most paths.
        #
        # The name goes in UNPREFIXED: the tool resolves `msg.value`, not
        # `env.msg.value`, and these already carry their own namespace, so no
        # parameter can collide with them (a Solidity identifier has no dot).
        try:
            ce[n] = parse_int(v)
        except ValueError:
            refused.append(n)
    for n, v in named_value_items(c.get("inputs")):
        try:
            ce[n] = parse_int(v)
        except ValueError:
            bty = param_types.get(n)
            if bytes_static_len(bty) is not None:
                bv = bytes_static_value_from_ce(bty, v)
                if bv is not None:
                    ce[n] = bv
                    continue
                refused.append(f"{n} ({bty}: counterexample value is not a concrete "
                               "bytesN aggregate)")
                continue
            if bytes_dynamic_type(bty):
                fields = struct_fields(v)
                if "length" in fields:
                    ce[f"{n}.length"] = fields["length"]
                    refused.append(f"{n} ({bty}: dynamic bytes aggregate; using length "
                                   "only. offset/capacity/initialized/padding are "
                                   "verifier-internal representation fields, not PUT "
                                   "coordinates)")
                else:
                    refused.append(f"{n} ({bty}: dynamic bytes aggregate without a "
                                   "concrete length field)")
                continue
            # A STRUCT is not unusable, it is unusable AS ONE COORDINATE. Its
            # scalar fields each are one, and the tool resolves `param.field`
            # now, so decompose rather than refuse the whole argument -- that
            # refusal is what left every EscrowSrc unit with nothing to
            # generalise. The parameter itself is still refused (an aggregate
            # has no interval); only its fields are added.
            fields = struct_fields(v)
            for fn, fv in fields.items():
                ce[f"{n}.{fn}"] = fv
            if fields:
                refused.append(f"{n} (aggregate; {len(fields)} scalar field(s) used "
                               f"instead: " + ", ".join(sorted(fields)) + ")")
            else:
                refused.append(n)
    for n, v in named_value_items(c.get("entry_storage")):
        try:
            ce["state." + n] = parse_int(v)
        except ValueError:
            source_n = re.sub(r"\$\d+$", "", n)
            sty = state_types.get(n) or state_types.get(source_n)
            if bytes_static_len(sty) is not None:
                bv = bytes_static_value_from_ce(sty, v)
                if bv is not None:
                    ce["state." + n] = bv
                    continue
                refused.append(f"state.{n} ({sty}: counterexample value is not a "
                               "concrete bytesN aggregate)")
                continue
            # ---- THE DECOMPOSITION ABOVE REACHED ONE OF THE TWO SOURCES ----
            #
            # `inputs` has had struct_fields since the EscrowSrc measurement
            # ("that refusal is what left every EscrowSrc unit with nothing to
            # generalise"). `entry_storage` never did: a struct-valued STATE
            # variable was refused whole, so a guard reading one of its scalar
            # fields had no coordinate under any flag. One fact, two ledgers,
            # and only one of them updated.
            #
            # MEASURED, farming/deposit: the payload's only aggregate is
            # `_farm`, the refusal line reads
            #     [coords] UNSUPPORTED, refused as coordinates (not scalar):
            #              state._farm
            # and six of the seven paths end at the coordinate gate saying the
            # separating quantity "is not in the payload at all".
            #
            # OFF BY DEFAULT. Turning it on changes the coordinate set of every
            # unit whose state holds a struct, which changes what every recorded
            # region is a statement ABOUT -- the same house rule --level0,
            # --max-holes and --max-region-pieces follow.
            fields = struct_fields(v, nested=True) if state_structs else {}
            for fn, fv in fields.items():
                ce[f"state.{n}.{fn}"] = fv
            if fields:
                refused.append(f"state.{n} (aggregate; {len(fields)} scalar field(s) used "
                               f"instead: " + ", ".join(sorted(fields)) + ")")
            else:
                refused.append("state." + n)
    for e in (c.get("extcall_returns") or []):
        name = (e.get("symbol") or e.get("name")) if isinstance(e, dict) \
            else None
        if name not in extcall_coord_specs:
            continue
        try:
            ce[name] = parse_int(e.get("value"))
        except (ValueError, TypeError):
            refused.append(f"{name} (external-call return coordinate had no concrete "
                           "scalar value)")
    return ce, refused


def bytes_static_len(type_string):
    m = re.fullmatch(r"bytes([1-9]|[12][0-9]|3[0-2])", type_string or "")
    return int(m.group(1)) if m else None


def bytes_dynamic_type(type_string):
    norm = (type_string or "").strip()
    for suffix in (" memory", " calldata", " storage"):
        if norm.endswith(suffix):
            norm = norm[:-len(suffix)].strip()
    return norm in ("bytes", "string")


def elementary_type_range(type_string):
    """Closed unsigned scalar range for a Solidity elementary type, if known."""
    norm = (type_string or "").strip()
    if norm in ("address", "address payable"):
        return 0, ADDRESS_MAX
    if norm == "bool":
        return 0, 1
    m = re.fullmatch(r"uint([0-9]*)", norm)
    if m:
        bits = int(m.group(1) or "256")
        if 0 < bits <= 256 and bits % 8 == 0:
            return 0, (1 << bits) - 1
    n = bytes_static_len(norm)
    if n is not None:
        return 0, (1 << (8 * n)) - 1
    return None


def dynamic_parameter_length_ranges(params):
    """ESBMC coordinate domains for dynamic bytes/string ABI lengths.

    Solidity exposes ``p.length`` as uint256, but the frontend's
    ``BytesDynamic.length`` field is ``size_t``.  Certification rejects a box
    outside that field's own domain instead of allowing the query value to
    wrap, so the first region must use the runtime representation's width.
    """
    items = params.items() if isinstance(params, dict) else (params or ())
    return {
        f"{name}.length": (0, SIZE_T_MAX)
        for name, type_string in items if name and bytes_dynamic_type(type_string)
    }


def unobserved_scalar_parameter_coords(params, observed, pins, env_names):
    """Return scalar ABI parameters absent from a path counterexample.

    ESBMC is allowed to slice a parameter that does not affect a path decision
    (for example, an unconditional revert).  Such a parameter is still a
    caller-controlled coordinate: omitting it would turn a universal region
    into a concrete replay merely because the witness did not need to read it.
    Aggregate and dynamic parameters remain outside this coordinate model and
    are left for the existing refusal/materialisation paths.
    """
    observed = set(observed or ())
    pins = set(pins or ())
    env_names = set(env_names or ())
    items = (params.items() if isinstance(params, dict) else (params or ()))
    return sorted({
        name
        for name, type_string in items if name and name not in observed and name not in pins
        and name not in env_names and elementary_type_range(type_string) is not None
    })


def bytes_static_value_from_ce(type_string, raw_value):
    """Return the raw uint value carried by a concrete bytesN CE aggregate."""
    n = bytes_static_len(type_string)
    if n is None:
        return None
    raw = str(raw_value).strip()
    if not raw or "nil" in raw:
        return None
    try:
        value = parse_int(raw)
    except ValueError:
        m = re.search(r"\.data\s*=\s*\{([^{}]*)\}", raw)
        if not m:
            return None
        items = [p.strip() for p in m.group(1).split(",") if p.strip()]
        data = []
        for item in items:
            try:
                b = parse_int(item)
            except ValueError:
                return None
            if b < 0 or b > 255:
                return None
            data.append(b)
        # ESBMC renders a fixed BytesStatic value through its 32-byte backing
        # buffer.  For bytesN, only the first N bytes are the source value;
        # zero padding after that is representation detail, not an unknown
        # input.  Do not discard non-zero data outside the declared width:
        # accepting it would silently reinterpret a malformed CE.
        if len(data) > n:
            if any(data[n:]):
                return None
            data = data[:n]
        data += [0] * (n - len(data))
        value = 0
        for b in data:
            value = (value << 8) | b
    if value < 0 or value >= (1 << (8 * n)):
        return None
    return value


def bytes_static_mapping_key_from_ce(type_string, raw_value):
    """Return the uint256 mapping-key literal for a bytesN CE value.

    The Solidity frontend does not index mappings by the raw BytesStatic
    aggregate. It lowers bytesN keys through bytes_static_to_mapping_key:
        (len << 248) | bytes_static_to_uint(data)

    So a slot such as m[strategyHash] can only be named in a stage-2 query if
    the aggregate parameter is first fixed to the concrete counterexample slice
    and emitted as that numeric key. The raw bytesN parameter itself is still a
    fuzzable coordinate when the AST says the source type is bytes1..bytes32.
    """
    n = bytes_static_len(type_string)
    if n is None:
        return None
    value = bytes_static_value_from_ce(type_string, raw_value)
    if value is None:
        return None
    key = (n << 248) | value
    return f"0x{key:064x}"


def bytes_static_mapping_key_from_value(type_string, value):
    """Return the uint256 mapping-key literal from a typed bytesN scalar.

    `coord_values` already decodes a concrete bytesN aggregate into the raw
    bytes integer when the AST type is available.  Salvaged or imported reports
    can therefore have enough typed CE data to name the mapping slot even when
    the original pretty-printed aggregate is unavailable.
    """
    n = bytes_static_len(type_string)
    if n is None:
        return None
    try:
        value = parse_int(value)
    except (TypeError, ValueError):
        return None
    if value < 0 or value >= (1 << (8 * n)):
        return None
    key = (n << 248) | value
    return f"0x{key:064x}"


def witnessed_raw_inputs(cwd, unit, paths, path_function=None):
    report = os.path.join(cwd, "cov-report.json")
    try:
        with open(report) as f:
            rep = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    wanted = {(int(enc), int(depth)) for enc, depth, _ in paths}
    out = []
    for c in rep.get("claims", []) or []:
        if c.get("status") != "F" or claim_unit(c) != unit:
            continue
        if not same_path_function(c.get("path_function"), path_function):
            continue
        try:
            key = (int(c["path_id"]), int(c["path_depth"]))
        except (KeyError, TypeError, ValueError):
            continue
        if key in wanted:
            out.append(named_values_dict(c.get("inputs") or {}))
    return out


def path_cov_probe_goal_cap(log):
    text = log or ""
    if "Sampling " in text and "instead of refusing" in text:
        return False
    return (("--path-cov-probe:" in text and "exceeding --path-cov-max-goals" in text)
            or "path coverage probe universe exceeded --path-cov-max-goals" in text)


def path_cov_probe_early_stop(log):
    return ("--path-cov-probe:" in (log or "")
            and "[run] EARLY STOP: --path-cov-probe added" in (log or ""))


def path_cov_probe_timeout(log):
    return ("--path-cov-probe" in (log or "") and "[run] TIMEOUT after" in (log or ""))


def path_cov_no_claims_reached_solver(log):
    return bool(RE_PATH_COV_NO_CLAIMS_REACHED.search(log or ""))


def path_cov_basic_enum_fallback_reason(log):
    if path_cov_probe_goal_cap(log):
        return "path-cov-probe-goal-cap"
    if path_cov_probe_early_stop(log):
        return "path-cov-probe-early-stop"
    if path_cov_probe_timeout(log):
        return "path-cov-probe-timeout"
    if path_cov_no_claims_reached_solver(log):
        return "path-coverage-no-claims-reached-solver"
    return None


def path_cov_probe_enum_timeout(timeout, probe_witnesses):
    if not probe_witnesses:
        return timeout
    if timeout <= PATH_PROBE_ENUM_MIN_S + 30:
        # A short RQ1 unit budget cannot afford the historical 30-second probe
        # minimum. Giving probe/all-witnesses the whole window means a timeout
        # leaves zero seconds for the basic enumeration fallback, so a cheap
        # two-claim overload produces no journal at all. Preserve the same 25%
        # policy used by long runs, with the remaining 75% reserved for the
        # evidence-producing basic enumeration and certification stages.
        return max(1, int(timeout * PATH_PROBE_ENUM_FRACTION))
    fractional = int(timeout * PATH_PROBE_ENUM_FRACTION)
    capped = min(PATH_PROBE_ENUM_CAP_S, fractional)
    return max(PATH_PROBE_ENUM_MIN_S, capped)


def agreed_bytes_mapping_key_literals(raw_inputs, params, typed_paths=None):
    literals, skipped = {}, []
    for name, type_string in params:
        if bytes_static_len(type_string) is None:
            continue
        vals = set()
        seen = 0
        for raw in raw_inputs:
            raw = named_values_dict(raw)
            if name not in raw:
                continue
            seen += 1
            key = bytes_static_mapping_key_from_ce(type_string, raw[name])
            if key is None:
                skipped.append(f"{name} ({type_string}: counterexample value is not a "
                               "concrete bytesN aggregate)")
                vals = set()
                break
            vals.add(key)
        if not seen:
            for item in typed_paths or []:
                ce = item[2] if isinstance(item, tuple) and len(item) >= 3 \
                    else item
                if not isinstance(ce, dict) or name not in ce:
                    continue
                seen += 1
                key = bytes_static_mapping_key_from_value(type_string, ce[name])
                if key is None:
                    skipped.append(f"{name} ({type_string}: typed path value is not a "
                                   "concrete bytesN scalar)")
                    vals = set()
                    break
                vals.add(key)
        if len(vals) == 1:
            literals[name] = next(iter(vals))
        elif seen and len(vals) > 1:
            skipped.append(f"{name} ({type_string}: witnessed paths disagree on the "
                           "bytesN mapping-key slice)")
    return literals, skipped


def _journal_claim_parts(entry):
    claim = entry.get("claim") or ""
    m = re.match(r"(?P<pf>.+):path:(?P<pid>\d+)$", claim)
    path_function = entry.get("path_function")
    path_id = entry.get("path_id")
    if m:
        path_function = path_function or m.group("pf")
        path_id = path_id or m.group("pid")
    condition = entry.get("condition") or ""
    cm = re.match(r"(?P<unit>[^:]+):path:(?P<pid>\d+)$", condition)
    unit = cm.group("unit") if cm else ""
    path_id = path_id or (cm.group("pid") if cm else None)
    if not unit and path_function:
        fm = re.search(r"@F@([^#]+)#", path_function)
        unit = fm.group(1) if fm else ""
    return path_function, unit, path_id


def _journal_witness_count(entry):
    try:
        return int(entry.get("witness_count"))
    except (TypeError, ValueError):
        return 1 + len(entry.get("witnesses") or [])


def report_from_ce_journal(journal):
    """Convert a partial CE journal into a partial path enumeration report.

    A journal row exists only after ESBMC has refuted a path claim, so this is
    refutation evidence for candidate generation only. The returned report is
    stamped partial and is still fed into the normal certification stage before
    any region can be counted as proved.
    """
    if journal.get("kind") != "solidity-complete-path-ce-journal":
        return None
    witnessed = journal.get("witnesses") or {}
    if not isinstance(witnessed, dict) or not witnessed:
        return None
    claims = []
    for entry in witnessed.values():
        if not isinstance(entry, dict):
            continue
        path_function, unit, path_id = _journal_claim_parts(entry)
        if not path_function or not unit or path_id is None:
            continue
        depth = entry.get("path_depth") or entry.get("decision_depth")
        if depth is None:
            try:
                depth = int(path_id).bit_length() - 1
            except (TypeError, ValueError):
                continue
        try:
            path_id_int = int(path_id)
            depth_int = int(depth)
        except (TypeError, ValueError):
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
                "witness_count": _journal_witness_count(entry),
            },
            "condition":
            f"{unit}:path:{path_id_int}",
            "decisions":
            entry.get("decisions") or [],
            "entry_storage":
            named_values_dict(entry.get("entry_storage")),
            "env":
            named_values_dict(entry.get("env"), env=True),
            "events":
            entry.get("events") or [],
            "exit_kind":
            "revert" if entry.get("revert_pre_rollback") else "normal",
            "extcall_returns": [{
                "symbol": name,
                "value": value,
            } for name, value in named_value_items(entry.get("extcall_returns"))],
            "final_state":
            named_values_dict(entry.get("final_state")),
            "function":
            "",
            "inputs":
            named_values_dict(entry.get("inputs")),
            "line":
            0,
            "path_depth":
            depth_int,
            "path_function":
            path_function,
            "path_id":
            str(path_id_int),
            "return_value":
            entry.get("return_value"),
            "return_value_known":
            bool(entry.get("return_value_known")),
            "state_written_value_unavailable":
            entry.get("state_written_unrendered") or [],
            "status":
            "F",
            "witnessed_in_earlier_round":
            False,
        }
        extra_witnesses = []
        for witness in entry.get("witnesses") or []:
            if not isinstance(witness, dict):
                continue
            extra_witnesses.append({
                "entry_storage":
                named_values_dict(witness.get("entry_storage")),
                "env":
                named_values_dict(witness.get("env"), env=True),
                "extcall_returns": [{
                    "symbol": name,
                    "value": value,
                } for name, value in named_value_items(witness.get("extcall_returns"))],
                "final_state":
                named_values_dict(witness.get("final_state")),
                "inputs":
                named_values_dict(witness.get("inputs")),
                "return_value":
                witness.get("return_value"),
                "return_value_known":
                bool(witness.get("return_value_known")),
            })
        if extra_witnesses:
            claim["witnesses"] = extra_witnesses
        claims.append(claim)
    if not claims:
        return None
    total = journal.get("claims_total") or len(claims)
    decided = journal.get("claims_decided")
    try:
        total_int = int(total)
    except (TypeError, ValueError):
        total_int = len(claims)
    try:
        decided_int = int(decided)
    except (TypeError, ValueError):
        decided_int = len(claims)
    return {
        "claims": claims,
        "coverage_type": "solidity-complete-path",
        "partial": True,
        "summary": {
            "F_feasible_with_ce":
            len(claims),
            "F_with_multiple_witnesses":
            sum(1 for c in claims if (c.get("ce_extraction") or {}).get("witness_count", 1) > 1),
            "U_undecided":
            max(0, total_int - len(claims)),
            "covered":
            len(claims),
            "partial":
            True,
            "paths_total":
            total_int,
            "total":
            total_int,
            "uncovered":
            max(0, total_int - len(claims)),
            "witnesses_total":
            sum((c.get("ce_extraction") or {}).get("witness_count", 1) for c in claims),
        },
        "veriput_salvage": {
            "from": "cov-ce-journal.json",
            "claims_decided": decided_int,
            "claims_total": total_int,
            "reason": "outer-timeout-with-feasible-path-witnesses",
        },
    }


def partial_journal_report(cwd):
    path = os.path.join(cwd, "cov-ce-journal.json")
    try:
        with open(path, encoding="utf-8") as stream:
            return report_from_ce_journal(json.load(stream))
    except (OSError, json.JSONDecodeError):
        return None


def enumeration_salvage_path(cwd):
    return os.path.join(cwd, "enumeration-salvage.json")


def enumeration_report_snapshot_path(cwd):
    return os.path.join(cwd, "enumeration-report.json")


def generalise_progress_path(cwd):
    return os.path.join(cwd, "generalise-progress.json")


def ce_collection_path(cwd):
    return os.path.join(cwd, "ce-collection.json")


def _ce_collection_value(value):
    if isinstance(value, dict):
        return {str(k): _ce_collection_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ce_collection_value(v) for v in value]
    return str(value) if isinstance(value, int) else value


def _journal_entries_for_paths(cwd, paths, path_function=None):
    """Return the journal rows that exactly identify the enumerated paths.

    ``paths`` contains the normalized coordinate projection used by the
    generaliser.  That projection deliberately drops aggregate state and
    external-call values, so it is not sufficient as a replay witness by
    itself.  The CE journal is the authoritative source for those values.

    Matching is by the complete path identity, not by ``enc`` alone.  Path
    numbers are local to a function and certification-query journals may carry
    suffixed ids; accepting an ambiguous or foreign row here would turn a
    real witness into a plausible-looking, but wrong, replay artifact.
    """
    journal_path = os.path.join(cwd, "cov-ce-journal.json")
    try:
        with open(journal_path, encoding="utf-8") as stream:
            journal = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return journal_path, None, {}, ["cov-ce-journal.json is absent or invalid"]
    if journal.get("kind") != "solidity-complete-path-ce-journal":
        return journal_path, journal, {}, ["cov-ce-journal.json has an unsupported kind"]
    rows = journal.get("witnesses")
    if not isinstance(rows, dict):
        return journal_path, journal, {}, ["cov-ce-journal.json has no witness map"]

    wanted = {}
    for enc, depth, _ce in paths:
        wanted[(str(int(enc)), str(int(depth)))] = (enc, depth)
    found = {}
    errors = []
    for value in rows.values():
        if not isinstance(value, dict):
            continue
        pf, _unit, pid = _journal_claim_parts(value)
        try:
            key = (str(int(pid)), str(int(value.get("path_depth"))))
        except (TypeError, ValueError):
            continue
        if path_function and not same_path_function(pf, path_function):
            continue
        target = wanted.get(key)
        if target is None:
            continue
        enc, depth = target
        if enc in found:
            errors.append(f"multiple journal rows matched path enc={enc} depth={depth}")
            continue
        found[enc] = value
    for enc, depth, _ce in paths:
        if enc not in found:
            errors.append(f"no exact journal witness for path enc={enc} depth={depth}")
    return journal_path, journal, found, errors


def _journal_ce_artifact(journal, entry, path_ce, members, decisions, refused, caveats):
    """Build an explicit, non-certifying artifact from one solver witness.

    The artifact intentionally carries observed environment/state and
    external-call values separately from source-call inputs.  Those observed
    values must not silently become fuzz coordinates or nondeterministic test
    setup.  This file records evidence only; Stage 4 still has to materialize
    and replay it, and Stage 2/3 must still certify any PUT claim.
    """
    path_function, unit, path_id = _journal_claim_parts(entry)
    return {
        "schema": "veriput-ce-artifact/1",
        "kind": "solver-refutation-witness",
        "verdict": "FAILED",
        "proof": {
            "certified": False,
            "valid": False,
            "put": False,
            "r1r2": False,
            "requires_stage4_replay": True,
            "requires_stage2_certification": True,
        },
        "path": {
            "enc": path_ce.get("enc"),
            "depth": path_ce.get("depth"),
            "path_id": str(path_id),
            "path_function": path_function,
            "unit": unit,
            "condition": entry.get("condition"),
        },
        "coordinates": _ce_collection_value(path_ce.get("counterexample", {})),
        "source_inputs": _ce_collection_value(entry.get("inputs") or []),
        "observed_environment": _ce_collection_value(entry.get("env") or []),
        "observed_entry_state": _ce_collection_value(entry.get("entry_storage") or []),
        "observed_final_state": _ce_collection_value(entry.get("final_state") or []),
        "observed_external_call_returns": _ce_collection_value(entry.get("extcall_returns") or []),
        "observed_return": {
            "value": entry.get("return_value"),
            "known": bool(entry.get("return_value_known")),
        },
        "observed_exit": {
            "kind": "revert" if entry.get("revert_pre_rollback") else "normal",
            "events": _ce_collection_value(entry.get("events") or []),
        },
        "members": _ce_collection_value(members),
        "decisions": _ce_collection_value(decisions),
        "unrendered_state": _ce_collection_value(entry.get("state_written_unrendered") or []),
        "refused_coordinates": list(refused or []),
        "caveats": list(caveats or []),
        "witness": {
            "real_solver_witness": True,
            "witness_count": _journal_witness_count(entry),
            "payload_symbols_protected": bool(entry.get("payload_symbols_protected")),
            "entry_storage_known": bool(entry.get("entry_storage_known", True)),
        },
        "source": {
            "kind":
            "cov-ce-journal",
            "journal_complete":
            bool(journal.get("complete")),
            "journal_partial":
            bool(journal.get("partial")),
            "claims_decided":
            journal.get("claims_decided"),
            "claims_total":
            journal.get("claims_total"),
            "coverage_is_complete":
            bool(journal.get("complete"))
            and journal.get("claims_decided") == journal.get("claims_total"),
        },
    }


def write_ce_collection(cwd,
                        args,
                        scope_label,
                        paths,
                        refused,
                        caveats,
                        members,
                        path_decisions,
                        *,
                        status,
                        reason=None):
    """Persist refutation evidence without promoting it to a test or proof."""
    journal = os.path.join(cwd, "cov-ce-journal.json")
    journal_copy = os.path.join(cwd, "ce-witness-journal.json")
    if os.path.exists(journal):
        shutil.copyfile(journal, journal_copy)
    _journal_path, journal_data, journal_rows, journal_errors = \
        _journal_entries_for_paths(cwd, paths, args.path_function)
    ce_artifacts = []
    if journal_data is not None:
        for enc, depth, ce in paths:
            entry = journal_rows.get(enc)
            if entry is None:
                continue
            ce_artifacts.append(
                _journal_ce_artifact(journal_data, entry, {
                    "enc": enc,
                    "depth": depth,
                    "counterexample": ce
                }, members.get(enc, []), path_decisions.get(enc, []), refused, caveats))
    data = {
        "schema":
        "veriput-ce-collection/1",
        "status":
        status,
        "reason":
        reason,
        "contract":
        args.contract,
        "unit":
        args.unit,
        "path_function":
        args.path_function,
        "scope":
        scope_label,
        "max_tx":
        args.max_tx,
        "timeout_s":
        args.timeout,
        "config":
        run_config(args, scope_label),
        "witnesses": [{
            "enc": enc,
            "depth": depth,
            "counterexample": _ce_collection_value(ce),
            "members": _ce_collection_value(members.get(enc, [])),
            "decisions": _ce_collection_value(path_decisions.get(enc, [])),
        } for enc, depth, ce in paths],
        # This is evidence, not a certification result.  The exact journal row
        # is retained so later concrete/PUT stages do not have to reconstruct
        # aggregate state or external-call observations from a flattened CE.
        "ce_artifacts":
        ce_artifacts,
        "ce_artifact_schema":
        "veriput-ce-artifact/1",
        "ce_artifact_errors":
        journal_errors,
        "ce_artifact_source": {
            "path": _journal_path,
            "journal_complete": bool(journal_data and journal_data.get("complete")),
            "journal_partial": bool(journal_data and journal_data.get("partial")),
            "claims_decided": (journal_data or {}).get("claims_decided"),
            "claims_total": (journal_data or {}).get("claims_total"),
        },
        "refused_coordinates":
        list(refused or []),
        "caveats":
        list(caveats or []),
        "cov_report":
        file_identity(enumeration_report_snapshot_path(cwd)),
        "ce_journal":
        file_identity(journal_copy),
        "progress":
        file_identity(generalise_progress_path(cwd)),
    }
    path = ce_collection_path(cwd)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def progress_jsonable(value):
    if isinstance(value, dict):
        return {str(k): progress_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [progress_jsonable(v) for v in value]
    if isinstance(value, set):
        return [progress_jsonable(v) for v in sorted(value)]
    return value


def write_generalise_progress(cwd, stage, **fields):
    """Persist the last expensive stage reached before a possible timeout."""
    path = generalise_progress_path(cwd)
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("schema", "path-generalise-progress/1")
    history = data.setdefault("history", [])
    event = {
        "stage": stage,
        "at_s": round(time.time(), 3),
    }
    for key, value in fields.items():
        event[key] = progress_jsonable(value)
    history.append(event)
    if len(history) > 40:
        del history[:-40]
    data = {
        "schema": data.get("schema", "path-generalise-progress/1"),
        "history": history,
    }
    data.update(event)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
    os.replace(tmp, path)


def write_enumeration_salvage(cwd, salvaged):
    meta = dict(salvaged.get("veriput_salvage") or {})
    meta.update({
        "partial": bool(salvaged.get("partial")),
        "path_count": len(salvaged.get("claims") or []),
    })
    summary = salvaged.get("summary") or {}
    if "witnesses_total" in summary:
        meta["witness_count"] = summary.get("witnesses_total")
    with open(enumeration_salvage_path(cwd), "w", encoding="utf-8") as stream:
        json.dump(meta, stream, indent=2, sort_keys=True)
    return meta


def read_enumeration_salvage(cwd):
    try:
        with open(enumeration_salvage_path(cwd), encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def enumerate_paths(esbmc,
                    sol,
                    contract,
                    unit,
                    max_tx,
                    timeout,
                    cwd,
                    ast=None,
                    focus=None,
                    memlimit="8g",
                    path_function=None,
                    esbmc_args=(),
                    state_structs=False,
                    probe_witnesses=0,
                    enumeration_index=None,
                    enumeration_report=None,
                    scope_label="whole",
                    param_types=None,
                    state_types=None,
                    extcall_coord_specs=None):
    """Step 1. Return paths, refusals, caveats, members, extras and decisions.

    `paths` = [(enc, depth, ce)]; `members` = {enc: {coord: [v, ...]}}, the
    values a coordinate is KNOWN to take on that path.

    ---- WHY THE MEMBERS COME FROM THIS RUN AND NOT A SECOND ONE ----

    `--all-witnesses --max-witnesses N` makes each REFUTED claim report up to N
    distinct input tuples instead of one. Under `--solidity-path-coverage` a
    refuted claim IS a path, so every witness arrives ALREADY ATTRIBUTED to the
    path it walks -- there is no "which path does this input take" question to
    answer and no query to pay for it. The enumeration run happens anyway, so
    the extra witnesses cost one flag.

    MEASURED on a 17-line contract, same unit and same bound, one flag apart:
        without:  witnesses_total  6,  F_with_multiple_witnesses 0
        with -8:  witnesses_total 48,  F_with_multiple_witnesses 6
    and `U_undecided` stayed 2 in both -- the two paths that merely held inside
    the bound gained NO witnesses, which is the negative control: the flag
    cannot manufacture evidence for a path nothing reaches.

    ⛔ ONE DIRECTION ONLY IS EVIDENCE. A coordinate taking MORE THAN ONE value
    across the witnesses is proof it is not a point. A coordinate taking ONE
    value proves NOTHING -- the solver returns whatever model it likes and is
    under no obligation to vary anything it was not asked about. So this returns
    the member SET and never a flag saying "point".

    Read by `coord_values`, the SAME function that reads the claim's own
    counterexample. A witness node carries the same `inputs`/`env`/
    `entry_storage` shape, and one fact read by two readers is how this project
    has already made two ledgers disagree.
    """
    # FRESHNESS, BY REMOVAL -- the same guard `certify` already has, and this is
    # the step that needed it more.
    #
    # `run` does NOT raise on a timeout: it returns the partial output with a
    # marker so callers can read it as UNKNOWN (see its docstring). Enumeration
    # then asks only `os.path.exists`, so a run that timed out, aborted, or was
    # refused at instrumentation time falls through to whatever
    # `cov-report.json` is already sitting in `cwd` -- and `--workdir` is
    # explicitly reusable, which is why `certify` needed this guard in the first
    # place ("the previous shrink round left one right here").
    #
    # A CROSS-UNIT stale report is caught: the `claim_unit(c) == unit` filter
    # empties, and the WIRING CHECK below exits loudly. The one that is NOT
    # caught is the same unit re-run in the same workdir under DIFFERENT flags --
    # another `--max-tx`, `--focus` on instead of off, a rebuilt binary. Then the
    # filter matches, the old (enc, depth, ce) triples flow into the bracket, the
    # refine rounds and every certification query, and the whole result is about
    # a configuration nobody asked for. Nothing downstream could notice: an enc
    # is just an integer.
    #
    # Deleting first also RESTORES the error below. With a stale file present,
    # "ESBMC produced no cov-report.json" -- the branch that exists to surface
    # ESBMC's own message about a solc mismatch or a missing contract -- can
    # never fire, so the actionable diagnostic is replaced by silent stale data.
    report = os.path.join(cwd, "cov-report.json")
    report_snapshot = enumeration_report_snapshot_path(cwd)
    salvage_sidecar = enumeration_salvage_path(cwd)
    if os.path.exists(salvage_sidecar):
        os.remove(salvage_sidecar)
    if os.path.exists(report_snapshot):
        os.remove(report_snapshot)
    if enumeration_report:
        validate_enumeration_import(enumeration_index, enumeration_report, esbmc, sol, ast,
                                    contract, unit, scope_label, max_tx, memlimit, probe_witnesses,
                                    esbmc_args)
        shutil.copyfile(enumeration_report, report)
        print(f"[enumerate] reused stage-1 report {enumeration_report}; "
              "no enumeration ESBMC process was started")
    else:
        if os.path.exists(report):
            os.remove(report)
        journal = os.path.join(cwd, "cov-ce-journal.json")
        if os.path.exists(journal):
            os.remove(journal)
        enum_args = ["--cov-report-json"]
        # --focus-function is intentionally name-level and therefore keeps all
        # overloads.  The schedule has already resolved this run to one AST
        # declaration, so narrow the measured denominator before solving rather
        # than discarding the other overloads from cov-report.json afterwards.
        # The dispatcher remains name-focused and can still enter either ABI
        # selector; only claims for this exact declaration are instrumented.
        enum_args += path_function_instrument_args(path_function)
        if probe_witnesses:
            enum_args += [
                "--branch-function-coverage", "--path-cov-probe", "--all-witnesses",
                "--max-witnesses",
                str(probe_witnesses)
            ]
        enum_timeout = path_cov_probe_enum_timeout(timeout, probe_witnesses)
        enum_started = time.monotonic()
        if enum_timeout != timeout:
            print(f"[enumerate] limiting --path-cov-probe/all-witnesses to "
                  f"{enum_timeout}s of the {timeout}s unit budget; remaining "
                  "time is reserved for basic enumeration and certification")
        log = run(esbmc,
                  sol,
                  contract,
                  enum_args,
                  max_tx,
                  enum_timeout,
                  cwd,
                  ast=ast,
                  focus=focus,
                  memlimit=memlimit,
                  esbmc_args=esbmc_args,
                  probe_claim_stop=(PATH_PROBE_EARLY_STOP_CLAIMS if probe_witnesses else None))
        fallback_reason = (path_cov_basic_enum_fallback_reason(log)
                           if not os.path.exists(report) else None)
        if probe_witnesses and fallback_reason:
            print("[enumerate] --path-cov-probe was too expensive for this "
                  "unit; retrying without probe/all-witnesses. The witness pool "
                  "will be smaller, but basic path enumeration can still feed "
                  "certification instead of timing out or classifying the unit "
                  f"as a driver refusal ({fallback_reason})")
            if os.path.exists(report):
                os.remove(report)
            if os.path.exists(journal):
                os.remove(journal)
            fallback_timeout = max(1, int(timeout - (time.monotonic() - enum_started)))
            fallback_args = (["--cov-report-json"] + path_function_instrument_args(path_function))
            log = run(esbmc,
                      sol,
                      contract,
                      fallback_args,
                      max_tx,
                      fallback_timeout,
                      cwd,
                      ast=ast,
                      focus=focus,
                      memlimit=memlimit,
                      esbmc_args=esbmc_args)
            fallback_reason = (path_cov_basic_enum_fallback_reason(log)
                               if not os.path.exists(report) else None)
        if fallback_reason == "path-coverage-no-claims-reached-solver":
            write_generalise_progress(
                cwd,
                "enumeration-no-claims-reached-solver",
                reason=fallback_reason,
                probe_witnesses=probe_witnesses,
                scope=scope_label,
                path_function=path_function,
            )
        if not os.path.exists(report):
            salvaged = partial_journal_report(cwd)
            if salvaged:
                with open(report, "w", encoding="utf-8") as stream:
                    json.dump(salvaged, stream, indent=2, sort_keys=True)
                meta = write_enumeration_salvage(cwd, salvaged)
                print(f"[enumerate] salvaged {len(salvaged.get('claims', []))} "
                      "witnessed path(s) from partial cov-ce-journal.json "
                      f"({meta.get('claims_decided')}/"
                      f"{meta.get('claims_total')} claims decided); regions "
                      "still require independent certification")
            else:
                # Preserve ESBMC's actionable frontend/configuration diagnostic.
                raise SystemExit("[enumerate] ESBMC produced no cov-report.json. "
                                 "Its output was:\n" + log)
    with open(report) as f:
        rep = json.load(f)
    try:
        shutil.copyfile(report, report_snapshot)
    except OSError as e:
        raise SystemExit(f"[enumerate] could not preserve stage-1 report at "
                         f"{report_snapshot}: {e}")

    claims = [
        c for c in rep.get("claims", [])
        if claim_unit(c) == unit and "path_id" in c and "path_depth" in c
    ]
    if path_function:
        claims = [c for c in claims if same_path_function(c.get("path_function"), path_function)]

    # OVERLOADS. Two functions sharing a name are two units with two independent
    # path-id spaces, and a stage-2 query identifies a path by (enc, depth)
    # alone. Merging them would hand the certification query an `enc` from the
    # wrong space -- a wrong answer, not an error. Refuse and name the
    # candidates instead of picking one.
    pfs = sorted({c.get("path_function") for c in claims})
    if len(pfs) > 1:
        raise SystemExit(f"[enumerate] '{unit}' names {len(pfs)} overloads; their path-id "
                         f"spaces are independent and must not be merged. Re-run with "
                         f"--path-function set to one of:\n  " + "\n  ".join(pfs))

    witnessed = [c for c in claims if c.get("status") == "F"]

    # WIRING CHECK. The old code printed "no witnessed path for this unit; that
    # is a result, not an error" whenever this list came back empty -- and it
    # ALWAYS came back empty, so a total wiring failure explained itself to the
    # operator as a legitimate negative result. The sentence is true only when
    # the report genuinely holds no F claim for the unit. When the report DOES
    # hold F claims for it and none survived, that is a hard failure.
    if not witnessed:
        any_f = [c for c in rep.get("claims", []) if c.get("status") == "F"]
        if any_f:
            units = sorted({claim_unit(c) for c in any_f})
            raise SystemExit(f"[enumerate] no F claim matched unit '{unit}', but the report "
                             f"holds {len(any_f)} F claim(s) for: {', '.join(units)}. "
                             f"That is a wiring failure, not a result.")

    out, refused = [], set()
    # Payload quantities that are NOT candidate coordinates, kept BESIDE the
    # coordinate dictionary rather than inside it. See payload_extras: they are
    # carried so the comparison can say the two counterexamples disagree about
    # them, and they must never reach a box.
    path_extras = {}
    path_decisions = {}
    probe_members = {}
    for goal in (rep.get("probe") or {}).get("goals", []):
        if pfs and goal.get("unit") not in pfs:
            continue
        for path in goal.get("paths") or []:
            key = (int(path["path_id"]), int(path["decision_depth"]))
            dst = probe_members.setdefault(key, [])
            for witness in path.get("witnesses") or []:
                wce, _ = coord_values(witness,
                                      state_structs=state_structs,
                                      param_types=param_types,
                                      state_types=state_types,
                                      extcall_coord_specs=extcall_coord_specs)
                dst.append(wce)
    for c in witnessed:
        ce, ref = coord_values(c,
                               state_structs=state_structs,
                               param_types=param_types,
                               state_types=state_types,
                               extcall_coord_specs=extcall_coord_specs)
        enc = int(c["path_id"])
        # The de-duplication below keeps the FIRST transaction instance of an
        # enc. Keep its metadata by the same rule; assignment here used to keep
        # the LAST instance's decisions beside the first instance's CE/depth.
        path_extras.setdefault(enc, payload_extras(c))
        path_decisions.setdefault(enc, [dict(d) for d in (c.get("decisions") or [])])
        refused.update(ref)
        # THE WITNESSES OF THIS CLAIM, AND OF NO OTHER. A claim's witnesses are
        # inputs that walk THIS (enc, depth); the duplicate-enc claim dropped
        # below is a different transaction instance at a different depth, and a
        # stage-2 query identifies a path by BOTH. Attributing its witnesses
        # here would hand the kept path members of a domain it does not have.
        #
        # KEPT AS WHOLE VECTORS, NOT PROJECTED TO PER-COORDINATE SETS. A witness
        # is a member of the path's domain only if it also satisfies the PINS,
        # and the pins are not known here -- they are settled in `main`, after
        # the auto msg.value pin and the unsettable-coordinate pins. Projecting
        # first would destroy exactly the information the pin filter needs: a
        # vector violating one pin would still contribute its values on every
        # OTHER coordinate, as though a member of a slice it is not in.
        vecs = [ce]
        for w in (c.get("witnesses") or []):
            wce, _ = coord_values(w,
                                  state_structs=state_structs,
                                  param_types=param_types,
                                  state_types=state_types,
                                  extcall_coord_specs=extcall_coord_specs)
            vecs.append(wce)
        vecs.extend(probe_members.get((enc, int(c["path_depth"])), ()))
        out.append((enc, int(c["path_depth"]), ce, vecs))
    # Same enc can appear once per transaction instance; keep one of each.
    seen, uniq, members = set(), [], {}
    for enc, depth, ce, vecs in out:
        if enc in seen:
            continue
        seen.add(enc)
        uniq.append((enc, depth, ce))
        members[enc] = vecs
    kept_decisions = {enc: path_decisions.get(enc, []) for enc, _, _ in uniq}
    resolved_path_function = pfs[0] if len(pfs) == 1 else path_function
    return (uniq, sorted(refused), extraction_caveats(witnessed), members, path_extras,
            kept_decisions, resolved_path_function)


def abi_gate_class(decisions):
    """Classify the explicit synthetic ABI value gate, if one was recorded."""
    gates = [d for d in (decisions or []) if d.get("synthetic_abi_gate")]
    if not gates:
        return None
    # The synthetic GOTO jumps into the body when msg.value == 0. In the report
    # that jump is `taken`; fall-through is the immediate ABI rejection path.
    return "body" if gates[0].get("arm") == "taken" else "reject"


def compiler_abi_gate_candidate_mapping(path_decisions, pins=None):
    """Return named candidates for pinned, compiler-generated ABI gates."""
    if "msg.value" not in (pins or {}):
        return {}
    candidates = {}
    for enc, decisions in sorted((path_decisions or {}).items()):
        for decision in decisions or []:
            claim = str(decision.get("branch_claim") or "")
            if not decision.get("synthetic_abi_gate") or \
                    "msg.value" not in claim:
                continue
            candidates.setdefault(enc, []).append({
                "coordinate":
                "msg.value",
                "arm": ("body" if decision.get("arm") == "taken" else "reject"),
                "branch_claim":
                claim,
                "decision":
                decision.get("index"),
            })
    return candidates


def structural_abi_gate_certificate(decisions, box, holes, ce):
    """Return a certificate reason for a path that is only the ABI value gate.

    This is deliberately narrower than "has an ABI gate". It applies only when
    the report says the complete path contains exactly that synthetic decision,
    and the measured region already matches the compiler-inserted non-payable
    split: body iff msg.value == 0, rejection iff msg.value != 0.
    """
    decisions = decisions or []
    if len(decisions) != 1 or not decisions[0].get("synthetic_abi_gate"):
        return None
    if "msg.value" not in box:
        return None
    lo, hi = box["msg.value"]
    punched = set((holes or {}).get("msg.value", ()))
    value = ce.get("msg.value")
    kind = abi_gate_class(decisions)
    if kind == "body":
        if (lo, hi) != (0, 0) or 0 in punched or value != 0:
            return None
        return ("STRUCTURAL ABI value gate: this complete path has no source "
                "decision beyond the compiler-inserted non-payable body gate, "
                "and its measured region is msg.value == 0")
    if kind == "reject":
        if lo <= 0 <= hi or value is None or value < lo or value > hi \
           or value in punched:
            return None
        return ("STRUCTURAL ABI value gate: this complete path has no source "
                "decision beyond the compiler-inserted non-payable reject "
                "gate, and its measured region excludes msg.value == 0")
    return None


def structural_no_coordinate_abi_reject_detail(enc, depth, decisions, ce):
    """Materialize the compiler-only nonpayable reject arm as a region.

    Auto-pinning msg.value to zero is correct for paths that enter a
    nonpayable function body, but it intentionally removes the ABI rejection
    arm.  When that arm is the complete path, the compiler gate itself gives
    the exact complementary domain and no search coordinate is needed.
    """
    box = {"msg.value": (1, UINT256_MAX)}
    reason = structural_abi_gate_certificate(decisions, box, {}, ce)
    if reason is None:
        return None
    return {
        "enc": enc,
        "piece": 1,
        "depth": depth,
        "verdict": "CERTIFIED",
        "retreated": {},
        "established": [],
        "extcall_pins": {},
        "certification_source": "structural-abi-gate-no-coordinate",
        "box": [{
            "name": "msg.value",
            "lo": "1",
            "hi": str(UINT256_MAX),
            "holes": [],
        }],
        "ce": {
            n: str(v)
            for n, v in sorted(ce.items())
        },
        "reason": reason,
    }


SIMPLE_BRANCH_RE = re.compile(r"^(.+?)\s*(==|!=|<=|>=|<|>)\s*(.+)$")


def _unwrap_not(expr):
    expr = (expr or "").strip()
    negated = False
    while expr.startswith("!(") and expr.endswith(")"):
        depth = 0
        whole = True
        for i, ch in enumerate(expr[2:], start=2):
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0 and i != len(expr) - 1:
                    whole = False
                    break
                depth -= 1
        if not whole:
            break
        expr = expr[2:-1].strip()
        negated = not negated
    if negated:
        return expr, True
    return expr, False


def _boolean_decision_relation(inner, was_not, arm=None):
    """Path condition for a bare boolean branch term, as term == 0/1."""
    term = (inner or "").strip()
    if not term:
        return None
    negated = False
    if term.startswith("!"):
        term = term[1:].strip()
        negated = True
    if not term:
        return None
    if SIMPLE_BRANCH_RE.match(term):
        return None
    # branch_claim is the assertion used to witness the arm; the path condition
    # is its negation.  For a boolean term, that flips the truth value unless
    # the claim was already printed as an outer `!(...)` arm.
    want_true = negated if not was_not else not negated
    if arm == "fall-through":
        want_true = not want_true
    return term, "==", "1" if want_true else "0"


def _decision_relation(branch_claim, arm=None):
    """Return the path condition encoded by a coverage decision.

    ESBMC's branch claim is the assertion used to witness the arm, so the path
    condition is its negation. The report prints either `x == y` or `!(x == y)`;
    this helper turns both spellings back into the condition the path actually
    follows. Only simple binary comparisons are admitted here.
    """
    inner, was_not = _unwrap_not(branch_claim)
    m = SIMPLE_BRANCH_RE.match(inner)
    if not m:
        return _boolean_decision_relation(inner, was_not, arm=arm)
    lhs, op, rhs = (m.group(1).strip(), m.group(2), m.group(3).strip())
    if not was_not:
        op = {"==": "!=", "!=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}[op]
    return lhs, op, rhs


def _flip_relation(op):
    return {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}[op]


def _compare_values(lhs, op, rhs):
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    raise ValueError(f"unsupported comparison operator: {op}")


def _decision_term(term, ce, pins, constants=None, coord_set=None):
    """Resolve a simple branch term to either a coordinate or a constant."""
    term = term.strip()
    if term in ce:
        return "coord", term
    state_term = "state." + term
    if coord_set and state_term in coord_set:
        return "coord", state_term
    if state_term in ce:
        return "coord", state_term
    if state_term in pins:
        return "const", pins[state_term]
    if term == "return_value$__msgSender$2" or \
       re.match(r"^return_value\$__msgSender\$\d+$", term):
        return "coord", "msg.sender"
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", term):
        state_name = "state." + term
        if state_name in pins:
            return "const", pins[state_name]
        if state_name in ce:
            return "coord", state_name
    m = re.match(r"^return_value\$([A-Za-z_][A-Za-z0-9_]*)\$\d+$", term)
    if m:
        state_name = "state." + m.group(1)
        if coord_set and state_name in coord_set:
            return "coord", state_name
        if state_name in pins:
            return "const", pins[state_name]
        if state_name in ce:
            return "coord", state_name
    if term in pins:
        return "const", pins[term]
    if term in (constants or {}):
        return "const", constants[term]
    try:
        return "const", parse_int(term)
    except ValueError:
        return None


def _coord_range(name, coord_types=None, type_ranges=None):
    if type_ranges and name in type_ranges:
        return type_ranges[name]
    if name in ("msg.sender", "tx.origin", "block.coinbase"):
        return 0, ADDRESS_MAX
    if name == "msg.value":
        return 0, UINT256_MAX
    if name == "block.chainid":
        return 0, CHAIN_ID_MAX
    ty = (coord_types or {}).get(name)
    if ty:
        tr = elementary_type_range(ty)
        if tr is not None:
            return tr
        if ty.strip().startswith(("contract ", "interface ", "enum ")):
            return 0, ADDRESS_MAX
    return 0, UINT256_MAX


def _box_intersect_eq(box, holes, name, value, coord_types=None, type_ranges=None):
    lo, hi = box.get(name, _coord_range(name, coord_types, type_ranges))
    value = int(value)
    if value < lo or value > hi or value in holes.get(name, set()):
        return False
    box[name] = (value, value)
    holes.pop(name, None)
    return True


def _box_intersect_neq(box, holes, name, value, coord_types=None, type_ranges=None):
    lo, hi = box.get(name, _coord_range(name, coord_types, type_ranges))
    value = int(value)
    if value < lo or value > hi:
        box[name] = (lo, hi)
        return True
    if lo == hi == value:
        return False
    if value == lo:
        box[name] = (lo + 1, hi)
        return True
    if value == hi:
        box[name] = (lo, hi - 1)
        return True
    box[name] = (lo, hi)
    holes.setdefault(name, set()).add(value)
    return True


def _box_intersect_order(box, holes, name, op, value, coord_types=None, type_ranges=None):
    lo, hi = box.get(name, _coord_range(name, coord_types, type_ranges))
    value = int(value)
    if op == "<":
        hi = min(hi, value - 1)
    elif op == "<=":
        hi = min(hi, value)
    elif op == ">":
        lo = max(lo, value + 1)
    elif op == ">=":
        lo = max(lo, value)
    else:
        raise ValueError(f"unsupported order operator: {op}")
    if lo > hi:
        return False
    box[name] = (lo, hi)
    kept = {h for h in holes.get(name, set()) if lo <= h <= hi}
    if kept:
        holes[name] = kept
    else:
        holes.pop(name, None)
    return True


def _relation_retreat_coord(lhs, rhs, ce, coord_set):
    """Pick the coordinate to pin for a simple coord-to-coord relation."""
    if lhs not in coord_set or rhs not in coord_set:
        return None
    if lhs not in ce or rhs not in ce:
        return None
    # Entry state is establishable by the PUT preamble and pinning it usually
    # leaves the caller/input side as the fuzz-facing quantity.
    if lhs.startswith("state.") and not rhs.startswith("state."):
        return lhs
    if rhs.startswith("state.") and not lhs.startswith("state."):
        return rhs
    return None


def _relation_establish_pair(lhs, rhs, coord_set):
    """Return (state-target, source-coordinate) for an establishable equality."""
    if lhs not in coord_set or rhs not in coord_set:
        return None
    if lhs.startswith("state.") and not rhs.startswith("state."):
        return lhs, rhs
    if rhs.startswith("state.") and not lhs.startswith("state."):
        return rhs, lhs
    return None


def _merge_established_target_into_source(box,
                                          holes,
                                          target,
                                          source,
                                          coord_types=None,
                                          type_ranges=None):
    tlo, thi = box.get(target, _coord_range(target, coord_types, type_ranges))
    slo, shi = box.get(source, _coord_range(source, coord_types, type_ranges))
    lo, hi = max(tlo, slo), min(thi, shi)
    if lo > hi:
        return False
    merged_holes = {
        h
        for h in set(holes.get(target, set())) | set(holes.get(source, set())) if lo <= h <= hi
    }
    box[source] = (lo, hi)
    box.pop(target, None)
    if merged_holes:
        holes[source] = merged_holes
    else:
        holes.pop(source, None)
    holes.pop(target, None)
    return True


def _structural_decision_region(decisions,
                                ce,
                                pins,
                                coords,
                                coord_types=None,
                                type_ranges=None,
                                constants=None,
                                allow_relation_retreat=False,
                                allow_relation_establish=False):
    """Derive a product region directly from simple complete-path decisions.

    This is a fast path for source shapes such as:

      nonpayable ABI gate; onlyOwner; if (arg == 0) revert; state = arg

    It is intentionally not a prover for arbitrary Solidity. Every decision
    must be a simple comparison whose terms resolve to one coordinate and one
    constant/pin/literal Solidity constant. If any decision is outside that
    grammar the caller falls back to the measured ladder and ESBMC
    certification.
    """
    decisions = decisions or []
    if not decisions:
        return None
    coord_set = set(coords or [])
    box = {n: _coord_range(n, coord_types, type_ranges) for n in coord_set}
    holes = {}
    retreated = {}
    established = {}
    alias = {}
    clauses = []
    for d in decisions:
        rel = _decision_relation(d.get("branch_claim"), d.get("arm"))
        if rel is None:
            return None
        lhs, op, rhs = rel
        lt = _decision_term(lhs, ce, pins, constants, coord_set=coord_set)
        rt = _decision_term(rhs, ce, pins, constants, coord_set=coord_set)
        if lt is None or rt is None:
            return None
        if lt[0] == "coord" and lt[1] in alias:
            lt = ("coord", alias[lt[1]])
        if rt[0] == "coord" and rt[1] in alias:
            rt = ("coord", alias[rt[1]])
        if lt[0] == "const" and rt[0] == "coord":
            lt, rt = rt, lt
            op = _flip_relation(op)
        if lt[0] == "coord" and rt[0] == "const":
            name, value = lt[1], rt[1]
            if name not in coord_set:
                # A decision on a pinned coordinate must already be satisfied
                # by the slice. It contributes no rendered box coordinate.
                if name not in pins:
                    return None
                ok = _compare_values(pins[name], op, value)
                if not ok:
                    return None
                clauses.append(f"{name} {op} {value} (pinned)")
                continue
            if op == "==":
                ok = _box_intersect_eq(box, holes, name, value, coord_types, type_ranges)
            elif op == "!=":
                ok = _box_intersect_neq(box, holes, name, value, coord_types, type_ranges)
            else:
                ok = _box_intersect_order(box, holes, name, op, value, coord_types, type_ranges)
            if not ok:
                return None
            clauses.append(f"{name} {op} {value}")
            continue
        if lt[0] == "const" and rt[0] == "const":
            ok = _compare_values(lt[1], op, rt[1])
            if not ok:
                return None
            clauses.append(f"{lt[1]} {op} {rt[1]} (constant)")
            continue
        if (allow_relation_retreat and lt[0] == "coord" and rt[0] == "coord"
                and op in ("==", "!=")):
            if op == "==" and allow_relation_establish:
                pair = _relation_establish_pair(lt[1], rt[1], coord_set)
                if pair is not None:
                    target, source = pair
                    if established.get(target, source) != source:
                        return None
                    if not _merge_established_target_into_source(box, holes, target, source,
                                                                 coord_types, type_ranges):
                        return None
                    established[target] = source
                    alias[target] = source
                    clauses.append(f"{target} := {source} at entry (establish relation)")
                    continue
            pin_name = _relation_retreat_coord(lt[1], rt[1], ce, coord_set)
            if pin_name is None:
                return None
            other = rt[1] if pin_name == lt[1] else lt[1]
            value = ce[pin_name]
            if not _box_intersect_eq(box, holes, pin_name, value, coord_types, type_ranges):
                return None
            if op == "==":
                ok = _box_intersect_eq(box, holes, other, value, coord_types, type_ranges)
            else:
                ok = _box_intersect_neq(box, holes, other, value, coord_types, type_ranges)
            if not ok:
                return None
            retreated[pin_name] = value
            clauses.append(f"{lt[1]} {op} {rt[1]} (retreat {pin_name}=={value})")
            continue
        # Coordinate-to-coordinate constraints are not product regions unless
        # the caller explicitly allows the relation-to-pin retreat above.
        return None
    reason = ("STRUCTURAL simple decision region: every complete-path decision "
              "is a comparison over a rendered coordinate and a constant, "
              "literal Solidity constant, pinned state value, or a retreated "
              "entry-state relation; equality relations over entry state may be "
              "established explicitly before the unit call; clauses: " + "; ".join(clauses))
    return box, holes, reason, retreated, established


def structural_decision_region(decisions,
                               ce,
                               pins,
                               coords,
                               coord_types=None,
                               type_ranges=None,
                               constants=None):
    got = _structural_decision_region(decisions,
                                      ce,
                                      pins,
                                      coords,
                                      coord_types=coord_types,
                                      type_ranges=type_ranges,
                                      constants=constants,
                                      allow_relation_retreat=False)
    if got is None:
        return None
    box, holes, reason, _retreated, _established = got
    return box, holes, reason


def structural_decision_regions(paths,
                                path_decisions,
                                pins,
                                coords,
                                coord_types=None,
                                type_ranges=None,
                                constants=None):
    out, holes, reasons = {}, {}, {}
    for enc, _depth, ce in paths:
        got = structural_decision_region(path_decisions.get(enc),
                                         ce,
                                         pins,
                                         coords,
                                         coord_types=coord_types,
                                         type_ranges=type_ranges,
                                         constants=constants)
        if got is None:
            return None, None, None
        box, h, reason = got
        out[enc] = box
        holes[enc] = h
        reasons[enc] = reason
    return out, holes, reasons


def structural_decision_regions_with_retreat(paths,
                                             path_decisions,
                                             pins,
                                             coords,
                                             coord_types=None,
                                             type_ranges=None,
                                             constants=None):
    out, holes, reasons, retreats = {}, {}, {}, {}
    for enc, _depth, ce in paths:
        got = _structural_decision_region(path_decisions.get(enc),
                                          ce,
                                          pins,
                                          coords,
                                          coord_types=coord_types,
                                          type_ranges=type_ranges,
                                          constants=constants,
                                          allow_relation_retreat=True)
        if got is None:
            return None, None, None, None
        box, h, reason, retreated, _established = got
        out[enc] = box
        holes[enc] = h
        reasons[enc] = reason
        retreats[enc] = retreated
    return out, holes, reasons, retreats


def structural_decision_regions_with_relations(paths,
                                               path_decisions,
                                               pins,
                                               coords,
                                               coord_types=None,
                                               type_ranges=None,
                                               constants=None):
    out, holes, reasons, retreats, establishes = {}, {}, {}, {}, {}
    for enc, _depth, ce in paths:
        got = _structural_decision_region(path_decisions.get(enc),
                                          ce,
                                          pins,
                                          coords,
                                          coord_types=coord_types,
                                          type_ranges=type_ranges,
                                          constants=constants,
                                          allow_relation_retreat=True,
                                          allow_relation_establish=True)
        if got is None:
            return None, None, None, None, None
        box, h, reason, retreated, established = got
        out[enc] = box
        holes[enc] = h
        reasons[enc] = reason
        retreats[enc] = retreated
        establishes[enc] = established
    return out, holes, reasons, retreats, establishes


def relation_establishable_state_targets(paths, path_decisions, pins, coords, constants=None):
    """State coordinates worth keeping free for an entry relation assignment."""
    coord_set = set(coords or [])
    out = set()
    for enc, _depth, ce in paths:
        for d in path_decisions.get(enc) or []:
            rel = _decision_relation(d.get("branch_claim"), d.get("arm"))
            if rel is None:
                continue
            lhs, op, rhs = rel
            if op != "==":
                continue
            lt = _decision_term(lhs, ce, pins, constants, coord_set=coord_set)
            rt = _decision_term(rhs, ce, pins, constants, coord_set=coord_set)
            if lt is None or rt is None:
                continue
            if lt[0] != "coord" or rt[0] != "coord":
                continue
            pair = _relation_establish_pair(lt[1], rt[1], coord_set)
            if pair is not None:
                out.add(pair[0])
    return out


def relation_establishable_env_sources(paths,
                                       path_decisions,
                                       pins,
                                       coords,
                                       env_names,
                                       constants=None):
    """Environment coordinates that must stay free to establish state relations."""
    coord_set = set(coords or [])
    env_set = set(env_names or [])
    out = set()
    for enc, _depth, ce in paths:
        for d in path_decisions.get(enc) or []:
            rel = _decision_relation(d.get("branch_claim"), d.get("arm"))
            if rel is None:
                continue
            lhs, op, rhs = rel
            if op != "==":
                continue
            lt = _decision_term(lhs, ce, pins, constants, coord_set=coord_set)
            rt = _decision_term(rhs, ce, pins, constants, coord_set=coord_set)
            if lt is None or rt is None:
                continue
            if lt[0] != "coord" or rt[0] != "coord":
                continue
            pair = _relation_establish_pair(lt[1], rt[1], coord_set)
            if pair is not None and pair[1] in env_set:
                out.add(pair[1])
    return out


def enumeration_has_arith_conditions(cwd):
    """Whether the enumeration saw checked-arithmetic conditions on any path."""
    report = os.path.join(cwd, "cov-report.json")
    try:
        with open(report) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return False
    summary = rep.get("summary") if isinstance(rep, dict) else {}
    ar = summary.get("arith_resolve") if isinstance(summary, dict) else {}
    try:
        if int((ar or {}).get("conditions_seen") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    try:
        return int(summary.get("arith_revert_only_paths") or 0) > 0
    except (TypeError, ValueError):
        return False


_MISSING = object()

EXTERNAL_SUCCESS_HELPERS = {
    "safeTransfer",
    "safeTransferFrom",
    "safeApprove",
    "safeIncreaseAllowance",
    "safeDecreaseAllowance",
    "functionCall",
    "functionCallWithValue",
    "verifyCallResult",
}


def is_external_success_decision(d, extcall_context=False):
    claim = (d or {}).get("branch_claim") or ""
    if claim not in ("!success", "!(!success)", "success", "!(success)"):
        return False
    fun = (d or {}).get("function") or ""
    return extcall_context or fun in EXTERNAL_SUCCESS_HELPERS


def _nondet_decision_splits(decisions_a, decisions_b):
    by_key = {}
    for d in decisions_a or []:
        key = (d.get("index"), d.get("function"), d.get("line"))
        by_key[key] = d
    out = []
    for d in decisions_b or []:
        key = (d.get("index"), d.get("function"), d.get("line"))
        other = by_key.get(key)
        claim = d.get("branch_claim") or ""
        other_claim = (other or {}).get("branch_claim") or ""
        if other is None:
            continue
        is_nondet = "NONDET(" in claim or "NONDET(" in other_claim
        is_external_success = (is_external_success_decision(d)
                               or is_external_success_decision(other))
        if not is_nondet and not is_external_success:
            continue
        if other.get("arm") != d.get("arm"):
            out.append(f"decision#{d.get('index')} "
                       f"{claim if is_nondet and 'NONDET(' in claim else other_claim}")
    return out


UNCONTROLLED_DECISION_TOKENS = (
    "__esbmc_hash_result",
    "NONDET(",
    "extcall.",
)


def _decision_has_uncontrolled_source(decisions, extcall_context=False):
    for d in decisions or []:
        claim = d.get("branch_claim") or ""
        if (any(tok in claim for tok in UNCONTROLLED_DECISION_TOKENS)
                or is_external_success_decision(d, extcall_context)):
            return True
    return False


def _decision_reads_free_coord(decision, ce, pins, coords, constants=None):
    decision = decision or {}
    rel = _decision_relation(decision.get("branch_claim"), decision.get("arm"))
    if rel is None:
        return False
    lhs, _op, rhs = rel
    coord_set = set(coords or [])
    for term in (lhs, rhs):
        got = _decision_term(term, ce, pins, constants, coord_set=coord_set)
        if got and got[0] == "coord" and got[1] in coord_set:
            return True
    return False


def _decision_is_pinned_msg_value_gate(*decisions, pins=None):
    """A nonpayable msg.value split excluded by the current pinned slice.

    Only the explicit synthetic marker identifies a compiler ABI gate. A
    source-level msg.value guard remains part of the decision context even
    when the caller pins msg.value.
    """
    if "msg.value" not in (pins or {}):
        return False
    for decision in decisions:
        if not (decision or {}).get("synthetic_abi_gate"):
            continue
        claim = str((decision or {}).get("branch_claim") or "")
        if "msg.value" in claim:
            return True
    return False


def uncontrolled_decision_splits(paths,
                                 path_decisions,
                                 coords,
                                 pins,
                                 constants=None,
                                 path_extras=None):
    """Sibling paths split only by a known untestable/nondet decision source.

    This is a refutation-only filter. It never proves a PUT region; it only
    avoids spending ESBMC certification budget on path pairs whose separating
    decision is not a generated-test-settable coordinate. The direct trigger is
    an ESBMC hash/nondet/extcall marker in the pair's decision context. The
    differing decision itself must not read a free coordinate, so ordinary input
    guards such as `amount > 9000` still go through normal region search.
    """
    # A pinned ABI/environment value defines the slice before path pairs are
    # compared. An out-of-slice witness cannot make an in-slice path
    # statically inseparable, and must not participate in this refutation-only
    # filter.
    excluded = pinned_slice_exclusions(paths, pins)
    paths = [path for path in paths if path[0] not in excluded]
    failed = {}
    by_enc = {enc: (depth, ce) for enc, depth, ce in paths}
    path_extras = path_extras or {}
    encs = sorted(by_enc)
    for i, enc_a in enumerate(encs):
        _depth_a, ce_a = by_enc[enc_a]
        # A pinned non-payable ABI gate is outside this slice. Remove it from
        # the active decision context before looking for uncontrolled sources;
        # otherwise incomplete/legacy gate metadata can make an out-of-slice
        # msg.value arm contaminate the body-path pair attribution.
        dec_a = [
            d for d in (path_decisions.get(enc_a) or [])
            if not _decision_is_pinned_msg_value_gate(d, pins=pins)
        ]
        by_key = {(d.get("index"), d.get("function"), d.get("line")): d for d in dec_a}
        for enc_b in encs[i + 1:]:
            _depth_b, ce_b = by_enc[enc_b]
            dec_b = [
                d for d in (path_decisions.get(enc_b) or [])
                if not _decision_is_pinned_msg_value_gate(d, pins=pins)
            ]
            extcall_context = any(
                n.startswith("extcall.")
                for n in set(path_extras.get(enc_a) or {}) | set(path_extras.get(enc_b) or {}))
            if not (_decision_has_uncontrolled_source(dec_a, extcall_context)
                    or _decision_has_uncontrolled_source(dec_b, extcall_context)):
                continue
            evidence = []
            for d in dec_b:
                key = (d.get("index"), d.get("function"), d.get("line"))
                other = by_key.get(key)
                if other is None or other.get("arm") == d.get("arm"):
                    continue
                if d.get("synthetic_abi_gate") or other.get("synthetic_abi_gate"):
                    continue
                if _decision_is_pinned_msg_value_gate(d, other, pins=pins):
                    continue
                if (_decision_reads_free_coord(d, ce_b, pins, coords, constants)
                        or _decision_reads_free_coord(other, ce_a, pins, coords, constants)):
                    continue
                claim = d.get("branch_claim") or other.get("branch_claim") or ""
                if (is_external_success_decision(d, extcall_context)
                        or is_external_success_decision(other, extcall_context)):
                    claim = "external-call success"
                evidence.append(f"decision#{d.get('index')} {claim}")
            if not evidence:
                continue
            reason = ("STATICALLY INSEPARABLE: this path has a witnessed sibling "
                      "whose source-level split is driven by an ESBMC hash/nondet/"
                      "external-call decision rather than by a generated-test-"
                      "settable coordinate (" + "; ".join(evidence) + "). This is "
                      "a refutation-only attribution: no PUT region is certified, "
                      "and no ESBMC region/certification query is started for this "
                      "path pair because a product region over the available "
                      "coordinates cannot force one value of that uncontrolled "
                      "decision while excluding the other.")
            failed.setdefault(enc_a, reason)
            failed.setdefault(enc_b, reason)
    return failed


def extcall_inseparable_failures(paths, path_extras, path_decisions=None):
    """Paths that no generated-test coordinate region can separate.

    This is a refutation-only static filter. It fires only for the measured
    deposit shape: two witnessed paths agree on every harvested settable value
    and differ only on `extcall.*`, a value the harness chose inside the run.
    A product region over call arguments, environment values, and entry state
    cannot force one external-call behavior while excluding the other.
    """
    failed = {}
    for i in range(len(paths)):
        enc_a, _depth_a, ce_a = paths[i]
        payload_a = dict(ce_a, **(path_extras.get(enc_a) or {}))
        for enc_b, _depth_b, ce_b in paths[i + 1:]:
            payload_b = dict(ce_b, **(path_extras.get(enc_b) or {}))
            names = sorted(set(payload_a) | set(payload_b))
            diff = [n for n in names if payload_a.get(n, _MISSING) != payload_b.get(n, _MISSING)]
            if diff:
                if any(not n.startswith("extcall.") for n in diff):
                    continue
                if any(
                        payload_a.get(n, _MISSING) is _MISSING
                        or payload_b.get(n, _MISSING) is _MISSING for n in diff):
                    continue
                evidence = diff
            else:
                evidence = _nondet_decision_splits((path_decisions or {}).get(enc_a),
                                                   (path_decisions or {}).get(enc_b))
                if not evidence:
                    continue
            reason = ("STATICALLY INSEPARABLE: this path has a witnessed sibling "
                      f"with the same generated-test-settable payload and differs "
                      "only on harness-chosen external-call behavior "
                      f"({', '.join(evidence)}). A generated PUT can choose call "
                      "arguments, supported environment values, and reproducible "
                      "entry state; it cannot choose whether the callee returns "
                      "success or failure unless a deterministic mock/stub fixture "
                      "is part of this cell. No ESBMC region query is started for "
                      "this path because any product region over the available "
                      "coordinates admits both siblings.")
            failed.setdefault(enc_a, reason)
            failed.setdefault(enc_b, reason)
    return failed


# ---- WHY THERE IS NO WITNESS, READ FROM THE REPORT RATHER THAN ASSUMED -------
#
# Every U claim carries a `u_reason`, and the two families below need OPPOSITE
# readings. Splitting them is not presentation: one family says the run looked
# and found nothing under its bound, the other says the run never found out.
#
# MEASURED, St1inch.balanceOf, the run that motivated this. The driver printed
#
#     no witnessed path for this unit; nothing to generalise. That is a result,
#     not an error ... (The report was checked: it holds no F claim for any
#     unit, so this really is the empty case and not a failed match.)
#
# while its own report said:
#
#     paths_total 3, covered 0, U 3, claims_abandoned_over_budget 2
#     u_reason: claim-budget-exceeded 2, bounded-holds 1
#
# Two of the three claims were ABANDONED because each hit the per-claim solver
# budget. Calling that "a result" is the failure-as-result pattern this file
# names in six other places, committed by the one branch that also asserts it
# has checked. The check it performed is real but answers a different question
# (are there F claims for OTHER units), and its parenthetical overstates it.
#
# ONLY `bounded-holds` IS IN THE SECOND FAMILY, and the line is drawn there on
# purpose. It is the one token that means the solver ANSWERED and there is no
# witness within the bound. Every other token names something the run did not
# do -- ran out of budget, never reached the solver, was refused at
# instrumentation, or was never entered because of the scope the CALLER chose.
# Those are all repairable from outside, so reporting them in the words of a
# result hides the repair.
U_NEVER_FOUND_OUT = {
    "claim-budget-exceeded":
    "the per-claim solver budget ran out (--path-cov-claim-timeout, "
    "default 120s); the claim was abandoned, not decided",
    "not-solved-this-run":
    "the claim was never handed to the solver in this run",
    "run-died-before-solving":
    "the run died before this claim reached the solver",
    "solver-unknown":
    "the solver answered `unknown`",
    "named-obstacle":
    "instrumentation named an obstacle on this claim, so it was never put "
    "to the solver",
    "unit-not-entered":
    "the dispatcher never entered this unit under the current scope, so "
    "the path was never attempted -- a --scope/--focus outcome, i.e. a "
    "property of the command line",
}
U_LOOKED_AND_FOUND_NONE = {
    "bounded-holds":
    "no counterexample exists WITHIN THE BOUND this run used. ⛔ That is "
    "NOT a statement that the path is unreachable -- a deeper --max-tx or "
    "--unwind may witness it",
}


def empty_enumeration_reason(cwd, unit):
    """(fatal, text) for an enumeration that witnessed nothing.

    `fatal` is True when at least one of this unit's claims was ABANDONED rather
    than decided. The distinction is the whole point: a budget outcome must not
    be reported in the words of a result, because every downstream reader --
    including the corpus sweep's own tables -- counts "no witnessed path" as a
    property of the contract.

    Reads the report the enumeration just wrote, in `cwd`. Nothing is re-run and
    nothing is inferred from the absence of a line: the per-claim `u_reason`
    field is read directly, and it was CHECKED to be populated on a real report
    before this was written, rather than assumed to exist.

    An unreadable report, or one carrying no claim for this unit at all, is
    itself reported as fatal-unknown rather than silently becoming the benign
    branch -- "we could not tell" and "we looked and there was nothing" are the
    two readings this function exists to keep apart.
    """
    report = os.path.join(cwd, "cov-report.json")
    try:
        with open(report) as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        return True, (f"and the reason CANNOT BE READ: {report} is missing or "
                      f"unparseable ({e}). This is not the empty case -- it is "
                      f"an unknown one")
    mine = [c for c in rep.get("claims", []) if claim_unit(c) == unit]
    if not mine:
        return True, ("and the report holds NO claim for this unit at all, so "
                      "nothing was even attempted for it. That is a scope or "
                      "wiring question, not a property of the contract")
    tally = empty_enumeration_tally(mine)
    abandoned = sorted(r for r in tally if r in U_NEVER_FOUND_OUT)
    looked = sorted(r for r in tally if r in U_LOOKED_AND_FOUND_NONE)
    unknown = sorted(r for r in tally
                     if r not in U_NEVER_FOUND_OUT and r not in U_LOOKED_AND_FOUND_NONE)
    lines = empty_enumeration_tally_lines(tally, len(mine))
    if abandoned and all(r == "named-obstacle" for r in abandoned) and not unknown:
        n = tally["named-obstacle"]
        head = (f"⛔ and it is NOT a result: {n} of {len(mine)} claim(s) were "
                f"named-obstacle paths. This is a structural model/chain "
                f"mismatch for the unit, not a solver-budget miss and not a "
                f"bounded no-path result; no certification query can use these "
                f"paths until the obstacle is removed.")
        return True, head + "\n  " + "\n  ".join(lines)
    if abandoned or unknown:
        n = sum(tally[r] for r in abandoned + unknown)
        head = (f"⛔ and it is NOT a result: {n} of {len(mine)} claim(s) were "
                f"ABANDONED or left undecided rather than answered, so the "
                f"empty witness set is an outcome of the BUDGET and the RUN, "
                f"not a property of this unit. Raising "
                f"--path-cov-claim-timeout, or reducing what each claim has to "
                f"solve, changes this number. Do not record it as coverage.")
        return True, head + "\n  " + "\n  ".join(lines)
    head = (f"and every one of this unit's claims was DECIDED: nothing was "
            f"abandoned. The witness set is genuinely empty for this bound and "
            f"scope -- which is still not a reachability claim, see below.")
    return False, head + "\n  " + "\n  ".join(lines) + (
        "\n  " + f"(reasons in the 'looked' family: {', '.join(looked)})" if looked else "")


def empty_enumeration_tally(claims):
    tally = {}
    for c in claims:
        reason = c.get("u_reason") or "(no u_reason field)"
        tally[reason] = tally.get(reason, 0) + 1
    return tally


def empty_enumeration_tally_lines(tally, total):
    lines = [f"{total} claim(s) for this unit, none witnessed:"]
    for r in sorted(tally):
        why = (U_NEVER_FOUND_OUT.get(r) or U_LOOKED_AND_FOUND_NONE.get(r)
               or "this driver does not know this reason token")
        lines.append(f"    {tally[r]}x {r} -- {why}")
    return lines


def empty_enumeration_diagnostic(cwd, unit):
    report = os.path.join(cwd, "cov-report.json")
    try:
        with open(report) as f:
            rep = json.load(f)
    except (OSError, ValueError) as e:
        return {
            "class": "unreadable-report",
            "report": report,
            "error": str(e),
        }
    mine = [c for c in rep.get("claims", []) if claim_unit(c) == unit]
    tally = empty_enumeration_tally(mine)
    abandoned = sorted(r for r in tally if r in U_NEVER_FOUND_OUT)
    looked = sorted(r for r in tally if r in U_LOOKED_AND_FOUND_NONE)
    unknown = sorted(r for r in tally
                     if r not in U_NEVER_FOUND_OUT and r not in U_LOOKED_AND_FOUND_NONE)
    total = len(mine)
    bounded = int(tally.get("bounded-holds") or 0)
    if not mine:
        cls = "no-claims-for-unit"
    elif bounded == total and total > 0:
        cls = "bounded-holds-only"
    elif abandoned or unknown:
        cls = "mixed-undecided"
    else:
        cls = "decided-no-witness"
    diag = {
        "class": cls,
        "claims_total": total,
        "bounded_holds": bounded,
        "never_found_out": {
            reason: int(tally[reason])
            for reason in abandoned
        },
        "looked_and_found_none": {
            reason: int(tally[reason])
            for reason in looked
        },
        "unknown_reasons": {
            reason: int(tally[reason])
            for reason in unknown
        },
    }
    if cls == "bounded-holds-only":
        diag["retry_hint"] = {
            "reason": "bounded-holds-only",
            "max_tx": 2,
            "unwind": 8,
            "scope": "focus",
        }
    return diag


def state_mutability(ast_path):
    """Each state variable's Solidity mutability, read from the solc AST.

    NOT a heuristic, and that distinction is the whole reason this reads the AST
    rather than guessing from the counterexamples. "The value is the same on
    every path" is TRUE of an immutable and also true of ordinary storage that
    happens not to vary, so inferring it would be the "saw nothing else,
    therefore it is this" move this project has got wrong repeatedly. solc states
    it outright: every VariableDeclaration carries `mutability` as one of
    "mutable" / "immutable" / "constant".

    Returns {name: mutability}. An unreadable or absent AST returns {}, which
    leaves every coordinate in place -- the previous behaviour exactly. That is
    failing OPEN, and it is the wrong direction on the merits (keeping a
    non-settable coordinate is the defect this addresses); it is accepted only
    because it is the status quo and because the alternative -- dropping
    coordinates when we could not read the AST -- would silently narrow what is
    generalised for a reason that has nothing to do with the contract. The
    exclusion is reported loudly instead, so its absence is visible.
    """
    if not ast_path or not os.path.exists(ast_path):
        return {}
    try:
        txt = open(ast_path).read()
        # solc's --ast-compact-json output carries a banner before the object.
        i = txt.index("{")
        ast = json.loads(txt[i:])
    except (OSError, ValueError):
        return {}
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if (n.get("nodeType") == "VariableDeclaration" and n.get("stateVariable")):
                nm, mu = n.get("name"), n.get("mutability")
                if nm and mu:
                    # A name declared twice with different mutability cannot be
                    # resolved from the name alone, so the SETTABLE reading wins:
                    # keeping a coordinate costs yield, dropping one that is
                    # really settable costs a region nobody asked to lose.
                    if out.get(nm) in (None, mu) or mu == "mutable":
                        out[nm] = mu
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(ast)
    return out


def function_mutability(ast_path, contract=None):
    """Each function's Solidity `stateMutability`, read from the solc AST.

    S10. `msg.value` is the single measured difference between a unit that
    certifies and one that certifies NOTHING, and it is not a close call:
    MEASURED on one contract, same command apart from the environment,
    0 of 5 paths certified against 4 of 5. A non-payable function carries an
    ABI-level decision on `msg.value`, so a box leaving it unconstrained always
    admits an input that reverts at the gate, and certification is refused
    however far the box is shrunk. Most real code is non-payable, so this is
    most units.

    THE POINT OF READING IT RATHER THAN PINNING BY POLICY. `--pin-env` pins
    every environment quantity the paths agree on, which changes what every
    region MEANS -- each becomes a statement about that environment slice, and
    that is why it is off by default. Pinning `msg.value = 0` on a function the
    SOURCE declares non-payable is not that. It is a fact about the contract:
    on chain, every call to a non-payable function that reaches the body has
    `msg.value == 0`, because the compiler-inserted gate reverts the rest. The
    region is not being narrowed to a slice; the excluded inputs cannot reach
    the body at all.

    What it DOES exclude is the ABI-gate revert path itself, whose whole domain
    is `msg.value != 0`. That path's region goes empty and is reported as empty
    -- which is honest, and is why this is announced on stdout rather than done
    quietly.

    Returns {name: mutability}. Same failure direction as `state_mutability`: an
    unreadable or absent AST returns {}, which pins nothing and reproduces the
    previous behaviour exactly. Failing OPEN is the wrong direction on the
    merits here too, and is accepted for the same reason -- it is the status
    quo, and the absence is reported loudly.

    OVERLOADS: a name declared twice with different mutability cannot be
    resolved from the name alone, so the PAYABLE reading wins. That is the
    conservative direction: it declines to pin, i.e. it declines to act.
    Deliberately the OPPOSITE tie-break to `state_mutability`'s -- there the
    risky move is dropping a coordinate that really is settable, here it is
    pinning a quantity that really can vary.

    SCOPED TO ONE CONTRACT, and that is not a refinement. Every benchmark input
    is a FLATTENED source: dozens of contracts, libraries and interfaces in one
    file, routinely declaring the same function name several times -- an
    `IERC20.transfer` beside the concrete `transfer`, a `deposit() payable` on
    some other contract beside the target's nonpayable `deposit`. Walking the
    whole AST and keying by bare name lets any of those collide, and because the
    tie-break is "payable wins" the collision resolves to payable: the pin is
    skipped and the driver prints "this unit is PAYABLE", which is an assertion
    about the unit that is FALSE. That is the report-a-state-it-does-not-have
    shape, on exactly the multi-contract inputs the corpus sweep uses, and the
    safe tie-break saves soundness without saving either the message or the
    yield.

    With `contract` given, only that `ContractDefinition`'s own functions are
    read. If no contract of that name is found the walk falls back to the whole
    AST and the caller is told, because silently reading nothing would turn a
    lookup failure into "this unit's mutability is unknown", which reads as a
    property of the source.
    """
    if not ast_path or not os.path.exists(ast_path):
        return {}
    try:
        txt = open(ast_path).read()
        ast = json.loads(txt[txt.index("{"):])
    except (OSError, ValueError):
        return {}

    def collect(node, out):
        if isinstance(node, dict):
            if node.get("nodeType") == "FunctionDefinition":
                nm, mu = node.get("name"), node.get("stateMutability")
                if nm and mu:
                    if out.get(nm) in (None, mu) or mu == "payable":
                        out[nm] = mu
            for v in node.values():
                collect(v, out)
        elif isinstance(node, list):
            for v in node:
                collect(v, out)
        return out

    if contract:
        # Every ContractDefinition by AST id is indexed so the selected target
        # can be resolved without colliding with same-named declarations in the
        # flattened file.  Inherited declarations are not target units in this
        # evaluation and must not supply mutability for a target declaration.
        by_id, target = {}, None

        def index(node):
            nonlocal target
            if isinstance(node, dict):
                if node.get("nodeType") == "ContractDefinition":
                    if node.get("id") is not None:
                        by_id[node["id"]] = node
                    if node.get("name") == contract:
                        target = node
                for v in node.values():
                    index(v)
            elif isinstance(node, list):
                for v in node:
                    index(v)

        index(ast)
        if target is not None:
            out = {}
            chain = target.get("linearizedBaseContracts") or [target.get("id")]
            for contract_id in reversed(chain):
                inherited = by_id.get(contract_id)
                if inherited is not None:
                    collect(inherited, out)
            return out
        # Fall through to the whole-AST read; the caller reports that it did.
    return collect(ast, {})


def declared_struct_fields(ast_path):
    """Every field name declared on any struct in the source.

    Same discipline as `state_mutability`: read the fact, do not pattern-match a
    name. The struct lowering introduces members the source never declared --
    `anon_pad$2` is one, measured on EscrowSrc's `Immutables` -- and those are
    not inputs: no generated test can set a padding word, and offering one as a
    coordinate is the same defect as offering an immutable.

    A NAME-level check, and that limit is stated rather than hidden: a lowering
    artifact whose name happened to coincide with a real field declared on some
    OTHER struct would survive. `anon_pad$2` does not, and the alternative --
    resolving each parameter's struct type through the AST -- is a great deal
    more machinery for a case nothing has produced.

    Empty when the AST is unreadable, which keeps every coordinate. That is the
    wrong direction on the merits and is accepted only because it matches the
    prior behaviour; the exclusion is reported loudly so its absence shows.
    """
    if not ast_path or not os.path.exists(ast_path):
        return set()
    try:
        txt = open(ast_path).read()
        ast = json.loads(txt[txt.index("{"):])
    except (OSError, ValueError):
        return set()
    out = set()

    def walk(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "StructDefinition":
                for m in n.get("members", []) or []:
                    if isinstance(m, dict) and m.get("name"):
                        out.add(m["name"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(ast)
    return out


def _ast_root(ast_path):
    """The solc AST object, or None. Same read every other AST reader here does."""
    if not ast_path or not os.path.exists(ast_path):
        return None
    try:
        txt = open(ast_path).read()
        return json.loads(txt[txt.index("{"):])
    except (OSError, ValueError):
        return None


def _walk_ast(n):
    """Yield every dict node in an AST subtree."""
    if isinstance(n, dict):
        yield n
        for v in n.values():
            yield from _walk_ast(v)
    elif isinstance(n, list):
        for v in n:
            yield from _walk_ast(v)


def _chain_nodes(ast, contract):
    """The ContractDefinition nodes of `contract` and its bases, base-first.

    Same C3 linearisation `function_mutability` follows, and scoped for the same
    measured reason: every benchmark input is a FLATTENED source carrying dozens
    of contracts, so a bare state-variable name collides across them routinely.
    An unscoped read here is worse than a wrong mutability, because a mapping
    name taken from some OTHER contract produces a coordinate the tool cannot
    resolve -- and an unresolvable coordinate makes the outer-box round report
    that it measured nothing.

    Falls back to the WHOLE AST when the contract is not found, which is what
    every other reader in this file does; the caller says so on stdout.
    """
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
    if target is None:
        return None
    chain = target.get("linearizedBaseContracts") or [target.get("id")]
    return [by_id[c] for c in reversed(chain) if c in by_id]


def _function_arity(fn):
    ps = ((fn.get("parameters") or {}).get("parameters") or [])
    return len(ps)


def _function_key(fn):
    name = fn.get("name")
    if not name:
        return None
    return (name, _function_arity(fn))


def _call_referenced_id(call):
    if not isinstance(call, dict) or call.get("nodeType") != "FunctionCall":
        return None
    expr = call.get("expression") or {}
    ref = expr.get("referencedDeclaration")
    return ref if isinstance(ref, int) else None


def _call_fallback_key_candidates(call):
    if not isinstance(call, dict) or call.get("nodeType") != "FunctionCall":
        return []
    expr = call.get("expression") or {}
    if isinstance(expr.get("referencedDeclaration"), int):
        return []
    args = call.get("arguments") or []
    nargs = len(args)
    if expr.get("nodeType") == "Identifier" and expr.get("name"):
        return [(expr["name"], nargs)]
    if expr.get("nodeType") == "MemberAccess":
        name = expr.get("memberName")
        if name:
            # Solidity library extension calls such as `x.sub(y)` resolve to
            # `sub(x, y)`, while ordinary member calls keep the explicit arity.
            return [(name, nargs), (name, nargs + 1)]
    return []


def _body_call_refs(fn):
    refs = set()
    for node in _walk_ast(fn.get("body")):
        ref = _call_referenced_id(node)
        if ref is not None:
            refs.add(ref)
    return refs


def _is_direct_self_recursive_wrapper(fn):
    """True for wrappers shaped exactly as `return f(args...);`.

    This is intentionally narrower than "recursive": bounded recursive code can
    be a real target for ESBMC. The benchmark timeout that motivated this check
    was a flattened helper whose whole body is an unconditional same-arity call
    to itself, so no finite path-coverage query gets a useful body witness from
    expanding it.
    """
    key = _function_key(fn)
    if key is None:
        return False
    body = fn.get("body") or {}
    statements = body.get("statements") or []
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if not isinstance(stmt, dict) or stmt.get("nodeType") != "Return":
        return False
    expr = stmt.get("expression") or {}
    self_id = fn.get("id")
    if isinstance(self_id, int) and _call_referenced_id(expr) == self_id:
        return True
    return key in _call_fallback_key_candidates(expr)


def direct_recursive_helpers_in_unit_closure(ast_path, contract, unit, declaration_id=None):
    """Direct self-recursive helper wrappers reachable from a target unit.

    This is a cheap refutation-side guard for path discovery. It does not prove
    that the unit has no path; it only says the first ESBMC enumeration would
    spend its budget expanding a helper whose source body has no base case.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return []
    functions = []
    owner = {}

    for cn in _walk_ast(ast):
        if cn.get("nodeType") != "ContractDefinition":
            continue
        cname = cn.get("name") or "<anonymous>"
        for node in _walk_ast(cn):
            if node.get("nodeType") == "FunctionDefinition":
                functions.append(node)
                owner[id(node)] = cname

    by_id = {id(fn): fn for fn in functions}
    by_decl = {fn["id"]: fn for fn in functions if isinstance(fn.get("id"), int)}
    chain = _chain_nodes(ast, contract) if contract else None
    if chain is None:
        chain = [ast]
    start_ids = set()
    for cn in chain:
        for node in _walk_ast(cn):
            kind = node.get("kind", "function")
            node_name = node.get("name") or (kind if kind in ("fallback", "receive") else "")
            if (node.get("nodeType") == "FunctionDefinition" and node_name == unit
                    and (declaration_id is None or node.get("id") == declaration_id)):
                start_ids.add(id(node))
    if declaration_id is None and len(start_ids) > 1:
        return []
    if not start_ids:
        return []

    graph = {id(fn): _body_call_refs(fn) for fn in functions}
    wrappers = {id(fn) for fn in functions if _is_direct_self_recursive_wrapper(fn)}
    seen = set()
    stack = list(start_ids)
    found = []
    while stack:
        fid = stack.pop()
        if fid in seen:
            continue
        seen.add(fid)
        fn = by_id.get(fid)
        if fn is None:
            continue
        if fid in wrappers:
            found.append(fn)
            continue
        for ref in graph.get(fid, ()):
            callee = by_decl.get(ref)
            if callee is None:
                continue
            cid = id(callee)
            if cid not in seen:
                stack.append(cid)

    def label(fn):
        return f"{owner.get(id(fn), '<unknown>')}.{fn.get('name')}/{_function_arity(fn)}"

    return sorted(set(label(fn) for fn in found))


def _type_string(n):
    return ((n or {}).get("typeDescriptions") or {}).get("typeString") or ""


def literal_state_constants(ast_path, contract=None):
    """Literal integer Solidity constants visible from the target contract."""
    ast = _ast_root(ast_path)
    if ast is None:
        return {}
    nodes = _chain_nodes(ast, contract) if contract else None
    if nodes is None:
        nodes = [ast]
    out = {}

    def numeric_literal(n):
        if not isinstance(n, dict) or n.get("nodeType") != "Literal":
            return None
        if n.get("subdenomination"):
            return None
        if n.get("kind") != "number":
            return None
        value = str(n.get("value") or "")
        try:
            return parse_int(value)
        except ValueError:
            pass
        m = re.fullmatch(r"([0-9]+)[eE]([0-9]+)", value)
        if m:
            return int(m.group(1)) * (10**int(m.group(2)))
        ty = ((n.get("typeDescriptions") or {}).get("typeString") or "")
        m = re.fullmatch(r"int_const ([0-9]+)_by_1", ty)
        if m:
            return int(m.group(1))
        return None

    def walk(n):
        if isinstance(n, dict):
            if (n.get("nodeType") == "VariableDeclaration" and n.get("constant") and n.get("name")):
                value = numeric_literal(n.get("value"))
                if value is not None:
                    out[n["name"]] = value
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    for node in nodes:
        walk(node)
    return out


def literal_state_constant_types(ast_path, contract=None):
    """Type strings for literal Solidity state constants visible to a contract."""
    ast = _ast_root(ast_path)
    if ast is None:
        return {}
    nodes = _chain_nodes(ast, contract) if contract else None
    if nodes is None:
        nodes = [ast]
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if (n.get("nodeType") == "VariableDeclaration" and n.get("constant") and n.get("name")
                    and isinstance(n.get("value"), dict)
                    and n["value"].get("nodeType") == "Literal"):
                td = n.get("typeDescriptions") or {}
                type_string = td.get("typeString")
                if not type_string:
                    tn = n.get("typeName") or {}
                    type_string = ((tn.get("typeDescriptions") or {}).get("typeString")
                                   or tn.get("name"))
                if type_string:
                    out[n["name"]] = type_string
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    for node in nodes:
        walk(node)
    return out


def path_cov_fixture_state_pins(esbmc_args, contract=None):
    """Scalar state pins implied by a --path-cov-fixture JSON file."""
    paths = []
    i = 0
    while i < len(esbmc_args or []):
        arg = esbmc_args[i]
        if arg == "--path-cov-fixture" and i + 1 < len(esbmc_args):
            paths.append(esbmc_args[i + 1])
            i += 2
            continue
        prefix = "--path-cov-fixture="
        if arg.startswith(prefix):
            paths.append(arg[len(prefix):])
        i += 1
    pins, skipped = {}, []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as stream:
                fixture = json.load(stream)
        except (OSError, ValueError) as exc:
            skipped.append(f"{path}: unreadable fixture ({exc})")
            continue
        fx_contract = fixture.get("contract")
        if contract and fx_contract and fx_contract != contract:
            skipped.append(f"{path}: fixture contract {fx_contract!r} does "
                           f"not match {contract!r}")
            continue
        state = fixture.get("state") or {}
        if not isinstance(state, dict):
            skipped.append(f"{path}: fixture state is not an object")
            continue
        for name, raw in state.items():
            if not name:
                continue
            try:
                pins[f"state.{name}"] = parse_int(str(raw))
            except ValueError:
                skipped.append(f"{path}: state.{name} is not a scalar integer")
    return pins, skipped


def mapping_state_vars(ast_path, contract=None):
    """Mapping state variables of this contract, as {name: (key_ts, value_ts)}.

    READ FROM THE SOURCE, not from the counterexample payload -- and that is the
    whole point of this function rather than a convenience.

    WHAT THE PAYLOAD DOES AND DOES NOT GIVE, both measured, because the first
    version of this comment claimed the payload "never" carries a mapping slot
    and that is false:

      * SlotMin: it DOES carry one -- `state.bal[0xFF..FF]` arrives as a free
        coordinate with no help from this function. The key is the LITERAL the
        counterexample happened to land on.
      * farming/balanceOf and farming/deposit: it carries none at all.
        `_balances` appears in neither the free coordinates nor the refused
        list.

    So the payload can only ever offer a slot at a key some counterexample
    already picked. The slot a guard actually reads -- `_balances[account]`,
    following the PARAMETER for every account rather than one address -- is not
    a payload name under any circumstances, because the payload is a list of
    values and that coordinate is a function of an input. That one is reachable
    only by proposing it, which is what this function exists to do.

    NESTING AND STRUCT VALUES ARE BOTH RETURNED NOW, and the entry says which.
    The old refusal read "a nested mapping needs a second hash and a struct
    needs a member offset", and by the time this was written HALF of it was
    stale: notes/coverage/poc/D44_MapStructValue.sol is a matched pair whose
    emitted PUTs carry `balStruct[k].amount` and `balStruct[k].tag` with the
    right mask and shift, so the member offset demonstrably works end to end.
    What that pair also settled is where the remaining cost is -- with the
    struct ruled out, aqua's silent four-level `_balances` is the NESTING.

    THE SHAPE, and it is backward compatible on purpose so the callers and
    their tests keep working unchanged:

        {name: (key_type, value_type)}                  -- one level, scalar
        {name: ((kt1, kt2, ...), value_type, tails)}    -- nested scalar
        {name: ((kt1, kt2, ...), value_type, tails,
                {tail: leaf_type})}                     -- struct leaf typed

    `tails` is [""] for a scalar value and [".field", ...] for a struct one.
    The optional fourth element gives callers the leaf's own elementary type;
    older callers that ignore it still read the first three elements exactly as
    before.
    A caller that only understands the 2-tuple still reads element 0 as the key
    type; it will see a tuple rather than a string and match no parameter,
    which fails CLOSED -- it proposes nothing rather than proposing a
    one-level name for a four-level store.

    A key that is not a value type is still refused, per level and by level.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return {}, []
    nodes = _chain_nodes(ast, contract) if contract else None
    if nodes is None:
        nodes = [ast]
    out, refused = {}, []
    # Struct definitions are collected from the WHOLE root, never from the
    # contract chain: a struct used as a mapping value is routinely declared in
    # a library or at file scope, and scoping the search to the chain would
    # refuse it as "unknown" for a reason that is about this walk, not about
    # the source.
    structs = _struct_scalar_fields(ast)
    struct_nodes = {}

    def collect_struct_nodes(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "StructDefinition" and n.get("name"):
                struct_nodes[n["name"]] = n
            for v in n.values():
                collect_struct_nodes(v)
        elif isinstance(n, list):
            for v in n:
                collect_struct_nodes(v)

    collect_struct_nodes(ast)

    def mapping_leaf_specs(tn, prefix="", key_types=None, seen=None):
        key_types = list(key_types or [])
        seen = set(seen or set())
        if not isinstance(tn, dict):
            return [], []
        if tn.get("nodeType") == "Mapping":
            kt = tn.get("keyType") or {}
            if kt.get("nodeType") != "ElementaryTypeName":
                return [], [
                    f"{prefix or '<root>'} (key at level {len(key_types)} is "
                    f"{_type_string(kt) or 'non-scalar'}, not a value type)"
                ]
            return mapping_leaf_specs(
                tn.get("valueType") or {}, prefix, key_types + [_type_string(kt)], seen)
        if tn.get("nodeType") == "ElementaryTypeName":
            if not key_types:
                return [], []
            tail = prefix or ""
            ty = _type_string(tn)
            return [(tuple(key_types), ty, tail, ty)], []
        sname = _user_type_name(tn)
        if not sname:
            return [], [
                f"{prefix or '<root>'} (value is "
                f"{_type_string(tn) or 'non-scalar'}; no scalar observable "
                "can be named inside it)"
            ]
        if sname in seen:
            return [], [f"{prefix or sname} (recursive struct type {sname})"]
        snode = struct_nodes.get(sname)
        if snode is None:
            return [], [
                f"{prefix or '<root>'} (struct {sname} was not found in the "
                "AST, so no scalar observable can be named inside it)"
            ]
        leaves, refused_local = [], []
        for member in snode.get("members", []) or []:
            if not isinstance(member, dict) or not member.get("name"):
                continue
            mt = member.get("typeName") or {}
            mprefix = prefix + "." + member["name"]
            if mt.get("nodeType") == "ElementaryTypeName":
                if key_types:
                    ty = _type_string(mt)
                    leaves.append((tuple(key_types), ty, mprefix, ty))
                continue
            sub_leaves, sub_refused = mapping_leaf_specs(mt, mprefix, key_types, seen | {sname})
            leaves.extend(sub_leaves)
            refused_local.extend(sub_refused)
        return leaves, refused_local

    def walk(n):
        if isinstance(n, dict):
            if (n.get("nodeType") == "VariableDeclaration" and n.get("stateVariable")
                    and n.get("name")):
                tn = n.get("typeName") or {}
                if tn.get("nodeType") == "Mapping":
                    nm = n["name"]
                    leaves, leaf_refused = mapping_leaf_specs(tn)
                    for r in leaf_refused:
                        refused.append(nm +
                                       r[len("<root>"):] if r.startswith("<root>") else f"{nm}.{r}")
                    if not leaves:
                        if not leaf_refused:
                            refused.append(f"{nm} (value is "
                                           f"{_type_string(tn) or 'non-scalar'}; it has no "
                                           "scalar mapping leaf this walk can resolve)")
                        pass
                    elif len(leaves) == 1 and leaves[0][2] == "" \
                            and len(leaves[0][0]) == 1:
                        out.setdefault(nm, (leaves[0][0][0], leaves[0][1]))
                    else:
                        groups = {}
                        for kts, _vts, tail, leaf_ty in leaves:
                            groups.setdefault(tuple(kts), []).append((tail, leaf_ty))
                        for kts, items in groups.items():
                            tails = [tail for tail, _ty in items]
                            leaf_types = {tail: ty for tail, ty in items}
                            out.setdefault(nm,
                                           (tuple(kts), "nested mapping leaf", tails, leaf_types))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    for node in nodes:
        walk(node)
    return out, refused


def _user_type_name(tn):
    """The struct/contract name a UserDefinedTypeName node refers to.

    Two spellings, because solc changed it: modern ASTs put it in
    `pathNode.name`, older ones directly in `name`. Reading only one silently
    returns None on the other and the value is then refused as unresolvable --
    a refusal about the reader, reported as a fact about the source.
    """
    if not isinstance(tn, dict):
        return None
    pn = tn.get("pathNode")
    if isinstance(pn, dict) and pn.get("name"):
        return str(pn["name"]).split(".")[-1]
    if tn.get("name"):
        return str(tn["name"]).split(".")[-1]
    return None


def _struct_scalar_fields(root):
    """{struct name: [(field, elementary type)]} -- SCALAR members only.

    NOT the same question as `declared_struct_fields`, which returns a flat set
    of every field name declared anywhere and exists to spot lowering
    artifacts. This one needs the fields OF ONE struct, in order, with their
    types, so a mapping's struct value can be named field by field. Two
    functions because they answer two questions; the flat set cannot answer
    this one and narrowing it would break its own caller.

    A non-scalar member (a nested struct, an array, a mapping) is simply not
    listed: it carries no interval, exactly as for a struct parameter.
    """
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "StructDefinition" and n.get("name"):
                fields = []
                for m in n.get("members", []) or []:
                    if not isinstance(m, dict) or not m.get("name"):
                        continue
                    mt = m.get("typeName") or {}
                    if mt.get("nodeType") == "ElementaryTypeName":
                        fields.append((m["name"], _type_string(mt)))
                out.setdefault(n["name"], fields)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(root)
    return out


def _state_slot_tail(coord):
    """(mapping_name, tail) for a `state.m[k]...tail` coordinate, if shaped so."""
    if not (coord or "").startswith("state."):
        return None
    rest = coord[len("state."):]
    m = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)", rest)
    if not m:
        return None
    name = m.group(1)
    i = len(name)
    if i >= len(rest) or rest[i] != "[":
        return None
    depth = 0
    saw_slot = False
    while i < len(rest):
        ch = rest[i]
        if ch == "[":
            depth += 1
            saw_slot = True
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0:
            break
        i += 1
    if depth != 0 or not saw_slot:
        return None
    return name, rest[i:]


def mapping_slot_type_ranges(maps, coords):
    """Type ranges for proposed mapping slot coordinates from source AST types."""
    ranges = {}
    for coord in coords:
        parsed = _state_slot_tail(coord)
        if parsed is None:
            continue
        name, tail = parsed
        spec = maps.get(name)
        if not spec:
            continue
        leaf_types = spec[3] if len(spec) > 3 and isinstance(spec[3], dict) \
            else {}
        ty = leaf_types.get(tail)
        if ty is None and tail == "":
            ty = spec[1] if len(spec) > 1 else None
        tr = elementary_type_range(ty)
        if tr is not None:
            ranges[coord] = tr
    return ranges


ESBMC_MAP_BASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
MAPPING_SOURCE_META_KEY = "__source_mapping_key"


def map_esbmc_certifiable(base):
    """Whether the ESBMC side can resolve this mapping base name today."""
    return ESBMC_MAP_BASE_RE.match(base or "") is not None


def mapping_source_key(name, spec):
    """The solc/source key represented by a Stage-2 mapping spec."""
    if not name:
        return None
    if spec and isinstance(spec[-1], dict):
        source = spec[-1].get(MAPPING_SOURCE_META_KEY)
        if source:
            return source
    return name


def _alias_mapping_spec(spec, source_key):
    """Attach source metadata without changing the old source-spec shape."""
    if not spec or len(spec) < 2:
        return spec
    kts, vts = spec[0], spec[1]
    tails = list(spec[2]) if len(spec) > 2 else [""]
    leaf_types = spec[3] if len(spec) > 3 and isinstance(spec[3], dict) else {}
    return (kts, vts, tails, dict(leaf_types), {MAPPING_SOURCE_META_KEY: source_key})


def mapping_query_key(maps, source_key):
    """Prefer an ESBMC store-name alias for a source mapping key."""
    if not maps or not source_key:
        return None
    aliases = sorted(name for name, spec in maps.items()
                     if mapping_source_key(name, spec) == source_key and name != source_key)
    if aliases:
        return aliases[0]
    if source_key in maps:
        return source_key
    return None


def add_esbmc_mapping_aliases(maps, state_store_names):
    """Add verifier-store aliases while keeping source specs backward-compatible."""
    out = dict(maps or {})
    for name, spec in list((maps or {}).items()):
        source_base = mapping_source_key(name, spec)
        store_base = (state_store_names or {}).get(source_base)
        if not store_base or store_base == source_base:
            continue
        if not map_esbmc_certifiable(store_base):
            continue
        out.setdefault(store_base, _alias_mapping_spec(spec, source_base))
    return out


def prefer_esbmc_mapping_aliases(maps):
    """Drop source-name rows when an ESBMC store-name alias exists."""
    aliases_by_source = {}
    for name, spec in (maps or {}).items():
        source = mapping_source_key(name, spec)
        if source and name != source:
            aliases_by_source.setdefault(source, []).append(name)
    preferred = {}
    for name, spec in (maps or {}).items():
        source = mapping_source_key(name, spec)
        if source and name == source and source in aliases_by_source:
            continue
        preferred[name] = spec
    return preferred


def state_coord_type_ranges(ast_path, contract, coords, state_store_names=None):
    """Elementary type ranges for source and ESBMC-alias state coordinates."""
    wanted = set(coords or [])
    try:
        state_types = contract_state_types(ast_path, contract)
    except (OSError, ValueError):
        return {}
    out = {}
    for name, ty in (state_types or {}).items():
        tr = elementary_type_range(ty)
        if tr is None:
            continue
        candidates = ["state." + name]
        alias = (state_store_names or {}).get(name)
        if alias:
            candidates.append("state." + alias)
        for coord in candidates:
            if coord in wanted:
                out[coord] = tr
    return out


# How many SUBSCRIPTED state coordinates (`state.m[k]`, `state.s[k].f`) one
# unit's region search may carry. Plain scalars are never capped. Keeping
# every closure match OOM'd ETHRegistrarController at 12 GiB and cost the
# whole case; the region search pays for every coordinate it carries.
STATE_SLOT_COORD_BUDGET = 6


def state_coord_source_name(coord):
    """The SOURCE state variable a coordinate names, or None if it names none.

    A coordinate is a path into one declared variable, and only the head of that
    path is a name the AST dependency closure knows. `state.deposits[0]`,
    `state.deposits[msg.sender]`, `state.guesses$11[msg.sender].block` and
    `state.feeBps$7` all name `deposits`, `deposits`, `guesses` and `feeBps`.

    Splitting on `$` ALONE was the bug this exists to keep fixed: it left the
    subscript attached, so `deposits[0]` was compared against a closure holding
    `deposits`, missed, and the coordinate was reported as a field outside the
    target's closure. That is the opposite of true for every mapping the unit
    actually reads, and the consequence was not a wrong region but a MISSING
    WITNESS: the counterexample harvest reports mapping entries under their
    CONCRETE key, that harvested coordinate was dropped here, and the slot
    proposer's symbolic `state.m[k]` then reached level 0 with nothing to probe
    and descended to the ladder carrying its full type range.
    """
    text = str(coord)
    if not text.startswith("state."):
        return None
    head = text[len("state."):]
    for sep in ("[", "."):
        head = head.split(sep, 1)[0]
    # Only a TRAILING `$<digits>` is ESBMC's lowering suffix. Splitting on a
    # bare `$` would also cut a legitimate Solidity identifier -- `$` is a
    # valid character in one -- so this mirrors `_pin_source_name` rather than
    # inventing a second, looser rule for the same name.
    return re.sub(r"\$\d+$", "", head)


def filter_unreferenced_state_coords(coords, dependencies, budget=STATE_SLOT_COORD_BUDGET):
    """Drop state coordinates outside a complete source dependency closure.

    Matching is by SOURCE NAME (`state_coord_source_name`), so `state.m[0]` and
    `state.m[msg.sender]` are both recognised as naming `m`. Splitting on `$`
    alone left the subscript attached, which meant a mapping entry survived iff
    ESBMC happened to give it a `$N` lowering suffix -- `state.d$23[msg.sender]`
    kept, `state.deposits[msg.sender]` dropped. That accident cost the WITNESS:
    the counterexample harvest reports mapping entries under a concrete key,
    that coordinate was dropped here, and the slot proposer's symbolic
    `state.m[k]` then reached level 0 with nothing to probe.

    MEASURED on the 35-subject stratified sample, matching against the source
    name and keeping every match: R1/R2 share of valid PUTs 55.6% -> 69.2%,
    PUT:concrete essentially flat (89.9% -> 89.2%). Keeping every match also
    OOM'd `ETHRegistrarController` at 12 GiB, which cost a whole case -- the
    region search pays for each coordinate, and a contract with many mapping
    entries in its closure can add a lot of them at once.

    Hence the BUDGET. Subscripted state coordinates are the ones that multiply,
    so only they are capped; plain scalars are all kept. What the budget cuts is
    NAMED in `dropped` exactly like a closure miss, never dropped silently.
    """
    if dependencies is None:
        return list(coords or []), []
    live = {str(name) for name in dependencies}
    kept, dropped, subscripted = [], [], []
    for coord in coords or []:
        source_name = state_coord_source_name(coord)
        if source_name is None:
            kept.append(coord)
            continue
        if source_name not in live:
            dropped.append(str(coord))
            continue
        if "[" in str(coord):
            subscripted.append(coord)
        else:
            kept.append(coord)
    # Deterministic and explainable: shortest first, then lexicographic. A
    # shorter path into the same variable is the less speculative coordinate,
    # and an arbitrary set order would make two runs of one configuration
    # disagree about which coordinates the budget bought.
    subscripted.sort(key=lambda c: (len(str(c)), str(c)))
    kept.extend(subscripted[:budget])
    for coord in subscripted[budget:]:
        dropped.append(f"{coord} (subscripted state coordinate beyond the "
                       f"retention budget of {budget}; the region is widened "
                       f"over it)")
    return kept, sorted(dropped)

def unit_params(ast_path, contract, unit, declaration_id=None):
    """This unit's parameters, as [(name, typeString)] in declaration order.

    The KEY of a proposed slot has to be something the query can express, and
    `resolve_coord` accepts a literal, a parameter of the unit, or `msg.sender`.
    A parameter is the interesting one: `_balances[account]` is the slot the
    guard actually reads, and no literal can stand in for it.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return []
    if contract:
        target = next(
            (node for node in _walk_ast(ast)
             if node.get("nodeType") == "ContractDefinition" and node.get("name") == contract),
            None)
        nodes = [target] if target is not None else [ast]
    else:
        nodes = [ast]
    found = []

    def walk(n):
        if isinstance(n, dict):
            kind = n.get("kind", "function")
            node_name = n.get("name") or (kind if kind in ("fallback", "receive") else "")
            if (n.get("nodeType") == "FunctionDefinition" and node_name == unit
                    and (declaration_id is None or n.get("id") == declaration_id)):
                ps = ((n.get("parameters") or {}).get("parameters") or [])
                params = []
                declared_names = {p.get("name") for p in ps if p.get("name")}
                for ordinal, p in enumerate(ps):
                    # The Solidity frontend gives omitted ABI parameters the
                    # same reserved name as the symbol table. Keeping the AST
                    # reader in lockstep is essential: otherwise ESBMC
                    # publishes a parameter coordinate that Stage 2 cannot
                    # resolve back to a source type and Stage 4 drops it.
                    name = p.get("name")
                    if not name:
                        name = f"omitted_param_{ordinal}"
                        suffix = 0
                        while name in declared_names:
                            suffix += 1
                            name = (f"omitted_param_{ordinal}_{suffix}")
                    params.append((name, _type_string(p)))
                found.append(params)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    for node in nodes:
        walk(node)
    # The target-only unit schedule prevents inherited declarations from being
    # passed here.  Keep the last-match behavior for callers that provide an
    # explicit declaration id or a legacy whole-AST input.
    return found[-1] if found else []


def propose_slot_coords(maps,
                        params,
                        limit,
                        dependencies=None,
                        slot_accesses=None,
                        key_literals=None):
    """Slot coordinate names to add, plus a line per candidate NOT added.

    A slot is proposed only where the KEY has a name the query can express and
    whose type MATCHES the mapping's declared key type. Type-matching is not
    pedantry: `bal[someUint]` on a `mapping(address => ...)` resolves to a
    different slot than the guard reads, and the region would then be certified
    about a quantity no execution touches.

    BUDGETED, and the budget is the caller's flag rather than a constant. Ladder
    cost is MULTIPLICATIVE in the coordinate count -- the same reason
    environment quantities are never made free coordinates here -- so proposing
    every (mapping x parameter) pair on a real contract would make the round
    unaffordable to buy a coordinate nothing may need. Whatever the budget cuts
    is named, never dropped silently.
    """
    cand, skipped = [], []
    param_types = dict(params)
    key_literals = dict(key_literals or {})

    def spec_parts(spec):
        kts = spec[0]
        # A one-level scalar mapping still arrives as the plain 2-tuple, so
        # both shapes are read here rather than forcing every caller to change.
        if isinstance(kts, str):
            kts = (kts, )
        tails = list(spec[2]) if len(spec) > 2 else [""]
        return kts, tails

    def key_matches(coord, key_type):
        if coord == "msg.sender":
            return key_type == "address"
        if param_types.get(coord) != key_type:
            return False
        return True

    def key_name(coord):
        return key_literals.get(coord, coord)

    def key_refusal(name, keys, lvl, key, kt):
        base = f"state.{name}" + "".join(f"[{k}]" for k in keys)
        return (f"{base} (source slot not proposed: key level {lvl} uses {key}, "
                f"but the verifier can only express unit parameters of type "
                f"'{kt}'" + (" or msg.sender" if kt == "address" else "") + ")")

    def push_slot(name, keys, tails):
        base = f"state.{name}" + "".join(f"[{k}]" for k in keys)
        for tail in tails:
            coord = base + tail
            if coord not in cand:
                cand.append(coord)

    def source_spec(name):
        query_name = mapping_query_key(maps, name)
        if query_name is not None:
            return query_name, maps[query_name], None
        base, dot, tail = name.partition(".")
        query_base = mapping_query_key(maps, base)
        if not dot or query_base is None:
            return None, None, None
        kts, tails = spec_parts(maps[query_base])
        wanted = "." + tail
        if wanted not in tails:
            return query_base, None, wanted
        spec = list(maps[query_base])
        if len(spec) > 2:
            spec[2] = [wanted]
        else:
            spec.extend(["", [wanted]][len(spec) - 1:])
        return query_base, tuple(spec), wanted

    def accept_source_slot(name, keys):
        source_base, spec, wanted_tail = source_spec(name)
        if spec is None:
            if wanted_tail is not None:
                skipped.append(f"state.{name}[...] source slot not proposed: solc storage "
                               "layout does not report that member as an ESBMC-queryable "
                               "scalar mapping entry")
            return False
        kts, tails = spec_parts(spec)
        if len(keys) != len(kts):
            return False
        resolved_keys = []
        for lvl, (key, kt) in enumerate(zip(keys, kts)):
            if key_matches(key, kt):
                resolved_keys.append(key_name(key))
                continue
            skipped.append(key_refusal(source_base, keys, lvl, key, kt))
            return False
        push_slot(source_base, resolved_keys, tails)
        return True

    if dependencies is None:
        map_order = sorted(maps)
    else:
        map_order = []
        for name in dependencies:
            query_name = mapping_query_key(maps, name)
            if query_name is not None and query_name not in map_order:
                map_order.append(query_name)
        for name in sorted(set(maps) - set(map_order)):
            skipped.append(f"state.{name}[...] (excluded by {SLOT_DEPENDENCY_POLICY}: "
                           "the target, its modifiers, and the transitive callable "
                           "closure contain no solc-resolved reference to this mapping)")

    if slot_accesses:
        source_seen = []
        for name, keys in slot_accesses:
            source_base, _spec, _wanted_tail = source_spec(name)
            if (dependencies is not None and name not in dependencies
                    and source_base not in map_order):
                continue
            if accept_source_slot(name, keys) and source_base not in source_seen:
                source_seen.append(source_base)
        for name in source_seen:
            if name in map_order:
                map_order.remove(name)
                skipped.append(f"state.{name}[...] fallback cross-product suppressed: "
                               "solc resolved concrete key chain(s) for this mapping in "
                               "the target's callable closure, so the budget is spent on "
                               "source slots before guessed same-type key combinations")

    for m in map_order:
        spec = maps[m]
        kts, tails = spec_parts(spec)

        # ---- ONE CANDIDATE KEY SET PER LEVEL ----
        per_level, bad = [], False
        for lvl, kt in enumerate(kts):
            keys = [key_name(p) for p, pt in params if key_matches(p, kt)]
            if kt == "address":
                keys.append("msg.sender")
            if not keys:
                skipped.append(f"state.{m}[...] (at key level {lvl} this unit has no "
                               f"parameter of the key type '{kt}', and the key type is "
                               f"not address so msg.sender does not apply). A nested "
                               f"store needs a key at EVERY level: one missing level "
                               f"leaves no slot to name, and a name with fewer keys "
                               f"would denote a whole sub-store instead")
                bad = True
                break
            per_level.append(keys)
        if bad:
            continue

        # ---- THE CROSS PRODUCT IS MULTIPLICATIVE, AND THAT IS SAID OUT LOUD --
        #
        # levels x keys-per-level x struct-fields. Four levels with three
        # candidate keys each and a two-field value is 162 names for ONE
        # mapping, and the ladder's cost is multiplicative in the coordinate
        # count. The caller's --slot-coords budget still cuts, and whatever it
        # cuts is named below rather than dropped -- but the reader has to know
        # the pool is this large before reading a truncated list.
        combos = [[]]
        for keys in per_level:
            combos = [c + [k] for c in combos for k in keys]
        if len(kts) > 1 or tails != [""]:
            skipped.append(f"state.{m} is a {len(kts)}-level store with "
                           f"{len(tails)} scalar observable(s) per slot: "
                           f"{len(combos) * len(tails)} candidate name(s) before the "
                           f"budget. This is a note, not a refusal")
        for combo in combos:
            push_slot(m, combo, tails)
    if limit and len(cand) > limit:
        skipped += [f"{c} (over the --slot-coords budget of {limit})" for c in cand[limit:]]
        cand = cand[:limit]
    return cand, skipped


def lowering_artifacts(coords, declared, param_types=None):
    """Struct-field coordinates the SOURCE never declared.

    Only `base.field` coordinates are considered, and only when the declared set
    is non-empty -- with nothing to compare against, everything would look
    undeclared and the whole coordinate list would vanish for a reason that has
    nothing to do with the contract.

    A dynamic ABI parameter is a source-level aggregate whose ``length`` is a
    real caller-controlled scalar.  ESBMC publishes that scalar as
    ``param.length``; it is not a lowering artifact, even though ``length`` is
    absent from Solidity's source struct declarations.
    """
    param_types = dict(param_types or {})
    if not declared:
        return {}
    out = {}
    for c in coords:
        if "." not in c or c.startswith(("state.", "msg.", "tx.", "block.")):
            continue
        base, field = c.rsplit(".", 1)
        param_type = str(param_types.get(base) or "").strip()
        param_type = re.sub(r"\s+(?:memory|calldata|storage)$", "", param_type)
        dynamic_param = (param_type in ("bytes", "string") or param_type.endswith("[]"))
        if field == "length" and dynamic_param:
            continue
        field = c.rsplit(".", 1)[1]
        if field not in declared:
            out[c] = "not declared on any struct in the source"
    return out


def unsettable_coords(coords, mutability):
    """Coordinates NO generated test can set, with the reason.

    An `immutable` is fixed at construction and an `constant` is baked into the
    code; neither is an input, and `vm.store` cannot reach either. Offering one
    as a free coordinate hands the verifier an input space WIDER than reality, so
    certification over it cannot succeed -- the witness simply moves the
    quantity, round after round, and the shrink budget runs out. That is not a
    search-power problem and no ladder fixes it.

    MEASURED, EscrowSrc: `cancel`'s only two free coordinates are `state.FACTORY`
    and `state.RESCUE_DELAY`, and BOTH are immutable -- the contract has no
    mutable state variable at all. Its 0-of-4 certification result was therefore
    never about the search; the loop was generalising over quantities that do not
    vary.

    Only `state.` coordinates are considered: parameters and environment
    quantities are settable by construction, and a name collision between a
    parameter and a state variable must not silently disqualify the parameter.
    """
    out = {}
    for c in coords:
        source_name = state_coord_source_name(c)
        if source_name is None:
            continue
        # Keyed by the SOURCE name, not by the raw suffix: `state.deposits[0]`
        # would otherwise be looked up as `deposits[0]` and never match a
        # mutability table holding `deposits`. Mappings are never constant, so
        # this site was not the one that hurt -- but the same naive slice was,
        # one function away, and one spelling of the rule is the point.
        mu = mutability.get(source_name)
        if mu in ("immutable", "constant"):
            out[c] = mu
    return out


def _pin_source_name(name):
    # `state.guesses[msg.sender].block` used to split on the FIRST dot, which
    # lands inside the mapping key and yields `guesses[msg`. Delegating keeps
    # one rule for "which declared variable does this coordinate name".
    resolved = state_coord_source_name(name)
    if resolved is not None:
        return resolved
    text = str(name or "")
    text = text.split(".", 1)[0]
    return re.sub(r"\$\d+$", "", text)


def nonquery_literal_constant_pins(pins, literal_constant_types):
    """State literal constants that should stay semantic pins, not query pins."""
    out = set()
    types = literal_constant_types or {}
    for name in pins or {}:
        if not str(name).startswith("state."):
            continue
        type_string = types.get(_pin_source_name(name))
        if bytes_static_len(type_string) is not None:
            out.add(name)
    return out


def certification_query_pins(pins, omit=None):
    """Pins that must constrain Stage-2 certification queries.

    Immutable/constant state values are not runtime coordinates a generated
    Foundry PUT can fuzz, but once the path harvest observed them they are still
    facts about the deployed contract slice being certified. Omitting them from
    the ESBMC query lets the solver refute a candidate region with states no
    deployment can produce.

    Literal bytesN constants are the exception. They are bytecode/source facts,
    not mutable deployment state, and the driver keeps their values in the
    semantic `pins` map plus the source `constants` catalogue. Passing them as
    state equality assumptions to ESBMC can force storage-slot constants through
    the runtime state encoding and make an otherwise tiny outer-box query fail
    in the SMT layer.
    """
    omit = set(omit or ())
    return {n: v for n, v in sorted((pins or {}).items()) if n not in omit}


def drop_unexpressible_query_names(names, pins=None, *region_maps):
    """Remove coordinates a certification query cannot express.

    Outer-box treats an unexpressible coordinate as "measure the remaining
    coordinates"; certification refuses the whole query.  Once an outer round
    has already named such coordinates, carrying them into certification can
    only buy a predictable refusal.  Dropping them asks ESBMC to certify the
    region for all values of those coordinates, which is stronger than the
    bounded query that the tool cannot spell.
    """
    dropped = set()
    for n in sorted(set(names or ())):
        if pins is not None and n in pins:
            pins.pop(n, None)
            dropped.add(n)
        for mapping in region_maps:
            if not mapping:
                continue
            for box in mapping.values():
                if isinstance(box, dict) and n in box:
                    box.pop(n, None)
                    dropped.add(n)
    return dropped


def geometric_values(limit, lo=0):
    """Round-1 ladder: magnitude-independent, one run.

    SYMMETRIC WHEN THE COORDINATE'S RANGE GOES NEGATIVE. The unsigned ladder
    starts at 0 and doubles upward, which brackets a bound of unknown magnitude
    in one run -- but on a signed coordinate every probe would then sit in the
    non-negative half, and a boundary at -1000 would be bracketed only by the
    endpoint. The negative side is laid the same way, mirrored, so the property
    the ladder exists for ("within a factor of two of the bound, whatever its
    magnitude, in ONE run") holds on both halves.

    `lo` defaults to 0, so an unsigned coordinate produces the identical list it
    always did -- byte for byte, which is what the must-flip pins.
    """
    vals, v = [0], 1
    while v <= limit:
        vals.append(v)
        v *= 2
    vals.append(limit)
    if lo < 0:
        v = 1
        while -v >= lo:
            vals.append(-v)
            v *= 2
        vals.append(lo)
    return sorted(set(vals))


def thin_to(values, k):
    """Keep at most k of `values`, evenly spaced, endpoints always kept.

    Endpoints matter more than the interior: the type bounds are what make a
    coordinate fully bounded at all, and a ladder that loses them leaves the
    coordinate half-open, which blocks the subtraction entirely.
    """
    if k >= len(values) or k <= 0:
        return list(values)
    if k == 1:
        return [values[0]]
    n = len(values)
    idx = sorted({0, n - 1} | {round(i * (n - 1) / (k - 1)) for i in range(k)})
    return [values[i] for i in idx][:max(k, 2)]


def budget_probe_values(values_by_coord, n_paths, budget):
    """Thin every coordinate's ladder so the ROUND stays inside a claim budget.

    The quantity being bounded is the number of claims EMITTED, and that is not
    a guess about where the cost is -- it is measured. On EscrowSrc.withdraw the
    geometric bracket laid ~1548 values per coordinate across 6 coordinates and
    5 paths, and in 300 seconds:

        n=148 queries reached the solver, total 6.9s of solving

    148 queries. Six point nine seconds. The other ~293 seconds went to
    instrumenting and encoding roughly ninety thousand claims. The round is
    EMISSION-bound, not solve-bound, so a budget expressed in solver time or in
    probes-answered would bound the wrong thing.

    Claims per round = sum over coordinates of (values x paths x 2 directions),
    so the per-coordinate allowance falls out directly. Never below 2: one value
    cannot distinguish a point domain from a vacuous path (see the level-0
    warning), and that is a soundness-adjacent property rather than a resolution
    one.
    """
    if budget <= 0 or not values_by_coord:
        return dict(values_by_coord), None
    ncoord = len(values_by_coord)
    per = budget // max(1, ncoord * max(1, n_paths) * 2)
    per = max(2, per)
    out, thinned = {}, 0
    for c, vals in values_by_coord.items():
        if len(vals) > per:
            out[c] = thin_to(list(vals), per)
            thinned += 1
        else:
            out[c] = list(vals)
    if not thinned:
        return out, None
    total = sum(len(v) for v in out.values()) * max(1, n_paths) * 2
    return out, (f"{thinned} coordinate(s) thinned to {per} value(s) to keep "
                 f"the round inside {budget} emitted claim(s) (~{total} after "
                 f"thinning). The round is EMISSION-bound -- measured: 1548 "
                 f"values per coordinate put only 148 queries in front of the "
                 f"solver in 300s, of which 6.9s was solving")


def level0_candidates(paths, coords, perturb=False, type_ranges=None):
    """LEVEL 0. Per coordinate, the values its siblings' counterexamples take.

    The five-level descent is single point -> small set -> interval, and this
    driver started at the interval. On a coordinate whose real constraint is an
    EQUALITY that is not a cheap start, it is the wrong shape: measured, the
    witness cut on `state.FACTORY` degenerated into round-after-round halving
    (292300...595 -> 429496731 -> 214748363 -> 107374179 -> 53687087 ->
    26843535 -> 13421759), and reaching a point that way needs about 160 rounds
    on 2^160.

    The candidate costs NOTHING to obtain, which is proposition 9: take the
    value the SIBLING'S OWN counterexample has on that coordinate. No extra
    query, no catalogue of constants -- and no catalogue matters, because a
    catalogue would be the third red line (values invented rather than derived
    from the model).

    Nor does it need a new query. The outer-box batch already emits one probe
    per DIRECTION, so a candidate list holding just v asks `c <= v` and
    `c >= v`, whose conjunction is `c == v`. Measured on a two-path unit whose
    revert sibling's projection is a single point: both probes hold for that
    path and the upper probe is refuted for the other, in one batch.

    SCOPE. This is `coordinate == constant` only. `coordinate A == coordinate B`
    is a cross-coordinate relation; it changes definition 6 (a region is a
    PRODUCT of per-coordinate sets) and lands on proposition 11, so it is an
    open method-layer item and is deliberately not attempted here.

    ---- `perturb` : THE SECOND VALUE THE TOOL ITSELF ASKS FOR ----

    A coordinate whose candidate list holds ONE value cannot distinguish a
    genuine point domain from a path with NO INPUTS AT ALL under the current
    pins: an unsatisfiable antecedent makes every probe hold vacuously, in both
    directions, at any value. That is not an inference -- the tool prints it,
    once per affected path, and names the repair:

        [level0] ⚠ enc=2: the point(s) on state._distributor, state._owner,
        state._totalSupply came from a ONE-VALUE candidate list, which CANNOT
        distinguish a genuine point domain from this path having NO inputs at
        all ... Try a second value on those coordinates: if both directions
        still hold, the interval inverts and the path is excluded from this
        slice.

    MEASURED, farming/setDistributor: FIVE of five witnessed paths carried that
    warning, on `state._distributor` and `state._totalSupply` every time. And
    the corpus-wide shape it predicts is already in the records -- 20 of the 137
    non-certified paths report `region is EMPTY on <coords> (lo > hi) under the
    current pins`, which IS the inversion, discovered one stage later and at
    full ladder cost.

    So `perturb` adds the neighbours v-1 and v+1 to any coordinate whose list is
    a single value. Three outcomes, and they are three different facts:

      * BOTH neighbours hold  -> the antecedent is vacuous; the interval
        inverts and the path is excluded from this slice. Not a point.
      * ONE neighbour holds   -> the domain is contiguous on that side only, so
        generalise in that direction and nowhere else.
      * NEITHER holds         -> a genuine single point. This is the only
        reading under which the old one-value list was right.

    CLAMPED TO THE COORDINATE'S OWN TYPE RANGE, from `type_ranges` -- the tool
    publishes `coordinate '<c>' has TYPE RANGE [lo, hi]` and this file already
    parses it (TYPE_RANGE_RE). Probing outside the type is not a neighbour: the
    value wraps and the probe measures a different number, which is the same
    defect that once produced an impossible bracket `lower in [2^255, 1)`.
    Without a range for a coordinate the neighbour is added only where it cannot
    leave an unsigned type (v-1 needs v > 0), which is the conservative half.

    OFF BY DEFAULT. Turning it on changes the candidate list of every unit, so
    it changes what every recorded region is a statement ABOUT -- the same house
    rule --level0, --max-holes and --max-region-pieces follow.
    """
    out = {}
    for c in coords:
        vals = sorted({ce[c] for _, _, ce in paths if c in ce})
        if not vals:
            continue
        if perturb and len(vals) == 1:
            v = vals[0]
            lo, hi = (type_ranges or {}).get(c, (None, None))
            nb = []
            if v - 1 >= (lo if lo is not None else 0):
                nb.append(v - 1)
            # No range known -> do NOT add v+1. An unsigned coordinate sitting
            # at its type maximum would wrap to 0, and a probe at 0 answers
            # about a value the path may genuinely contain -- turning "no
            # neighbour above" into "the neighbour above holds", i.e. inventing
            # the vacuity verdict. The conservative half is added instead, and
            # the caller reports which coordinates got only one side.
            if hi is not None and v + 1 <= hi:
                nb.append(v + 1)
            vals = sorted(set(vals) | set(nb))
        out[c] = vals
    return out


def outward_ladder(m, M, tlo, thi, budget=0):
    """The ladder ANCHORED at what is already known, walking OUTWARD only.

    `[m, M]` is bracketed by known members of the path's domain, so the boundary
    this ladder is looking for cannot be inside it. Rungs go at m and M, then
    double away from them: M+1, M+2, M+4, ... and m-1, m-2, m-4, ..., ending at
    the coordinate's own type limits.

    WHY THIS IS THE WHOLE POINT, and it is a RESOLUTION argument, not a cost one.
    The shared geometric ladder is 0, 1, 2, 4, ... 2^k -- anchored at ZERO, which
    is a place with no evidence attached to it. MEASURED on
    notes/coverage/poc/P14_Ladder.sol `bump`, whose enc=7 domain is amt in
    [10, 20] with the separation at 21:

        from 0   the rungs nearest the boundary are 16 and 32, so the bracket
                 comes back (16, 32] -- resolution 16, and the refine round
                 starts from a span it has to bisect four more times
        from M   the first rung is 21 itself, so the bracket is (20, 21] --
                 exact, in the SAME batch, with no refine round at all

    Same number of queries, different places to put them, and the places are
    chosen by evidence the enumeration already produced.

    ⛔ IT IS NOT A SEARCH. Every rung is laid before the batch and judged in it;
    nothing here reacts to an answer. That is the property the withdrawn
    widening route lost and the one the method is built on.

    The type limits are included so the coordinate stays FULLY BOUNDED: a
    half-open coordinate blocks the subtraction entirely, which is the defect
    `brackets_for` already records.

    ---- `budget`: HOW MANY RUNGS PER SIDE, AND WHY THE CAP IS NEEDED ----

    MEASURED on farming/deposit, and it is a cost result the resolution argument
    above does not cover: uncapped, this laid 5264 rungs across 24
    (path, coordinate) pairs -- 259 for a uint256 anchored at [0, 1] -- and the
    single solver batch carrying them did not return in 780s. The run died as
    `[run] TIMEOUT after 900s` inside that round, while the arm that skips the
    round entirely finished the same unit in 281s with every path named. Per-path
    anchoring changed WHERE the rungs go, not HOW MANY there are, and on a
    256-bit coordinate the count is what binds.

    `budget=N` keeps the N rungs NEAREST the member bracket on each side and
    drops the rest. Nearest, because those are the ones the evidence points at:
    the first rung beyond a value already proved to be in the domain is where the
    boundary is if it is anywhere close, and that is the entire reason for
    anchoring here rather than at zero.

    ⛔ WHAT IT COSTS, and the caller must print it: the outer rungs are the ones
    that would have bracketed a FAR boundary. Dropped, a far boundary comes back
    as a span reaching the type limit, which the refine round then bisects -- the
    same coarse outcome as the shared ladder, on that coordinate only. The
    anchors `m`, `M` and both type limits are NEVER dropped: without the limits
    the coordinate is half-open and the subtraction is blocked outright.

    `budget=0` means UNCAPPED and is the default, so a run that does not ask for
    the cap lays exactly the ladder it laid before this parameter existed.
    """
    vals = {m, M, tlo, thi}
    up, down = [], []
    step = 1
    while M + step <= thi:
        up.append(M + step)
        step *= 2
    step = 1
    while m - step >= tlo:
        down.append(m - step)
        step *= 2
    if budget > 0:
        # NEAREST-FIRST. Both lists are already generated outward from the
        # anchors, so a prefix IS the nearest N.
        up, down = up[:budget], down[:budget]
    vals.update(up)
    vals.update(down)
    return sorted(v for v in vals if tlo <= v <= thi)


def known_inside(paths, members, coords, pins, type_ranges=None):
    """The PATH-LABELLED POINT POOL, turned into two things the ladder can use.

    Returns (prune, endpoints, kept, notes).

      prune[c]     = (lo, hi), an OPEN interval every one of whose interior
                     values has a pre-determined answer on EVERY path of this
                     round, so laying a ladder probe there buys nothing.
      endpoints[c] = the values that DO buy something: each path's extreme
                     known members, and their immediate neighbours.
      kept[enc][c] = the member values that survived the pin filter, so the
                     caller can report what the pool actually holds.
      notes        = lines the caller must print; several of them are the
                     difference between "measured nothing" and "not collected".

    ---- WHY AN INTERIOR PROBE IS PRE-DETERMINED, WHICH IS NOT A HEURISTIC ----

    An outer-box round asks, per path p and per candidate v, whether `c <= v`
    holds for every input walking p, and whether `c >= v` does. Let m and M be
    the smallest and largest values c takes across p's KNOWN members. Then for
    any m < v < M:

        `c <= v` is REFUTED   -- the member at M walks p and exceeds v
        `c >= v` is REFUTED   -- the member at m walks p and falls below it

    Both answers are known before the query is issued. The probe is not a
    weaker probe or a probe likely to be uninformative; it is a question whose
    answer is already on disk. Dropping it removes emitted claims and nothing
    else, and the round is EMISSION-bound (measured: 1548 values per coordinate
    put 148 queries in front of the solver in 300s, of which 6.9s was solving).

    ---- WHY THE BRACKET CANNOT GET WORSE, PROVIDED THE ENDPOINTS ARE KEPT ----

    The upper bracket is (a, b] where a is the largest probed value REFUTED and
    b the smallest that HELD. Dropping interior values removes candidates for a,
    which could only make a smaller -- i.e. the bracket wider -- if nothing at
    or above them were probed. `endpoints` keeps M itself, and every dropped
    value is strictly below M, so a >= M >= every dropped value. The same
    argument mirrored gives the lower bracket. So this is a strict reduction in
    claims at no cost in resolution, and the must-flip is exactly that: fewer
    ladder values, IDENTICAL regions.

    ---- THE INTERSECTION, AND WHY IT IS OFTEN EMPTY ----

    One ladder is laid per COORDINATE and answered for every path in the batch,
    so a value may be dropped only when it is pre-determined for ALL of them:
    lo = max over paths of m_p, hi = min over paths of M_p. Paths whose domains
    do not overlap on c yield an empty intersection and nothing is dropped. That
    is the honest outcome, it is reported as such, and it is NOT read as "the
    probes bought nothing" -- see the second half, which is unconditional.

    ---- THE NEIGHBOURS ARE THE PERTURBATION PROBE ----

    m-1 and M+1 are added, clamped to the coordinate's published TYPE RANGE.
    They are what answers "is this side a WALL or a HOLE": if `c <= M` holds,
    the domain stops exactly at M and no ladder above it can find anything; if
    M+1 is refuted the domain continues and the search has a direction. Probing
    outside the type is NOT a neighbour -- the value wraps and the probe
    measures a different number -- so a side with no published range is left
    out and named, rather than guessed.

    ⛔ THE POOL IS NOT A CERTIFIED REGION. [m, M] means "both endpoints are
    known members", never "everything between them is". For a path whose domain
    is disconnected the interior may hold non-members; that is precisely why
    this only removes QUESTIONS WHOSE ANSWER IS KNOWN and never widens a box.
    """
    notes, kept = [], {}
    live_by_enc, n_violate, n_missing = live_witness_vectors(paths, members, pins)
    for enc, _, _ in paths:
        live = live_by_enc.get(enc) or []
        per = {}
        for c in coords:
            vals = sorted({v[c] for v in live if c in v})
            if vals:
                per[c] = vals
        kept[enc] = per
    if n_violate or n_missing:
        notes.append(f"[probe] {n_violate} witness vector(s) DISCARDED for violating a "
                     f"pin and {n_missing} for not carrying every pinned name. A pooled "
                     f"value is used as a KNOWN MEMBER of the slice, so a vector that "
                     f"is not in the slice may not contribute one")

    prune, endpoints = {}, {}
    for c in coords:
        per_path = [kept[enc].get(c) for enc, _, _ in paths]
        if any(v is None for v in per_path):
            # A path with no member on c leaves every value on c informative
            # FOR THAT PATH, and the ladder is shared, so nothing may be
            # dropped. A proposed mapping slot is exactly this case.
            continue
        los = [v[0] for v in per_path]
        his = [v[-1] for v in per_path]
        lo, hi = max(los), min(his)
        if lo + 1 < hi:
            prune[c] = (lo, hi)
        ends = set(los) | set(his)
        tlo, thi = (type_ranges or {}).get(c, (None, None))
        for v in sorted(ends):
            if tlo is not None and v - 1 >= tlo:
                ends.add(v - 1)
            if thi is not None and v + 1 <= thi:
                ends.add(v + 1)
        endpoints[c] = sorted(ends)
    return prune, endpoints, kept, notes


def single_point_coords(box):
    """Coordinates this path's box has collapsed to one value."""
    return sorted(n for n, (lo, hi) in box.items() if lo == hi)


def point_has_known_member(members, enc, coord, point, pins):
    """Whether a witnessed vector already shows `coord == point` on this path."""
    for vec in members.get(enc) or []:
        bad = False
        for name, value in pins.items():
            if name in vec and vec[name] != value:
                bad = True
                break
        if bad:
            continue
        if coord in vec and vec[coord] == point:
            return True
    return False


def equality_coords(boxes, coords, expected_paths):
    """Coordinates that came back a single point for EVERY witnessed path.

    Only these may skip the geometric ladder. The condition is deliberately the
    strong one: the batch lays one candidate list per coordinate for all paths
    at once, so a coordinate still needing a range for even one path still needs
    the ladder. A coordinate whose sibling is a point but whose own domain is
    everything-but-that-point is NOT equality-type -- that path needs a range,
    and what it actually needs is a punched interval, which this representation
    does not have.

    A path that produced no box at all is NOT evidence of anything; requiring
    every expected path to be present keeps "we did not measure it" from
    reading as "it is a point". Same rule as everywhere else in this file.
    """
    if len(boxes) < expected_paths or not boxes:
        return []
    out = []
    for c in coords:
        if all(c in b and b[c][0] == b[c][1] for b in boxes.values()):
            out.append(c)
    return sorted(out)


BOX_RE = re.compile(r"path enc=(\d+) depth=\d+ OUTER box \(D_path is CONTAINED in it\): (.*)")
BRACKET_RE = re.compile(r"path enc=(\d+) BRACKET \(refine[^)]*\): (.*)")
REGION_RE = re.compile(r"path enc=(\d+) CERTIFIED region after subtracting sibling outer boxes "
                       r"\(zero queries\): ([^—]*)(— WARNING.*)?")
# ---- EVERY DECIMAL THE TOOL PRINTS MAY CARRY A SIGN ----
#
# Five patterns in this file matched `\d+` only. That was correct while
# `coord_expressible` accepted unsigned bit-vectors alone, and it becomes a
# SILENT failure the moment signed coordinates are accepted: a published
# `TYPE RANGE [-578..., 578...]` matches nothing, `type_ranges` stays empty, and
# `_span` falls back to `(0, UINT256_MAX)` -- so the ladder is laid over the
# wrong range and the loop measures nothing while looking like it ran. The
# driver has to be signed-ready BEFORE the tool starts emitting signed ranges,
# or the first signed run is a silent no-op that has to be diagnosed twice.
#
# One shared fragment so the five cannot drift apart again.
_INT = r"-?\d+"
SHRINK_RE = re.compile(r"retry with (\S+) in \[(" + _INT + r"), (" + _INT + r")\]")
# ---- THE PUNCH SUGGESTION, which the tool has printed all along ----
#
# `audit_certify_witness` emits BOTH a SHRINK suggestion (cut a side off the
# interval) and, when the witness sits strictly inside it, a PUNCH suggestion
# (remove that one value, Definition 5). The driver has never parsed the second
# one: `notes/interval-input-scope-and-plan.md` records it as "implemented and
# never wired".
#
# The difference is not resolution, it is DETERMINISM. A side cut can only keep
# the side holding this path's own counterexample, so WHICH side survives is
# decided by a value the solver picked. Measured on one address coordinate: the
# same region came out as `[256, 2^160-1]` or `[0, 254]` depending on the
# sibling's counterexample -- a factor of 5.7e45 -- while a hole gives
# `[0, 2^160-1] \ {v}` in both cases.
#
# The suggestion line names one or more `<coord> != <value>` pairs separated by
# "; ". Anchored to the SUGGESTION LINE first and only then scanned for pairs:
# `!=` appears in prose elsewhere in the same output, and a bare scan over the
# whole log would harvest text as a coordinate.
PUNCH_LINE_RE = re.compile(r"PUNCH SUGGESTION for '[^']*' — (.*)$", re.M)
PUNCH_PAIR_RE = re.compile(r"(\S+) != (" + _INT + r")")
# The tool publishes each coordinate's own type range. The driver chooses the
# ladder and cannot choose it correctly without this: laying probes over the
# whole 256-bit range on a 160-bit `address` puts most of them OUTSIDE the type,
# where they wrap and measure a different number. Measured -- that is how an
# impossible-looking bracket (`lower in [2^255, 1)`) arose, and the inverted
# span it produced killed the loop.
TYPE_RANGE_RE = re.compile(r"coordinate '([^']+)' has TYPE RANGE \[(" + _INT + r"), (" + _INT +
                           r")\]")

# `name in [lo, hi]`, optionally followed by Definition 5's punched set
# `\ {v, w}`. One regex for both so the hole can never be read as belonging to
# the NEXT coordinate: they are captured in the same match as their interval.
INTERVAL_RE = re.compile(r"(\S+) in \[(" + _INT + r"), (" + _INT + r")\](?: \\ \{([-0-9, ]+)\})?")


def parse_intervals(text):
    # Scanned, not split: an interval contains ", " itself, so splitting on it
    # cuts every interval in half and silently yields nothing.
    return {m.group(1): (int(m.group(2)), int(m.group(3))) for m in INTERVAL_RE.finditer(text)}


def parse_holes(text):
    """The values REMOVED from each coordinate's interval (Definition 5).

    Returned SEPARATELY from the intervals rather than folded into the box,
    because every existing consumer of a box expects `(lo, hi)` and a silently
    richer value would be read as a plain interval by whichever one was not
    updated -- which is the failure this project keeps hitting from the other
    side. A missing `\\ {...}` yields no entry at all, so a region measured
    before punched intervals existed parses exactly as it always did.
    """
    out = {}
    for m in INTERVAL_RE.finditer(text):
        if m.group(4):
            out[m.group(1)] = sorted({int(v) for v in m.group(4).split(",") if v.strip()})
    return out


def brackets_for(coord, brackets, type_range=None):
    """Where the SEPARATION boundary still is, per the bracket report.

    A bracket that runs into the type limit (upper ending at 2^256-1, or lower
    starting at 0) is not a separation point -- it says "no bound was found
    inside the type", i.e. the bound IS the type limit. Refining towards it
    keeps the span at the full type range and the loop never narrows, which is
    exactly what it did before this was excluded.
    """
    lo, hi = None, None
    type_lo, type_hi = type_range or (0, UINT256_MAX)
    for txt in brackets.values():
        for m in re.finditer(
                re.escape(coord) + r" (upper|lower) in [\[(](" + _INT + r"), (" + _INT + r")[\])]",
                txt):
            a, b = int(m.group(2)), int(m.group(3))
            # THE TEST IS WHETHER THE BRACKET CONSTRAINS ANYTHING, not whether
            # its far end reaches the type limit.
            #
            # An upper bracket `(a, b]` says the true bound lies above a and at
            # most b. Dropping it because b == typemax throws away `a`, which is
            # a real constraint -- and on a uint256 coordinate whose domain
            # reaches the top, b == typemax is the COMMON case, not the
            # exceptional one.
            #
            # MEASURED, the first run in which the bracket and a refine round
            # both completed: the bracket said
            #     immutables.amount upper in (5.14e61, 2^256-1]
            # and the refine round's span came back (0, 2^256-1) -- the whole
            # type. The shrink then had nothing to work from and halved, which
            # is the degenerate bisection that had been blamed on the method.
            #
            # What the old rule was RIGHT about is kept: a bracket spanning the
            # coordinate's entire range constrains nothing and must still be
            # dropped, or the loop "refines" towards a span it already had.
            if m.group(1) == "upper" and a <= 0 and b >= UINT256_MAX:
                continue
            if m.group(1) == "lower" and a <= 0 and b >= UINT256_MAX:
                continue
            # ---- A DEGENERATE PATH MUST NOT POLLUTE THE UNION ----
            #
            # The span is the union over ALL paths, so one path whose bracket
            # spans the coordinate's whole range drags the shared span back to
            # everything -- and with five paths that is close to certain.
            # MEASURED: the bracket said `immutables.amount upper in (5.14e61,
            # 2^256-1]` and the refine span still came back (0, 2^256-1).
            #
            # Such a bracket contributes ZERO information to the union by
            # definition, so dropping it is not a policy choice and costs
            # nothing. This is deliberately NOT per-path spans: those would
            # multiply the claim count by the path count, and the claim count is
            # what the round's cost tracks. Removing a contribution that says
            # nothing is free; paying a path multiplier on coordinates whose
            # union is already tight is not.
            if a <= type_lo and b >= type_hi:
                continue
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    return (lo, hi) if lo is not None else None


def single_path_outer_claim_shards(spec, claim_lower_bound):
    """Split an oversized single-path product into independent coordinates."""
    coords = spec.get("coords") or []
    paths = spec.get("paths") or []
    if len(paths) != 1 or len(coords) <= 1 or claim_lower_bound <= 32:
        return [spec]
    shards = []
    for coord in coords:
        shard = copy.deepcopy(spec)
        name = coord["name"]
        shard["coords"] = [copy.deepcopy(coord)]
        for entry in shard["paths"]:
            entry["ce"] = {n: v for n, v in entry.get("ce", {}).items() if n == name}
            if "coords" in entry:
                entry["coords"] = [c for c in entry["coords"] if c["name"] == name]
                if not entry["coords"]:
                    del entry["coords"]
        shards.append(shard)
    return shards


def parse_outer_round_output(log):
    """Merge coordinate-sharded outer-box reports into path products."""
    boxes, brackets, regions, warned = {}, {}, {}, set()
    region_holes, type_ranges = {}, {}
    for line in log.splitlines():
        m = TYPE_RANGE_RE.search(line)
        if m:
            type_ranges[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        m = BOX_RE.search(line)
        if m:
            boxes.setdefault(int(m.group(1)), {}).update(parse_intervals(m.group(2)))
        m = BRACKET_RE.search(line)
        if m:
            enc = int(m.group(1))
            brackets[enc] = ", ".join(txt for txt in (brackets.get(enc), m.group(2)) if txt)
        m = REGION_RE.search(line)
        if m:
            enc = int(m.group(1))
            regions.setdefault(enc, {}).update(parse_intervals(m.group(2)))
            region_holes.setdefault(enc, {}).update(parse_holes(m.group(2)))
            if m.group(3):
                warned.add(enc)
    return boxes, brackets, regions, warned, region_holes, type_ranges


def outer_round(esbmc,
                sol,
                contract,
                unit,
                paths,
                coords,
                pins,
                probes,
                max_tx,
                timeout,
                cwd,
                spans=None,
                geometric=False,
                ast=None,
                focus=None,
                memlimit="8g",
                values_by_coord=None,
                extra_values=None,
                type_ranges=None,
                claim_budget=0,
                esbmc_args=(),
                prune_inside=None,
                path_values=None):
    """Steps 2-4: one batch. Returns (boxes, brackets, regions, warned).

    `values_by_coord` overrides the ladder for the coordinates it names, which
    is how level 0 is expressed: an equality-type coordinate gets the handful of
    sibling counterexample values instead of 258 powers of two. Everything else
    about the round -- the fixed per-path assumption, the batch, the
    subtraction, the region computation -- is untouched, which is the point.
    The five-level descent says level 2 keeps its mechanism verbatim.
    """
    values_by_coord = values_by_coord or {}
    extra_values = extra_values or {}
    spec_coords = []
    geo = {}
    pruned = {}
    for c in coords:
        if c in values_by_coord:
            spec_coords.append({"name": c, "values": [str(v) for v in values_by_coord[c]]})
            continue
        # KEEP THE EXACTLY-KNOWN POINTS ALONGSIDE THE LADDER.
        #
        # A ladder measures a bound only to its own resolution, so a sibling
        # whose real projection is a single point comes back as an INTERVAL --
        # and the punched cut, which needs that point exactly, then cannot fire.
        # Measured end to end on a two-path unit: level 0 resolved the sibling to
        # `to == 255`, the refine round reported `[230, 256]` for the same path,
        # and the region fell back to the side cut punching was built to replace.
        #
        # These are the same zero-cost candidates level 0 already derived
        # (proposition 9: the sibling's own counterexample value), so keeping
        # them costs two probes per coordinate and no query of their own. They
        # are added rather than substituted: the ladder still has to measure the
        # paths whose projection really is a range.
        extra = [str(v) for v in sorted(extra_values.get(c, ()))]
        if geometric:
            # Bound the ladder by the coordinate's OWN type where it is known.
            # A probe above the type maximum is built as a constant of that type
            # and wraps, so it measures a different number; the tool now drops
            # such values and says so, but laying them at all wastes the ladder
            # and leaves the bracket describing a range the type cannot hold.
            # `UINT256_MAX` stays the default for a coordinate whose range has
            # not been published yet -- the previous behaviour exactly, which is
            # what the FIRST round has to fall back on since nothing has been
            # measured before it.
            tr = (type_ranges or {}).get(c, (0, UINT256_MAX))
            vals = [str(v) for v in geometric_values(tr[1], tr[0])]
            # ---- DROP THE RUNGS WHOSE ANSWER IS ALREADY ON DISK ----
            #
            # `prune_inside[c]` is an OPEN interval whose interior is bracketed
            # by KNOWN MEMBERS of every path in this batch, so both directions
            # are refuted there before the query is issued (see `known_inside`).
            # The endpoints themselves arrive through `extra_values` and are
            # deliberately NOT dropped: they are what keeps the bracket from
            # widening, and without them this would trade claims for resolution
            # instead of removing questions that have no answer to give.
            pr = (prune_inside or {}).get(c)
            if pr:
                before = len(vals)
                vals = [v for v in vals if not (pr[0] < int(v) < pr[1])]
                pruned[c] = (before - len(vals), pr)
            geo[c] = sorted(set(vals + extra), key=int)
            spec_coords.append({"name": c, "values": None})
        else:
            if spans is None:
                # ---- A LEVEL-0 ROUND HAS NOTHING TO ASK ABOUT THIS ONE ----
                #
                # Level 0's candidate list is "the values the SIBLINGS' own
                # counterexamples take here" (proposition 9), so a coordinate no
                # counterexample mentions has no candidate and does not appear
                # in `values_by_coord`. With `geometric` false and `spans` None
                # this fell into the line below and died with
                # `TypeError: 'NoneType' object is not subscriptable`.
                #
                # MEASURED, farming/deposit with
                # `--slot-coord state._balances[msg.sender] --level0`: a
                # PROPOSED mapping slot carries no counterexample value by
                # construction -- the driver says so itself two lines earlier
                # ("there is no counterexample value for a slot") -- so every
                # such run crashed after enumeration, at 59s, with 7 witnessed
                # paths and NO region attempted. The slot feature was verified
                # against a hand-written outer-box spec and against three
                # regressions, and never once through this branch, which
                # `--level0` makes the DEFAULT path for the corpus sweep.
                #
                # Refused by name rather than skipped silently: a coordinate
                # dropped from a round is unconstrained in it, and the caller
                # decides that, not this function.
                raise SystemExit(f"[round] a LEVEL-0 round was asked about coordinate "
                                 f"'{c}', which has no candidate list. Level 0's candidates "
                                 f"are the values the siblings' own counterexamples take, so "
                                 f"a coordinate no counterexample mentions -- a PROPOSED "
                                 f"mapping slot is the case that exists -- has none, and "
                                 f"there is no span to fall back on either. The caller must "
                                 f"leave such a coordinate out of the level-0 round and say "
                                 f"that it did; guessing a range here would measure a "
                                 f"coordinate nobody asked about")
            lo, hi = spans[c]
            spec = {"name": c, "lo": str(lo), "hi": str(hi)}
            if extra:
                spec["values"] = extra
            spec_coords.append(spec)
    spec = {
        "unit":
        unit,
        "probes":
        probes,
        "coords":
        spec_coords,
        "pin": [{
            "name": n,
            "value": str(v)
        } for n, v in pins.items()],
        "paths": [{
            "enc": e,
            "depth": d,
            "ce": {
                k: str(v)
                for k, v in ce.items() if k in coords
            }
        } for e, d, ce in paths]
    }
    # ---- PER-PATH LADDERS, WHICH ONE SHARED `coords` LIST CANNOT EXPRESS ----
    #
    # A rung is worth laying for a path only OUTSIDE that path's known domain,
    # and two paths of one unit are separated precisely by the coordinate being
    # measured -- so what is worth probing for one is exactly what is not worth
    # probing for its sibling. MEASURED on P14_Ladder/bump with eight witnesses
    # per path: enc=7's known members bracket [16, 20] and enc=6's bracket
    # [2^256-4, 2^256-1]. The intersection is EMPTY, so the shared list could
    # drop nothing at all -- which the round reported, and which is the general
    # case rather than bad luck on that contract.
    #
    # The tool takes this as `paths[].coords[].values` and REPLACES the shared
    # ladder for that (path, coordinate). It refuses an EMPTY list rather than
    # reading it as "no probe here", so a coordinate can never end up silently
    # unmeasured for one path while the spec claims to have specified it.
    # ---- THE CLAIM BUDGET HAS TO REACH THIS LIST TOO ----
    #
    # `budget_probe_values` thins `geo`, and `geo` is the SHARED ladder. A
    # per-path list bypasses it entirely, so --claim-budget would silently stop
    # binding the moment per-path ladders were switched on -- a flag that cannot
    # fire looks exactly like one that fired and found nothing, which is the
    # shape this file names in six other places. Same arithmetic as the shared
    # case: claims = values x 2 directions, summed over the (path, coordinate)
    # pairs, and never below 2 per pair (one value cannot tell a point domain
    # from a vacuous path).
    pv_note = None
    if path_values and claim_budget > 0:
        npair = sum(len(p) for p in path_values.values()) or 1
        per_pair = max(2, claim_budget // (npair * 2))
        thinned = {}
        n_thin = 0
        for enc, per in path_values.items():
            thinned[enc] = {}
            for c, vs in per.items():
                if len(vs) > per_pair:
                    thinned[enc][c] = thin_to(list(vs), per_pair)
                    n_thin += 1
                else:
                    thinned[enc][c] = list(vs)
        if n_thin:
            pv_note = (f"{n_thin} per-path ladder(s) thinned to {per_pair} "
                       f"rung(s) to keep the round inside {claim_budget} "
                       f"emitted claim(s) over {npair} (path, coordinate) "
                       f"pair(s). Endpoints are always kept, so the coordinate "
                       f"stays fully bounded")
        path_values = thinned
    n_over = 0
    for entry in spec["paths"]:
        per = (path_values or {}).get(entry["enc"]) or {}
        over = [{
            "name": n,
            "values": [str(v) for v in vs]
        } for n, vs in sorted(per.items()) if vs and n in coords]
        if over:
            entry["coords"] = over
            n_over += len(over)
    if pv_note:
        print(f"[round] PER-PATH LADDERS THINNED: {pv_note}")
    if path_values is not None:
        print(f"[round] PER-PATH LADDERS: {n_over} (path, coordinate) "
              f"override(s) written into the spec" +
              ("" if n_over else " — none, so every path gets the shared ladder. This is a "
               "statement about the evidence available per path, not about "
               "the mechanism"))
    if geo:
        geo, note = budget_probe_values(geo, len(paths), claim_budget)
        if note:
            print(f"[round] LADDER THINNED: {note}")
        for sc in spec_coords:
            if sc.get("values") is None:
                sc["values"] = geo[sc["name"]]
    # SAID EITHER WAY. A pruning step that prints only when it fires is
    # indistinguishable from one that can never fire -- this project's own
    # always-empty-channel shape -- and the "nothing was dropped" case is the
    # COMMON one on paths whose domains do not overlap, so its silence would be
    # read as "the probes bought nothing" when the truth is "these paths share
    # no interior".
    if geometric and prune_inside is not None:
        if pruned:
            print("[probe] LADDER PRUNED: " +
                  f"{sum(n for n, _ in pruned.values())} rung(s) dropped "
                  "whose answer is already on disk — " +
                  "; ".join(f"{c}: {n} inside ({pr[0]}, {pr[1]})"
                            for c, (n, pr) in sorted(pruned.items())))
        else:
            print("[probe] LADDER NOT PRUNED: no coordinate is bracketed by "
                  "known members on EVERY path of this batch, so no rung has a "
                  "pre-determined answer. The ladder is laid exactly as it "
                  "would have been without the probes — this is a statement "
                  "about the paths' overlap, NOT about the probes")
    path = os.path.abspath(os.path.join(cwd, "outer.json"))
    with open(path, "w") as f:
        json.dump(spec, f)
    n_probe = sum(len(c.get("values", [])) or (probes + 2) for c in spec_coords)
    kind = ("geometric-bracket" if geometric else ("linear-refine" if spans else "level-0"))
    write_generalise_progress(
        cwd,
        "outer-round-started",
        round_kind=kind,
        coordinate_count=len(spec_coords),
        path_count=len(paths),
        candidate_values_per_direction=n_probe,
        timeout_s=timeout,
    )
    # WALL CLOCK PER ROUND, printed. The bracket round's cost is a number the
    # evaluation needs and has never had: the only figures ever collected for it
    # came from runs that were ALSO hitting the type-wrap defect, so "did not
    # finish" could not be separated from "too slow". Those are different
    # claims and only one of them is about the method. Timed here, at the single
    # place a round is issued, so no caller can report a cost it did not
    # measure. ("did not finish" above is deliberately not called "too slow".)
    # A single-path outer box is a Cartesian product of independently measured
    # coordinate bounds. Keeping every coordinate's ladder in one solver query
    # adds no cross-coordinate evidence, but it does make the solver retain all
    # probe claims at once. The old-blockhash settle regression reached 62
    # claims this way and both Bitwuzla and CVC5 were killed at 6 GiB.
    #
    # Split only the semantics-obvious case. Multi-path rounds stay intact:
    # their certified regions subtract sibling boxes, and a coordinate that
    # separates siblings can affect which subtraction is representable.
    claim_lower_bound = n_probe * 2 * len(paths)
    shard_specs = single_path_outer_claim_shards(spec, claim_lower_bound)
    round_specs = []
    for index, shard in enumerate(shard_specs, 1):
        if len(shard_specs) == 1:
            shard_path = path
        else:
            shard_path = os.path.abspath(os.path.join(cwd, f"outer-shard-{index}.json"))
            with open(shard_path, "w") as f:
                json.dump(shard, f)
        round_specs.append((shard_path, shard))
    if len(round_specs) > 1:
        print(f"[round] SINGLE-PATH CLAIM SHARDS: at least "
              f"{claim_lower_bound} directional claim(s) split into "
              f"{len(round_specs)} "
              "coordinate query(ies); the original total timeout is shared")

    _t0 = time.time()
    deadline = time.monotonic() + timeout
    logs = []
    shard_failure = None
    for shard_index, (shard_path, _) in enumerate(round_specs, 1):
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            logs.append(f"[run] TIMEOUT after {timeout}s: outer-box "
                        "coordinate shards exhausted their shared deadline\n")
            shard_failure = round_failure_reason(logs[-1])
            break
        if len(round_specs) > 1:
            print(f"[round] outer-box coordinate shard {shard_index}/"
                  f"{len(round_specs)}: "
                  f"{round_specs[shard_index - 1][1]['coords'][0]['name']}")
        shard_log = run(esbmc,
                        sol,
                        contract, ["--path-cov-outer-box", shard_path],
                        max_tx,
                        remaining,
                        cwd,
                        ast=ast,
                        focus=focus,
                        memlimit=memlimit,
                        esbmc_args=esbmc_args)
        logs.append(shard_log)
        shard_failure = round_failure_reason(shard_log)
        if shard_failure:
            break
    log = "\n".join(logs)
    _wall = time.time() - _t0
    # ---- THE ROUND'S NAME IS DERIVED FROM WHAT THE ROUND IS, NOT FROM A
    # ---- FIELD THAT HAPPENS TO CORRELATE WITH IT ----
    #
    # This read `"level-0" if values_by_coord else ...`, and `values_by_coord`
    # only says "some coordinates carry an explicit value list instead of a
    # ladder" -- which is TRUE of a geometric or a refine round the moment
    # `equality_coords` has found an equality-type coordinate, because those
    # keep their handful of sibling values in every later round ("so these skip
    # the geometric ladder").
    #
    # MEASURED on farming/startFarming, whose two state coordinates are
    # equality-type for all 26 paths: the driver printed `level-0` THREE TIMES
    # -- 23.7s with ~9 candidates, 180.0s with ~1037 candidates immediately
    # followed by `[outer-box] ROUND MEASURED NOTHING` and an empty `[bracket]`,
    # then 119.9s with ~9 again. By candidate count and by what follows it, the
    # middle round IS the geometric bracket wearing level-0's name, and the
    # third is the refine round. P05_Hole/pick, which has no equality-type
    # coordinate, prints the three names correctly -- so the defect is invisible
    # on exactly the units simple enough to check by eye.
    #
    # It is not cosmetic. certify_all.py's parse_driver reads these prefixes on
    # purpose ("so the sweep cannot disagree with the tool about what was
    # measured"), and per-round cost attribution is what decides whether a
    # KILLED unit was a budget outcome or a defect -- the question
    # killed_triage.py exists to answer.
    #
    # `geometric` and `spans` are the round's own inputs and cannot be true of
    # another round: level 0 passes neither, the bracket passes `geometric`, and
    # the refine round passes `spans`.
    print(f"[round] {kind}: {_wall:.1f}s wall, {len(spec_coords)} coordinate(s),"
          f" ~{n_probe} candidate value(s) per direction, {len(paths)} path(s)")
    print("[round] " + round_accounting(log))
    # A timed-out round measures nothing, and "measured nothing" is reported
    # downstream as "no fully bounded region was measured" -- which reads as a
    # property of the path. Say which it was, here, where it is known.
    # A COORDINATE THE TOOL CANNOT RESOLVE. The counterexample harvest and the
    # coordinate resolver disagree: mappings and dynamic arrays are lowered to
    # contract-scope globals rather than fields of the contract object, so
    # `state._DOCKED` IS reported in entry_storage and is NOT resolvable as a
    # coordinate. The driver believed the report, and the tool refused -- rightly,
    # since dropping the bound would widen THIS path's own box and hence its
    # region. What the driver must not do is what it did: come back with no
    # regions and report them downstream as "no fully bounded region was
    # measured", which reads as a property of the path.
    failure = shard_failure or round_failure_reason(log)
    write_generalise_progress(
        cwd,
        "outer-round-finished",
        round_kind=kind,
        wall_s=round(_wall, 1),
        failure=failure,
    )
    if failure:
        meta_path = save_failed_round(cwd, kind, spec, log, failure, _wall)
        print(f"[outer-box] ROUND MEASURED NOTHING — {failure}")
        print(f"[outer-box] failed round evidence: {meta_path}")
    (boxes, brackets, regions, warned, region_holes, type_ranges) = parse_outer_round_output(log)
    return (boxes, brackets, regions, warned, failure, region_holes, type_ranges,
            unresolvable_coords(log))


# The `ERROR: ` PREFIX IS NOT OPTIONAL DECORATION. VACUOUS and UNDECIDED are
# emitted with log_error, which prepends `ERROR: `; CERTIFIED and REFUTED go
# through log_status and do not. Anchoring on `^--path-cov-certify` therefore
# matched exactly the two GOOD outcomes and missed both bad ones -- so a vacuous
# certification read as "no verdict at all", which is the one reading that loses
# the whole point of the gate.
#
# MEASURED end to end, not reasoned about: a real driver run shrank enc=3 to
# `amt in [0,49]` under `state.bal == 50`, the tool correctly printed
# `RESULT: VACUOUS` (amt <= 49 can never be > 50), and the driver reported
# "ESBMC printed neither SUCCESSFUL nor FAILED". The pure-function tests could
# not have caught it: they were written from the format string, not from a log.
# ---- THE TOKEN IS CAPTURED WHOLE, AND AN UNKNOWN ONE IS FATAL ----
#
# This used to be an alternation, `(CERTIFIED|REFUTED|VACUOUS|UNDECIDED)\b`, and
# it failed in BOTH directions the moment the tool grew a fifth verdict:
#
#   * a token the alternation does not list does not match at all, so `result`
#     stays None and `verdict()` falls through to the whole-line VERIFICATION
#     SUCCESSFUL / FAILED reading -- the exact fallback the tool's own banner
#     says must not be used, because the non-vacuity witness is refuted on every
#     run that certifies. A new verdict would therefore be read as its OPPOSITE.
#   * worse, `UNDECIDED-TRUNCATED` DOES match, as `UNDECIDED`: `-` is a
#     non-word character, so `\b` is satisfied right after the prefix. The
#     driver would have mapped a "the bound may have manufactured this" verdict
#     onto plain UNKNOWN, silently, and looked like it handled it.
#
# So the token is captured whole and looked up in a table that has no default.
# An unrecognised value is a HARD FAILURE: it means the tool knows a distinction
# this driver does not, and there is no safe direction to guess in -- one guess
# certifies a region nothing checked, the other shrinks a region that was
# already correct.
CERTIFY_RESULT_RE = re.compile(r"^(?:ERROR: )?--path-cov-certify: RESULT: ([A-Z][A-Z-]*)")

# Tool token -> the state this driver acts on. UNDECIDED-TRUNCATED is its own
# state and is deliberately NOT folded into UNKNOWN: UNKNOWN means the run did
# not produce an answer (crash, timeout, solver gave up) and the cause is a
# property of the RUN, while UNDECIDED-TRUNCATED means the run answered but the
# unwind bound may have manufactured the answer -- the cause is a property of
# the BOUND, and the fix is a bigger --unwind / --unwindset, which is actionable
# in a way a crash is not. Collapsing them would hide the one repair the
# operator can actually make.
CERTIFY_RESULT_MAP = {
    "CERTIFIED": "SUCCESSFUL",
    "REFUTED": "FAILED",
    "UNSAFE": "FAILED",
    "VACUOUS": "VACUOUS",
    "UNDECIDED": "UNKNOWN",
    "UNDECIDED-TRUNCATED": "UNDECIDED_TRUNCATED",
}

# ---- THE CERTIFY BRANCH'S OWN REFUSAL, WHICH IS NOT THE OUTER BOX'S ----
#
# The two branches refuse an unresolvable name with DIFFERENT sentences, and
# that is the whole reason this regex exists separately:
#
#   outer box   "unit '<id>' has no input named 'state._DOCKED'"
#   certify     "REFUSING THE QUERY because coordinate 'state._DOCKED' cannot
#                be expressed: the name does not resolve to an input of this
#                unit"
#
# They also see different inputs: the outer-box spec carries pins in a `pin`
# field the tool resolves separately, while `certify` folds every pin into the
# `box` as a degenerate bound. So a pin the certify branch cannot express is one
# the outer-box rounds never even complained about -- MEASURED: a first attempt
# at this fix harvested the outer-box wording, and on the very run that
# motivated it the outer-box rounds said nothing at all and the fix never fired.
# Two branches, two sentences, and a detector wired to the wrong one is a
# detector that is never wrong and never right.
CERT_UNEXPRESSIBLE_RE = re.compile(
    r"REFUSING THE QUERY because coordinate '([^']+)' cannot be expressed")

# The loop names the third state carries. Anchored to the RESULT line rather
# than to the bare phrase, because the generic "Coverage may be UNDER-REPORTED"
# warning ends with the SAME words and puts its loops on following lines -- a
# bare scan would capture the empty tail of the warning and report "no loops"
# on a run that named several. `.` does not cross newlines here (no DOTALL), so
# the match is confined to the one line that carries both parts.
CERT_TRUNCATED_RE = re.compile(r"RESULT: UNDECIDED-TRUNCATED.*?Loops truncated: (.*)$", re.M)


def unexpressible_coords(log):
    """Coordinates the CERTIFY branch refused to express, from its own message.

    Certification refuses the WHOLE query on one such name -- rightly, since
    dropping a requested bound would certify a wider box than the one asked
    about. So a single unexpressible pin returns no verdict for EVERY path of
    the unit, and the driver used to report that as "ESBMC printed neither
    SUCCESSFUL nor FAILED": true, and useless.

    MEASURED on aqua.rawBalances and aqua.safeBalances, the first two real units
    of the first corpus sweep. `state._DOCKED` is a contract-scope global, not a
    component of the contract object, so it cannot be a coordinate; the driver
    reads it as a `constant` state variable and pins it at its counterexample
    value. Every certification query then failed in about three seconds -- which
    is what distinguished it from a timeout and is what made it findable.
    """
    return sorted(set(CERT_UNEXPRESSIBLE_RE.findall(log)))


def verdict(log):
    """'SUCCESSFUL' / 'FAILED' / 'VACUOUS' / 'UNDECIDED_TRUNCATED' / 'UNKNOWN'.

    THE TOOL'S `RESULT:` LINE WINS, and reading the verdict line instead is now
    an INVERSION rather than a coarser reading. The certification query emits a
    non-vacuity witness at the path's own exit -- an assert carrying only
    `tr != enc || cnt != depth` -- which is REFUTED exactly when the box admits
    an execution that walks the path. That is the precondition of the whole
    certificate, so on a run that CERTIFIES the witness fails and ESBMC prints
    `VERIFICATION FAILED`. A driver reading the verdict line would record every
    certificate as a refutation and shrink a box that was already correct.

    The witness also creates a state the verdict line never had. Before it, a
    semantically unsatisfiable box -- `state.s in [0,0]` against a constructor
    that assigns 7, which is well-formed, in-type, non-empty and admits nothing
    -- made every exit assert hold for want of an execution and printed
    SUCCESSFUL with exit 0. That is VACUOUS, and it must not be folded into
    either side: accepting it certifies a region containing no input, and
    shrinking it responds to an empty box by making it emptier.

    The whole-line verdict stays as the FALLBACK, for two live cases: an older
    ESBMC that does not print the RESULT line, and a run that dies before
    reaching it.

    THE OLDER REASON THIS FUNCTION EXISTS IS STILL LIVE, and it is why the
    fallback is read as a LINE and never as a substring: a measured, total
    failure of the soundness gate. The test used to be:

        if "VERIFICATION SUCCESSFUL" in log: return True

    and ESBMC opens every bounded Solidity run with

        WARNING: ... A VERIFICATION SUCCESSFUL result is bounded -- it means no
        violation within 1 transaction(s), NOT an unbounded proof; ...

    so the substring is present on EVERY run, whatever the solver said. Every
    certification therefore came back true. Certification is the ONLY soundness
    gate in this pipeline -- subtraction is a constructor, the outer box only
    ever over-approximates -- so a gate that is unconditionally green does not
    weaken the method, it removes it. Measured on the minimal contract: all
    three paths were reported as certified regions while ESBMC's own verdict on
    each was FAILED, one of them adding "no single-coordinate shrink ... the
    region has to be split".

    UNKNOWN is a THIRD state and must stay one. ESBMC can die on an assertion
    inside the SMT layer (`Tuple AST mismatch`, seen on this very contract when
    a coordinate is pinned) and then print NEITHER verdict line. Folding that
    into FAILED would make the loop respond to a crash by shrinking the box, i.e.
    by treating "we never found out" as "refuted".

    UNDECIDED_TRUNCATED IS A FIFTH STATE, for the same reason UNKNOWN is a third
    one. It says the run DID answer but a loop was cut at the unwind bound while
    unwinding assertions were disabled, so the executions that would have
    witnessed the path may simply have been assumed away and the vacuity verdict
    is not the tool's to give. Neither shrinking nor accepting is defensible --
    and neither is calling it UNKNOWN, because unlike a crash it names a repair
    (raise --unwind / --unwindset for the loop it prints).

    AN UNRECOGNISED TOKEN IS FATAL, never a silent fall-through. See
    CERTIFY_RESULT_MAP above for the two ways the previous alternation got this
    wrong, one of which mapped the new token onto UNKNOWN while looking handled.
    """
    result = None
    seen = "UNKNOWN"
    for line in log.splitlines():
        s = line.strip()
        m = CERTIFY_RESULT_RE.match(s)
        if m:
            result = m.group(1)
        elif s == "VERIFICATION SUCCESSFUL":
            seen = "SUCCESSFUL"
        elif s == "VERIFICATION FAILED":
            seen = "FAILED"
    if result is not None:
        if result not in CERTIFY_RESULT_MAP:
            raise SystemExit(f"[certify] ESBMC printed an unrecognised verdict token "
                             f"'RESULT: {result}'. This driver knows only "
                             f"{', '.join(sorted(CERTIFY_RESULT_MAP))}. Refusing to guess: "
                             f"the tool and this script disagree about what verdicts exist, "
                             f"and every fallback available here is wrong in a way nothing "
                             f"downstream could notice -- treating it as no verdict reads "
                             f"the whole-line VERIFICATION SUCCESSFUL/FAILED, which is "
                             f"INVERTED for this mode (the non-vacuity witness is refuted on "
                             f"every run that certifies), and treating it as any known token "
                             f"records a judgement the tool did not make. Teach this script "
                             f"the token instead")
        return CERTIFY_RESULT_MAP[result]
    return seen


def boxes_intersect(a, b, a_holes=None, b_holes=None):
    """Do two boxes share at least one point?

    Two boxes intersect iff they overlap on EVERY coordinate: a box is a
    conjunction, so one disjoint coordinate separates them entirely. A
    coordinate present in one box and absent from the other is unconstrained
    there, hence overlapping on it.

    HOLES MUST BE READ HERE, and this is not a refinement -- ignoring them is a
    live FALSE ALARM. The caller treats an intersection as a hard invariant
    violation and exits, so a pair of perfectly correct regions would kill the
    run. The exact shape occurs on the first contract punched intervals were
    measured on: enc=2 certifies `to in [255, 255]` and enc=3 certifies
    `to in [0, 2^160-1] \\ {255}`. Read without the hole those share the point
    255; read with it they are disjoint, which is what the partition
    proposition requires and what the two certification queries independently
    confirmed.

    A coordinate separates the two when every value in the OVERLAP has been
    punched out by one side or the other -- either side is enough, since a hole
    removes the value from that region and the point must lie in both.
    """
    a_holes = a_holes or {}
    b_holes = b_holes or {}
    for n, (lo, hi) in a.items():
        if n not in b:
            continue
        blo, bhi = b[n]
        if hi < blo or bhi < lo:
            return False
        olo, ohi = max(lo, blo), min(hi, bhi)
        punched = {v for v in set(a_holes.get(n, ())) | set(b_holes.get(n, ())) if olo <= v <= ohi}
        if len(punched) >= ohi - olo + 1:
            return False
    return True


def relation_constrained_boxes_intersect(a,
                                         b,
                                         a_holes=None,
                                         b_holes=None,
                                         a_established=None,
                                         b_established=None):
    a_established = a_established or {}
    b_established = b_established or {}
    if not a_established and not b_established:
        return boxes_intersect(a, b, a_holes, b_holes)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for box in (a, b):
        for n in box:
            find(n)
    for rels in (a_established, b_established):
        for target, source in rels.items():
            union(target, source)

    intervals = {}
    holes_by_root = {}
    for box, hs in ((a, a_holes or {}), (b, b_holes or {})):
        for n, (lo, hi) in box.items():
            r = find(n)
            if r in intervals:
                lo, hi = max(lo, intervals[r][0]), min(hi, intervals[r][1])
            if lo > hi:
                return False
            intervals[r] = (lo, hi)
            holes_by_root.setdefault(r, set()).update(hs.get(n, ()))
    for r, (lo, hi) in intervals.items():
        punched = {v for v in holes_by_root.get(r, set()) if lo <= v <= hi}
        if len(punched) >= hi - lo + 1:
            return False
    return True


def certified_overlap(ok, holes=None, established=None, extcall_pins=None):
    """Pairs of CERTIFIED regions that intersect. Must always be empty.

    Path domains partition the input space, so two distinct paths cannot both
    be certified over boxes sharing a point: an input in the intersection would
    have to walk both. A non-empty result is therefore not "imprecision", it is
    proof that something upstream is wrong.

    This exists because that is exactly how the unconditionally-green gate was
    caught, and it was caught BY EYE. The first output after the wiring was
    fixed listed enc=2 and enc=7 with the same box `a in [0, 5]`, which
    contradicts the partition proposition on its face; re-running each
    certification query by hand then returned FAILED for both. A human noticed a
    contradiction that the code could have noticed itself.

    The general lesson, worth more than this function: the propositions this
    method rests on -- domains are disjoint, a region only ever narrows, F+I+U
    equals the path count -- are not only paper material. They are executable
    consistency checks, and the cheap ones belong in the loop.
    """
    bad = []
    holes = holes or {}
    established = established or {}
    extcall_pins = extcall_pins or {}
    encs = sorted(ok)
    for i, e1 in enumerate(encs):
        for e2 in encs[i + 1:]:
            p1, p2 = extcall_pins.get(e1, {}), extcall_pins.get(e2, {})
            incompatible_extcall = any(n in p2 and p2[n] != v for n, v in p1.items())
            if (not incompatible_extcall and relation_constrained_boxes_intersect(
                    ok[e1], ok[e2], holes.get(e1), holes.get(e2), established.get(e1),
                    established.get(e2))):
                bad.append((e1, e2))
    return bad


REACHED_RE = re.compile(r"(\d+) of (\d+) ladder probe\(s\) reached the solver")
DECISION_RE = re.compile(r"Runtime decision procedure: ([0-9.]+)s")


def round_accounting(log):
    """The three numbers WITHOUT which a cost claim may not be made.

    "The round did not finish" is not evidence of "the ladder is too long". It
    is equally consistent with one query hanging, with the solver giving up, and
    with an unsatisfiable assumption making the solver behave erratically. Those
    are different defects and only one of them is about cost -- so a round that
    reports only its wall clock cannot support any conclusion at all, and this
    project has already had to retract one cost claim built exactly that way.

    So every round reports:

      * decided / total -- is it uniformly slow, or stuck on one query? 417 of
        420 answered means one query hung; 3 of 420 means it never got going.
      * the per-query wall clock distribution (max and median) -- 420 x 0.26s is
        a ladder-length problem; 417 fast plus one enormous is not.
      * the verdict mix -- a timeout is slow, an `unknown` is the solver giving
        up, and a wall of UNSAT can mean the assumption itself is unsatisfiable
        (measured on this very unit: the subtraction inverted an interval, and
        an unsatisfiable assumption is exactly where solver behaviour stops
        being a measurement of anything).

    Read off ESBMC's own output rather than timed here, so the numbers describe
    the solver's work and not the driver's bookkeeping around it.
    """
    m = REACHED_RE.search(log)
    decided, total = (m.group(1), m.group(2)) if m else ("?", "?")
    times = sorted(float(x) for x in DECISION_RE.findall(log))
    npass = log.count("✓ PASSED:")
    nfail = log.count("✗ FAILED:")
    if times:
        med = times[len(times) // 2]
        dist = (f"per-query wall: n={len(times)} max={times[-1]:.3f}s "
                f"median={med:.3f}s total={sum(times):.1f}s")
    else:
        # NOT "0 seconds": no query reported a time at all, which is itself the
        # finding when a round comes back empty.
        dist = "per-query wall: NO query reported a decision time"
    return (f"accounting: {decided} of {total} probe(s) reached the solver; "
            f"{dist}; verdicts PASSED={npass} FAILED={nfail}")


def unresolvable_coords(log):
    """Names the tool says it cannot resolve as coordinates of this unit.

    THE SAME SENTENCE `round_failure_reason` READS, harvested unconditionally
    rather than only when the round measured nothing -- and that difference is a
    live defect, not a tidy-up.

    The two stage-2 branches treat an unresolvable name in OPPOSITE ways, by
    design: an outer-box round rejects the coordinate and carries on measuring
    the others, while certification refuses the WHOLE query, because dropping a
    requested bound would certify a wider box than the one asked about. Both are
    right. What follows from the pair is that a name the outer-box round merely
    complained about will KILL every certification query of that unit -- and
    since the round still produced regions, `round_failure_reason` returns None
    and the complaint was never read.

    MEASURED on aqua.rawBalances and aqua.safeBalances, the first two real units
    of the first corpus sweep. `state._DOCKED` is a mapping, lowered to a
    contract-scope global rather than a field of the contract object, so the
    counterexample harvest reports it in `entry_storage` and the resolver
    refuses it. The driver reads it as a `constant` state variable and PINS it
    at its counterexample value -- and every certification query on those units
    then came back with no verdict in three seconds. The reported reason was
    "ESBMC printed neither SUCCESSFUL nor FAILED", which is true and useless.

    Dropping such a name is SOUND in the only direction that matters: an
    unmentioned coordinate is universally quantified, so the certificate becomes
    STRONGER, not wider. It is still announced, because it changes what the
    region is a statement about.
    """
    # ---- TWO WORDINGS, AND THE ONE THIS MATCHED IS NOT THE ONE EMITTED ----
    #
    # MEASURED, on a hand-built outer-box spec naming `state.nosuch[k]`. The
    # tool refuses it twice and loudly:
    #
    #   WARNING: --path-cov-outer-box: unit '<id>' — REFUSING coordinate
    #   'state.nosuch[k]': the name does not resolve to an input of this unit
    #   WARNING: --path-cov-outer-box: 1 coordinate(s) were REFUSED and appear
    #   in NO box below: state.nosuch[k] (...)
    #
    # Neither sentence contains "has no input named", which is all this function
    # looked for -- so it returned nothing, `round_failure_reason` reported no
    # cause, and the refused coordinate vanished from the run with NOTHING on
    # screen. The regions printed afterwards simply do not mention it, which
    # reads as "unconstrained" and is exactly the silent-disappearance condition
    # the coordinate accounting exists to prevent.
    #
    # This is the detector-wired-to-the-wrong-branch failure in its usual shape:
    # a check that can never fire looks identical, from outside, to a check that
    # never had anything to report. The older wording is kept because an older
    # binary still emits it, and a driver that stops recognising a message it
    # used to handle is the same defect pointing the other way.
    return sorted(
        set(re.findall(r"has no input named '([^']+)'", log))
        | set(re.findall(r"REFUSING coordinate '([^']+)'", log)))


def round_failure_reason(log):
    """Why an outer-box round measured NOTHING, or None if it ran.

    "No regions" is reported downstream as "no fully bounded region was
    measured", which reads as a property of the path. It is usually a property
    of the run, and the two causes seen on real input need different words:

      * a coordinate the tool cannot RESOLVE. The counterexample harvest and the
        coordinate resolver disagree -- mappings and dynamic arrays are lowered
        to contract-scope globals rather than fields of the contract object, so
        `state._DOCKED` is reported in entry_storage and is not accepted as a
        coordinate. The tool is right to refuse (dropping the bound would widen
        this path's own box, hence its region); the driver was wrong to have no
        way of saying so.
      * a BUDGET outcome: the round was killed.

    Collapsing either into "no region for this path" is the failure-as-result
    pattern this file keeps running into.
    """
    unresolved = unresolvable_coords(log)
    # ---- A REFUSED COORDINATE IS NOT THE SAME AS A ROUND THAT MEASURED
    # ---- NOTHING, AND SAYING SO WOULD BE A FALSE REPORT ----
    #
    # The tool states the distinction itself: "No probe is emitted for it and no
    # bound is attributed to it; the remaining coordinates are measured as
    # usual." So a round that refuses one name out of five still produces boxes,
    # brackets and regions for the other four.
    #
    # This branch predates the detector actually firing -- while
    # `unresolvable_coords` matched only a wording the tool no longer emits, the
    # condition was unreachable and its overreach could not show. Repairing the
    # detector without repairing this would have turned a silent miss into a
    # confident falsehood printed on every run with one refused coordinate,
    # which is the worse of the two.
    #
    # The presence of an OUTER box line is the direct evidence that the round
    # measured something, read off the same log rather than inferred from the
    # coordinate count. Where there is none, the original reading stands.
    if unresolved and not BOX_RE.search(log):
        return ("the outer-box round rejected coordinate(s) " + ", ".join(unresolved) +
                " as unresolvable and produced no box at all, so nothing was "
                "measured (a COORDINATE-SUPPORT gap, not a property of the "
                "path)")
    if "[run] TIMEOUT after" in log:
        return ("no outer-box round finished, so nothing was measured for "
                "this path (a BUDGET outcome, not a property of the path)")
    # Fail-closed on the exit code. 0 = SUCCESSFUL, 1 = FAILED; anything else
    # means ESBMC did not finish, whatever it printed on the way. Checked LAST
    # so the two named causes keep their specific wording, and checked AT ALL
    # because the named causes are a whitelist and a whitelist of failures is
    # open at the bottom.
    m = re.search(r"\[run\] EXIT (-?\d+)", log)
    if m and m.group(1) not in ("0", "1"):
        code = m.group(1)
        # subprocess reports a signal-killed child as a NEGATIVE number, not as
        # 128+n; both forms are mapped so the message names the real cause
        # rather than falling through to "did not complete". Measured: the
        # string-typed-coordinate abort arrives as -6, not 134.
        why = {
            "6": "conversion error",
            "124": "killed on timeout",
            "-15": "killed (SIGTERM)",
            "-9": "killed (SIGKILL)",
            "134": "ABORTED (SIGABRT)",
            "-6": "ABORTED (SIGABRT)",
            "139": "crashed (SIGSEGV)",
            "-11": "crashed (SIGSEGV)",
        }.get(code, "did not complete")
        return (f"ESBMC exited {code} ({why}), so the round measured nothing "
                f"(a TOOL outcome, not a property of the path). The last "
                f"ERROR line in its output names the cause")
    return None


def empty_coords(box, holes=None):
    """Coordinates whose region is EMPTY -- no value survives.

    An empty box certifies VACUOUSLY: `assume(lo <= x <= hi)` with lo > hi is
    `assume(false)`, so `assert(tr == pi)` holds for want of any execution and
    the query answers SUCCESSFUL. The tool's own non-vacuity argument -- put the
    assert on EVERY exit -- addresses a different vacuity and does not cover an
    unsatisfiable assumption.

    Not hypothetical: with the environment pinned, the ABI-gate revert path's
    domain is empty, the sibling subtraction duly produced lo > hi, and the run
    reported it as a certified region. The pin had excluded the path; the honest
    statement is that, not a certificate.

    A PUNCHED interval has a SECOND way of being empty and `lo > hi` cannot see
    it: `[5, 5] \\ {5}` is a well-formed interval that admits no value. Same
    consequence -- an unsatisfiable assumption certifies vacuously -- so it is
    the same check, not a new one. The tool refuses such a box too; both sides
    check it because a gate the caller is expected to provide is a gate that has
    already failed once here.
    """
    holes = holes or {}
    out = []
    for n, (lo, hi) in box.items():
        if lo > hi:
            out.append(n)
            continue
        punched = {v for v in holes.get(n, ()) if lo <= v <= hi}
        if len(punched) >= hi - lo + 1:
            out.append(n)
    return sorted(out)


def ce_in_region(box, holes, ce):
    """C2 -- coordinates on which the region EXCLUDES this path's own CE.

    The counterexample is a KNOWN member of the path's domain: the enumeration
    witnessed the path with it. So a certified region that does not contain it
    has certainly cut into the real domain, and the certificate is about some
    other set than the one the path actually has.

    This is not a new proposition. It is the exact rule every subtraction cut
    already obeys inside the tool ("a cut is legal only if this path's own
    counterexample survives it"), and the driver has had `ce` in hand the whole
    time and never checked it. Two things can break it from the driver side:
    holes are carried ACROSS shrink rounds, so a hole punched in round 1 can
    land on the CE after a later side cut moves the interval; and a `--pin` the
    caller supplies can conflict with the CE outright.

    Pure arithmetic, no query. Returns a list of human-readable violations,
    empty when the region contains the CE.
    """
    holes = holes or {}
    bad = []
    for n, (lo, hi) in sorted(box.items()):
        if n not in ce:
            continue
        v = ce[n]
        if v < lo or v > hi:
            bad.append(f"{n}: CE {v} outside [{lo}, {hi}]")
        elif v in set(holes.get(n, ())):
            bad.append(f"{n}: CE {v} was PUNCHED OUT of [{lo}, {hi}]")
    return bad


def region_size(box, holes=None):
    """C3 -- |R| for a punched product region, as an exact integer.

    The arithmetic is the tool's own `path_cov_kept_in` per coordinate, times
    over the coordinates. Shared so the two sides cannot disagree about whether
    a region is empty while agreeing about which is wider.

    Used for the monotonicity check: a region may only ever get NARROWER across
    shrink rounds. "Only ever narrower" is the invariant the whole subtraction
    rests on and it has lived in comments on both sides without either ever
    computing it.
    """
    holes = holes or {}
    total = 1
    for n, (lo, hi) in sorted(box.items()):
        if hi < lo:
            return 0
        k = (hi - lo + 1) - len([v for v in holes.get(n, ()) if lo <= v <= hi])
        if k <= 0:
            return 0
        total *= k
    return total


def coordinate_accounting(payload, buckets):
    """C5 -- payload names that landed in NO bucket at all.

    Every name the counterexample payload carries must end up somewhere the
    report can show: a free coordinate, a pin, an environment quantity left
    unconstrained, a dropped lowering artifact, an unsettable pinned at its CE,
    or a name the tool refused. A name in NONE of those has silently vanished
    between the report and the region, and the region is then a statement about
    a smaller input space than the one the path actually has -- with nothing on
    screen to say so.

    This is the generic form of a defect this project has already had in the
    specific: `state._DOCKED` is reported in `entry_storage` and is refused by
    the coordinate resolver, and the driver believed the report while the tool
    refused, with no line anywhere saying the two disagreed.

    COVERAGE, NOT PARTITION, and that is deliberate rather than lax. An
    unsettable coordinate is ALSO added to `pins` (that is what "pinned at the
    counterexample value" means), so demanding exactly one bucket would fail on
    correct input. The property worth checking is that nothing falls through.

    `buckets` is an ordered mapping name -> iterable, so the report can say
    WHICH bucket each name reached and the caller decides the vocabulary.
    Returns (unaccounted, where) with `where` mapping each payload name to the
    bucket names that claim it.
    """
    where = {}
    for n in sorted(payload):
        hit = [b for b, names in buckets.items() if n in (names or ())]
        if hit:
            where[n] = hit
    return sorted(set(payload) - set(where)), where


def witness_values(cwd,
                   unit,
                   state_structs=False,
                   param_types=None,
                   state_types=None,
                   extcall_coord_specs=None):
    """The REFUTING input's payload, harvested from the certification run.

    ⛔ `state_structs` MUST BE THREADED IN FROM THE SAME FLAG THAT DECOMPOSED THE
    ENUMERATION PAYLOAD. Both sides go through `coord_values`, and reading the
    two under different settings makes the driver compare a payload that has
    `state.x.f` against one that never can. MEASURED, farming/deposit, the first
    run with --state-struct-fields: every coordinate-gate verdict carried

        NOTE: the two payloads do not carry the same keys, so the comparison
        above covers only the shared ones -- only in this path's:
        state._farm.farmInfo.finished

    and that asymmetry was this function defaulting the flag OFF, not anything
    ESBMC failed to render. Same shape as the defect that flag was written for:
    one fact, and only one of its readers updated.

    The certification query already runs with --cov-report-json, so this costs
    nothing: the refutation's counterexample is on disk by the time the verdict
    is read.

    Filtered to THIS unit rather than taking the first refutation in the file.
    Other units' path claims are instrumented in the same run, and while under
    --focus they come back undecided rather than refuted, "they happen not to be
    F right now" is a property of the current configuration, not of the report.
    Reading a foreign unit's counterexample here would produce a difference list
    about the wrong function -- confidently, and with no way to notice.
    """
    report = os.path.join(cwd, "cov-report.json")
    if not os.path.exists(report):
        return {}
    try:
        with open(report) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return {}

    def same_unit(c):
        return claim_unit(c) == unit or same_path_function(c.get("path_function"), unit)

    for c in rep.get("certify_safety_refutations", []):
        if c.get("status") == "F" and same_unit(c):
            ce, _ = coord_values(c,
                                 state_structs=state_structs,
                                 param_types=param_types,
                                 state_types=state_types,
                                 extcall_coord_specs=extcall_coord_specs)
            ce.update(payload_extras(c))
            return ce
    for c in rep.get("claims", []):
        if c.get("status") == "F" and same_unit(c):
            ce, _ = coord_values(c,
                                 state_structs=state_structs,
                                 param_types=param_types,
                                 state_types=state_types,
                                 extcall_coord_specs=extcall_coord_specs)
            ce.update(payload_extras(c))
            return ce
    return {}


def payload_extras(c):
    """The claim's payload quantities that are NOT candidate coordinates.

    Today that is `extcall_returns`: nondet locals of the unit under test, of
    which an external call's success flag or return value is the common case.
    They are namespaced `extcall.<name>` -- a Solidity identifier cannot
    contain a dot, and the environment's own dotted names (`msg.value`) start
    with a different word, so nothing can collide.

    ⛔ THESE ARE NOT COORDINATES AND MUST NEVER BECOME ONE HERE. A coordinate
    is a quantity a generated test can supply as a call argument; a local the
    harness chose is not. They are carried so the comparison can SAY they
    differ, which is a different job from acting on the difference.

    Non-scalar entries are skipped rather than refused loudly: the aggregates
    in this list (`_sol_save_this`, a whole mapping store) are restore-snapshot
    plumbing, and naming them in a refusal line would bury the one scalar that
    matters under them.
    """
    out = {}
    for e in (c.get("extcall_returns") or []):
        name = (e.get("symbol") or e.get("name")) if isinstance(e, dict) \
            else None
        if not name:
            continue
        try:
            out["extcall." + name] = parse_int(e.get("value"))
        except (ValueError, TypeError):
            continue
    return out


def _source_contract_chunk(source, name):
    if not source or not name:
        return ""
    m = re.search(r"\b(?:abstract\s+)?(?:contract|interface)\s+" + re.escape(name) + r"\b[^{]*\{",
                  source)
    if m is None:
        return ""
    depth, i = 1, m.end()
    while i < len(source) and depth:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[m.end():i - 1] if depth == 0 else ""


def _function_return_types_from_source(source):
    out = {}
    rx = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*([^;{]*)[;{]", re.S)
    for m in rx.finditer(source or ""):
        returns = []
        rm = re.search(r"\breturns\s*\((.*?)\)", m.group(3), re.S)
        if rm:
            for raw in rm.group(1).split(","):
                text = re.sub(r"\s+", " ", raw.strip())
                parts = text.split()
                if len(parts) >= 2 and parts[-2] in ("memory", "calldata", "storage"):
                    parts = parts[:-1]
                elif len(parts) >= 2:
                    parts = parts[:-1]
                typ = " ".join(p for p in parts if p not in ("memory", "calldata", "storage"))
                if typ:
                    returns.append(typ)
        params = []
        for raw in m.group(2).split(","):
            text = re.sub(r"\s+", " ", raw.strip())
            if not text:
                continue
            parts = text.split()
            if len(parts) >= 2 and parts[-2] in ("memory", "calldata", "storage"):
                parts = parts[:-2]
            elif len(parts) >= 2:
                parts = parts[:-1]
            typ = " ".join(p for p in parts
                           if p not in ("memory", "calldata", "storage", "payable"))
            if typ:
                params.append({"uint": "uint256", "int": "int256"}.get(typ, typ))
        out.setdefault(m.group(1), []).append({
            "signature": f"{m.group(1)}({','.join(params)})",
            "returns": returns
        })
    return out


def extcall_length_coordinate_specs(source, contract, unit):
    """Local uint length variables backed by mockable interface array returns."""
    chunk = _source_contract_chunk(source or "", contract)
    if not chunk:
        return []
    returns_by_name = _function_return_types_from_source(source or "")
    array_locals = {}
    assign_rx = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\.\s*"
                           r"([A-Za-z_]\w*)\s*\([^;]*\)\s*;")
    for local, receiver, fname in assign_rx.findall(chunk):
        choices = returns_by_name.get(fname) or []
        if choices and all(choice == choices[0] for choice in choices):
            choices = choices[:1]
        if len(choices) != 1:
            continue
        returns = choices[0].get("returns") or []
        if len(returns) != 1 or not returns[0].endswith("[]"):
            continue
        array_locals[local] = {
            "state_var": receiver,
            "function": fname,
            "signature": choices[0]["signature"],
            "return_type": returns[0],
        }
    specs, seen = [], set()
    len_rx = re.compile(r"\buint(?:256)?\s+([A-Za-z_]\w*)\s*=\s*"
                        r"([A-Za-z_]\w*)\s*\.\s*length\s*;")
    for coord, local in len_rx.findall(chunk):
        base = array_locals.get(local)
        if not base:
            continue
        key = (coord, base["state_var"], base["signature"])
        if key in seen:
            continue
        seen.add(key)
        spec = dict(base)
        spec.update({
            "coord": coord,
            "array_local": local,
            "source": "constructor-state-interface-array-length",
        })
        loop_rx = re.compile(r"\bfor\s*\(\s*uint(?:256)?\s+([A-Za-z_]\w*)\s*=\s*"
                             r"([0-9]+)\s*;\s*\1\s*<\s*" + re.escape(coord) +
                             r"\s*;\s*(?:\+\+\1|\1\s*\+\+)\s*\)")
        lm = loop_rx.search(chunk)
        if lm:
            spec.update({
                "loop_var": lm.group(1),
                "loop_init": lm.group(2),
                "loop_bound": coord,
                "loop_step": "++",
            })
        specs.append(spec)
    return specs


def _is_positive_loop_guard_claim(claim, loop_var, coord):
    text = re.sub(r"\s+", "", str(claim or ""))
    return text == f"{loop_var}<{coord}"


def _is_loop_guard_claim(claim, loop_var, coord):
    text = re.sub(r"\s+", "", str(claim or ""))
    return text in (f"{loop_var}<{coord}", f"!({loop_var}<{coord})")


def structural_extcall_length_no_loop_regions(paths, path_decisions, coords, specs):
    """Direct no-loop regions for mockable external array lengths.

    ESBMC can report the local `n = urls.length` in counterexamples while still
    refusing it as an outer-box query coordinate.  For the narrow source shape
    `for (uint256 i = K; i < n; ++i)`, the first guard's exit arm is exactly
    `n <= K`.  Foundry can realize that region by mocking the interface call to
    return a dynamic array of fuzzed length, so this gives Stage 4 a genuine
    rendered coordinate without asking ESBMC to solve over an unqueryable local.
    """
    coord_set = set(coords or [])
    specs_by_coord = {
        spec.get("coord"): spec
        for spec in (specs or []) if isinstance(spec, dict) and spec.get("coord") in coord_set
        and spec.get("loop_var") and spec.get("loop_init") is not None
    }
    out, holes, reasons, chosen = {}, {}, {}, set()
    for enc, _depth, ce in sorted(paths):
        for coord, spec in sorted(specs_by_coord.items()):
            if coord not in ce:
                continue
            try:
                init = parse_int(spec.get("loop_init"))
            except (TypeError, ValueError):
                continue
            loop_var = spec.get("loop_var")
            guards = [
                d for d in (path_decisions.get(enc) or [])
                if _is_loop_guard_claim(d.get("branch_claim"), loop_var, coord)
            ]
            if not guards:
                continue
            # Only the first guard exit is hash-independent.  Later exits have
            # already executed the loop body, whose path may depend on ESBMC's
            # nondeterministic hash abstraction.
            first_guard = min(guards, key=lambda d: int(d.get("index") or 0))
            if not _is_positive_loop_guard_claim(first_guard.get("branch_claim"), loop_var, coord):
                continue
            key = (coord, init)
            if key in chosen:
                continue
            chosen.add(key)
            out[enc] = {coord: (0, init)}
            holes[enc] = {}
            reasons[enc] = ("STRUCTURAL external-call array length no-loop region: "
                            f"{coord} is {spec.get('array_local')}.length from "
                            f"{spec.get('state_var')}.{spec.get('signature')}, and the "
                            f"first loop guard {loop_var} < {coord} exits when "
                            f"{coord} in [0, {init}]. Stage 4 realizes this coordinate "
                            "with a Foundry interface mock.")
            break
    return out, holes, reasons


def extraction_caveats(claims):
    """What the report SAYS it could not harvest, in its own words.

    `ce_extraction` carries a per-claim note naming each family of value the
    counterexample harvest could not render, with the mechanism. It has been in
    every report this driver has ever read and the driver has never looked at it.

    That matters exactly where the reach gate lands. "The witness agrees on every
    scalar in the payload" leaves three candidates for what actually separates
    the paths -- an unnamed intermediate, an external-call return, a non-scalar --
    and the report already narrows that: on aqua it says external-call returns
    are not harvested at all, because the returned value reaches the user's
    variable through a tuple-field extraction that `get_nondet_symbol` does not
    traverse, so the trace step is dropped before classification.

    Quoting it is not the same as concluding the discriminating quantity IS an
    external-call return -- it is one named candidate with a mechanism instead of
    three unnamed ones. The wording below keeps that distinction.
    """
    out = {}
    for c in claims:
        for k, v in (c.get("ce_extraction") or {}).items():
            if k.endswith("_unavailable_reason") and v:
                out.setdefault(k[:-len("_unavailable_reason")], v)
    return out


def assumed_holes(holes, pins):
    """Which values the query assumed were EXCLUDED, per coordinate.

    A pin adds none: it is already a single value. Kept beside assumed_ranges
    for the same reason -- the trust check has to compare the report against the
    query that was really issued, and after punched intervals the query is an
    interval MINUS a set.
    """
    out = {n: list(v) for n, v in (holes or {}).items() if n not in pins}
    return out


def assumed_ranges(box, pins):
    """What the certification query ACTUALLY assumed, per coordinate.

    Built the same way `certify` builds the spec it sends -- box intervals plus
    each pin as a degenerate one -- so the check downstream compares the report
    against the query that was really issued, not against a reconstruction of it.
    """
    r = dict(box)
    for n, v in pins.items():
        r[n] = (v, v)
    return r


def outside_assumed(name, value, ranges, holes=None):
    """Is this witness value OUTSIDE the set the query itself assumed?

    A refuting witness is supposed to be an input FROM the assumed box. When the
    reported value for a coordinate falls outside that box, the report and the
    query disagree, and the report is the one that cannot be trusted: the query's
    assumption is what the solver actually worked under.

    Measured, on three fixtures, without needing to know which mechanism applies:

      * bound binds. Same revert path, same query, only the `msg.sender` bound
        differing: [255,255] (the banned sender) is SUCCESSFUL, [0,0] is FAILED.
        So an assumed bound does constrain the quantity the guard reads.
      * harvest faithful when the path READS the quantity and makes no nested
        call: the same query reports `msg.sender: 0`, matching its pin.
      * harvest can contradict the bound otherwise: on a path that never reads
        the quantity the reported value is an unconstrained symbol (pin 5,
        reported 0), and on a unit that makes nested calls the reported value can
        be a post-wrapper one (pin 0, reported 32509824) -- call wrappers
        overwrite `msg_sender` with the callee's identity, which the emitter
        documents.

    This check needs none of that. It compares what was reported against what was
    assumed, which is a proposition the pipeline already relies on, made into a
    runtime check -- the rule that the certification gate was caught by in the
    first place.

    A PUNCHED value counts as outside. `assume(lo <= c <= hi && c != h)` says the
    solver never had `c == h` available, so a report claiming it is the same
    contradiction as one outside the interval, and it must not be offered as the
    discriminating quantity either. Reading only the interval would have let the
    one value the query most explicitly excluded through.
    """
    r = ranges.get(name) if ranges else None
    if not r:
        return False
    lo, hi = r
    if value < lo or value > hi:
        return True
    return value in set((holes or {}).get(name, ()))


def divergence_pairs(path_ce, wit_ce, ranges=None, holes=None):
    """The divergence as DATA: (usable, untrusted, only_path, only_wit).

    `divergence_text` below computes exactly this and renders it to prose, and
    for a long time prose was the only form it existed in. That is this
    project's own "one fact in two ledgers, the second a regex over sentences"
    shape waiting to happen: anything downstream that needs to know WHICH
    quantity separated the two counterexamples -- and a retreat-to-a-pin policy
    needs precisely that -- would have to re-parse the sentence this function
    writes, and would silently stop matching the first time it is reworded.

    `usable` holds the triples a policy may act on. `untrusted` holds those
    whose witness value contradicts the bound the query itself assumed, and
    they are returned SEPARATELY rather than dropped, because "there was no
    difference" and "every difference we saw is unusable" are different
    findings -- collapsing them is what manufactured an empty-divergence
    reading out of a measurement problem once already.

    An empty `wit_ce` returns four empty lists, which the caller must NOT read
    as "no difference": it means the refutation carried no payload at all.
    `divergence_text` keeps that distinction in its own first branch.
    """
    if not wit_ce:
        return [], [], [], []
    only_path = sorted(set(path_ce) - set(wit_ce))
    only_wit = sorted(set(wit_ce) - set(path_ce))
    diff_all = [(n, path_ce[n], wit_ce[n]) for n in sorted(set(path_ce) & set(wit_ce))
                if path_ce[n] != wit_ce[n]]
    untrusted = [t for t in diff_all if outside_assumed(t[0], t[2], ranges, holes)]
    usable = [t for t in diff_all if t not in untrusted]
    return usable, untrusted, only_path, only_wit


def divergence_text(path_ce, wit_ce, bounded, caveats=None, ranges=None, holes=None):
    """Name the quantity the refuting witness differs on. The point of this file.

    "Refuted with no single-coordinate cut available" is the reach gate, and it
    is the number the evaluation leans on -- but on its own it says only that the
    witness agrees with the path's counterexample on every BOUNDED coordinate.
    It never says WHICH quantity they actually differ on, so reading the missing
    coordinate CLASS off it is an inference: one looks at what was refused and
    assumes that must have been the discriminating thing. That is "saw nothing
    else, therefore it is this", which is the inference this project has got
    wrong five times.

    The information was there the whole time. Both counterexamples are on disk;
    the difference is arithmetic. With it, the evaluation's coordinate-availability
    table explains the reach-gate bucket by MEASUREMENT instead of by argument,
    which is the link those two tables are supposed to have.

    Three outcomes, kept apart on purpose:
      * a non-empty diff -- named, with which side is bounded;
      * an EMPTY diff -- the witness agrees on every scalar in the payload too,
        so whatever separates them is not in the payload at all. Said out loud,
        because an empty list is exactly the shape that reads as "nothing to
        report";
      * NO payload -- the harvest failed. Not the same as "no difference", and
        collapsing the two would be this file's recurring failure-as-result bug.
    """
    if not wit_ce:
        return ("; the refutation carried NO payload, so the differing quantity "
                "could not be read at all -- that is a missing harvest, NOT a "
                "finding of 'no difference'")
    # SYMMETRIC. Comparing only the shared keys and then reporting that the
    # witness "agrees on EVERY scalar quantity" is false the moment one payload
    # carries a key the other does not: an extra scalar on the witness side would
    # be dropped by the intersection and the message would still claim total
    # agreement. Overclaiming is the one thing this function must not do -- its
    # entire purpose is to say precisely what was and was not compared.
    # ONE COMPUTATION, TWO RENDERINGS. The triples come from
    # `divergence_pairs` so a policy reading the data and this sentence
    # describing it cannot disagree about what was compared.
    diff, untrusted, only_path, only_wit = divergence_pairs(path_ce, wit_ce, ranges, holes)
    asym = ""
    if only_path or only_wit:
        asym = ("; NOTE: the two payloads do not carry the same keys, so the "
                "comparison above covers only the shared ones" +
                (f" -- only in this path's: {', '.join(only_path)}" if only_path else "") +
                (f" -- only in the witness's: {', '.join(only_wit)}" if only_wit else ""))
    # `untrusted` above holds the differences whose witness value contradicts
    # the bound this very query assumed. Reporting those as "the witness differs
    # on X" is how a diagnosis went wrong: EscrowSrc.cancel was read as "the
    # divergence lives in an unpinned msg.sender", giving that its own failure
    # cell, when the sender WAS pinned and the reported value simply was not the
    # entry-time one. They are not dropped -- an unexplained contradiction
    # between the report and the query is worth saying out loud -- but they must
    # not be offered as the discriminating quantity.
    untrusted_note = ""
    if untrusted:

        def _assumed(n):
            txt = f"[{ranges[n][0]}, {ranges[n][1]}]"
            hs = sorted((holes or {}).get(n, ()))
            return txt + (" \\ {" + ", ".join(str(h) for h in hs) + "}" if hs else "")

        untrusted_note = ("; NOTE: the witness value reported for " +
                          ", ".join(f"{n} (={wv}, assumed in {_assumed(n)})"
                                    for n, _, wv in untrusted) +
                          " lies OUTSIDE the bound this query assumed, so it is NOT the "
                          "entry-time value and is excluded from the difference above. A "
                          "path that never reads the quantity leaves it unconstrained, and "
                          "a nested call overwrites msg.sender with the callee's identity; "
                          "either way the reported value says nothing about what separates "
                          "these paths")
    # ALL observed differences were untrusted. This must NOT fall through to the
    # "agrees on every scalar" branch below: "there was no difference" and "every
    # difference we saw contradicts our own assumption" are different findings,
    # and collapsing them is the failure-as-result pattern this file keeps
    # running into -- here it would manufacture an empty-divergence reading (the
    # reach-gate bucket) out of a measurement problem.
    if not diff and untrusted:
        return ("; every difference between the witness and this path's "
                "counterexample was on a quantity whose reported value "
                "contradicts the bound this query assumed, so NONE of them is "
                "usable. This is NOT the empty-divergence case: it is a payload "
                "that could not be compared" + asym + untrusted_note)
    if not diff and asym:
        # Not "they agree on everything": they agree on everything COMPARABLE,
        # and something was not comparable. Those are different findings.
        return ("; the witness agrees with this path's counterexample on every "
                "scalar the two payloads have in common" + asym + untrusted_note)
    if not diff:
        msg = ("; and the witness agrees with this path's counterexample on "
               "EVERY scalar quantity in the payload as well, so whatever "
               "separates the two is not in the payload at all -- an unnamed "
               "intermediate, an external-call return, or a non-scalar. This "
               "is the explicit unknown bucket, not an empty result")
        # NARROW IT with what the report already said. These notes name families
        # the harvest could not render AND why; quoting one is not the same as
        # concluding it is the discriminating quantity, and the wording says so.
        for fam, why in sorted((caveats or {}).items()):
            msg += (f". The report additionally states that '{fam}' is NOT "
                    f"harvested at all, so it could not have shown up in the "
                    f"comparison above even if it were the discriminating "
                    f"quantity -- that makes it a NAMED candidate, not a "
                    f"conclusion. Its stated reason: {why}")
        return msg + untrusted_note
    return ("; the witness differs from this path's counterexample on: " +
            ", ".join(f"{n} (path={pv}, witness={wv})" +
                      ("" if n in bounded else " [NOT a bounded coordinate]")
                      for n, pv, wv in diff) + asym + untrusted_note)


def cut_towards(lo, hi, wv, pv):
    """§Certification's cut: the side of `wv` on which `pv` lies.

    Verbatim from the method:

        On any coordinate where they do differ, keeping the side of y_c on
        which x_{π,c} lies removes y and keeps x_π, so every such coordinate
        offers a sound cut

    So the DIRECTION is not a suggestion to be read off a log -- it is decided
    by the two counterexamples, both of which this driver holds. Returns the
    kept interval clamped to [lo, hi], or None where the two agree (no cut is
    offered there).
    """
    if pv < wv:
        return (lo, min(hi, wv - 1))
    if pv > wv:
        return (max(lo, wv + 1), hi)
    return None


def coord_kept(lo, hi, holes=()):
    """How many values a coordinate's set admits. `region_size`, per coordinate."""
    if hi < lo:
        return 0
    return (hi - lo + 1) - len([v for v in holes if lo <= v <= hi])


def tiny_safety_cut_retreat(box, coord, removed, ce, streak, threshold):
    """Pin one coordinate when safety cuts are clearly chasing a relation.

    Solidity 0.8 arithmetic checks can refute a product region whose real safe
    domain is relational, e.g. `x + y <= UINT_MAX`. A product box cannot spell
    that. The normal cut rule then keeps removing one boundary value from `x`
    forever: sound, but useless under a finite shrink budget.

    This fallback is deliberately narrower than "a tiny cut happened":
      * it is called only for `RESULT: UNSAFE` refutations;
      * the same coordinate must have been cut by one value repeatedly;
      * pinning it must still leave a non-environment coordinate wide.

    That keeps single-coordinate checks such as `x + 2` on the normal path: two
    one-value cuts are exactly the right answer there, not a reason to give up
    the whole `x` range.
    """
    if removed != 1 or streak < threshold:
        return None
    if coord not in box or coord not in ce:
        return None
    lo, hi = box[coord]
    pv = ce[coord]
    if lo == hi or not (lo <= pv <= hi):
        return None
    if not any(n != coord and not is_env(n) and bhi > blo for n, (blo, bhi) in box.items()):
        return None
    return {coord: pv}


def _coord_survival_rank(name):
    """Which coordinate is most valuable to keep wide in a partial retreat."""
    if is_env(name):
        return 0
    if name.startswith("state."):
        return 1
    return 2


def _coord_retreat_rank(name):
    """Which coordinates should be given up first when a relation refutes."""
    if is_env(name):
        return 0
    if name.startswith("state."):
        return 1
    return 2


def multi_difference_retreat(box, holes, ce, usable, pins):
    """Pin all but the best wide coordinate for a multi-coordinate refutation.

    A refutation that differs from x_pi on several rendered coordinates is often
    not a rectangular boundary at all: checked arithmetic, ownership established
    from caller state, and mapping-key/state relations are product-region hostile.
    Repeating one side cut per query is then the slow path to the same result:
    eventually the loop gives up most coordinates, but spends one ESBMC query per
    boundary movement.

    This is still only a refutation response. It proves nothing about the final
    region. It merely chooses the narrower candidate region to ask ESBMC about
    next: keep one useful coordinate wide (prefer an ordinary function input over
    state, and state over environment), pin the other differing coordinates at
    x_pi where that value is inside this piece, and let certification decide.
    """
    actionable = []
    for name, pv, wv in usable:
        if name in pins or name not in box:
            continue
        lo, hi = box[name]
        hs = set(holes.get(name, ()))
        if lo == hi or not (lo <= pv <= hi) or pv in hs:
            continue
        nb = cut_towards(lo, hi, wv, pv) if lo <= wv <= hi else None
        before = coord_kept(lo, hi, hs)
        after = None
        removed = None
        if nb is not None:
            nlo, nhi = nb
            after = coord_kept(nlo, nhi, hs)
            if 0 < after < before:
                removed = before - after
        actionable.append({
            "name": name,
            "value": pv,
            "width": before,
            "after": after,
            "removed": removed,
            "survival": _coord_survival_rank(name),
            "retreat": _coord_retreat_rank(name),
        })
    if len(actionable) < 2:
        return {}
    survivor = max(actionable,
                   key=lambda item:
                   (item["survival"], item["width"], -item["retreat"], item["name"]))
    retreatable = [item for item in actionable if item["name"] != survivor["name"]]
    if not retreatable:
        return {}
    # Pin lower-value coordinates first. If several ordinary inputs remain, this
    # still keeps one fuzz-facing input wide instead of collapsing the whole path.
    retreatable.sort(key=lambda item: (item["retreat"], item["width"], item["name"]))
    return {item["name"]: item["value"] for item in retreatable}


def refutation_response(box, holes, ce, wit, pins, ranges=None, assumed_hs=None):
    """What §Certification prescribes for THIS refutation. (kind, payload).

    ⛔ THIS REPLACES READING THE TOOL'S SUGGESTION, and the two are not
    equivalent. `shrink_target` takes the FIRST `retry with ...` line the tool
    printed and applies it verbatim; the method says something else:

        every such coordinate offers a sound cut, and VeriPUT takes the one
        that removes the fewest values, leaving the region as wide as the
        refutation allows.

    Three differences follow, and each is a defect on its own:
      * no SELECTION across candidates -- whichever coordinate the tool named
        first was taken, with no comparison of how much each cut removes;
      * no independent check that the kept side holds x_π. C2
        (`ce_in_region`) runs only on a SUCCESSFUL verdict, so a cut that drops
        the path's own counterexample is invisible on a path that never
        certifies -- which is every path this branch is reached on;
      * a first suggestion naming a PINNED coordinate returned None and ended
        the path, even where other differing coordinates were cuttable.

    The five outcomes, kept apart because they call for opposite actions:

      "no-payload"  the refutation carried no counterexample. NOT "no
                    difference" -- a missing harvest.
      "untrusted"   every difference seen contradicts the bound this query
                    itself assumed, so none is usable. Also NOT "no
                    difference".
      "coords-gate" y and x_π agree on every coordinate. §Certification:
                    "they differ only on a quantity no test can set, and the
                    path goes to the gate of Section coords instead of to
                    another round." So this is a TERMINAL outcome, not a
                    refutation to answer.
      "cut"         (coord, lo, hi, removed) -- the sound cut removing the
                    fewest values.
      "pin"         {coord: value} -- §Certification's retreat: "Where a
                    refutation cannot be answered by cutting the coordinate it
                    points at, OR WHERE CUTTING WOULD LEAVE THAT COORDINATE ONE
                    VALUE, VeriPUT pins the coordinate to the value x_π gives
                    it and carries on with the others." Both triggers are
                    implemented; the second is the one that is easy to miss,
                    and a one-value interval reached by cutting is exactly the
                    shape it exists to stop. A third implementation detail is
                    the multi-coordinate relation case: keep one useful
                    fuzz-facing coordinate wide and pin the other differing
                    coordinates before spending a query on a side cut.
      "no-retreat"  [coord, ...] -- they DIFFER, no cut is available, and the
                    retreat cannot be taken either, because x_pi's value on
                    every such coordinate lies OUTSIDE this piece. A piece from
                    S3's split excludes x_pi by construction, so pinning at
                    x_pi would REPLACE the piece with a point outside it rather
                    than narrow it. Kept apart from "coords-gate" because that
                    one means the two AGREE everywhere, which is a claim about
                    ESBMC's harvest; this is a claim about S3's bookkeeping,
                    and merging them files one as the other.
    """
    usable, untrusted, only_path, _only_wit = divergence_pairs(ce, wit, ranges, assumed_hs)
    if not wit:
        return "no-payload", None
    if not usable:
        fallback = {
            n: ce[n]
            for n in only_path if n in box and n not in pins and box[n][0] != box[n][1]
            and box[n][0] <= ce[n] <= box[n][1] and ce[n] not in set(holes.get(n, ()))
        }
        if fallback:
            return "pin", fallback
        return ("untrusted", untrusted) if untrusted else ("coords-gate", None)
    multi_retreat = multi_difference_retreat(box, holes, ce, usable, pins)
    if multi_retreat:
        return "pin", multi_retreat
    best, best_removed, retreat = None, None, {}
    for name, pv, wv in usable:
        if name in pins or name not in box:
            # Not part of the region's own description: a pin is what the
            # region is a statement ABOUT, and a name absent from the box is
            # unconstrained. Neither offers a cut, and neither is a retreat --
            # pinning an already-pinned coordinate changes nothing.
            continue
        lo, hi = box[name]
        hs = tuple(holes.get(name, ()))
        # BOTH points must lie in the interval being cut. `wv` outside it means
        # the witness was not solved under this bound (the trust check should
        # have caught it); `pv` outside it is a C2 violation, and cutting there
        # could not "keep x_π" whichever side were taken.
        if not (lo <= wv <= hi and lo <= pv <= hi):
            continue
        if pv in hs:
            continue
        nb = cut_towards(lo, hi, wv, pv)
        if nb is None:
            continue
        nlo, nhi = nb
        before, after = coord_kept(lo, hi, hs), coord_kept(nlo, nhi, hs)
        if after <= 0 or after >= before:
            # A cut that removes nothing is not progress, and one that empties
            # the coordinate is not sound. Neither is a retreat either.
            continue
        if after == 1:
            # THE SECOND TRIGGER, stated in the method and easy to lose: a cut
            # that leaves one value IS a pin, so make it one explicitly and
            # carry on with the other coordinates rather than spending a round
            # to arrive at the same place with a worse report.
            retreat[name] = pv
            continue
        removed = before - after
        if best_removed is None or removed < best_removed:
            best_removed = removed
            best = (removed, name, nlo, nhi)
    if best is not None:
        return "cut", (best[1], best[2], best[3], best[0])
    if retreat:
        return "pin", retreat
    # Differences exist and none of them yields a cut: the refutation cannot be
    # answered on any coordinate it points at. THE FIRST TRIGGER.
    #
    # ---- x_pi HAS TO BE INSIDE THE PIECE, AND ON A SPLIT PIECE IT IS NOT ----
    #
    # §Certification's retreat "pins the coordinate to the value x_pi gives
    # it". That presupposes x_pi is a member of the set being pinned. On a
    # piece produced by S3's split it is not, BY CONSTRUCTION: the discarded
    # side of a cut becomes a piece precisely because it excludes the
    # counterexample the kept side holds. Pinning there does not narrow the
    # piece, it REPLACES it with a point lying outside it -- and since every
    # piece of one path retreats to the SAME x_pi value, all of them collapse
    # onto one set and the shrink budget is spent several times over on it.
    #
    # MEASURED, farming/deposit enc=3622 and enc=3623, three runs out of three
    # (the recorded arm, the --claim-budget arm, and that arm's control):
    # pieces 2, 3 and 4 each print
    #     [retreat piece N] PINNED amount==1157920892...639934 at its x_pi value
    # with byte-identical |R| and then run byte-identical walks. Piece 2's own
    # amount interval at that moment is [1, 3]; the value pinned into it is
    # ~1.16e77.
    #
    # ⚠ C3 CANNOT CATCH THIS, and that is worth stating because the invariant
    # looks like it should. Pinning a coordinate to one value makes |R|
    # SMALLER, and C3 is a size check -- a set leaving its own piece is
    # invisible to it. The guard has to live here, where the value is chosen.
    #
    # The CUT branch above has carried exactly this test all along
    # (`lo <= wv <= hi and lo <= pv <= hi`); only the retreat was missing it.
    fallback = {
        n: pv
        for n, pv, _wv in usable if n in box and n not in pins and box[n][0] != box[n][1]
        and box[n][0] <= pv <= box[n][1] and pv not in set(holes.get(n, ()))
    }
    if fallback:
        return "pin", fallback
    # ⛔ NOT "coords-gate". That kind means y and x_pi AGREE on every
    # coordinate, which is a statement about what the model can express; this
    # is the opposite -- they DO differ, and the differences all sit on
    # coordinates whose x_pi value this piece does not contain. Reporting it as
    # the coordinate gate would file an S3 bookkeeping outcome as an ESBMC
    # harvesting gap, which is the one confusion those two buckets exist to
    # prevent.
    outside = sorted(n for n, pv, _wv in usable
                     if n in box and n not in pins and not (box[n][0] <= pv <= box[n][1]))
    if outside:
        return "no-retreat", outside
    return "coords-gate", None


def shrink_target(log, pins):
    """The cut a refutation suggests, as (coord, lo, hi), or None.

    ⚠ KEPT FOR `--cut-policy tool`, which reproduces every recorded arm
    verbatim. `refutation_response` above is what the METHOD specifies and is
    the default; this reads the tool's first suggestion instead.

    A cut on a PINNED coordinate is refused. The pin is what the region is a
    statement ABOUT, so narrowing it silently swaps the slice the caller asked
    for. Measured: with the environment pinned, a refutation suggested a cut on
    block.number, and the loop merged it into the box beside the pin fixing
    block.number to a single value -- two contradictory constraints on one
    coordinate.
    """
    m = SHRINK_RE.search(log)
    if not m:
        return None
    coord = m.group(1)
    if coord in pins:
        return None
    return coord, int(m.group(2)), int(m.group(3))


def punch_targets(log, pins, box=None):
    """Values the refutation says can be PUNCHED OUT, as [(coord, value)].

    Same refusal rule as `shrink_target`: never a PINNED coordinate. The pin is
    what the region is a statement ABOUT, so removing a value from it silently
    changes the slice the caller asked for -- and a pin is a single value, so
    punching it would empty the coordinate outright.

    Also refuses a value outside the coordinate's current interval when `box` is
    given. The tool suggests against the box it was HANDED, and by the time the
    driver applies it the interval may already have been cut by another round;
    a hole outside the surviving interval removes nothing and would print beside
    the region as evidence about values it no longer contains.

    Returns [] when the log carries no suggestion, which is what keeps a
    SHRINK-only log behaving exactly as it did before this existed.
    """
    if "RESULT: UNSAFE" in log:
        return []
    m = PUNCH_LINE_RE.search(log)
    if not m:
        return []
    out = []
    for c, v in PUNCH_PAIR_RE.findall(m.group(1)):
        if c in pins:
            continue
        val = int(v)
        if box is not None and c in box:
            lo, hi = box[c]
            if val < lo or val > hi:
                continue
        out.append((c, val))
    return out


def cut_of(box, nb):
    """The single coordinate a suggested cut moved, or None.

    Read by DIFFING rather than by threading the tool's suggestion through
    `certify`'s return: the box the loop advances to is the only thing the rest
    of the loop trusts, so deriving the cut from it cannot drift from what was
    actually applied. None when the two differ on zero or several coordinates --
    the caller has a no-progress branch for the first and must not guess at the
    second.
    """
    d = [n for n in nb if box.get(n) != nb[n]]
    return d[0] if len(d) == 1 else None


def copy_holes(holes):
    """A DEEP copy of a punched set. `dict(holes)` is not one, and that matters.

    `holes` is `{coord: [int, ...]}`, so `dict(holes)` shares the LIST objects
    with the original -- and the punch branch mutates those lists in place
    (`setdefault` then `append` then `sort`). With S3, a piece enqueued with
    `dict(holes)` therefore keeps growing holes that its PARENT punches after
    the split: the piece is certified over a region carrying a hole it never
    derived, its printed region shows that hole, and the partition check uses it
    to mask a genuine overlap. It runs backwards too -- a piece recorded in
    `ok_holes` has its stored region mutated afterwards by a sibling's punch, so
    the final report can print a punched set that was never part of the query
    that certified it.

    Needs both `--max-region-pieces > 1` and `--max-holes > 0`, which is exactly
    the combination the S3 note names as the next thing to measure.
    """
    return {k: list(v) for k, v in (holes or {}).items()}


def split_on_cut(box, coord, lo, hi):
    """S3 -- a refutation's cut, as the KEPT piece plus the pieces it discards.

    Today the loop replaces `box` with the tool's suggested cut and throws the
    other side away. That side is not known to be outside the path's domain: the
    cut excludes ONE refuting witness, and the rest of the discarded side may be
    domain the path really has. Certification is a per-query judgement, so the
    UNION of several separately certified boxes is itself certified -- the
    representation is what cannot express a union, and a LIST of boxes can.

    Returns `(kept, rest)`. `kept` is the box with the coordinate narrowed to the
    suggestion; `rest` holds the at most two complement pieces, each a full box
    differing from the original in that one coordinate. `rest` is empty when the
    suggestion is not a proper sub-interval, which is the case the caller must
    keep behaving exactly as it did before -- a "cut" that removes nothing is not
    a split, it is a loop that failed to make progress, and the existing
    `nb == box` branch is what reports that.

    The suggestion is INTERSECTED with the current interval first. The tool
    suggests against the box it was handed, and by the time this runs the
    interval may already have been narrowed by an earlier round; a complement
    computed from an unclamped suggestion would hand back a piece reaching
    outside the region that was measured.
    """
    if coord not in box:
        return dict(box), []
    olo, ohi = box[coord]
    lo, hi = max(lo, olo), min(hi, ohi)
    if lo > hi:
        # The suggestion does not meet the current interval at all. No kept
        # piece is defensible, so report no split and let the caller's
        # no-progress branch speak.
        return dict(box), []
    kept = dict(box)
    kept[coord] = (lo, hi)
    rest = []
    if lo > olo:
        b = dict(box)
        b[coord] = (olo, lo - 1)
        rest.append(b)
    if hi < ohi:
        b = dict(box)
        b[coord] = (hi + 1, ohi)
        rest.append(b)
    return kept, rest


VIOLATED_HEAD_RE = re.compile(r"^Violated property:\s*$")


def violated_properties(log):
    """Every `Violated property:` block ESBMC printed, VERBATIM.

    ⛔ THIS QUOTES, IT DOES NOT CLASSIFY. The caller wants to know whether a
    refuted single-point query died on a compiler-inserted check or on a
    quantity outside the coordinate set, and the temptation is to answer that
    here with a keyword test over the block's text. A reader hard-wired to one
    vocabulary is a failure this file has already had twice: it fires on every
    run or on none, and from outside those two look identical. So the block is
    carried out whole and the judgement is left to whoever reads it.

    ESBMC prints

        Violated property:
          file X.sol line N column C function f
          <the property's own description>
          <the expression>

    and closes it with a blank line. A run may print more than one.

    RETURNS a list of strings. An EMPTY list is NOT "no property was violated":
    it is also what --result-only produces, and that is this driver's own
    default on every other query. The caller must say which of the two it is
    rather than reading the absence as a fact about the run.
    """
    out, cur = [], None
    for line in log.splitlines():
        if VIOLATED_HEAD_RE.match(line.rstrip()):
            if cur:
                out.append("\n".join(cur))
            cur = [line.rstrip()]
            continue
        if cur is not None:
            if not line.strip():
                out.append("\n".join(cur))
                cur = None
            else:
                cur.append(line.rstrip())
    if cur:
        out.append("\n".join(cur))
    return out


def certify(esbmc,
            sol,
            contract,
            unit,
            enc,
            depth,
            box,
            ce,
            pins,
            max_tx,
            timeout,
            cwd,
            ast=None,
            focus=None,
            memlimit="8g",
            holes=None,
            esbmc_args=(),
            state_structs=False,
            want_property=False,
            establish=None,
            param_types=None,
            state_types=None,
            extcall_coord_specs=None):
    """Step 5. Returns (verdict, suggested_box_or_None, witness).

    `holes` is Definition 5's punched set, and it must reach the query or the
    box certified is WIDER than the region reported. The subtraction now
    produces `[0, 2^160-1] \\ {255}` where it used to produce one side of 255;
    sending only the interval would ask about a region containing the sibling's
    own point, which is refutable by construction -- so the loop would shrink a
    region that was already correct, and the yield the hole bought would be
    given straight back.
    """
    holes = holes or {}

    def bound(n, lo, hi):
        b = {"name": n, "lo": str(lo), "hi": str(hi)}
        hs = sorted(holes.get(n, ()))
        if hs:
            b["holes"] = [str(h) for h in hs]
        return b

    spec = {
        "unit":
        unit,
        "enc":
        enc,
        "depth":
        depth,
        "ce": {
            k: str(v)
            for k, v in ce.items()
        },
        "box": [bound(n, lo, hi) for n, (lo, hi) in box.items()] + [{
            "name": n,
            "lo": str(v),
            "hi": str(v)
        } for n, v in pins.items()]
    }
    if establish:
        spec["establish"] = [{
            "target": target,
            "source": source
        } for target, source in sorted(establish.items())]
    path = os.path.abspath(os.path.join(cwd, "cert.json"))
    with open(path, "w") as f:
        json.dump(spec, f)
    # FRESHNESS, enforced by removal rather than by a timestamp. This run may
    # produce no report at all -- it can be refused at instrumentation time, or
    # killed -- and the previous shrink round left one right here. Reading that
    # would attribute an OLDER refutation's payload to this box, confidently and
    # with nothing to notice it by. Deleting first makes "no report" mean exactly
    # that, which the caller already distinguishes as the no-payload branch.
    stale = os.path.join(cwd, "cov-report.json")
    if os.path.exists(stale):
        os.remove(stale)
    write_generalise_progress(
        cwd,
        "certify-query-started",
        enc=enc,
        depth=depth,
        box=spec["box"],
        establish=spec.get("establish", []),
        timeout_s=timeout,
        want_property=want_property,
    )
    _t0 = time.time()
    proof_esbmc_args = k_induction_proof_args(esbmc_args)
    log = run(esbmc,
              sol,
              contract, ["--path-cov-certify", path, "--cov-report-json"],
              1,
              timeout,
              cwd,
              ast=ast,
              focus=focus,
              memlimit=memlimit,
              esbmc_args=proof_esbmc_args,
              result_only=not want_property)
    _wall = time.time() - _t0
    v = verdict(log)
    if v in ("UNKNOWN", "UNDECIDED_TRUNCATED"):
        # Keep the complete tool output for failed certification attempts. A
        # generic exit code is not enough to distinguish an ESBMC frontend
        # abort, solver failure, and a resource termination.
        diagnostic_path = os.path.join(cwd, f"singlepoint_enc{enc}_unknown.log")
        try:
            with open(diagnostic_path, "w", encoding="utf-8") as stream:
                stream.write(log)
        except OSError as exc:
            print(f"[certify] could not persist UNKNOWN log for enc={enc}: "
                  f"{exc}")
        error_lines = [
            line.strip() for line in log.splitlines() if line.lstrip().startswith("ERROR:")
        ]
        if error_lines:
            print(f"[certify] enc={enc} diagnostic: " + " | ".join(error_lines[-3:]))
    write_generalise_progress(
        cwd,
        "certify-query-finished",
        enc=enc,
        depth=depth,
        verdict=v,
        wall_s=round(_wall, 1),
        failure=round_failure_reason(log) if v == "UNKNOWN" else None,
    )
    why = "UNSAFE" if "RESULT: UNSAFE" in log else None
    # REFUSED IS NOT UNKNOWN. A query the tool declined to attempt because one
    # coordinate cannot be expressed is a fact about the SPEC, and it is fixable
    # by the caller -- unlike a crash or a timeout, which are facts about the
    # run. Folding it into UNKNOWN is what made the first corpus sweep report
    # every real unit as "ESBMC printed neither SUCCESSFUL nor FAILED".
    unexp = unexpressible_coords(log)
    if v != "FAILED":
        # SUCCESSFUL: certified. VACUOUS: the box admits nothing, so there is
        # nothing to cut. UNKNOWN: no verdict at all -- the caller must not
        # shrink on it, so no box and no punch are suggested either.
        #
        # AND WHEN IT IS UNKNOWN, SAY WHY. "ESBMC printed neither SUCCESSFUL nor
        # FAILED" is true and useless, and on the first full corpus sweep it was
        # the SECOND largest failure bucket -- 22 paths -- with no way to tell a
        # timeout from a crash from an unresolvable coordinate. The machinery to
        # name it already exists and was only ever applied to outer-box rounds.
        #
        # UNDECIDED_TRUNCATED carries the LOOP NAMES through the same channel,
        # because that is the whole difference between it and UNKNOWN: it names
        # the repair. "No verdict" is not actionable; "loop 62 at f:11 was cut
        # at the unwind bound" is a --unwindset away from one.
        if v == "UNKNOWN":
            why = round_failure_reason(log)
        elif v == "UNDECIDED_TRUNCATED":
            m = CERT_TRUNCATED_RE.search(log)
            why = (f"loop(s) cut at the unwind bound: {m.group(1).strip()}"
                   if m else "the tool did not name the truncated loop(s), which it "
                   "normally does -- read the run log rather than trusting "
                   "this line")
        return v, None, {}, [], unexp, why
    if want_property:
        # ---- THE WHOLE LOG, ON DISK, NAMED BY THE PATH IT BELONGS TO ----
        #
        # One file per (path, query) so a later reader cannot attribute one
        # refutation's trace to another -- the workdir is reused across rounds
        # and a single fixed filename would hand back whichever round wrote
        # last, confidently and with nothing to notice it by. Written before
        # anything is parsed out of it, so a parse that goes wrong still
        # leaves the evidence.
        try:
            with open(os.path.join(cwd, "singlepoint_enc%s.log" % enc), "w") as _lf:
                _lf.write(log)
        except OSError as _e:
            print("[certify] could not persist the single-point log for "
                  "enc=%s: %s -- the verdict below still stands, only the "
                  "trace behind it is unavailable" % (enc, _e))
        # THE THIRD STATE IS WRITTEN OUT, not left as an empty string. "No
        # block" and "a block saying X" are different findings, and only one
        # of them is a reason.
        blocks = violated_properties(log)
        if blocks:
            why = ("ESBMC's own `Violated property` block(s) on this "
                   "refutation, quoted: " + " || ".join(b.replace("\n", " / ") for b in blocks))
        else:
            why = ("ESBMC printed NO `Violated property:` block on this "
                   "refutation. The counterexample WAS requested for this "
                   "query, so the absence is a fact about the run rather than "
                   "about --result-only -- but it still leaves which cause "
                   "applies UNKNOWN, and is never evidence that no inserted "
                   "check was tripped")
    # Harvested on every refutation, not only when the shrink fails: the caller
    # needs it in the budget-exhausted branch too, and by then this run's report
    # has been overwritten by the next one.
    wit = witness_values(cwd,
                         unit,
                         state_structs=state_structs,
                         param_types=param_types,
                         state_types=state_types,
                         extcall_coord_specs=extcall_coord_specs)
    # BOTH suggestions are returned; WHICH to apply is the caller's policy. The
    # tool prints both and says outright that neither is strictly better --
    # punching converges only where the excluded set is a few points, a side cut
    # is what makes progress when the boundary is an interval. Deciding here
    # would put policy in the measurement path, which is the split this whole
    # script is built on.
    punches = punch_targets(log, pins, box)
    cut = shrink_target(log, pins)
    if cut is None:
        return v, None, wit, punches, unexp, why
    coord, lo, hi = cut
    nb = dict(box)
    nb[coord] = (lo, hi)
    return v, nb, wit, punches, unexp, why


def resolve_scope(scope, focus_flag, unit):
    """(scope label for the record, --focus-function argument or None).

    THE ALPHABET, NOT THE LENGTH. `--solidity-max-tx N` is how many entry calls
    a transaction sequence may make; `--focus-function` is which entries it may
    choose from. Until now this driver could only say "just this unit" or
    "everything", and the middle -- a SET -- is the configuration that matters:
    a unit whose interesting paths sit behind another unit's writes needs that
    other unit in the alphabet and nothing else.

    THE SPELLING IS MEASURED, not assumed, and only one of the two obvious ones
    works. On Tiny.sol at --solidity-max-tx 2:

      --focus-function deposit,withdraw          8 path claims (F 3 + F 5),
                                                 identical to no focus at all
      --focus-function deposit --focus-function withdraw
                                                 ERROR: option
                                                 '--focus-function' cannot be
                                                 specified more than once
      --focus-function withdraw                  5 claims, F 3 + 2
                                                 bounded-holds -- the
                                                 state-guarded paths are
                                                 unreachable under a
                                                 single-entry alphabet, which
                                                 is what Tiny.sol exists to
                                                 show
      --focus-function nosuchunit                exit 6, "is not a
                                                 public/external function of
                                                 contract 'Tiny'"

    The last cell is the negative control that makes the first meaningful: a
    comma list parsed as ONE name would land there, not on 8 claims.

    DEFAULTS ARE UNCHANGED. With neither flag the answer is `whole`/None, which
    is what `--focus` absent has always produced.
    """
    if scope is not None and focus_flag:
        raise SystemExit("[scope] --scope and --focus are two spellings of one decision, "
                         "and accepting both would make the recorded `scope` depend on "
                         "which this function reads first -- one fact in two ledgers, which "
                         "is the defect this project keeps paying for. Pass exactly one.")
    if scope is None:
        return ("focus" if focus_flag else "whole"), (unit if focus_flag else None)
    s = scope.strip()
    if s == "whole":
        return "whole", None
    if s == "focus":
        return "focus", unit
    names = [n.strip() for n in s.split(",") if n.strip()]
    if not names:
        raise SystemExit(f"[scope] --scope {scope!r} names no unit. Use 'whole', 'focus', "
                         f"or a comma-separated list of public/external function names.")
    if unit not in names:
        # The unit being generalised MUST be in its own alphabet. Otherwise the
        # dispatcher can never enter it, every one of its paths comes back
        # unit-not-entered, and the run reports "no witnessed path" -- which
        # reads as a property of the contract rather than of the command line.
        raise SystemExit(f"[scope] --scope names {', '.join(names)}, which does not include "
                         f"the unit being generalised ('{unit}'). The dispatcher could then "
                         f"never enter it, so every path would come back unwitnessed and "
                         f"the run would report that as a property of the contract. Add it "
                         f"to the set.")
    return "focus:" + ",".join(names), ",".join(names)


# ---- ONE DIRECTORY, ONE CONFIGURATION ---------------------------------------
#
# `enumerate_paths` already names this hazard in its own comment and cannot fix
# it from where it stands:
#
#   "The one that is NOT caught is the same unit re-run in the same workdir
#    under DIFFERENT flags -- another --max-tx, --focus on instead of off, a
#    rebuilt binary. Then the filter matches, the old (enc, depth, ce) triples
#    flow into the bracket, the refine rounds and every certification query, and
#    the whole result is about a configuration nobody asked for. Nothing
#    downstream could notice: an enc is just an integer."
#
# MEASURED that it is not hypothetical: pointing two runs at one --workdir, one
# at --max-tx 1 and one at --max-tx 2, both exited 0 and the second silently
# overwrote the first's generalise-result.json. The tx=1 answer (3 enumerated
# paths) and the tx=2 answer (5, two of them at a depth the tx=1 run cannot
# produce) are different measurements, and afterwards the directory claims only
# the later one -- with no record that it ever said anything else.
#
# THE BINARY IS PART OF THE CONFIGURATION. A rebuilt esbmc changes what an enc
# means as surely as a different --max-tx does, and this project has already
# hung old-build numbers on a new build's name without anything objecting. Its
# size and mtime are cheap and change on every rebuild.
RUN_CONFIG_SCHEMA = "solidity-path-generalise-config/3"


def file_identity(path):
    """Stable-enough identity for an input consumed by this run.

    Nanosecond mtime plus size catches rebuilt binaries and regenerated source
    files without hashing a several-hundred-megabyte executable before every
    unit.  The absolute path is part of the identity because two equal-looking
    basenames can be different flattened contracts.
    """
    if not path:
        return None
    resolved = path
    if os.sep not in path:
        resolved = shutil.which(path) or path
    abspath = os.path.realpath(resolved)
    try:
        st = os.stat(abspath)
        return {"path": abspath, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {"path": abspath, "size": None, "mtime_ns": None}


def _single_option(argv, flag):
    """Return one option value, or None; reject malformed manifests."""
    positions = [i for i, value in enumerate(argv) if value == flag]
    if not positions:
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise SystemExit(f"[enumerate-import] malformed {flag} in cmdArgv")
    return argv[positions[0] + 1]


def _recorded_argv(recorded):
    argv = recorded.get("cmdArgv")
    if isinstance(argv, list):
        return argv
    cmd = recorded.get("cmd")
    if isinstance(cmd, str):
        return shlex.split(cmd)
    return None


def _realpath_or_none(path):
    return os.path.realpath(path) if path else None


def validate_enumeration_import(index_path, report_path, esbmc, sol, ast, contract, unit,
                                scope_label, max_tx, memlimit, probe_witnesses, esbmc_args):
    """Fail closed unless a stage-1 report is this exact enumeration run."""
    if not index_path:
        raise SystemExit("[enumerate-import] --enumeration-report requires "
                         "--enumeration-index")
    try:
        with open(index_path) as stream:
            index = json.load(stream)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[enumerate-import] cannot read {index_path}: {exc}")
    try:
        with open(report_path) as stream:
            report_data = json.load(stream)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[enumerate-import] cannot read {report_path}: {exc}")

    mismatches = []

    def expect(name, actual, expected):
        if actual != expected:
            mismatches.append(f"{name}: report={actual!r}, requested={expected!r}")

    def memlimit_bytes(value):
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"\s*([0-9]+)\s*([bBkKmMgGtT]?)\s*", value)
        if not match:
            return None
        scale = {
            "": 1,
            "b": 1,
            "k": 1024,
            "m": 1024**2,
            "g": 1024**3,
            "t": 1024**4,
        }[match.group(2).lower()]
        return int(match.group(1)) * scale

    def expect_memlimit(name, actual, expected):
        if actual == expected:
            return
        actual_bytes = memlimit_bytes(actual)
        expected_bytes = memlimit_bytes(expected)
        if (actual_bytes is not None and expected_bytes is not None
                and expected_bytes >= actual_bytes):
            return
        mismatches.append(f"{name}: report={actual!r}, requested={expected!r}")

    if scope_label == "whole":
        collector_scope, focus_with, focus = "whole", [], None
    elif scope_label == "focus":
        collector_scope, focus_with, focus = "single", [], unit
    elif scope_label.startswith("focus:"):
        names = scope_label[len("focus:"):].split(",")
        focus_with = [name for name in names if name != unit]
        collector_scope, focus = "set", ",".join([unit] + focus_with)
    else:
        raise SystemExit(f"[enumerate-import] unknown scope {scope_label!r}")

    schema = index.get("schema")
    legacy = schema is None and "flatInput" in index and "runs" in index
    if schema not in ("veriput-pathcov-collection/2", None) or (schema is None and not legacy):
        expect("schema", schema, "veriput-pathcov-collection/2")

    config = index.get("config") or {}
    if legacy:
        expect("source", _realpath_or_none(index.get("flatInput")), _realpath_or_none(sol))
        print("[enumerate-import] using legacy stage-1 index without "
              "collection/2 identity fields; validating command shape and "
              "unit report instead")
    else:
        expect("schema", schema, "veriput-pathcov-collection/2")
        expect("source", index.get("flatInputIdentity"), file_identity(sol))
        expect("AST", index.get("astInputIdentity"), file_identity(ast))
        expect("ESBMC binary", index.get("esbmcIdentity"), file_identity(esbmc))
        expect("probe witnesses", config.get("probeWitnesses"), probe_witnesses)

    expect("contract", (index.get("primary") or {}).get("name"), contract)
    expect("unit set", config.get("onlyUnits"), [unit])
    expect("max-tx", config.get("solidityMaxTx"), max_tx)
    expect_memlimit("memlimit", config.get("memlimit"), memlimit)
    expect("solver/ESBMC flags", config.get("solverFlags"), list(esbmc_args))
    expect("scope", config.get("scope"), collector_scope)
    expect("focus-with", config.get("focusWith"), focus_with)
    expect("instrument-only-unit", config.get("instrumentOnlyUnit"), collector_scope == "set")

    report = os.path.abspath(report_path)
    reports_dir = os.path.abspath(index.get("reportsDir", ""))
    if os.path.dirname(report) != reports_dir:
        mismatches.append(f"report directory: {report!r} is not under "
                          f"manifest reportsDir {reports_dir!r}")
    tag = os.path.splitext(os.path.basename(report))[0]
    matching = [run for run in index.get("runs", []) if run.get("tag") == tag]
    if len(matching) != 1:
        mismatches.append(f"run record: expected one tag {tag!r}, found "
                          f"{len(matching)}")
    else:
        recorded = matching[0]
        expect("run function", recorded.get("function"), unit)
        expect("report present", recorded.get("reportPresent"), True)
        expect("outer timeout", recorded.get("killedByOuterTimeout"), False)
        argv = _recorded_argv(recorded)
        if not isinstance(argv, list) or len(argv) < 2:
            mismatches.append("cmdArgv/cmd: absent or malformed")
        else:
            if legacy:
                expect("command binary path", _realpath_or_none(argv[0]), _realpath_or_none(esbmc))
                expect("command AST path", _realpath_or_none(argv[1]), _realpath_or_none(ast))
            else:
                expect("command binary", file_identity(argv[0]), file_identity(esbmc))
                expect("command AST", file_identity(argv[1]), file_identity(ast))
            command_source = _single_option(argv, "--sol")
            expect("command source", _realpath_or_none(command_source), _realpath_or_none(sol))
            expect("command contract", _single_option(argv, "--contract"), contract)
            expect("command max-tx", _single_option(argv, "--solidity-max-tx"), str(max_tx))
            expect_memlimit("command memlimit", _single_option(argv, "--memlimit"), memlimit)
            expect("command focus", _single_option(argv, "--focus-function"), focus)
            expect("command instrument-only", _single_option(argv, "--path-cov-instrument-only"),
                   unit if collector_scope == "set" else None)
            expected_witnesses = (str(probe_witnesses) if probe_witnesses else None)
            legacy_probe_missing = legacy and probe_witnesses and ("--path-cov-probe" not in argv)
            if legacy_probe_missing:
                print("[enumerate-import] legacy report has no "
                      "--path-cov-probe / --all-witnesses provenance; witness "
                      "pool widening is unavailable, so the later probe stage "
                      "may report a one-vector limitation")
            else:
                expect("command witnesses", _single_option(argv, "--max-witnesses"),
                       expected_witnesses)
                expect("command all-witnesses", "--all-witnesses" in argv, bool(probe_witnesses))
                expect("command path probe", "--path-cov-probe" in argv, bool(probe_witnesses))
                expect("command branch-function probe", "--branch-function-coverage" in argv,
                       bool(probe_witnesses))
            expect("command solver/ESBMC flags", all(flag in argv for flag in esbmc_args), True)

    report_claims = report_data.get("claims") if isinstance(report_data, dict) \
        else None
    if not isinstance(report_claims, list):
        mismatches.append("unit report: claims absent or malformed")

    if mismatches:
        raise SystemExit("[enumerate-import] REFUSING incompatible stage-1 "
                         "report:\n  " + "\n  ".join(mismatches))


def arg_value(args, name, default=None):
    """Read a CLI value while keeping pure-function test Namespaces concise."""
    return getattr(args, name, default)


def run_config(args, scope_label):
    """The fields that change WHAT IS MEASURED, as a comparable dict."""
    # Every field is written even at its default.  An absent key in a v2 stamp
    # is an unknown configuration, not permission to substitute today's
    # default.  Lists that are semantic sets are sorted; raw ESBMC arguments
    # retain order because option order can affect command-line parsing.
    return {
        "schema":
        RUN_CONFIG_SCHEMA,
        "contract":
        arg_value(args, "contract"),
        "unit":
        arg_value(args, "unit"),
        "path_function":
        arg_value(args, "path_function"),
        "max_tx":
        arg_value(args, "max_tx", 1),
        "scope":
        scope_label,
        "esbmc":
        file_identity(arg_value(args, "esbmc", "esbmc")),
        "sol":
        file_identity(arg_value(args, "sol")),
        "ast":
        file_identity(arg_value(args, "ast")),
        "probes":
        arg_value(args, "probes", 16),
        "refine_rounds":
        arg_value(args, "refine_rounds", 3),
        "shrink_rounds":
        arg_value(args, "shrink_rounds", 4),
        "safety_retreat_after_tiny_cuts":
        arg_value(args, "safety_retreat_after_tiny_cuts", 2),
        "witness_check":
        bool(arg_value(args, "witness_check", True)),
        "cut_policy":
        arg_value(args, "cut_policy", "spec"),
        "max_region_pieces":
        arg_value(args, "max_region_pieces", 1),
        "max_holes":
        arg_value(args, "max_holes", 0),
        "timeout":
        arg_value(args, "timeout", 900),
        "ce_collection_only":
        bool(arg_value(args, "ce_collection_only", False)),
        "memlimit":
        arg_value(args, "memlimit", "8g"),
        "env_coords":
        sorted(arg_value(args, "env_coord", []) or []),
        "claim_budget":
        arg_value(args, "claim_budget", 0),
        "level0":
        bool(arg_value(args, "level0", False)),
        "level0_perturb":
        bool(arg_value(args, "level0_perturb", False)),
        "skip_bracket":
        bool(arg_value(args, "skip_bracket", False)),
        "probe_witnesses":
        arg_value(args, "probe_witnesses", 0),
        "probe_ladder":
        bool(arg_value(args, "probe_ladder", False)),
        "probe_ladder_budget":
        arg_value(args, "probe_ladder_budget", 0),
        "no_auto_pin_value":
        bool(arg_value(args, "no_auto_pin_value", False)),
        "pin_env":
        bool(arg_value(args, "pin_env", False)),
        "env_coord_disagreed":
        bool(arg_value(args, "env_coord_disagreed", False)),
        "pin_agreed_establishable_env":
        bool(arg_value(args, "pin_agreed_establishable_env", False)),
        "pin_agreed_state":
        bool(arg_value(args, "pin_agreed_state", False)),
        "slot_coords":
        arg_value(args, "slot_coords", 0),
        "slot_dependency_policy":
        SLOT_DEPENDENCY_POLICY,
        "slot_coord":
        sorted(arg_value(args, "slot_coord", []) or []),
        "pins":
        sorted(arg_value(args, "pin", []) or []),
        "pin_extcall":
        bool(arg_value(args, "pin_extcall", False)),
        "static_extcall_inseparable":
        bool(arg_value(args, "static_extcall_inseparable", False)),
        "static_uncontrolled_inseparable":
        bool(arg_value(args, "static_uncontrolled_inseparable", False)),
        "esbmc_args":
        list(arg_value(args, "esbmc_arg", []) or []),
        "state_struct_fields":
        bool(arg_value(args, "state_struct_fields", False)),
        "enumeration_index":
        file_identity(arg_value(args, "enumeration_index")),
        "enumeration_report":
        file_identity(arg_value(args, "enumeration_report")),
        "allow_recursive_helper_enumeration":
        bool(arg_value(args, "allow_recursive_helper_enumeration", False)),
    }


def stamp_workdir(cwd, cfg):
    """Refuse to reuse a directory that holds another configuration's output.

    Fails CLOSED. The alternative -- warn and continue -- leaves the stale
    artefacts on disk under the new configuration's name, which is the exact
    state this check exists to make impossible.
    """
    path = os.path.join(cwd, "run-config.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        if old is not None:
            diff = [k for k in sorted(set(old) | set(cfg)) if old.get(k) != cfg.get(k)]
            if diff:
                lines = "\n".join(f"    {k}: previously {old.get(k)!r}, now {cfg.get(k)!r}"
                                  for k in diff)
                raise SystemExit(f"[workdir] REFUSING to reuse {cwd}: it holds the output "
                                 f"of a DIFFERENT configuration.\n{lines}\n"
                                 f"  A path identity (enc, depth) means something different "
                                 f"under each, and every artefact here -- cov-report.json, "
                                 f"outer.json, cert.json, generalise-result.json -- is "
                                 f"overwritten in place, so a mixed directory yields a "
                                 f"result whose provenance cannot be stated. Point --workdir "
                                 f"somewhere else, or delete this one on purpose.")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esbmc", default="esbmc")
    ap.add_argument("--sol", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--ce-collection-only",
                    action="store_true",
                    help="run only path enumeration, then persist concrete "
                    "counterexamples and exit. This mode never certifies "
                    "a region and never treats a witness as a valid test.")
    ap.add_argument("--probes", type=int, default=16)
    ap.add_argument("--refine-rounds", type=int, default=3)
    ap.add_argument("--shrink-rounds",
                    type=int,
                    default=4,
                    help="how many refutations one PIECE may absorb before it "
                    "is given up. Per piece, not per path: with "
                    "--max-region-pieces above 1 the first piece would "
                    "otherwise spend the whole path's budget.")
    ap.add_argument("--safety-retreat-after-tiny-cuts",
                    type=int,
                    default=2,
                    help="for `RESULT: UNSAFE` certification refutations, pin "
                    "a coordinate at x_pi after this many consecutive "
                    "one-value cuts on that same coordinate, but only if "
                    "another non-environment coordinate remains wide. "
                    "This is the product-region fallback for relational "
                    "checked-arithmetic domains such as x + y <= UINT_MAX; "
                    "set 0 to disable it.")
    ap.add_argument("--no-witness-check",
                    dest="witness_check",
                    action="store_false",
                    default=True,
                    help="do NOT put §Certification's single-point query on a "
                    "path whose region collapsed to its witness.\n"
                    "⛔ ON BY DEFAULT, and unlike --level0 / --max-holes / "
                    "--max-region-pieces this is not a policy knob. Path "
                    "enumeration deliberately keeps the compiler-inserted "
                    "arithmetic and bounds checks OUT of a path's identity; "
                    "certification turns them back on. A witness that trips "
                    "one was therefore never asked about, and the concrete "
                    "replay test built from it reverts on the real "
                    "contract. The method: 'A witness that fails it "
                    "receives no test and is reported with the path.' This "
                    "is the only route by which the pipeline can deliver a "
                    "RED test.\n"
                    "COST: one query per path that did not certify. Every "
                    "quantity in it is fixed, which is why the method calls "
                    "the answer a matter of evaluation rather than a second "
                    "search. Pass this flag to reproduce a recorded arm, "
                    "which had no such query, and accept that its concrete "
                    "replay tests are uncleared.")
    ap.add_argument("--cut-policy",
                    choices=("spec", "tool"),
                    default="spec",
                    help="how a refutation's cut is chosen.\n"
                    "`spec` (DEFAULT) follows §Certification: every "
                    "coordinate on which the witness and x_pi differ "
                    "offers a sound cut -- keep the side of y_c that x_pi "
                    "lies on -- and the one REMOVING THE FEWEST VALUES is "
                    "taken. Where no coordinate can be cut, or where "
                    "cutting would leave one value, that coordinate is "
                    "PINNED at x_pi and the loop carries on with the "
                    "others (partial generalisation, which the method "
                    "calls the common outcome).\n"
                    "`tool` is the PREVIOUS behaviour and is kept only so "
                    "the recorded arms reproduce verbatim: it takes the "
                    "FIRST `retry with ...` line the tool printed, applies "
                    "it unread, makes no comparison across candidates, and "
                    "gives the path up entirely when that first suggestion "
                    "names a pinned coordinate.\n"
                    "⛔ The default is NOT the conservative one here, and "
                    "deliberately so. Everywhere else in this file a new "
                    "policy is off by default because the method does not "
                    "choose between the alternatives; here it does, and a "
                    "default that keeps deviating from the frozen method "
                    "IS the defect. Recorded numbers are reproduced with "
                    "--cut-policy tool, not by leaving the deviation in "
                    "place.")
    ap.add_argument("--max-region-pieces",
                    type=int,
                    default=1,
                    help="how many BOXES one path's region may be reported as. "
                    "A refutation's cut splits the box into the side the "
                    "tool suggests keeping and the side(s) it discards, "
                    "and the discarded side is NOT known to be outside the "
                    "path's domain -- the cut excludes one refuting "
                    "witness, not everything beyond it. Certification is a "
                    "per-query judgement, so the UNION of separately "
                    "certified boxes is itself certified; it is the "
                    "REPRESENTATION (Definition 6: a region is a product "
                    "of per-coordinate sets) that cannot hold a union, and "
                    "a list of boxes can.\n"
                    "DEFAULT 1, i.e. OFF -- the discarded side is thrown "
                    "away exactly as before, so every existing number is "
                    "reproduced verbatim. Same house rule as --level0 and "
                    "--max-holes, and for the same reason: keeping both "
                    "sides changes what a default run reports, and it "
                    "costs queries -- worst case pieces x shrink-rounds "
                    "certification runs for one path.\n"
                    "A piece that does NOT contain the path's own "
                    "counterexample is certified on the strength of the "
                    "tool's non-vacuity witness alone (the C2 membership "
                    "check has no known member to use there), and every "
                    "such piece says so on its own line.")
    ap.add_argument("--max-holes",
                    type=int,
                    default=0,
                    help="per coordinate, how many values the loop may PUNCH "
                    "OUT (Definition 5) before it falls back to a side "
                    "cut. The tool prints a PUNCH suggestion whenever the "
                    "refuting witness sits strictly inside the interval, "
                    "and a hole is the better cut where it applies: it "
                    "removes ONE value, while a side cut removes the whole "
                    "side that does not hold this path's counterexample -- "
                    "so WHICH side survives is decided by a value the "
                    "solver picked, not by the method. Measured on one "
                    "address coordinate: the same region came out as "
                    "[256, 2^160-1] or [0, 254] depending only on the "
                    "sibling's counterexample, a factor of 5.7e45, while a "
                    "hole gives [0, 2^160-1] \\ {v} either way. It is NOT "
                    "strictly better, which is why this is a budget and "
                    "not a switch: against a boundary that is an INTERVAL "
                    "a punch removes one value per round forever, where a "
                    "side cut crosses it in one. DEFAULT 0, i.e. OFF, "
                    "which reproduces every existing number verbatim -- "
                    "the same house rule --level0 follows, and for the "
                    "same reason: this is POLICY, the tool itself says "
                    "neither cut is strictly better, and a policy that "
                    "silently changes what a default run reports is a "
                    "policy nobody chose. Raise it to opt in.")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--ast",
                    default=None,
                    help="prebuilt .solast, passed positionally. Needed for "
                    "any source whose pragma pins a solc this machine "
                    "does not have -- every flattened benchmark input "
                    "does.")
    ap.add_argument("--scope",
                    default=None,
                    metavar="whole|focus|a,b,c",
                    help="which entries the harness dispatcher may call -- the "
                    "ALPHABET of the transaction sequence, where --max-tx "
                    "is its LENGTH. `whole` lets it call anything (the "
                    "default, and what --focus absent has always meant); "
                    "`focus` narrows it to --unit; a comma-separated list "
                    "narrows it to that SET, which is the case neither of "
                    "the other two covers and the one that matters for a "
                    "unit whose interesting paths sit behind another "
                    "unit's writes. MEASURED on Tiny.sol at --max-tx 2: "
                    "`--scope withdraw` witnesses 3 paths and leaves 2 "
                    "bounded-holds, `--scope deposit,withdraw` witnesses "
                    "all 8, identically to `whole`. Mutually exclusive "
                    "with --focus, which is the older spelling of "
                    "`--scope focus`.")
    ap.add_argument("--focus",
                    action="store_true",
                    help="narrow the harness dispatcher to --unit. Does NOT "
                    "change the enumeration (verified by comparing "
                    "content-addressed path key sets, not just counts); "
                    "it is a pure scope control, and on a real contract "
                    "it is the difference between finishing in seconds "
                    "and exceeding a 900s budget with nothing to show.")
    ap.add_argument("--memlimit",
                    default="8g",
                    help="passed to ESBMC. Keep it at or below whatever the "
                    "caller computed for the machine; this used to be "
                    "hardcoded, so a caller's limit was a line nobody "
                    "read.")
    ap.add_argument("--env-coord",
                    action="append",
                    default=[],
                    help="promote one environment quantity (e.g. "
                    "block.timestamp, block.chainid, or tx.gasprice) to "
                    "a FREE coordinate instead of a "
                    "pin. Named one at a time on purpose: ladder cost is "
                    "multiplicative in the coordinate count.\n"
                    "This exists because the blanket 'never probe the "
                    "environment' rule blocks exactly the cases that "
                    "matter. The environment quantities the paths DISAGREE "
                    "on are the DISCRIMINATING ones -- a timelocked "
                    "withdraw has its paths separated by block.timestamp, "
                    "so pinning it is impossible (the paths disagree by "
                    "construction) and dropping it leaves the guard "
                    "unconstrained, which refuses certification. Such a "
                    "coordinate has to be probed.")
    ap.add_argument("--claim-budget",
                    type=int,
                    default=0,
                    help="cap the number of CLAIMS a GEOMETRIC-BRACKET round "
                    "emits, thinning each coordinate's ladder evenly to "
                    "fit.\n"
                    "⛔ IT DOES NOT REACH A REFINE ROUND, AND THE PREVIOUS "
                    "WORDING ('an outer-box round') SAID IT DID. In "
                    "`outer_round`, `budget_probe_values` is applied to "
                    "`geo`, and `geo` is populated ONLY inside the `if "
                    "geometric:` branch -- a refine round leaves it empty, "
                    "so `if geo:` is false and nothing is thinned. Nor "
                    "could it be: on a refine round the driver sends `lo` "
                    "and `hi` and the TOOL lays the values, so there is no "
                    "list here to thin. The only lever on a refine round's "
                    "size is --probes.\n"
                    "THAT MATTERS BECAUSE THE REFINE ROUND IS THE ONE THAT "
                    "BINDS on a real unit. MEASURED, farming/startFarming "
                    "under --skip-bracket (so the bracket never ran at "
                    "all): the refine round emitted 6 coords x 10 values x "
                    "26 paths x 2 directions = 3120 probes, answered ~1552 "
                    "of them in 180.1s -- median query 0.064s, max 0.129s, "
                    "77.1s of it solving -- and was cut by the "
                    "per-invocation timeout with ROUND MEASURED NOTHING. A "
                    "reader who set --claim-budget to rescue that round "
                    "would have got a silent no-op, which is this "
                    "project's own always-empty-channel shape: a flag that "
                    "cannot fire looks exactly like one that fired and "
                    "found nothing.\n"
                    "OFF by default (0), and deliberately so: the right "
                    "value has to be MEASURED and has not been.\n"
                    "What is measured is that the round is EMISSION-bound "
                    "rather than solve-bound -- on EscrowSrc.withdraw a "
                    "geometric bracket over 6 coordinates and 5 paths put "
                    "only 148 queries in front of the solver in 300s, of "
                    "which 6.9s was solving, while ~15000 claims were "
                    "being instrumented. So a budget expressed in solver "
                    "time or in probes-answered would bound the wrong "
                    "quantity.\n"
                    "STATE THE COUNTING UNIT BEFORE QUOTING A RATE. An "
                    "earlier version of this help said the rate did not "
                    "reproduce -- 64 'claims' in 16.6s against 192 in "
                    "14.1s -- and that was two different units compared: "
                    "64 LADDER PROBES against 192 VCCs. In VCCs it is "
                    "monotonic and consistent, 0.065 and 0.073 s/VCC.\n"
                    "The multiplier matters here: on that unit one ladder "
                    "probe became FOUR VCCs. This flag caps ladder VALUES, "
                    "so what it bounds and what costs differ by a per-unit "
                    "factor the driver cannot see.\n"
                    "What IS measured: GOTO creation and symex are flat in "
                    "the claim count; slicing and encoding are negligible "
                    "(0.58s across 192 claims); about half the wall clock "
                    "is solving and the other half is UNACCOUNTED work "
                    "ESBMC does not timestamp. Nothing identifies that "
                    "half yet.\n"
                    "So pick a budget by MEASURING your own round (halve "
                    "the ladder, see whether it finishes), not by "
                    "arithmetic on a rate. That is why this is off by "
                    "default.")
    ap.add_argument("--level0",
                    action="store_true",
                    help="try LEVEL 0 first: one batch whose candidate list "
                    "per coordinate is the set of values the siblings' "
                    "own counterexamples take there (proposition 9 -- no "
                    "extra query, no catalogue of constants). A "
                    "coordinate that comes back a single point for EVERY "
                    "witnessed path is equality-type and skips the "
                    "geometric ladder; everything else descends to level "
                    "2 with its mechanism unchanged.\n"
                    "Off by default, so every existing number is "
                    "reproduced verbatim without it.\n"
                    "SCOPE: `coordinate == constant` only. `coordinate A "
                    "== coordinate B` is a cross-coordinate relation, "
                    "changes definition 6, and is an open method-layer "
                    "item -- it is not attempted here.")
    ap.add_argument("--level0-perturb",
                    action="store_true",
                    help="LEVEL 0b: after the level-0 round, re-probe every "
                    "coordinate whose point came from a ONE-VALUE "
                    "candidate list, at that value's NEIGHBOURS (v-1, "
                    "v+1), clamped to the coordinate's published TYPE "
                    "RANGE.\n"
                    "WHY IT EXISTS: level 0 asks `c <= v` and `c >= v`. "
                    "Both hold when the domain really is {v} -- and both "
                    "ALSO hold, at ANY v, when the antecedent is "
                    "UNSATISFIABLE, because every probe then holds "
                    "vacuously. From one value the two are "
                    "indistinguishable, and the vacuous case renders as a "
                    "tight, confident-looking point box. The round already "
                    "prints that warning per path; this is the action it "
                    "names ('try a second value on those coordinates').\n"
                    "WHY A SECOND ROUND: the neighbours must be clamped to "
                    "the type range, and the type range is published BY "
                    "the level-0 round -- probing outside the type wraps, "
                    "and a wrapped probe would MANUFACTURE the 'both "
                    "directions hold' verdict. Level 0 is measured at "
                    "6.6-7.5s on a real unit, so the extra round is cheap "
                    "against the geometric bracket that follows it.\n"
                    "It DECIDES nothing new: it widens the candidate list "
                    "so the EXISTING empty-region guard can fire. Requires "
                    "--level0. Off by default, because it changes the "
                    "candidate list of every unit and therefore what every "
                    "recorded region is a statement about.")
    ap.add_argument("--skip-bracket",
                    action="store_true",
                    help="skip round 1 and start refining from each "
                    "coordinate's full type range. This is NOT a new "
                    "policy: it is the branch the code already takes when "
                    "the bracket yields nothing (`brackets_for(...) or "
                    "(0, UINT256_MAX)`), made reachable without first "
                    "paying for the bracket. Round 1 ignores --probes and "
                    "lays one candidate per power of two -- 258 per "
                    "coordinate per direction -- which on a real contract "
                    "unit does not finish. Cost: the first refine round "
                    "starts at full-type resolution, so separation now "
                    "depends on the counterexamples being far apart "
                    "rather than on a measured bracket.")
    ap.add_argument("--probe-witnesses",
                    type=int,
                    default=0,
                    metavar="N",
                    help="ask the ENUMERATION run for up to N distinct inputs "
                    "per path instead of one (--all-witnesses "
                    "--max-witnesses N), and use the extra ones as KNOWN "
                    "MEMBERS of that path's domain.\n"
                    "WHAT IT BUYS, and the two halves are different:\n"
                    "  * UNCONDITIONAL: a coordinate that takes more than "
                    "one value across a path's witnesses is PROVED not to "
                    "be a point, before any query. The one-value "
                    "blindness --level0-perturb exists to break is simply "
                    "absent on those coordinates.\n"
                    "  * CONDITIONAL: where every path in the batch has "
                    "members bracketing the same interval, every ladder "
                    "rung strictly inside it has an answer that is "
                    "already on disk (both directions refuted, by the "
                    "members themselves), so those rungs are dropped. "
                    "Paths whose domains do not overlap yield nothing "
                    "here, and the round says so rather than staying "
                    "silent.\n"
                    "The extreme members and their immediate neighbours "
                    "are always KEPT on the ladder: they are what stops "
                    "the bracket widening (the largest refuted probe is "
                    "then at least the largest known member), and the "
                    "neighbours are the perturbation that tells a WALL "
                    "from a HOLE at the boundary.\n"
                    "COST: no extra run. The enumeration happens anyway; "
                    "this is one flag on it. The witnesses arrive already "
                    "attributed, because under --solidity-path-coverage a "
                    "refuted claim IS a path.\n"
                    "DEFAULT 0, i.e. OFF, so every recorded number "
                    "reproduces verbatim -- the same house rule --level0, "
                    "--max-holes, --slot-coords and --state-struct-fields "
                    "follow. It changes the ladder, and therefore what "
                    "every region was measured by.")
    ap.add_argument("--probe-ladder",
                    action="store_true",
                    help="lay the GEOMETRIC BRACKET round's ladder PER PATH, "
                    "anchored at that path's own known members and "
                    "doubling OUTWARD, instead of one shared ladder "
                    "anchored at zero. Requires --probe-witnesses.\n"
                    "WHY: the shared ladder's rungs are 0, 1, 2, 4, ... "
                    "2^k -- anchored at a value with no evidence attached "
                    "to it. MEASURED on P14_Ladder/bump, whose enc=7 "
                    "domain is `amt in [10, 20]` with the separation at "
                    "21: from zero the nearest rungs are 16 and 32, so "
                    "the bracket is (16, 32]; anchored at the known "
                    "member 20 the first rung IS 21 and the bracket is "
                    "(20, 21] -- exact, in the same batch. Same queries, "
                    "better places, and the places come from evidence the "
                    "enumeration already produced.\n"
                    "It needs the TOOL side: one shared `coords` list "
                    "cannot say where one path's rungs go, and two paths "
                    "of a unit are separated precisely by the coordinate "
                    "being measured, so their known domains are disjoint "
                    "on it by construction. The spec therefore carries "
                    "`paths[].coords[].values`, which REPLACES the shared "
                    "ladder for that (path, coordinate).\n"
                    "⛔ REQUIRES A PUBLISHED TYPE RANGE for the "
                    "coordinate, i.e. --level0 (which publishes them). "
                    "Without one the outward rungs would run past the "
                    "type, wrap, and measure different numbers; such a "
                    "coordinate is left on the shared ladder and named.\n"
                    "GEOMETRIC ROUND ONLY. The refine rounds send `lo`/"
                    "`hi` and the TOOL lays the values inside them; "
                    "freezing a per-path list there would fix the "
                    "resolution the refine loop exists to improve.\n"
                    "Off by default, same house rule as every other "
                    "ladder-changing flag here.")
    ap.add_argument("--probe-ladder-budget",
                    type=int,
                    default=0,
                    metavar="N",
                    help="keep only the N rungs NEAREST the member bracket on "
                    "each side of a per-path ladder, and print how many "
                    "were dropped. DEFAULT 0 = uncapped, i.e. the ladder "
                    "as it was laid before this flag existed.\n"
                    "MEASURED, farming/deposit: uncapped, the per-path "
                    "ladder laid 5264 rungs across 24 (path, coordinate) "
                    "pairs -- 259 for a uint256 anchored at [0, 1] -- and "
                    "the solver batch carrying them did not return in "
                    "780s; the run died as `[run] TIMEOUT after 900s` "
                    "inside that round, while the arm skipping the round "
                    "finished the same unit in 281s. Anchoring per path "
                    "changed WHERE the rungs go, not HOW MANY, and on a "
                    "256-bit coordinate the count is what binds.\n"
                    "⛔ IT IS A LOSS, and it is printed as one: a boundary "
                    "beyond the last kept rung is no longer bracketed and "
                    "comes back as a span reaching the type limit, which "
                    "the refine round then bisects. The anchors and both "
                    "type limits are never dropped -- without the limits "
                    "the coordinate is half-open and the subtraction is "
                    "blocked outright.")
    ap.add_argument("--no-auto-pin-value",
                    action="store_true",
                    help="do NOT pin msg.value to 0 on a unit the source "
                    "declares non-payable. The pin is ON by default, and "
                    "unlike --level0 / --max-holes / --max-region-pieces "
                    "this default is deliberately NOT the conservative "
                    "one, because it is not a policy: a non-payable "
                    "function's ABI gate reverts every call carrying "
                    "value, so no input with msg.value != 0 reaches the "
                    "body and pinning it excludes nothing reachable. "
                    "MEASURED, same contract and command apart from the "
                    "environment: 0 of 5 paths certified unconstrained, "
                    "4 of 5 with it. What the pin DOES exclude is the "
                    "ABI-gate revert path itself, whose whole domain is "
                    "msg.value != 0; its region is then reported EMPTY. "
                    "Pass this flag to get that path back and lose the "
                    "others.")
    ap.add_argument("--pin-env",
                    action="store_true",
                    help="pin each msg./tx./block. quantity on which every "
                    "witnessed path agrees, at that value. Off by default "
                    "because it changes what every region MEANS -- each "
                    "becomes a statement about that environment slice, "
                    "which is printed with it. Measured effect: without "
                    "it a non-payable function certifies nothing, because "
                    "its ABI gate is a decision on an unconstrained "
                    "msg.value.")
    ap.add_argument("--env-coord-disagreed",
                    action="store_true",
                    help="promote every PUT-ESTABLISHABLE environment quantity "
                    "the witnessed paths DISAGREE on to a free coordinate, "
                    "instead of requiring each to be named with "
                    "--env-coord. The establishable set is imported from "
                    "the PUT emitter; unsupported block./tx. quantities "
                    "remain named as unsupported because a certified "
                    "region the test cannot enter is not a PUT.\n"
                    "WHY: --pin-env already computes this exact partition "
                    "and already prints of the disagreeing side 'Left "
                    "unconstrained, so a path guarded by one of these "
                    "cannot certify.' The repair it names was the "
                    "operator's job; this applies it. A quantity the "
                    "paths disagree on is DISCRIMINATING by construction "
                    "-- pinning it is impossible and dropping it leaves "
                    "the guard unconstrained -- so it is the one kind of "
                    "environment quantity that has to be probed.\n"
                    "⛔ ANYTHING ALREADY PINNED IS SKIPPED, and msg.value "
                    "is why. On a non-payable unit the ABI-gate path's "
                    "counterexample carries a nonzero value while every "
                    "other path carries 0, so msg.value DISAGREES -- and "
                    "promoting it would cancel the auto-pin whose "
                    "measured effect is 0 of 5 paths certified against 4 "
                    "of 5.\n"
                    "Off by default: it changes the coordinate set, and "
                    "ladder cost is multiplicative in the coordinate "
                    "count.")
    ap.add_argument("--pin-agreed-establishable-env",
                    action="store_true",
                    help="pin every PUT-ESTABLISHABLE environment quantity on "
                    "which all witnessed paths agree. This is narrower "
                    "than --pin-env: unsupported environment quantities "
                    "such as tx.origin or msg.data stay unconstrained, so "
                    "a certified region remains generatable as a PUT. "
                    "Use with --env-coord-disagreed to split the "
                    "environment into free discriminators and reproducible "
                    "pins.")
    ap.add_argument("--pin-agreed-state",
                    action="store_true",
                    help="pin every STATE coordinate on which all witnessed "
                    "paths' counterexamples agree, at that value -- the "
                    "mirror of --pin-env, for state variables instead of "
                    "the environment.\n"
                    "WHY IT IS NOT A LOSS OF YIELD: the entry state is "
                    "never havoc'd, so a region bound on a state variable "
                    "is assumed against a value the constructor already "
                    "fixed, and the emitter DROPS every such bound wider "
                    "than a point rather than establish it. A wide state "
                    "coordinate therefore cannot reach the emitted test "
                    "under any circumstances; leaving it free only gives "
                    "the shrink loop a degree of freedom to produce "
                    "refuting witnesses on.\n"
                    "MEASURED, farming/setDistributor: with msg.sender "
                    "promoted but state._owner left free, all five paths "
                    "end 'shrink round budget exhausted; the witness "
                    "differs ... state._owner (path=1, witness=8119...)' "
                    "and 0 certify in 347.5s. Pinning it certifies 4 of 5 "
                    "in 87s.\n"
                    "RUNS AFTER the immutable/constant classification, so "
                    "a constant -- which every path agrees on by "
                    "definition -- is still reported as NOT SETTABLE "
                    "rather than silently folded into this line.\n"
                    "Off by default: every region measured under it is a "
                    "statement about that entry-state slice, and the pin "
                    "is printed with it.")
    ap.add_argument("--path-function",
                    default=None,
                    help="disambiguate overloads: the exact mangled "
                    "path_function to generalise.")
    ap.add_argument("--slot-coords",
                    type=int,
                    default=0,
                    metavar="N",
                    help="propose up to N MAPPING SLOTS as free coordinates, "
                    "read from solc's own declaration: for each mapping "
                    "state variable with a value-type key and a scalar "
                    "value, one coordinate `state.<m>[<k>]` per parameter "
                    "of this unit whose type matches the key type (plus "
                    "msg.sender on an address key).\n"
                    "WHY IT HAS TO BE PROPOSED RATHER THAN HARVESTED: the "
                    "coordinate set is otherwise exactly the counterexample "
                    "payload's key set, and a payload can only offer a slot "
                    "at a key some counterexample already picked. MEASURED "
                    "both ways -- SlotMin's payload DOES carry "
                    "`state.bal[0xFF..FF]`, a literal key, while farming's "
                    "carries no `_balances` slot at all. Neither can ever "
                    "give `_balances[account]`, the slot the guard reads "
                    "for EVERY account: the payload is a list of values and "
                    "that coordinate is a function of an input.\n"
                    "DEFAULT 0, i.e. OFF, so every existing number is "
                    "reproduced verbatim -- the same house rule --level0, "
                    "--max-holes and --max-region-pieces follow. It is a "
                    "budget rather than a switch because ladder cost is "
                    "MULTIPLICATIVE in the coordinate count, which is the "
                    "same reason environment quantities are never free "
                    "coordinates here.\n"
                    "A proposed slot carries NO counterexample value (the "
                    "payload has none), which is sound: the ladder is laid "
                    "over its full type range and every check that reads a "
                    "CE skips a coordinate it has none for. VERIFIED "
                    "against the tool, not assumed -- an outer-box round on "
                    "a slot coordinate with no `ce` entry resolves it, "
                    "publishes its TYPE RANGE, and returns it in the "
                    "bracket and the region.")
    ap.add_argument("--slot-coord",
                    action="append",
                    default=[],
                    metavar="EXPR",
                    help="add ONE slot coordinate by name, e.g. "
                    "state.bal[msg.sender]. Always honoured, independently "
                    "of the --slot-coords budget: naming it is the explicit "
                    "request the budget exists to ration. A name the tool "
                    "cannot resolve is refused by the tool and reported, "
                    "not silently dropped.")
    ap.add_argument("--pin",
                    action="append",
                    default=[],
                    help="coord=value, e.g. state.bal=50. Pinned coordinates "
                    "are NOT generalised; every region reported is a "
                    "statement about that slice and carries the pin.")
    ap.add_argument("--pin-extcall",
                    action="store_true",
                    help="fix every quantity the HARNESS chose inside the "
                    "execution -- an external call's success bit is the "
                    "common one -- at THIS path's counterexample value, "
                    "as `extcall.<name>`. Off by default and deliberately "
                    "so: such a quantity is not a call argument, so a "
                    "region certified under it holds only of the "
                    "executions in which the callee behaved that way, and "
                    "a generated test must realise the value some other "
                    "way (a mock) for the region to describe it. The pin "
                    "is PER PATH, unlike --pin, because the sibling paths "
                    "of a call site differ in exactly this quantity; it "
                    "is recorded on every region it applies to.")
    ap.add_argument("--static-extcall-inseparable",
                    action="store_true",
                    help="before region search, mark witnessed sibling paths "
                    "that agree on every generated-test-settable payload "
                    "and differ only on concrete harvested extcall.* "
                    "values as NOT_CERTIFIED. This is OFF by default "
                    "because an artefact/stub fixture may intentionally "
                    "realise the extcall behavior; the official gate-cell "
                    "POC recipe enables it because that cell has no such "
                    "fixture.")
    ap.add_argument("--static-uncontrolled-inseparable",
                    action="store_true",
                    help="before region search, mark witnessed sibling paths "
                    "whose differing source decision is driven by a known "
                    "uncontrolled ESBMC hash/nondet/extcall source and "
                    "does not read a free generated-test coordinate as "
                    "NOT_CERTIFIED. Refutation-only: this never proves a "
                    "PUT region, it only avoids spending refine/certify "
                    "budget on a split a product region cannot force.")
    ap.add_argument("--esbmc-arg",
                    action="append",
                    default=[],
                    metavar="ARG",
                    help="pass one extra argument straight to EVERY ESBMC "
                    "invocation this driver makes -- enumeration, every "
                    "outer-box round and every certification query. "
                    "Repeatable, and each token is a separate "
                    "--esbmc-arg (use `--esbmc-arg=--unwindset` "
                    "`--esbmc-arg=55:512,56:512`; the `=` form is needed "
                    "whenever the value itself starts with a dash, or "
                    "argparse reads it as the next option).\n"
                    "WHY: the tool's own refusal names repairs this "
                    "driver could not apply. UNDECIDED-TRUNCATED says "
                    "'Re-run this path with a larger --unwind, or "
                    "--unwindset/--unwindsetname on the loop(s) named' "
                    "and then names them -- measured on farming/approve, "
                    "loops 55 and 56 in _str_assign. Stage 4 "
                    "(solidity_path_put.py) closed this gap; stage 2 had "
                    "not.\n"
                    "APPLIED TO EVERY INVOCATION on purpose: a bound that "
                    "differs between the round that measured a region and "
                    "the query that certifies it is two measurements "
                    "wearing one name.\n"
                    "⛔ STRATEGY FLAGS ARE REFUSED, by the same list "
                    "solidity_path_put.py uses -- imported, not copied, "
                    "so the two drivers cannot drift about which flags "
                    "are safe.")
    ap.add_argument("--state-struct-fields",
                    action="store_true",
                    help="decompose a STRUCT-VALUED STATE VARIABLE into its "
                    "scalar leaves, as `state.<var>.<field>[.<field>...]`, "
                    "instead of refusing the variable whole.\n"
                    "WHY: `coord_values` has decomposed struct-valued "
                    "PARAMETERS since the EscrowSrc measurement -- its own "
                    "comment says the whole-argument refusal 'is what left "
                    "every EscrowSrc unit with nothing to generalise' -- "
                    "and entry_storage was never wired to the same "
                    "function. One fact, two ledgers.\n"
                    "It also DESCENDS into nested aggregates, which the "
                    "depth-1 rule declines to do. That is not decoration: "
                    "farming/deposit's entire `_farm` payload is "
                    "`{ .farmInfo = { .finished = 0 } }`, whose only "
                    "depth-1 field is itself an aggregate, so without the "
                    "descent this flag would resolve zero coordinates on "
                    "the very unit it was written for -- a flag that "
                    "cannot fire looks exactly like one that fired and "
                    "found nothing.\n"
                    "OFF BY DEFAULT, same house rule as --level0, "
                    "--max-holes, --max-region-pieces and --slot-coords: "
                    "it changes the coordinate set of every unit whose "
                    "state holds a struct, and therefore what every region "
                    "measured under it is a statement ABOUT. Every "
                    "recorded number reproduces verbatim without it.\n"
                    "⛔ IT DOES NOT PROMISE THE MISSING QUANTITY. Only "
                    "leaves the REPORT actually rendered can become "
                    "coordinates; a field ESBMC did not render is still "
                    "absent, and on farming/deposit `userInfo.checkpoint` "
                    "and `farmInfo.duration` are exactly that case. "
                    "Whether the coordinate gate moves is the measurement, "
                    "not the claim.")
    ap.add_argument("--enumeration-index",
                    default=None,
                    help="stage-1 pathcov index.json to validate before reusing "
                    "an enumeration. Must be paired with "
                    "--enumeration-report; any configuration mismatch is "
                    "a hard refusal before an ESBMC process starts.")
    ap.add_argument("--enumeration-report",
                    default=None,
                    help="stage-1 unit report to reuse for enumeration. The "
                    "versioned --enumeration-index is the authority for "
                    "source, AST, binary, unit, scope, max-tx, memory, "
                    "witness and solver-option compatibility.")
    ap.add_argument("--allow-recursive-helper-enumeration",
                    action="store_true",
                    help="do not apply the AST preflight that refuses a target "
                    "unit whose call closure reaches a direct "
                    "self-recursive function/helper wrapper. The default "
                    "refusal is a budget guard, not a proof: it exists to "
                    "avoid spending a whole path-discovery timeout on "
                    "flattened wrappers shaped as `return f(args...)`.")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    if bool(args.enumeration_index) != bool(args.enumeration_report):
        raise SystemExit("--enumeration-index and --enumeration-report must be "
                         "passed together")

    # Checked BEFORE the workdir is stamped and before any query is issued: a
    # refusal that arrives after the enumeration has run has already spent the
    # expensive part.
    refusal = check_esbmc_args(args.esbmc_arg)
    if refusal:
        raise SystemExit("[esbmc-arg] " + refusal)
    if args.esbmc_arg:
        print(f"[esbmc-arg] passing to EVERY ESBMC invocation: "
              f"{' '.join(args.esbmc_arg)}")
    if (args.probe_ladder or args.level0_perturb) and not args.level0:
        args.level0 = True
        print("[level0] enabled automatically because " +
              ("--probe-ladder needs published coordinate type ranges" if args.
               probe_ladder else "--level0-perturb has no effect without the level-0 batch"))

    pins = {}
    for p in args.pin:
        n, _, v = p.partition("=")
        pins[n] = parse_int(v)
    fixture_pins, fixture_pin_skipped = path_cov_fixture_state_pins(args.esbmc_arg, args.contract)
    for n, v in sorted(fixture_pins.items()):
        if n in pins and pins[n] != v:
            raise SystemExit(f"[fixture] {n}={v} from --path-cov-fixture conflicts with "
                             f"explicit --pin {n}={pins[n]}")
        pins.setdefault(n, v)
    if fixture_pins:
        print("[fixture] scalar state pin(s) imported from "
              "--path-cov-fixture: " + ", ".join(f"{n}=={v}"
                                                 for n, v in sorted(fixture_pins.items())) +
              ". These are part of the reused path-coverage run's entry "
              "state, so source decisions that read getters can use them")
    if fixture_pin_skipped:
        print("[fixture] state pin(s) not imported: " + "; ".join(fixture_pin_skipped))

    cwd = args.workdir or tempfile.mkdtemp(prefix="pathgen-")
    os.makedirs(cwd, exist_ok=True)

    # Resolved BEFORE the directory is stamped: the scope is part of what makes
    # two runs incomparable, so it has to be in the stamp rather than checked
    # after the first query has already overwritten the previous run's report.
    scope_label, focus = resolve_scope(args.scope, args.focus, args.unit)
    stamp_workdir(cwd, run_config(args, scope_label))
    write_generalise_progress(
        cwd,
        "started",
        contract=args.contract,
        unit=args.unit,
        scope=scope_label,
        max_tx=args.max_tx,
        timeout_s=args.timeout,
    )
    print(f"[workdir] {cwd}")
    print(f"[scope] {scope_label}" + (
        f" — dispatcher restricted to {focus}" if focus else " — every entry may be dispatched") +
          f", --solidity-max-tx {args.max_tx}. The scope is the ALPHABET of "
          f"the call sequence and max-tx is its LENGTH; both are recorded in "
          f"run-config.json and in the result, because a run of one "
          f"configuration may not be quoted into another's table")

    if not args.allow_recursive_helper_enumeration:
        declaration_id = path_function_declaration_id(args.path_function)
        if args.path_function and declaration_id is None:
            raise SystemExit(f"[enumerate] malformed path_function {args.path_function!r}: "
                             "expected a trailing #<solc-node-id>")
        recursive_helpers = direct_recursive_helpers_in_unit_closure(args.ast,
                                                                     args.contract,
                                                                     args.unit,
                                                                     declaration_id=declaration_id)
        if recursive_helpers:
            print("[enumerate] no witnessed path for this unit, ⛔ and it is "
                  "NOT a result: target call closure reaches direct "
                  "self-recursive function/helper wrapper(s): " + ", ".join(recursive_helpers) +
                  ". This preflight starts no ESBMC process and proves "
                  "nothing about reachability; it refuses only the flattened "
                  "`return f(args...)` shape that otherwise consumes the "
                  "enumeration budget before any witness is published. Fix the "
                  "flattened helper or pass --allow-recursive-helper-enumeration "
                  "to measure it anyway.")
            return 1

    declaration_id = path_function_declaration_id(args.path_function)
    if args.path_function and declaration_id is None:
        raise SystemExit(f"[enumerate] malformed path_function {args.path_function!r}: "
                         "expected a trailing #<solc-node-id>")
    enumeration_param_types = dict(
        unit_params(args.ast, args.contract, args.unit, declaration_id=declaration_id))
    try:
        enumeration_state_types = contract_state_types(args.ast, args.contract)
    except (OSError, ValueError):
        enumeration_state_types = {}
    try:
        with open(args.sol, encoding="utf-8") as stream:
            flat_source_text = stream.read()
    except OSError:
        flat_source_text = ""
    extcall_length_specs = extcall_length_coordinate_specs(flat_source_text, args.contract,
                                                           args.unit)
    if extcall_length_specs:
        print("[coords] external-call array length coordinate(s) recognised: " +
              ", ".join(f"{s['coord']} := {s['state_var']}.{s['signature']}.length"
                        for s in extcall_length_specs) +
              ". These are promoted only when ESBMC reports the matching "
              "local in extcall_returns; Stage 4 must realize them with a "
              "Foundry interface mock")

    (paths, refused, caveats, members, path_extras, path_decisions,
     resolved_path_function) = enumerate_paths(args.esbmc,
                                               args.sol,
                                               args.contract,
                                               args.unit,
                                               args.max_tx,
                                               args.timeout,
                                               cwd,
                                               ast=args.ast,
                                               focus=focus,
                                               memlimit=args.memlimit,
                                               path_function=args.path_function,
                                               esbmc_args=args.esbmc_arg,
                                               state_structs=args.state_struct_fields,
                                               probe_witnesses=args.probe_witnesses,
                                               enumeration_index=args.enumeration_index,
                                               enumeration_report=args.enumeration_report,
                                               scope_label=scope_label,
                                               param_types=enumeration_param_types,
                                               state_types=enumeration_state_types,
                                               extcall_coord_specs=extcall_length_specs)
    args.path_function = resolved_path_function
    all_paths = list(paths)
    write_generalise_progress(
        cwd,
        "enumerated",
        witnessed=len(paths),
        refused=len(refused or []),
        caveats=len(caveats or []),
        path_function=resolved_path_function,
        salvage=read_enumeration_salvage(cwd),
    )
    if not paths:
        # ⛔ THE OLD TEXT HERE ASSERTED A RESULT AND WAS WRONG ON REAL INPUT.
        # It said "That is a result, not an error ... (The report was checked:
        # it holds no F claim for any unit, so this really is the empty case)".
        # The check it referred to is `enumerate_paths`'s wiring check, which
        # only asks whether OTHER units have F claims; it never looks at why
        # THIS unit's claims came back U. On St1inch.balanceOf two of three
        # claims had been abandoned at the per-claim budget and the sentence
        # above reported that as a property of the contract.
        fatal, why = empty_enumeration_reason(cwd, args.unit)
        empty_diag = empty_enumeration_diagnostic(cwd, args.unit)
        print(f"[enumerate] no witnessed path for this unit, {why}")
        if not fatal:
            print("  A path with no counterexample has no known member of its "
                  "domain to keep, so there is nothing to grow a region "
                  "around. ⛔ Still not a reachability statement: it is bounded "
                  "by this run's --max-tx, --unwind and scope.")
        write_generalise_progress(
            cwd,
            "no-witness",
            fatal_empty_enumeration=fatal,
            reason=why,
            empty_witness_class=empty_diag.get("class"),
            empty_witness_diagnostic=empty_diag,
        )
        if args.ce_collection_only:
            artifact = write_ce_collection(cwd,
                                           args,
                                           scope_label,
                                           paths,
                                           refused,
                                           caveats,
                                           members,
                                           path_decisions,
                                           status="no-witness",
                                           reason=why)
            print("[ce-collection] no witness was found within this bounded "
                  f"run; artifact written to {artifact}")
            return 0
        return 1
    if args.ce_collection_only:
        artifact = write_ce_collection(cwd,
                                       args,
                                       scope_label,
                                       paths,
                                       refused,
                                       caveats,
                                       members,
                                       path_decisions,
                                       status="witnessed")
        write_generalise_progress(cwd,
                                  "ce-collected",
                                  witnessed=len(paths),
                                  artifact=os.path.basename(artifact))
        print("[ce-collection] persisted refutation evidence only; no "
              f"region was certified and no test was emitted: {artifact}")
        return 0
    arith_conditions_seen = enumeration_has_arith_conditions(cwd)
    if arith_conditions_seen:
        print("[structural] checked-arithmetic conditions were seen during "
              "enumeration; simple decision regions will still be measured by "
              "ESBMC certification, because source branch decisions alone do "
              "not exclude overflow/div-by-zero panic inputs")
    query_unit = args.path_function or args.unit
    print(f"[enumerate] exact query unit: {query_unit}")
    print(f"[enumerate] {len(paths)} witnessed path(s): " + ", ".join(f"enc={e} depth={d}"
                                                                      for e, d, _ in paths))
    abi_classes = {enc: abi_gate_class(path_decisions.get(enc)) for enc, _, _ in paths}
    if any(v is not None for v in abi_classes.values()):
        print("[enumerate] synthetic ABI value gate classes: " +
              ", ".join(f"enc={enc}:{kind}"
                        for enc, kind in sorted(abi_classes.items()) if kind is not None))
    if refused:
        # Say it. Every region printed below is a statement about the SLICE
        # through these, not about the whole input space.
        print(f"[coords] UNSUPPORTED, refused as coordinates (not scalar): "
              f"{', '.join(refused)}. Every region below is a statement about "
              f"the slice through whatever values they took in the "
              f"counterexample, and does NOT generalise over them.")

    # ---- EVM environment: pin agreement, optionally promote disagreements ----
    #
    # Environment quantities are not made FREE coordinates by default: the
    # ladder cost is multiplicative in the coordinate count and the bracket
    # round is already the binding cost on real input. A later opt-in promotion
    # may make only PUT-establishable disagreements free.
    #
    # They are pinned only where EVERY witnessed path's counterexample agrees on
    # the value. A pin that contradicts some path's own counterexample would
    # place that path's known domain member OUTSIDE the box, and "keep a known
    # member" is the invariant that stops the subtraction cut from carving away
    # the real region. Where the paths disagree the quantity is left
    # unconstrained -- which is the status quo, and is reported rather than
    # passed over, because an unconstrained gate is exactly what refuses
    # certification.
    env_names = sorted({k for _, _, ce in paths for k in ce if is_env(k)} - set(args.env_coord))
    if args.env_coord:
        print(f"[env] probed as free coordinate(s): "
              f"{', '.join(sorted(args.env_coord))}")

    # ---- S10: msg.value on a NON-PAYABLE unit is 0, as a fact not a policy ----
    #
    # This is the single largest measured difference between certifying and
    # certifying nothing. Same contract, same command apart from the
    # environment: 0 of 5 paths certified, against 4 of 5.
    #
    # It is deliberately NOT `--pin-env` made default. That flag pins every
    # environment quantity the paths agree on, which turns each region into a
    # statement about an environment SLICE, and that is a real change of
    # meaning. This pins one quantity, on units whose SOURCE declares that the
    # quantity cannot be anything else: a non-payable function's
    # compiler-inserted gate reverts every call carrying value, so no input with
    # `msg.value != 0` reaches the body. Nothing reachable is excluded.
    #
    # What IS excluded is the ABI-gate revert path itself, whose entire domain
    # is `msg.value != 0`. Its region goes empty and is reported as empty. That
    # is the cost, it is stated here, and it is why this prints rather than
    # happening quietly.
    # SCOPED TO THE CONTRACT UNDER TEST AND ITS BASES. On a flattened input a
    # bare function name collides across contracts, and because the tie-break is
    # "payable wins" a collision silently skips the pin AND prints "this unit is
    # PAYABLE" -- a false claim about the unit, on exactly the multi-contract
    # inputs the corpus sweep uses.
    fn_mut = function_mutability(args.ast, args.contract)
    mu = fn_mut.get(args.unit)
    if args.no_auto_pin_value:
        print("[env] --no-auto-pin-value: msg.value is NOT pinned even if this "
              "unit is non-payable. A non-payable unit's ABI gate is a decision "
              "on msg.value, so leaving it unconstrained refuses certification "
              "however far the box is shrunk")
    elif "msg.value" not in {k for _, _, ce in paths for k in ce}:
        pass  # not in the payload; nothing to pin
    elif "msg.value" in pins:
        pass  # an explicit --pin always wins
    elif mu is None:
        # Same failure direction as every other AST read here, and reported for
        # the same reason: an exclusion that does not happen must still be
        # visible, or its absence reads as a property of the contract.
        print("[env] msg.value NOT auto-pinned: this unit's stateMutability "
              "could not be read" +
              (" (no --ast given)"
               if not args.ast else f" (the AST names {len(fn_mut)} function(s), not "
               f"'{args.unit}')") + ". A non-payable unit cannot certify while msg.value is "
              "unconstrained, so this is a yield loss with a nameable cause")
    elif mu == "payable":
        # THE MUST-FLIP. A payable function really can be called with value, so
        # pinning it to 0 would generalise over a strictly smaller input space
        # than the contract has -- and say nothing about it.
        print("[env] msg.value NOT pinned: this unit is PAYABLE, so a call may "
              "carry value and pinning it to 0 would exclude reachable inputs")
    else:
        pins["msg.value"] = 0
        print(f"[env] msg.value PINNED to 0: the source declares '{args.unit}' "
              f"{mu} (not payable), so every call reaching its body has "
              f"msg.value == 0 -- the ABI gate reverts the rest. This is a fact "
              f"about the contract, not a slice: no reachable input is "
              f"excluded. The ABI-gate revert path itself IS excluded, and its "
              f"region will be reported EMPTY. Disable with "
              f"--no-auto-pin-value")
    abi_candidates = compiler_abi_gate_candidate_mapping(path_decisions, pins)
    if abi_candidates:
        print("[candidates] compiler ABI gate mapping: " +
              "; ".join(f"enc={enc}: " + ", ".join(f"{candidate['coordinate']}="
                                                   f"{candidate['arm']}"
                                                   for candidate in candidates)
                        for enc, candidates in sorted(abi_candidates.items())))
    # Apply the first slice pin before deriving agreed state pins. Otherwise an
    # ABI-reject witness can contribute an impossible state value to the body
    # slice merely because its counterexample was enumerated first.
    initial_pin_excluded = pinned_slice_exclusions(paths, pins)
    paths = [path for path in paths if path[0] not in initial_pin_excluded]
    # ---- THE PARTITION THIS FILE ALREADY COMPUTES, APPLIED ------------------
    #
    # Placed HERE, after the msg.value auto-pin, so a pinned quantity is never
    # promoted: msg.value disagrees across the paths of every non-payable unit
    # (the ABI-gate path's counterexample carries a nonzero value) and promoting
    # it would cancel the pin that buys 4 of 5 certifications instead of 0.
    #
    # The agreement test is character for character the one `--pin-env` uses
    # below. Two tests that could disagree about what "the paths agree" means
    # would put one quantity in both groups.
    if args.env_coord_disagreed:
        promoted, kept = derive_env_coord_disagreed(paths, env_names, pins)
        if promoted:
            env_names = [n for n in env_names if n not in promoted]
            print(f"[env] PROMOTED to free coordinate(s) because the "
                  f"{len(paths)} witnessed paths DISAGREE on the value: " + ", ".join(promoted) +
                  ". A quantity the paths disagree on is what separates them, "
                  "so pinning it is impossible and leaving it unconstrained "
                  "refuses certification however far the box is shrunk")
        else:
            # ⛔ SAY IT. A derivation that finds nothing must not look like one
            # that never ran.
            print("[env] --env-coord-disagreed derived NOTHING" +
                  (": no environment quantity is in this unit's payload"
                   if not env_names else ": every candidate was excluded -- " + "; ".join(kept)) +
                  ". No coordinate was added")
    if args.pin_agreed_establishable_env:
        relation_env_coords = relation_establishable_env_sources(
            paths, path_decisions, pins, sorted({k
                                                 for _, _, ce in paths
                                                 for k in ce} - set(pins)), env_names)
        if relation_env_coords:
            env_names = [n for n in env_names if n not in relation_env_coords]
            print("[env] NOT pinned because a complete-path decision can "
                  "establish state from this environment coordinate: " +
                  ", ".join(sorted(relation_env_coords)) +
                  ". These stay free until the structural relation pass, "
                  "which can certify an explicit entry assignment such as "
                  "`state.owner := msg.sender` instead of collapsing both "
                  "sides to a concrete point")
        agreed, kept = derive_agreed_establishable_env_pins(paths, env_names, pins)
        for n, v in agreed.items():
            pins.setdefault(n, v)
        decision_env_names = decision_read_env_coords(paths, path_decisions, pins, env_names)
        quantified = derive_agreed_unpinned_establishable_env_coords(
            paths, env_names, pins, decision_env_names=decision_env_names)
        if quantified:
            env_names = [n for n in env_names if n not in quantified]
            print("[env] NOT pinned but promoted to free coordinate(s): " +
                  ", ".join(sorted(quantified)) +
                  ". All witnessed paths agree on an unestablishable "
                  "Foundry value, so keeping it as an environment bucket "
                  "would make it neither pinned nor fuzzed; each promoted "
                  "quantity is read by a complete-path decision, so ESBMC "
                  "must certify any widened executable sender range")
        if agreed:
            print(f"[env] pinned PUT-establishable agreement "
                  f"(all {len(paths)} paths agree): " +
                  ", ".join(f"{n}={v}" for n, v in sorted(agreed.items())))
        else:
            print("[env] --pin-agreed-establishable-env derived NOTHING" +
                  (": no environment quantity is in this unit's payload" if not env_names else
                   ": every candidate was excluded -- " + "; ".join(kept)) + ". No pin was added")
    if args.pin_env and env_names:
        agreed, disagreed = {}, []
        for n in env_names:
            vals = {ce.get(n) for _, _, ce in paths}
            if len(vals) == 1 and None not in vals:
                agreed[n] = vals.pop()
            else:
                disagreed.append(n)
        for n, v in agreed.items():
            pins.setdefault(n, v)  # an explicit --pin always wins
        if agreed:
            print(f"[env] pinned (all {len(paths)} paths agree): " +
                  ", ".join(f"{n}={v}" for n, v in sorted(agreed.items())))
        if disagreed:
            print(f"[env] NOT pinned, paths disagree on the witnessed value: "
                  f"{', '.join(disagreed)}. Left unconstrained, so a path "
                  f"guarded by one of these cannot certify.")
    elif env_names:
        # The count must EXCLUDE anything the auto-pin above already fixed, or
        # the line contradicts the one printed two paragraphs earlier -- and the
        # msg.value sentence must not be repeated once msg.value is pinned,
        # which is exactly the "gate that reports a state it no longer has"
        # shape this file keeps catching elsewhere.
        loose = [n for n in env_names if n not in pins]
        if loose:
            print(f"[env] {len(loose)} environment quantity(s) left "
                  f"unconstrained (--pin-env is off)" +
                  ("" if "msg.value" in
                   pins else ". A non-payable function has an ABI-level decision on "
                   "msg.value, so its paths cannot certify while it is "
                   "unconstrained") + ".")

    coords = sorted({k for _, _, ce in paths for k in ce} - set(pins) - set(env_names))

    # A parameter that is unused by a path is normally absent from the CE
    # payload because slicing has no backward edge from the path claim to its
    # entry assignment.  It remains a caller-controlled input, however.  Add
    # scalar ABI parameters from the AST/type table as full-domain coordinates
    # so unconditional paths (notably deliberate revert-only methods) can be
    # certified and rendered as PUTs instead of being forced into concrete
    # fallback solely because the witness did not read the argument.
    observed_payload_names = {k for _, _, ce in paths for k in ce}
    unobserved_param_coords = unobserved_scalar_parameter_coords(enumeration_param_types,
                                                                 observed_payload_names, pins,
                                                                 env_names)
    if unobserved_param_coords:
        coords = sorted(set(coords) | set(unobserved_param_coords))
        print("[coords] ABI parameter(s) absent from the CE payload but kept "
              "as full-domain scalar coordinates: " + ", ".join(unobserved_param_coords) +
              ". The path did not read these caller inputs; it did not prove "
              "that they are fixed")

    # State pins for unrelated fields can dominate the outer-box formula on a
    # flattened contract.  Widening over those fields is sound when the source
    # dependency closure is complete and contains no inline assembly, because
    # the target cannot read them.  Assembly is a fail-closed escape hatch: its
    # storage reads do not necessarily have AST declaration edges.
    state_dependency_filter = {
        "mode": "disabled",
        "live": [],
        "dropped": [],
        "evidence": [],
    }
    dropped_state_coords = []
    state_deps, state_dep_evidence = unit_state_dependencies(args.ast,
                                                             args.contract,
                                                             args.unit,
                                                             declaration_id=declaration_id)
    has_assembly, assembly_evidence = unit_contains_inline_assembly(args.ast,
                                                                    args.contract,
                                                                    args.unit,
                                                                    declaration_id=declaration_id)
    if state_deps is not None and not has_assembly:
        coords, dropped_state_coords = filter_unreferenced_state_coords(coords, state_deps)
        state_dependency_filter = {
            "mode": "source-closure-no-inline-assembly",
            "live": sorted(str(name) for name in state_deps),
            "dropped": dropped_state_coords,
            "evidence": list(state_dep_evidence),
        }
        if dropped_state_coords:
            print("[coords] DROPPED unrelated state coordinate(s) outside "
                  "the target dependency closure: " + ", ".join(dropped_state_coords) +
                  ". The certified region is widened over these fields; "
                  "the source closure contains no inline assembly")
    else:
        state_dependency_filter["evidence"] = list(state_dep_evidence or [])
        state_dependency_filter["evidence"].extend(assembly_evidence or [])
        if has_assembly:
            print("[coords] state dependency filtering disabled: inline "
                  "assembly is present in the target closure")

    # ---- Drop coordinates NO generated test can set ----
    #
    # An `immutable` is fixed at construction, a `constant` is in the code; the
    # counterexample harvest reports both under entry_storage because the model
    # makes them members of the contract object, and the driver was turning them
    # into FREE coordinates. That gives the verifier a wider input space than
    # reality, so certification over such a coordinate cannot succeed: the
    # witness just moves it every round until the shrink budget is gone.
    #
    # They are PINNED, not deleted. The path's own counterexample value is the
    # value the deployed contract has, so pinning states the slice truthfully and
    # keeps every region a statement about a reachable configuration -- whereas
    # dropping them would leave the quantity unconstrained, which is the same
    # constraint as never having mentioned it.
    # Lowering artifacts first: a struct member the SOURCE never declared is not
    # an input at all (padding), so it is DROPPED rather than pinned -- pinning
    # would print it beside the region as though it were part of the slice the
    # caller asked about, and it is not a quantity anything can ask about.
    artifacts = lowering_artifacts(coords, declared_struct_fields(args.ast),
                                   enumeration_param_types)
    if artifacts:
        coords = [c for c in coords if c not in artifacts]
        print("[coords] DROPPED as struct-lowering artifact(s), not source "
              "fields: " + ", ".join(f"{c} ({w})" for c, w in sorted(artifacts.items())) +
              ". The struct lowering introduces padding members the source "
              "never declared; no generated test can set one, so offering it "
              "as a coordinate is the same defect as offering an immutable")

    unsettable_query_pins = set()
    unsettable = unsettable_coords(coords, state_mutability(args.ast))
    if unsettable:
        for c in sorted(unsettable):
            v = next((ce[c] for _, _, ce in paths if c in ce), None)
            if v is not None:
                pins.setdefault(c, v)
                unsettable_query_pins.add(c)
        coords = [c for c in coords if c not in unsettable]
        print("[coords] NOT SETTABLE by any generated test, pinned at the "
              "counterexample value instead of generalised: " +
              ", ".join(f"{c} ({unsettable[c]}, =={pins[c]})" for c in sorted(unsettable)) +
              ". An immutable is fixed at construction and a constant is in "
              "the code; neither is an input, so generalising over one asks "
              "the verifier about inputs no test can produce")
        if unsettable_query_pins:
            print("[coords] ESBMC query pins INCLUDE immutable/constant "
                  "coordinate(s): " + ", ".join(sorted(unsettable_query_pins)) +
                  ". They are not runtime fuzz coordinates, but they are "
                  "facts about this deployed-contract slice; omitting them "
                  "would let the verifier refute the region with an "
                  "impossible constructor/bytecode state")
    elif args.ast:
        print("[coords] every state coordinate is a MUTABLE state variable "
              "(checked against the AST), so none was excluded")
    else:
        print("[coords] no --ast given, so state-variable mutability could NOT "
              "be checked: an immutable or constant coordinate would be "
              "generalised over as if a test could set it. Pass --ast to have "
              "them pinned instead")

    # ---- THE STATE MIRROR OF --pin-env --------------------------------------
    #
    # AFTER the immutable/constant classification above, on purpose: a constant
    # is agreed on by every path by definition, and pinning it here first would
    # take it out of `coords` before `unsettable_coords` ever saw it -- turning
    # an accurate "NOT SETTABLE ... immutable/constant" line into a generic
    # "pinned" one. The classification would be lost with nothing on screen
    # saying so.
    if args.pin_agreed_state:
        agreed_state, varying = {}, []
        relation_state = relation_establishable_state_targets(paths, path_decisions, pins, coords)
        relation_kept = []
        live_by_enc, n_pin_witness_bad, n_pin_witness_missing = \
            live_witness_vectors(paths, members, pins)
        witness_varied = []
        for c in list(coords):
            if not c.startswith("state."):
                continue
            if c in relation_state:
                relation_kept.append(c)
                continue
            vals = set()
            for enc, _depth, ce in paths:
                live = live_by_enc.get(enc) or [ce]
                vals.update(v[c] for v in live if c in v)
            if len(vals) == 1 and None not in vals:
                agreed_state[c] = next(iter(vals))
            else:
                varying.append(c)
                if len(vals) > 1:
                    witness_varied.append(c)
        for n, v in agreed_state.items():
            pins.setdefault(n, v)  # an explicit --pin always wins
        if agreed_state:
            coords = [c for c in coords if c not in agreed_state]
            print(f"[coords] STATE PINNED (all {len(paths)} paths' "
                  f"counterexamples agree): " +
                  ", ".join(f"{n}=={v}" for n, v in sorted(agreed_state.items())) +
                  ". The entry state is not havoc'd, so a bound on one of "
                  "these constrained nothing in the query and the emitter "
                  "drops it unless it is a point -- leaving it free only "
                  "gives the shrink loop somewhere to find refuting "
                  "witnesses. Every region below is a statement about this "
                  "entry-state slice" + (f". STILL FREE, the paths disagree on: " +
                                         ", ".join(sorted(varying)) if varying else ""))
            if witness_varied:
                print("[coords] STATE NOT PINNED because witness/probe inputs "
                      "already show multiple values inside the pinned slice: " +
                      ", ".join(sorted(witness_varied)) +
                      ". Fuzz/probe evidence can refute a point-slice "
                      "assumption cheaply; it is not proof of the final "
                      "region, but it is enough to keep the coordinate live "
                      "for ESBMC certification")
            if n_pin_witness_bad or n_pin_witness_missing:
                print("[coords] STATE PIN witness pool filtered: "
                      f"{n_pin_witness_bad} vector(s) violated an existing pin "
                      f"and {n_pin_witness_missing} missed a pinned name")
            if relation_kept:
                print("[coords] STATE NOT PINNED because a complete-path "
                      "decision can establish it from another coordinate: " +
                      ", ".join(sorted(relation_kept)) +
                      ". These stay free until the structural relation pass, "
                      "which will certify an explicit entry assignment such "
                      "as `state.owner := msg.sender` instead of collapsing "
                      "the caller coordinate to a point")
        else:
            # ⛔ The same must-say rule as the environment side above.
            if relation_kept and not varying:
                why_state = (": every agreed state coordinate is relation-"
                             "establishable")
            elif not varying:
                why_state = ": no state coordinate survived to this point"
            else:
                why_state = (f": the {len(paths)} paths disagree on every state "
                             f"coordinate -- " + ", ".join(sorted(varying)))
            print("[coords] --pin-agreed-state derived NOTHING" + why_state + ". No pin was added")
            if witness_varied:
                print("[coords] STATE NOT PINNED because witness/probe inputs "
                      "already show multiple values inside the pinned slice: " +
                      ", ".join(sorted(witness_varied)) +
                      ". This is refutation evidence for the point pin only; "
                      "the final widened region is still certified by ESBMC")
            if n_pin_witness_bad or n_pin_witness_missing:
                print("[coords] STATE PIN witness pool filtered: "
                      f"{n_pin_witness_bad} vector(s) violated an existing pin "
                      f"and {n_pin_witness_missing} missed a pinned name")
            if relation_kept:
                print("[coords] STATE NOT PINNED because a complete-path "
                      "decision can establish it from another coordinate: " +
                      ", ".join(sorted(relation_kept)) +
                      ". No agreed-state pin was added for these coordinate(s)")

    # The path enumeration intentionally includes compiler-generated ABI reject
    # paths so coverage remains complete. Once the non-payable slice pins
    # msg.value to zero, however, those witnesses are outside the queried
    # domain. Leaving them in sibling subtraction makes the body path empty;
    # leaving them in static pair attribution makes an out-of-slice gate look
    # like an uncontrolled body decision. Keep them in the final report, but
    # remove them from every active region computation.
    pin_excluded = pinned_slice_exclusions(all_paths, pins)
    if pin_excluded:
        print("[slice] excluding " + ", ".join(f"enc={enc}" for enc in sorted(pin_excluded)) +
              " witnessed path(s) outside the pinned slice from region "
              "search: " + "; ".join(pin_excluded[enc] for enc in sorted(pin_excluded)))
        paths = [path for path in paths if path[0] not in pin_excluded]
        active_names = {name for _, _, ce in paths for name in ce}
        coords = [name for name in coords if name in active_names]

    # ---- MAPPING SLOTS: PROPOSED from the source, never harvested ----
    #
    # Everything above derives `coords` from the counterexample payload, which
    # can only ever offer a slot at a key some counterexample already picked --
    # MEASURED both ways: SlotMin's payload carries `state.bal[0xFF..FF]` and
    # farming's carries no `_balances` slot at all.
    #
    # A PARAMETER-keyed slot is a different thing and is not a payload name
    # under any circumstances: the payload is a list of values, and
    # `_balances[account]` is a function of an input. Until this block existed
    # the tool's support for that shape was dead code from the driver's side --
    # it fired for a hand-written spec and for the three regressions, and could
    # not fire on any driver-generated run. Off by default, because the ladder
    # cost is multiplicative in the coordinate count.
    slot_added = []
    static_slot_type_ranges = {}
    if args.slot_coords or args.slot_coord:
        maps, map_refused = mapping_state_vars(args.ast, args.contract)
        state_store_names, state_store_evidence = \
            contract_state_esbmc_store_names(args.ast, args.contract)
        for evidence in state_store_evidence:
            print(f"[coords] {evidence}")
        maps = add_esbmc_mapping_aliases(maps, state_store_names)
        alias_pairs = sorted((mapping_source_key(name, spec), name) for name, spec in maps.items()
                             if mapping_source_key(name, spec) != name)
        if alias_pairs:
            print("[coords] ESBMC mapping store aliases: " + ", ".join(f"{src} -> {dst}"
                                                                       for src, dst in alias_pairs))
        maps = prefer_esbmc_mapping_aliases(maps)
        declaration_id = path_function_declaration_id(args.path_function)
        if args.path_function and declaration_id is None:
            raise SystemExit(f"[coords] malformed path_function {args.path_function!r}: "
                             "expected a trailing #<solc-node-id>")
        params = unit_params(args.ast, args.contract, args.unit, declaration_id=declaration_id)
        dependencies, dependency_evidence = unit_state_dependencies(args.ast,
                                                                    args.contract,
                                                                    args.unit,
                                                                    declaration_id=declaration_id)
        slot_accesses, slot_access_evidence = unit_mapping_slot_accesses(
            args.ast, args.contract, args.unit, declaration_id=declaration_id, access_mode="read")
        region_dependencies = (sorted({name
                                       for name, _keys in slot_accesses})
                               if slot_accesses is not None else None)
        key_literals, key_literal_skipped = agreed_bytes_mapping_key_literals(witnessed_raw_inputs(
            cwd, args.unit, paths, args.path_function),
                                                                              params,
                                                                              typed_paths=paths)
        proposed, skipped = propose_slot_coords(
            maps,
            params,
            args.slot_coords,
            dependencies=([] if region_dependencies is None else region_dependencies),
            slot_accesses=[] if slot_accesses is None else slot_accesses,
            key_literals=key_literals)
        skipped += key_literal_skipped
        if not args.slot_coords:
            proposed, skipped = [], []
        # An explicit --slot-coord is not rationed and not type-checked here:
        # the caller named it, and the tool is the arbiter of whether it
        # resolves. Duplicates are dropped so the ladder is not laid twice.
        for c in args.slot_coord:
            if c not in proposed:
                proposed.append(c)
        static_slot_type_ranges = mapping_slot_type_ranges(maps, proposed)
        slot_added = [c for c in proposed if c not in coords]
        explicit_slots = set(args.slot_coord)
        decision_slots = decision_read_slot_coords(paths, path_decisions, slot_added)
        irrelevant_slots = sorted(set(slot_added) - explicit_slots - decision_slots)
        if irrelevant_slots:
            proposed = [c for c in proposed if c not in irrelevant_slots]
            slot_added = [c for c in slot_added if c not in irrelevant_slots]
            for c in irrelevant_slots:
                static_slot_type_ranges.pop(c, None)
            print("[coords] MAPPING SLOT(s) NOT proposed for path-region "
                  "queries because no complete-path decision reads them: " +
                  ", ".join(irrelevant_slots) + ". They remain unconstrained, which is their exact "
                  "full-domain projection; explicit --slot-coord requests "
                  "are never filtered by this gate")
        coords = sorted(set(coords) | set(slot_added))
        if dependency_evidence:
            print(f"[coords] mapping dependency policy "
                  f"{SLOT_DEPENDENCY_POLICY}: " + "; ".join(dependency_evidence))
            if region_dependencies is not None:
                write_only = sorted(set(dependencies or []) - set(region_dependencies))
                if write_only:
                    print("[coords] mapping(s) used only as write targets are "
                          "kept out of path-region slot coordinates and left "
                          "for the PUT oracle/R2 stage: " + ", ".join("state." + name
                                                                      for name in write_only))
        if slot_access_evidence:
            print("[coords] mapping READ slot access priority: " + "; ".join(slot_access_evidence))
        if key_literals:
            print("[coords] bytesN mapping key(s) fixed to the witnessed "
                  "counterexample slice, not treated as fuzz coordinates: " +
                  ", ".join(f"{k}->{v}" for k, v in sorted(key_literals.items())))
        if slot_added:
            typed = {
                c: static_slot_type_ranges[c]
                for c in slot_added if c in static_slot_type_ranges
            }
            print("[coords] MAPPING SLOT(s) proposed from solc's declaration "
                  "(a payload can only offer a slot at a key some "
                  "counterexample already picked, so a PARAMETER-keyed one can "
                  "enter no other way): " + ", ".join(sorted(slot_added)) +
                  ". Each is laid over its FULL type range -- there is no "
                  "counterexample value for a slot, so no known member of the "
                  "domain constrains it, and the C2 membership check simply "
                  "has nothing to say about it" +
                  (". Static leaf type range(s): " +
                   ", ".join(f"{c}=[{lo},{hi}]"
                             for c, (lo, hi) in sorted(typed.items())) if typed else ""))
        if skipped:
            print("[coords] slot candidate(s) NOT proposed: " + "; ".join(skipped))
        if map_refused:
            print("[coords] mapping(s) whose SHAPE has no slot coordinate: " +
                  "; ".join(map_refused))
        if not slot_added:
            print("[coords] NO mapping slot was added. This is a statement "
                  "about the source and the budget, not about the tool: see "
                  "the lines above for each candidate's reason")

    # ---- C5: coordinate accounting, before any region is measured ----
    #
    # Every name the payload carries must be visible somewhere in the report.
    # One that reaches no bucket has vanished between the counterexample and the
    # region, and the region is then about a smaller input space than the path
    # has, with nothing on screen saying so. Checked here, where every bucket is
    # final and before a single query is issued -- a measurement taken over an
    # input set that was never accounted for is a measurement of the wrong
    # thing.
    payload_names = {k for _, _, ce in paths for k in ce}
    unaccounted, _where = coordinate_accounting(
        payload_names, {
            "free coordinate": coords,
            "pinned": set(pins),
            "environment (unconstrained)": set(env_names),
            "dropped lowering artifact": set(artifacts),
            "dropped unrelated state coordinate": set(dropped_state_coords),
            "unsettable, pinned at its CE": set(unsettable),
            "refused by the tool": set(refused or ())
        })
    if unaccounted:
        print("[coords] ACCOUNTING VIOLATED — " +
              f"{len(unaccounted)} payload name(s) reached NO bucket: " + ", ".join(unaccounted) +
              ". Each is a quantity the counterexample carries that the "
              "region neither bounds, pins, drops nor refuses, so it is "
              "silently unconstrained and the region describes a smaller "
              "input space than the path has. Refusing to measure")
        return 1

    if not coords:
        # NAME BOTH HALVES. "Every coordinate is pinned" is true and useless:
        # the question a reader has is whether this unit is hard to generalise
        # or simply not addressable by the current coordinate KINDS, and those
        # call for opposite work. MEASURED on EscrowSrc.cancel, which is exactly
        # this case: its two scalar coordinates are both `immutable`, and its one
        # real argument is a struct the coordinate layer refuses -- so there was
        # never anything to generalise, and the 0-of-4 it used to report as
        # "shrink round budget exhausted" was never a search-power result.
        why = []
        if unsettable:
            why.append(f"{len(unsettable)} coordinate(s) are fixed at deployment "
                       f"(immutable/constant) and no test can set them: " +
                       ", ".join(sorted(unsettable)))
        if refused:
            why.append(f"{len(refused)} name(s) were refused as UNSUPPORTED because "
                       f"the coordinate kinds cannot express them (struct, mapping, "
                       f"non-scalar): " + ", ".join(refused))
        if pins and not why:
            why.append("every coordinate was pinned by request")
        no_coord_reason = "; ".join(why) or "no generalisable coordinate"
        print("[coords] NO GENERALISABLE COORDINATE — " + no_coord_reason +
              ". This is a COORDINATE-KIND result, not a search result: the "
              "paths were witnessed and their region is a point, so each "
              "falls back to its concrete counterexample test. Widening the "
              "ladder or the shrink budget cannot change it")
        ce_by_enc = {e: ce for e, _d, ce in all_paths}
        depth_by_enc = {e: d for e, d, _ce in all_paths}
        pin_box = {n: (pv, pv) for n, pv in pins.items()}
        no_coord_certified = []
        no_coord_failed = []
        for enc, _depth, ce in sorted(all_paths, key=lambda path: path[0]):
            reject_detail = structural_no_coordinate_abi_reject_detail(
                enc, depth_by_enc.get(enc), path_decisions.get(enc), ce)
            if reject_detail is not None:
                print(f"  enc={enc}: CERTIFIED — "
                      f"{reject_detail['reason']}")
                no_coord_certified.append(reject_detail)
                continue
            pin_violations = ce_in_region(pin_box, {}, ce)
            if pin_violations:
                why = (f"EXCLUDED FROM THE SLICE by the pins "
                       f"({'; '.join(pin_violations)}), so this no-coordinate "
                       f"path has no domain in the slice being generalised")
                print(f"  enc={enc}: NOT CERTIFIED — {why}")
                no_coord_failed.append({
                    "enc": enc,
                    "depth": depth_by_enc.get(enc),
                    "verdict": "NOT_CERTIFIED",
                    "reason": why,
                    "concrete_fallback": False,
                    "witness_check": "PIN-EXCLUDED-NO-COORDINATE",
                    "ce": {
                        n: str(v)
                        for n, v in sorted(ce_by_enc.get(enc, {}).items())
                    },
                })
                continue
            if (len(path_decisions.get(enc) or []) == 1
                    and abi_gate_class(path_decisions.get(enc)) == "body"):
                reason = ("STRUCTURAL ABI value gate with no free coordinate: "
                          "the no-coordinate slice satisfies every pin, and the "
                          "only complete-path decision is the compiler-inserted "
                          "non-payable body gate")
                print(f"  enc={enc}: CERTIFIED — {reason}")
                no_coord_certified.append({
                    "enc": enc,
                    "piece": 1,
                    "depth": depth_by_enc.get(enc),
                    "verdict": "CERTIFIED",
                    "retreated": {},
                    "established": [],
                    "extcall_pins": {},
                    "certification_source": "structural-abi-gate-no-coordinate",
                    "box": [],
                    "ce": {
                        n: str(v)
                        for n, v in sorted(ce_by_enc.get(enc, {}).items())
                    },
                })
                continue
            why = (no_coord_reason + ". NO GENERALISABLE COORDINATE: this complete witness has "
                   "no free coordinate for a parameterized region")
            print(f"  enc={enc}: NOT CERTIFIED — {why}; this path falls back "
                  "to its concrete counterexample test")
            no_coord_failed.append({
                "enc": enc,
                "depth": depth_by_enc.get(enc),
                "verdict": "NOT_CERTIFIED",
                "reason": why,
                "concrete_fallback": True,
                "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                "ce": {
                    n: str(v)
                    for n, v in sorted(ce_by_enc.get(enc, {}).items())
                },
            })
        enumeration_report_path = (args.enumeration_report or enumeration_report_snapshot_path(cwd))
        result_path = os.path.join(cwd, "generalise-result.json")
        with open(result_path, "w") as f:
            json.dump(
                {
                    "schema": "path-generalise-result/1",
                    "contract": args.contract,
                    "unit": args.unit,
                    "path_function": args.path_function,
                    "max_tx": args.max_tx,
                    "scope": scope_label,
                    "extcall_length_coordinates": extcall_length_specs,
                    "enumeration_source": {
                        "mode": ("imported-stage-1" if args.enumeration_report else "direct"),
                        "index": file_identity(args.enumeration_index),
                        "report": file_identity(enumeration_report_path),
                        "salvage": read_enumeration_salvage(cwd),
                    },
                    "pins": {
                        n: str(v)
                        for n, v in sorted(pins.items())
                    },
                    "path_decisions": {
                        str(enc): {
                            "abi_gate_class": abi_gate_class(decisions),
                            "decisions": decisions,
                        }
                        for enc, decisions in sorted(path_decisions.items())
                    },
                    "dropped_by_certify": [],
                    "no_coordinate_reason": no_coord_reason,
                    "certified": no_coord_certified,
                    "not_certified": no_coord_failed,
                    "enumerated": [{
                        "enc": e,
                        "depth": d
                    } for e, d, _ in all_paths],
                },
                f,
                indent=2,
                sort_keys=True)
        write_generalise_progress(
            cwd,
            ("certified-no-coordinate" if no_coord_certified else "no-generalizable-coordinate"),
            witnessed=len(paths),
            certified=len(no_coord_certified),
            not_certified=len(no_coord_failed),
            refused=list(refused),
            unsettable=sorted(unsettable),
            pins={
                n: str(v)
                for n, v in sorted(pins.items())
            },
            reason=no_coord_reason,
        )
        return 0 if no_coord_certified else 1
    # ---- `FREE:` IS A MARKER, NOT DECORATION ----
    #
    # certify_all matched this line as "any [coords] line without a [pinned:
    # suffix", minus a WHITELIST of prose prefixes to exclude. A whitelist of
    # exclusions is open at the bottom, and it opened: the slot-coordinate lines
    # added since are not on it, so the sweep recorded
    # `coords: ["mapping(s) whose SHAPE has no slot coordinate: _allowances
    # (...)"]` -- one prose sentence where the coordinate list belongs.
    # MEASURED on results_slotcoord_deposit.jsonl, first row.
    # The marker makes the real line identifiable by what it IS rather than by
    # what the other lines are not. The older spelling still parses, because a
    # reader that stops recognising a message it used to handle is the same
    # defect pointing the other way.
    literal_constant_types = literal_state_constant_types(args.ast, args.contract)
    nonquery_pins = nonquery_literal_constant_pins(pins, literal_constant_types)
    if nonquery_pins:
        print("[coords] ESBMC query pins OMIT literal bytesN constant "
              "coordinate(s): " + ", ".join(sorted(nonquery_pins)) +
              ". They remain semantic pins in the reported slice and source "
              "constant catalogue, but they are bytecode facts rather than "
              "runtime state assumptions")
    print(f"[coords] FREE: {', '.join(coords)}" + (f"   [pinned: {pins}]" if pins else ""))
    write_generalise_progress(
        cwd,
        "coordinates-selected",
        coords=list(coords),
        coord_count=len(coords),
        pins={
            n: str(v)
            for n, v in sorted(pins.items())
        },
        nonquery_pins=sorted(nonquery_pins),
        unsettable_query_pins=sorted(unsettable_query_pins),
        state_dependency_filter=state_dependency_filter,
    )
    declaration_id = path_function_declaration_id(args.path_function) \
        if args.path_function else None
    coord_types = dict(
        unit_params(args.ast, args.contract, args.unit, declaration_id=declaration_id))
    parameter_type_ranges = {
        name: tr
        for name, type_string in coord_types.items()
        for tr in (elementary_type_range(type_string), ) if tr is not None
    }
    parameter_type_ranges.update(dynamic_parameter_length_ranges(coord_types))
    constants = literal_state_constants(args.ast, args.contract)
    state_store_names_for_ranges, _state_store_range_evidence = \
        contract_state_esbmc_store_names(args.ast, args.contract)
    state_type_ranges = state_coord_type_ranges(args.ast, args.contract, coords,
                                                state_store_names_for_ranges)
    declared_type_ranges = dict(parameter_type_ranges)
    declared_type_ranges.update(state_type_ranges)
    declared_type_ranges.update(static_slot_type_ranges)
    structural_seed_regions, structural_seed_holes, structural_seed_source, \
        structural_seed_retreats, structural_seed_establishes = {}, {}, {}, {}, {}
    pre_structural_regions, pre_structural_holes, pre_structural_source = \
        structural_extcall_length_no_loop_regions(
            paths, path_decisions, coords, extcall_length_specs)
    if pre_structural_regions:
        print("[structural] " + ", ".join(f"enc={enc}" for enc in sorted(pre_structural_regions)) +
              " has an external-call array length no-loop product region; "
              "removing it from uncontrolled-split filtering and "
              "outer-box/refine so ESBMC does not have to query an "
              "unresolvable extcall local")
        paths = [p for p in paths if p[0] not in pre_structural_regions]

    def query_pins():
        return certification_query_pins(pins, omit=nonquery_pins)

    pre_failed = {}
    if args.static_extcall_inseparable:
        pre_failed.update(extcall_inseparable_failures(paths, path_extras, path_decisions))
    if args.static_uncontrolled_inseparable:
        pre_failed.update(
            uncontrolled_decision_splits(paths,
                                         path_decisions,
                                         coords,
                                         pins,
                                         constants=constants,
                                         path_extras=path_extras))
    if pre_failed:
        pairs = []
        encs = [enc for enc, _, _ in paths]
        for i, enc_a in enumerate(encs):
            if enc_a not in pre_failed:
                continue
            for enc_b in encs[i + 1:]:
                if enc_b not in pre_failed:
                    continue
                pa = dict(next(ce for e, _d, ce in paths if e == enc_a),
                          **(path_extras.get(enc_a) or {}))
                pb = dict(next(ce for e, _d, ce in paths if e == enc_b),
                          **(path_extras.get(enc_b) or {}))
                diff = [
                    n for n in sorted(set(pa) | set(pb))
                    if pa.get(n, _MISSING) != pb.get(n, _MISSING)
                ]
                if diff and all(n.startswith("extcall.") for n in diff):
                    pairs.append(f"{enc_a}/{enc_b} on {', '.join(diff)}")
        print("[inseparable] " +
              ("; ".join(pairs) if pairs else "static uncontrolled decision split") +
              ". These path(s) are removed from region search and recorded "
              "as NOT_CERTIFIED with a method-level attribution; this is a "
              "refutation-only filter, not a proof.")
        paths = [p for p in paths if p[0] not in pre_failed]

    if not paths:
        if pin_excluded and not pre_failed:
            print("[slice] every witnessed path is outside the pinned slice; "
                  "no ESBMC region query is started for this unit.")
        else:
            print("[inseparable] every remaining witnessed path was attributed "
                  "to an uncontrolled split; no ESBMC region query is started "
                  "for this unit.")
        args.level0 = False
        args.probe_witnesses = 0
        args.probe_ladder = False
        args.skip_bracket = True
        args.refine_rounds = 0
        brackets, regions, warned, round_failure = {}, {}, set(), None
        region_holes = {}
        structural_regions = None
        structural_region_source = {}
        last_failure = None
    else:
        brackets = regions = warned = round_failure = region_holes = None
        structural_regions = None
        structural_region_source = {}
    structural_region_retreats = {}
    structural_region_establishes = {}

    for enc, _depth, ce in paths:
        got = _structural_decision_region(path_decisions.get(enc),
                                          ce,
                                          pins,
                                          coords,
                                          coord_types=coord_types,
                                          type_ranges=declared_type_ranges,
                                          constants=constants,
                                          allow_relation_retreat=True,
                                          allow_relation_establish=True)
        if got is None:
            continue
        box, h, reason, retreated, established = got
        if not retreated and not established:
            continue
        structural_seed_regions[enc] = box
        structural_seed_holes[enc] = h
        structural_seed_source[enc] = reason
        structural_seed_retreats[enc] = retreated
        structural_seed_establishes[enc] = established
    if structural_seed_regions:
        print("[structural] relation-aware seed region(s) available for " +
              ", ".join(f"enc={enc}" for enc in sorted(structural_seed_regions)) +
              ". These do NOT skip ESBMC certification; they replace the "
              "product-box ladder result for those paths so owner/sender "
              "relations are tested as an established entry-state slice.")
    seed_excluded_by_pin = {
        enc
        for enc, _depth, ce in paths if ce_in_region({
            n: (v, v)
            for n, v in pins.items()
        }, {}, ce)
    }
    if (structural_seed_regions and paths
            and all(enc in structural_seed_regions or enc in seed_excluded_by_pin
                    for enc, _depth, _ce in paths)):
        if args.level0 or args.probe_witnesses or args.probe_ladder:
            print("[structural] every path not excluded by pins has a "
                  "relation-aware seed; skipping level0, witness pool "
                  "probes and per-path ladders")
        args.level0 = False
        args.probe_witnesses = 0
        args.probe_ladder = False

    if paths and not arith_conditions_seen:
        candidate_structural_regions, candidate_structural_holes, \
            candidate_structural_source = {}, {}, {}
        for enc, _depth, ce in paths:
            got = structural_decision_region(path_decisions.get(enc),
                                             ce,
                                             pins,
                                             coords,
                                             coord_types=coord_types,
                                             type_ranges=declared_type_ranges,
                                             constants=constants)
            if got is None:
                continue
            box, h, reason = got
            candidate_structural_regions[enc] = box
            candidate_structural_holes[enc] = h
            candidate_structural_source[enc] = reason
        if (candidate_structural_regions and len(candidate_structural_regions) < len(paths)):
            pre_structural_regions.update(candidate_structural_regions)
            pre_structural_holes.update(candidate_structural_holes)
            pre_structural_source.update(candidate_structural_source)
            print("[structural] " + ", ".join(f"enc={enc}"
                                              for enc in sorted(candidate_structural_regions)) +
                  " already has a simple decision product region; removing "
                  "it from ladder/refine so harder sibling paths cannot hide "
                  "a certified ABI/source gate")
            paths = [p for p in paths if p[0] not in candidate_structural_regions]

    if paths and not arith_conditions_seen:
        early_structural_regions, _, _ = structural_decision_regions(
            paths,
            path_decisions,
            pins,
            coords,
            coord_types=coord_types,
            type_ranges=declared_type_ranges,
            constants=constants)
        if early_structural_regions is not None:
            if args.level0 or args.probe_witnesses or args.probe_ladder:
                print("[structural] every witnessed path is already "
                      "expressible as a simple decision product region; "
                      "skipping level0, witness pool probes, per-path ladders, "
                      "bracket and refine")
            args.level0 = False
            args.probe_witnesses = 0
            args.probe_ladder = False

    # ---- LEVEL 0: is the real constraint an EQUALITY? ----
    #
    # Runs BEFORE the geometric bracket because the descent is single point ->
    # small set -> interval, and starting at the interval is a skipped level,
    # not a cheap start. Costs one batch whose candidate list is at most one
    # value per path per coordinate (proposition 9: the candidate is the
    # sibling's own counterexample value), against 258 per coordinate for the
    # bracket.
    # Both stay EMPTY when --level0 is off, so the ladder is laid exactly as it
    # was before level 0 existed. `cand` is read by the bracket and refine calls
    # below whether or not level 0 ran, so it has to exist either way -- without
    # this it is a NameError on every default run, i.e. the flag would become
    # mandatory by accident.
    eq_values, cand = {}, {}
    # Names the TOOL refuses as coordinates of this unit, accumulated across
    # every round. Read at the end, before certification, because certification
    # refuses the whole query on one such name while the outer-box rounds carry
    # on -- so this set is exactly the difference between the two branches.
    unresolvable = set()
    pre_dropped_unresolvable = set()

    def drop_unresolvable_query_pins(stage, names):
        """Drop pins once an outer round proves their names unqueryable."""
        fresh = set(names or ()) - pre_dropped_unresolvable
        if not fresh:
            return set()
        dropped = drop_unexpressible_query_names(fresh, pins, structural_seed_regions,
                                                 structural_seed_holes, pre_structural_regions,
                                                 pre_structural_holes)
        if dropped:
            pre_dropped_unresolvable.update(dropped)
            print(f"[{stage}] PRE-DROPPED " + ", ".join(sorted(dropped)) +
                  " from subsequent region-search query pins because an "
                  "outer-box round has already proved ESBMC cannot express "
                  "the name(s) as unit coordinates. Later rounds therefore "
                  "measure the remaining coordinates for ALL values of the "
                  "dropped name(s), which is stronger than repeatedly "
                  "refusing the batch.")
        return dropped

    # Learned from the tool, round by round, and never guessed. Empty until a
    # round has published one, so the FIRST ladder falls back to the full 256-bit
    # range exactly as before -- there is nothing to know it from yet.
    # ABI coordinates have a source-level domain before ESBMC publishes any
    # TYPE RANGE line. Seed it here so the first geometric round cannot probe
    # values that the Solidity parameter cannot represent (notably bytesN).
    type_ranges = dict(declared_type_ranges)

    def merge_type_ranges(measured):
        """Keep measured ranges inside the source-level coordinate domain."""
        for name, measured_range in (measured or {}).items():
            previous = type_ranges.get(name)
            if previous is None:
                type_ranges[name] = measured_range
                continue
            lo = max(previous[0], measured_range[0])
            hi = min(previous[1], measured_range[1])
            if lo <= hi:
                type_ranges[name] = (lo, hi)
            else:
                print(f"[types] ignoring contradictory measured range for "
                      f"{name}: source={previous}, measured={measured_range}")

    # Coordinates whose level-0 point rests on a ONE-VALUE candidate list, i.e.
    # the ones the round's own warning says cannot be told apart from a vacuous
    # antecedent. Union across paths, because the candidate list is laid per
    # COORDINATE for every path at once.
    at_risk = set()
    if args.level0:
        cand = level0_candidates(paths, coords)
        # ---- LEVEL 0 CAN ONLY ASK ABOUT COORDINATES A WITNESS NAMES ----
        #
        # Its candidates are the values the siblings' own counterexamples take,
        # so a coordinate no counterexample mentions has no question to put. A
        # PROPOSED mapping slot is exactly that -- the driver announces it two
        # lines above ("there is no counterexample value for a slot") -- and
        # before this the round was handed the coordinate anyway and died with
        # a TypeError on `spans[c]`, after enumeration and before any region.
        #
        # Left OUT and SAID SO. A coordinate absent from a round is
        # unconstrained in that round, and level 0's only output is which
        # coordinates are equality-type; a coordinate it never asked about
        # simply is not one, which is the correct answer rather than a gap.
        l0_coords = [c for c in coords if c in cand]
        l0_skipped = [c for c in coords if c not in cand]
        if l0_skipped:
            print("[level0] NOT asked about " + ", ".join(l0_skipped) +
                  ": no witnessed counterexample gives a value there, so "
                  "level 0 has no candidate to probe. They are NOT "
                  "equality-type by this round's silence -- they were never "
                  "asked -- and they descend to the ladder with their full "
                  "type range, which is what a proposed mapping slot needs")
        (l0_boxes, _, _, _, l0_failure, _, tr_new, unres) = outer_round(
            args.esbmc,
            args.sol,
            args.contract,
            query_unit,
            paths,
            l0_coords,
            query_pins(),
            args.probes,
            args.max_tx,
            args.timeout,
            cwd,
            values_by_coord=cand,
            ast=args.ast,
            focus=focus,
            memlimit=args.memlimit,
            esbmc_args=args.esbmc_arg) if l0_coords else (
                {}, {}, {}, set(),
                "every coordinate was left out of level 0 (no counterexample names "
                "any of them), so no level-0 round was issued at all", {}, {}, [])
        unresolvable.update(unres)
        drop_unresolvable_query_pins("level0", unres)
        # Level 0 lays no ladder, but it DOES publish every coordinate's type
        # range -- so the geometric bracket that follows can be bounded by the
        # type instead of by 2^256. That ordering is why the fix costs no extra
        # run: the information is already on the way past.
        merge_type_ranges(tr_new)
        if l0_failure:
            # Not "no equality coordinates". Say which it was, here, where it is
            # known -- the same rule the rest of this file follows.
            print(f"[level0] round measured NOTHING — {l0_failure}; "
                  f"descending to the geometric ladder for every coordinate")
        else:
            eq = equality_coords(l0_boxes, coords, len(paths))
            for enc, b in sorted(l0_boxes.items()):
                pts = single_point_coords(b)
                print(f"[level0] enc={enc} single-point on: " +
                      (", ".join(f"{n}=={b[n][0]}" for n in pts) if pts else "(none)"))
                # ⚠ A ONE-VALUE LADDER CANNOT TELL A POINT DOMAIN FROM AN
                # EMPTY ONE, and that is a false-certification route, not a
                # presentation issue.
                #
                # Level 0 lays ONE candidate v per coordinate and asks `c <= v`
                # and `c >= v`. Both hold when the domain really is {v} -- and
                # both ALSO hold, for EVERY v, when the antecedent
                # `tr == enc && cnt == depth && pins` is UNSATISFIABLE, because
                # then every probe holds vacuously. From one value the two are
                # indistinguishable, and the vacuous case renders as a tight,
                # confident-looking point box.
                #
                # MEASURED on EscrowSrc.cancel with the environment pinned:
                # enc=2 passes BOTH directions at v=5 (box `[5, 5]`) while its
                # reachable sibling enc=6 is refuted on both at the same value.
                # enc=2 is excluded from the slice by the pins; the "point" was
                # never a measurement of anything.
                #
                # The geometric/linear ladders catch it for free -- with two
                # values v1 < v2 both holding, u <= v1 < v2 <= l inverts the
                # interval and the empty-region guard fires. Only a one-value
                # ladder is blind, so the caution is raised exactly here.
                # PER COORDINATE, not per path, and the difference is the whole
                # accuracy of the warning. A coordinate is blind exactly when
                # its candidate list held ONE value -- which happens whenever
                # every sibling's counterexample agreed there. With two or more
                # candidates a vacuous path inverts the interval and is caught,
                # so those coordinates are not at risk at all.
                #
                # MEASURED, controlled (identical 14 pins, only the candidate
                # count varying), EscrowSrc.cancel enc=2 on immutables.taker:
                #     1 value  -> [5, 5]                    looks measured
                #     2 values -> [1000, 5] (EMPTY lo > hi)  actually vacuous
                # and that is also why level 0 reported enc=2 a "point" on four
                # of six coordinates rather than all six: the four are exactly
                # the ones whose siblings all agreed, so their candidate list
                # had one value. The path was vacuous on every coordinate.
                blind = []
                confirmed_by_member = []
                for n in pts:
                    if len(cand.get(n, ())) >= 2:
                        continue
                    point = b[n][0]
                    if point_has_known_member(members, enc, n, point, query_pins()):
                        confirmed_by_member.append(n)
                    else:
                        blind.append(n)
                # COLLECTED, not just printed. The warning has named this set
                # on every run since it was written and nothing could act on
                # it; --level0-perturb below is the action, and it needs the
                # union across paths because the candidate list is laid PER
                # COORDINATE for all paths at once.
                at_risk.update(blind)
                if confirmed_by_member:
                    print(f"[level0] enc={enc}: point on " + ", ".join(confirmed_by_member) +
                          " confirmed by this path's witnessed input under "
                          "the current non-conflicting pins, so it is not "
                          "sent to level0b's vacuity probe")
                if blind:
                    print(f"[level0] ⚠ enc={enc}: the point(s) on " + ", ".join(blind) +
                          " came from a ONE-VALUE candidate list, which "
                          "CANNOT distinguish a genuine point domain from "
                          "this path having NO inputs at all under the "
                          "current pins -- an unsatisfiable antecedent makes "
                          "every probe hold vacuously, in both directions, "
                          "at any value. Try a second value on those "
                          "coordinates: if both directions still hold, the "
                          "interval inverts and the path is excluded from "
                          "this slice. Coordinates with two or more "
                          "candidates are NOT at risk -- there the inversion "
                          "is what catches it")
            if eq:
                eq_values = {c: cand[c] for c in eq if c in cand}
                print(f"[level0] EQUALITY-TYPE (a single point for all "
                      f"{len(paths)} path(s)), so these skip the geometric "
                      f"ladder: {', '.join(eq)}")
            else:
                print("[level0] no coordinate was a single point for every "
                      "path; every coordinate descends to level 2. Note this "
                      "is NOT the same as 'no path has a point projection' -- "
                      "the per-path results are printed above")

        # ---- LEVEL 0b: THE SECOND VALUE, WHICH IS THE ONLY THING THAT CAN
        # ---- TELL A POINT DOMAIN FROM AN EMPTY ONE ----
        #
        # WHY IT IS A SECOND ROUND AND NOT AN ARGUMENT TO THE FIRST. The
        # neighbours have to be clamped to the coordinate's own type range, and
        # the type range is published BY the round above (`type_ranges` is empty
        # until `tr_new` lands). Probing outside the type is not a neighbour: an
        # unsigned coordinate at its maximum wraps to 0, and a probe at 0 answers
        # about a value the path may genuinely contain -- which would MANUFACTURE
        # the "both directions hold" verdict, i.e. invent the vacuity this round
        # exists to detect. So the ordering is forced, and it is affordable:
        # level 0 is measured at 6.6s and 7.5s on farming/setDistributor against
        # a 240s budget the geometric bracket then blows entirely.
        #
        # ⛔ IT DECIDES NOTHING BY ITSELF. It widens the candidate list and lets
        # the EXISTING empty-region guard fire, exactly as the warning above says
        # ("with two values v1 < v2 both holding, u <= v1 < v2 <= l inverts the
        # interval"). No new verdict is invented here; a mechanism that was blind
        # is given the second value it was blind for.
        if args.level0_perturb and at_risk:
            pert = level0_candidates(paths, coords, perturb=True, type_ranges=type_ranges)
            widened = {
                c: pert[c]
                for c in sorted(at_risk) if len(pert.get(c, ())) > len(cand.get(c, ()))
            }
            no_range = [c for c in sorted(at_risk) if c not in type_ranges]
            if no_range:
                # NAMED, never silent. Without a published range only the lower
                # neighbour is added, so those coordinates get a ONE-SIDED probe
                # -- enough to refute a point, not enough to establish vacuity --
                # and a reader who could not see which is which would take a
                # half-probed coordinate for a fully probed one.
                print("[level0b] ⚠ no TYPE RANGE published for " + ", ".join(no_range) +
                      ": only the LOWER neighbour is probed there. That can "
                      "refute a point but cannot establish vacuity, because "
                      "vacuity needs BOTH directions to hold")
            if not widened:
                print("[level0b] no at-risk coordinate could be widened "
                      "(every neighbour fell outside its type range), so the "
                      "one-value blindness above STANDS -- it is not resolved")
            else:
                print("[level0b] re-probing " + ", ".join(f"{c}: {cand.get(c)} -> {widened[c]}"
                                                          for c in widened) +
                      " -- both directions holding on a NEIGHBOUR means the "
                      "antecedent is unsatisfiable and the path is excluded "
                      "from this slice, not that the domain is that point")
                cand2 = dict(cand)
                cand2.update(widened)
                (b2, _, _, _, f2, _, tr2, unres2) = outer_round(args.esbmc,
                                                                args.sol,
                                                                args.contract,
                                                                query_unit,
                                                                paths,
                                                                [c for c in coords if c in cand2],
                                                                query_pins(),
                                                                args.probes,
                                                                args.max_tx,
                                                                args.timeout,
                                                                cwd,
                                                                values_by_coord=cand2,
                                                                ast=args.ast,
                                                                focus=focus,
                                                                memlimit=args.memlimit,
                                                                esbmc_args=args.esbmc_arg)
                merge_type_ranges(tr2)
                unresolvable.update(unres2)
                drop_unresolvable_query_pins("level0b", unres2)
                if f2:
                    print(f"[level0b] round measured NOTHING — {f2}; the "
                          f"one-value blindness above STANDS")
                else:
                    for enc, b in sorted(l0_boxes.items()):
                        was = single_point_coords(b)
                        nb = b2.get(enc)
                        if nb is None:
                            print(f"[level0b] enc={enc}: NO BOX in the widened "
                                  f"round. That is not a verdict -- the path "
                                  f"produced no measurement here, so its "
                                  f"level-0 point is neither confirmed nor "
                                  f"refuted")
                            continue
                        inverted = sorted(n for n in was if n in nb and nb[n][0] > nb[n][1])
                        still = sorted(n for n in was if n in nb and nb[n][0] == nb[n][1])
                        if inverted:
                            print(f"[level0b] enc={enc}: VACUOUS on " + ", ".join(inverted) +
                                  " — both directions held at a neighbour, so "
                                  "the interval inverted. This path has NO "
                                  "input under the current pins; its level-0 "
                                  "'point' measured nothing")
                        if still:
                            print(f"[level0b] enc={enc}: CONFIRMED point on " + ", ".join(still) +
                                  " — the neighbour was refuted, so this is a "
                                  "genuine single-value domain")
                    # The widened list descends to the ladder, which is where the
                    # existing empty-region guard turns an inversion into an
                    # excluded path without any new decision rule.
                    cand = cand2

    # ---- THE PATH-LABELLED POINT POOL, BUILT BEFORE THE LADDER IS LAID ----
    #
    # Placed HERE, after level 0 and before the bracket, because the neighbours
    # must be clamped to each coordinate's published TYPE RANGE and the ranges
    # are published BY a round -- level 0's, when it ran. Without --level0 the
    # ranges are still empty and the neighbours are left out and named, rather
    # than guessed: a probe above the type wraps and measures a different
    # number, which is how an impossible bracket (`lower in [2^255, 1)`) arose.
    #
    # `probe_extra` starts as `cand` exactly, so with --probe-witnesses 0 every
    # call below is byte-for-byte the call it was before this existed.
    prune, endpoints, kept_pool = {}, {}, {}
    probe_extra = dict(cand)
    if args.probe_witnesses:
        n_vec = {enc: len(members.get(enc) or []) for enc, _, _ in paths}
        if max(n_vec.values(), default=0) <= 1:
            # A REPORT-LEVEL REFUSAL, NOT A RESULT. One vector per path is what
            # a run WITHOUT --all-witnesses produces, so reading it as "no
            # coordinate varies" would report the absence of collection as the
            # absence of evidence -- the shape this project keeps paying for.
            print(f"[probe] ⚠ --probe-witnesses {args.probe_witnesses} was "
                  f"requested and every path came back with ONE input vector. "
                  f"Nothing below can fire. That is a statement about this "
                  f"report, not about the paths: it is what a run without "
                  f"--all-witnesses looks like")
        prune, endpoints, kept_pool, notes = known_inside(paths, members, coords, query_pins(),
                                                          type_ranges)
        for n in notes:
            print(n)
        varied = sorted(
            (enc, c, vs) for enc, per in kept_pool.items() for c, vs in per.items() if len(vs) > 1)
        # COLLECTED, not admitted. The pin filter runs between the two, and its
        # count is on the DISCARD line printed just above -- two numbers side by
        # side that mean different things is how this project has already made
        # one quantity read as another, so each says which it is.
        print(f"[probe] pool: {sum(n_vec.values())} input vector(s) COLLECTED "
              f"over {len(paths)} path(s) (the DISCARD line above, if any, "
              f"says how many of them were kept out of the pool); "
              f"{len(varied)} (path, coordinate) pair(s) PROVED not a point")
        for enc, c, vs in varied:
            print(f"[probe]   enc={enc} {c}: known members bracket "
                  f"[{vs[0]}, {vs[-1]}] ({len(vs)} distinct)")
        # ⛔ ONE DIRECTION ONLY. A pair NOT listed above is not thereby a point:
        # the solver varies what it likes and is under no obligation to move a
        # coordinate nothing asked about. Said here so the list above is not
        # read as a partition.
        if not varied:
            print("[probe] no (path, coordinate) pair varied. ⛔ That is NOT "
                  "evidence that any of them is a point -- a single value "
                  "proves nothing about the domain, only about the model the "
                  "solver happened to return")
        no_range = sorted(c for c in endpoints if c not in type_ranges)
        if no_range:
            print("[probe] ⚠ no TYPE RANGE published for " + ", ".join(no_range) +
                  ": their boundary NEIGHBOURS are left out, so the wall/hole "
                  "question is not asked there. Run with --level0 (which "
                  "publishes the ranges) to get them")
        for c, vs in endpoints.items():
            probe_extra[c] = sorted(set(probe_extra.get(c, ())) | set(vs))

    # ---- THE PER-PATH LADDER, ANCHORED AT EACH PATH'S OWN MEMBERS ----
    path_ladders = None
    if args.probe_ladder:
        if not args.probe_witnesses:
            raise SystemExit("[probe] --probe-ladder needs --probe-witnesses: the anchor is "
                             "the path's own KNOWN MEMBERS, and without extra witnesses "
                             "every path has exactly one, so the 'ladder' would be a single "
                             "point wearing the name of a bracket")
        path_ladders, no_tr, dropped = {}, set(), {}
        for enc, _, _ in paths:
            for c, vs in sorted((kept_pool.get(enc) or {}).items()):
                if c not in type_ranges:
                    # LEFT ON THE SHARED LADDER AND NAMED. Doubling outward
                    # without a type limit runs past the type, and such a value
                    # is built as a constant OF that type, so it wraps and the
                    # probe asks about a different number -- the defect that
                    # once produced `lower in [2^255, 1)`.
                    no_tr.add(c)
                    continue
                tlo, thi = type_ranges[c]
                path_ladders.setdefault(enc,
                                        {})[c] = outward_ladder(vs[0],
                                                                vs[-1],
                                                                tlo,
                                                                thi,
                                                                budget=args.probe_ladder_budget)
                if args.probe_ladder_budget:
                    dropped[(enc, c)] = (len(outward_ladder(vs[0], vs[-1], tlo, thi)) -
                                         len(path_ladders[enc][c]))
        if no_tr:
            print("[probe] ⚠ no TYPE RANGE published for " + ", ".join(sorted(no_tr)) +
                  ": they keep the SHARED ladder. Run with --level0, which "
                  "publishes the ranges, to anchor them too")
        n_rungs = sum(len(v) for per in path_ladders.values() for v in per.values())
        print(f"[probe] PER-PATH LADDER: "
              f"{sum(len(p) for p in path_ladders.values())} (path, "
              f"coordinate) pair(s) anchored at their own known members, "
              f"{n_rungs} rung(s) in total. Each doubles OUTWARD from the "
              f"member bracket, so the first rung sits one step beyond a value "
              f"already proved to be in the domain")
        for enc, per in sorted(path_ladders.items()):
            for c, vs in sorted(per.items()):
                mem = kept_pool[enc][c]
                print(f"[probe]   enc={enc} {c}: anchored at [{mem[0]}, "
                      f"{mem[-1]}], {len(vs)} rung(s)")
        # ---- NO SILENT CAP ----
        #
        # A capped ladder that printed only its own size would read as "this
        # coordinate needed 6 rungs", which is a statement about the contract.
        # It is a statement about the budget, so the number dropped is printed
        # beside it, with what the loss IS: a boundary further out than the last
        # kept rung is no longer bracketed and comes back as a span reaching the
        # type limit.
        n_dropped = sum(dropped.values())
        if n_dropped:
            print(f"[probe] LADDER CAPPED at --probe-ladder-budget "
                  f"{args.probe_ladder_budget} rung(s) per side: "
                  f"{n_dropped} rung(s) DROPPED across "
                  f"{sum(1 for v in dropped.values() if v)} (path, coordinate) "
                  f"pair(s), the ones FURTHEST from the known members. On a "
                  f"coordinate whose boundary is beyond the last kept rung the "
                  f"bracket now reaches the type limit and the refine round "
                  f"bisects it -- the same coarse outcome as the shared ladder, "
                  f"on that coordinate only. The anchors and both type limits "
                  f"are never dropped")
            for (enc, c), n in sorted(dropped.items()):
                if n:
                    print(f"[probe]   enc={enc} {c}: {n} rung(s) dropped")

    # ---- STRUCTURAL DECISION REGION FAST PATH ------------------------------
    #
    # Must run BEFORE the geometric bracket, because that bracket is the binding
    # cost on the exact class this shortcut handles. Measured on farming
    # setDistributor: level 0 decides the 2x2 source partition in 16s, then the
    # bracket/refine machinery spends the rest of a 120s attempt rediscovering
    # that `msg.value == 0`, `msg.sender == owner`, and `distributor_ != 0` are
    # equality/inequality gates. If every complete-path decision is already a
    # simple product constraint over rendered coordinates and pins, the region
    # is the decision tree itself and certification can be structural.
    structural_region_source = {}
    structural_holes = {}
    structural_reasons = {}
    structural_region_retreats = {}
    structural_region_establishes = {}
    if not paths:
        structural_regions = {}
    elif (structural_seed_regions
          and all(enc in structural_seed_regions or enc in seed_excluded_by_pin
                  for enc, _depth, _ce in paths)):
        structural_regions = dict(structural_seed_regions)
        structural_holes = dict(structural_seed_holes)
        structural_reasons = {}
        structural_region_retreats = dict(structural_seed_retreats)
        structural_region_establishes = dict(structural_seed_establishes)
    elif arith_conditions_seen:
        structural_regions, structural_holes, structural_reasons, \
            structural_region_retreats, structural_region_establishes = \
            structural_decision_regions_with_relations(
                paths, path_decisions, pins, coords, coord_types=coord_types,
                type_ranges=type_ranges, constants=constants)
        structural_region_retreats = structural_region_retreats or {}
        structural_region_establishes = structural_region_establishes or {}
        if structural_regions is not None and not any(
                list(structural_region_retreats.values()) +
                list(structural_region_establishes.values())):
            structural_regions = None
            structural_holes = {}
            structural_reasons = {}
            structural_region_retreats = {}
            structural_region_establishes = {}
    else:
        structural_regions, structural_holes, structural_reasons = \
            structural_decision_regions(
                paths, path_decisions, pins, coords, coord_types=coord_types,
                type_ranges=type_ranges, constants=constants)
        if structural_regions is None:
            structural_regions, structural_holes, structural_reasons, \
                structural_region_retreats, structural_region_establishes = \
                structural_decision_regions_with_relations(
                    paths, path_decisions, pins, coords,
                    coord_types=coord_types, type_ranges=type_ranges,
                    constants=constants)
            structural_region_retreats = structural_region_retreats or {}
            structural_region_establishes = structural_region_establishes or {}
            if structural_regions is not None and not any(
                    list(structural_region_retreats.values()) +
                    list(structural_region_establishes.values())):
                structural_regions = None
                structural_holes = {}
                structural_reasons = {}
                structural_region_retreats = {}
                structural_region_establishes = {}
    if paths and structural_regions is not None:
        brackets, regions, warned, round_failure = {}, structural_regions, \
            set(), None
        region_holes = structural_holes
        if structural_region_retreats or structural_region_establishes:
            structural_region_source = {}
            print("[structural] relation-aware simple decision regions "
                  f"derived for every witnessed path; skipping geometric "
                  f"bracket and refine. {len(regions)} region(s) now go to "
                  f"ESBMC certification, not structural certification, because "
                  f"the product box relies on a per-path entry-state relation" +
                  (" and checked-arithmetic conditions were seen elsewhere "
                   "in the enumeration" if arith_conditions_seen else ""))
        else:
            structural_region_source = structural_reasons
            print("[structural] simple decision regions derived for every "
                  f"witnessed path; skipping geometric bracket and refine. "
                  f"{len(regions)} region(s) now go directly to structural "
                  f"certification")
        for enc in sorted(regions):
            bits = []
            for n, (lo, hi) in sorted(regions[enc].items()):
                h = sorted((region_holes.get(enc) or {}).get(n, ()))
                bits.append(f"{n} in [{lo}, {hi}]" +
                            (f" \\ {{{', '.join(map(str, h))}}}" if h else ""))
            print(f"[structural] enc={enc}: " + ", ".join(bits))
            if structural_region_retreats.get(enc):
                print(f"[structural] enc={enc}: relation retreat " +
                      ", ".join(f"{n}=={v}"
                                for n, v in sorted(structural_region_retreats[enc].items())))
            if structural_region_establishes.get(enc):
                print(f"[structural] enc={enc}: relation establish " + ", ".join(
                    f"{target}:={source}"
                    for target, source in sorted(structural_region_establishes[enc].items())))
    elif paths:
        # Round 1: geometric bracket.
        if args.skip_bracket:
            brackets, regions, warned, round_failure = {}, {}, set(), None
            region_holes = {}
            print("[bracket] SKIPPED (--skip-bracket): refining from each "
                  "coordinate's full type range, which is the same fallback "
                  "the code takes when the bracket measures nothing")
        else:
            (_, brackets, regions, warned, round_failure, region_holes, tr_new,
             unres) = outer_round(args.esbmc,
                                  args.sol,
                                  args.contract,
                                  query_unit,
                                  paths,
                                  coords,
                                  query_pins(),
                                  args.probes,
                                  args.max_tx,
                                  args.timeout,
                                  cwd,
                                  geometric=True,
                                  ast=args.ast,
                                  focus=focus,
                                  memlimit=args.memlimit,
                                  values_by_coord=eq_values,
                                  extra_values=probe_extra,
                                  type_ranges=type_ranges,
                                  claim_budget=args.claim_budget,
                                  esbmc_args=args.esbmc_arg,
                                  prune_inside=prune if args.probe_witnesses else None,
                                  path_values=path_ladders)
            merge_type_ranges(tr_new)
            unresolvable.update(unres)
            drop_unresolvable_query_pins("bracket", unres)
            print(f"[bracket] {brackets}")
    # MEASURED, and it is the binding cost on real input: the geometric round
    # ignores --probes entirely (see geometric_values) and lays down one probe
    # per power of two, i.e. 258 candidate bounds per coordinate per direction.
    # For a 5-path, 2-coordinate unit that is over five thousand ladder probes
    # in one batch. On a toy contract it is instant; on EscrowSrc.withdraw the
    # round does not finish in 100s, while the SAME unit with one path, one
    # coordinate and one probe (6 ladder probes) finishes in about 2s and
    # produces a correct outer box, bracket and candidate region. Bounding the
    # bracket ladder is a METHOD decision -- it trades away the
    # "magnitude-independent in ONE run" property -- so it is an open item, not
    # something to invent here.
    last_failure = round_failure

    # Rounds 2..N: linear inside the union of the brackets, per coordinate.
    def _span(c):
        # Clamped to the coordinate's own type where it is known. A span whose
        # upper end is above the type maximum is a span the type cannot hold, so
        # every probe the tool lays inside it above that point is dropped -- and
        # what is left is the ladder crowded into the wrong place.
        lo, hi = (brackets_for(c, brackets, type_ranges.get(c)) or (0, UINT256_MAX))
        tlo, thi = type_ranges.get(c, (0, UINT256_MAX))
        return (max(lo, tlo), min(hi, thi))

    if structural_regions is None:
        spans = {c: _span(c) for c in coords}
        for r in range(args.refine_rounds):
            (_, brackets, regions, warned, round_failure, region_holes, tr_new,
             unres) = outer_round(args.esbmc,
                                  args.sol,
                                  args.contract,
                                  query_unit,
                                  paths,
                                  coords,
                                  query_pins(),
                                  args.probes,
                                  args.max_tx,
                                  args.timeout,
                                  cwd,
                                  spans=spans,
                                  ast=args.ast,
                                  focus=focus,
                                  memlimit=args.memlimit,
                                  values_by_coord=eq_values,
                                  extra_values=probe_extra,
                                  type_ranges=type_ranges,
                                  esbmc_args=args.esbmc_arg)
            merge_type_ranges(tr_new)
            unresolvable.update(unres)
            drop_unresolvable_query_pins(f"refine {r + 1}", unres)
            last_failure = round_failure or last_failure
            print(f"[refine {r+1}] spans={spans} regions={regions}" +
                  (f" holes={ {k: v for k, v in region_holes.items() if v} }"
                   if any(region_holes.values()) else "") +
                  (f" UNSEPARATED={sorted(warned)}" if warned else ""))
            new = {c: (brackets_for(c, brackets, type_ranges.get(c)) or spans[c]) for c in coords}
            if new == spans:
                break
            spans = new

    dropped_by_certify = set(pre_dropped_unresolvable)
    if unresolvable:
        # STATE ONLY WHAT IS TRUE AT THIS POINT. An earlier version of this line
        # asserted "no region below carries a bound on them and each holds for
        # ALL their values" -- which is false while the name is still sitting in
        # `pins`, and a pinned one DOES reach the certify spec (pins are folded
        # into the box there) and DOES refuse the query. The two branches
        # disagree about these names, and that disagreement is the fact worth
        # printing; the consequence is printed later, by the branch that
        # actually acts.
        pinned_too = sorted(n for n in unresolvable if n in pins)
        print("[coords] the outer-box rounds refused " + ", ".join(sorted(unresolvable)) +
              " as coordinate(s) of this unit, so no OUTER BOX carries a "
              "bound on them" + (f". NOTE: {', '.join(pinned_too)} is/are still PINNED, and the "
                                 f"certification branch folds pins into the box, so the query "
                                 f"may be refused on them and the pin dropped below" if pinned_too
                                 else " and every region below holds for ALL their values"))
        pre_dropped = drop_unexpressible_query_names(unresolvable, pins, regions, region_holes,
                                                     structural_seed_regions, structural_seed_holes,
                                                     pre_structural_regions, pre_structural_holes)
        if pre_dropped:
            dropped_by_certify.update(pre_dropped)
            print("[certify] PRE-DROPPED " + ", ".join(sorted(pre_dropped)) +
                  " before the first certification query because the "
                  "outer-box round has already proved ESBMC cannot express "
                  "these name(s) as unit coordinates. Re-querying them once "
                  "per path would only reproduce a known refusal; every "
                  "certified region below therefore holds for ALL values of "
                  "the dropped name(s)")

    # Certify every candidate, shrinking on the witness when refuted.
    failed = dict(pin_excluded)
    failed.update(pre_failed)
    ok, ok_holes, ok_retreated, ok_established, ok_source = \
        {}, {}, {}, {}, {}
    # Whether certification had to change the first candidate region after a
    # refutation.  RQ3 derives the no-region-refinement arm from Full using
    # this per-piece fact; recording configured round counts is insufficient,
    # because those counts do not say whether a particular path used them.
    ok_refinement_used = {}
    # Per (enc, piece), like ok_retreated and for the same reason: a region
    # reported without naming the harness-chosen values it was certified under
    # reads as an unconditional statement, and the emitter's rendering decision
    # depends on the difference.
    ok_extcall = {}
    # §Certification's floor, per path: what the single-point query answered.
    # A path absent from here was never asked (--no-witness-check), which is a
    # DIFFERENT state from "asked and discharged" and must stay one.
    witness_check = {}

    # Names the certify branch refused and the loop dropped. Accumulated across
    # paths because `pins` is global, so the drop is announced once but affects
    # every path after it.

    def run_single_point_witness_check(enc, depth, ce, xpins, establishes):
        """Run §Certification's concrete-replay floor for one failed path."""
        if not args.witness_check:
            return ""
        point = {c: (ce[c], ce[c]) for c in coords if c in ce}
        if not point:
            witness_check[enc] = "NOT-PUT"
            return (" ⚠ The single-point check of §Certification was NOT put: "
                    "none of this path's free coordinates carries a counterexample "
                    "value, so there is no point to fix. Whether its witness trips "
                    "a compiler-inserted check is therefore UNKNOWN, not clear")

        wv, _wnb, _ww, _wp, _wunexp, wwhy = certify(args.esbmc,
                                                    args.sol,
                                                    args.contract,
                                                    query_unit,
                                                    enc,
                                                    depth,
                                                    point,
                                                    ce,
                                                    dict(query_pins(), **xpins),
                                                    args.max_tx,
                                                    args.timeout,
                                                    cwd,
                                                    ast=args.ast,
                                                    focus=focus,
                                                    memlimit=args.memlimit,
                                                    holes={},
                                                    esbmc_args=args.esbmc_arg,
                                                    want_property=True,
                                                    establish=dict(establishes or {}),
                                                    param_types=enumeration_param_types,
                                                    state_types=enumeration_state_types,
                                                    extcall_coord_specs=extcall_length_specs)
        witness_check[enc] = wv
        if wv == "SUCCESSFUL":
            print(f"[witness enc={enc}] the single point survives the inserted "
                  "checks, so the concrete replay test stands")
            return (". The single-point check of §Certification was PUT and "
                    "DISCHARGED, so this path's witness satisfies the "
                    "compiler-inserted checks and its concrete replay test stands")
        if wv == "FAILED":
            print(f"[witness enc={enc}] ⛔ REFUTED at the single point: this "
                  "path gets NO test")
            return (". ⛔ AND NO TEST IS EMITTED FOR IT. The single-point check of "
                    "§Certification was put on {x_pi} and REFUTED: some input "
                    "satisfying every pinned coordinate does NOT walk this path. "
                    "⚠ TWO causes are possible and this verdict does not separate "
                    "them, so neither may be quoted as the reason. (a) the witness "
                    "trips a compiler-inserted check -- enumeration keeps those "
                    "out of a path's identity and certification turns them on, "
                    "which is why the method puts this query at all. (b) a "
                    "quantity OUTSIDE the coordinate set is still free: the point "
                    "pins only the free coordinates, and the method says of the "
                    "rest that certification 'quantifies over it: where varying it "
                    "can lead away from π, that query is refuted'. On this unit "
                    "the refused and unmodelled quantities include the mapping "
                    "slots and any external-call return. Either way the path's own "
                    "counterexample is not shown to walk it under the checks a real "
                    "run performs, so no test is emitted. " +
                    (wwhy or "No `Violated property` block was harvested for this "
                     "refutation, so which of the two causes applies stays "
                     "UNKNOWN"))
        print(f"[witness enc={enc}] single-point check {wv}; the replay test "
              "is NOT cleared")
        return (f". The single-point check of §Certification came back {wv}" +
                (f" ({wwhy})" if wwhy else "") +
                ", so whether this witness trips a compiler-inserted check is "
                "UNDECIDED. It is NOT cleared -- an undecided answer is not a "
                "discharged one")

    def concrete_fallback_cleared(enc):
        if enc in pre_failed:
            return False
        if not args.witness_check:
            return True
        return witness_check.get(enc) == "SUCCESSFUL"

    for enc, box in sorted(pre_structural_regions.items()):
        key = (enc, 1)
        ok[key] = dict(box)
        ok_holes[key] = copy_holes(pre_structural_holes.get(enc) or {})
        ok_refinement_used[key] = False
        if str(pre_structural_source.get(enc)
               or "").startswith("STRUCTURAL external-call array length no-loop region"):
            ok_source[key] = "structural-extcall-length-no-loop"
        else:
            ok_source[key] = "structural-simple-decision"
        witness_check[enc] = "STRUCTURAL"
        print(f"[certify enc={enc}] STRUCTURAL simple decision region: "
              f"{pre_structural_source[enc]}. No ESBMC certification query "
              "is started for this path.")
    for enc, depth, ce in paths:
        box = regions.get(enc)
        if enc in structural_seed_regions:
            box = structural_seed_regions[enc]
            print(f"[certify enc={enc}] using relation-aware structural "
                  f"seed: {structural_seed_source[enc]}. ESBMC certification "
                  f"is still required for this path.")
        if box is None:
            excluded_by_pin = ce_in_region({n: (pv, pv) for n, pv in pins.items()}, {}, ce)
            if excluded_by_pin:
                failed[enc] = (f"EXCLUDED FROM THE SLICE by the pins "
                               f"({'; '.join(excluded_by_pin)}), so no product region "
                               f"was searched for this path. ⛔ This is NOT a failure to "
                               f"certify: this path's own counterexample does not satisfy "
                               f"the pins, so the path was never in the slice being "
                               f"generalised. For the ABI-value gate path of a non-payable "
                               f"unit that is exactly what auto-pinning msg.value costs, "
                               f"and it is announced when the pin is applied. Counting it "
                               f"against the certification rate prices a stated design "
                               f"cost as a search result")
                continue
            failed[enc] = (last_failure or "no fully bounded region was measured")
            continue
        # The punched set travels WITH the box through the whole shrink loop.
        # A side cut applied below narrows the interval, and a hole outside the
        # narrowed interval removes nothing -- but it is also harmless to keep,
        # and dropping it here would need its own justification, so it stays and
        # the tool's own emptiness check is the arbiter.
        holes = dict(structural_seed_holes.get(enc) or (region_holes.get(enc) or {}))
        xpins = dict(path_extras.get(enc, {})) if args.pin_extcall else {}
        empty = empty_coords(box, holes)
        if empty:
            # ---- THE PIN ATTRIBUTION IS NAMED ON THE OTHER BRANCH ONLY, AND
            # ---- THIS IS THE BRANCH THAT ACTUALLY FIRES ON THE CORPUS ----
            #
            # The SUCCESSFUL branch below already runs exactly this check and
            # already has the right sentence for it ("this path is EXCLUDED
            # FROM THE SLICE by the pins ... for the ABI-value gate path under
            # a non-payable unit this is exactly what auto-pinning msg.value
            # costs"). A path whose region came back EMPTY never reaches it --
            # it is `continue`d right here -- so on the corpus that detector
            # fires ZERO times while the case it describes is the single most
            # common non-certification reason.
            #
            # MEASURED, results_pieces_corpus.jsonl read row by row: 14 of the
            # 113 witnessed paths carry this generic sentence, one per unit,
            # always the shallowest enc, and every one of those units has
            # `msg_value_pin: fired`. That is 12% of the denominator recorded
            # as "could not certify" when the honest statement is "the driver's
            # own auto-pin excluded it, by design, and said so when it applied
            # the pin".
            #
            # THE CHECK IS A DISCRIMINATOR, NOT A RELABEL, and it has to be:
            # the path's OWN counterexample is a known member of its domain, so
            # a CE that violates a pin is PROOF the pin excluded this path,
            # while a CE satisfying every pin leaves the original sentence
            # standing untouched. Both outcomes are reachable on real input --
            # the ABI-gate path takes the first, a region emptied by
            # subtraction takes the second.
            excluded_by_pin = ce_in_region({n: (pv, pv) for n, pv in pins.items()}, {}, ce)
            if excluded_by_pin:
                failed[enc] = (f"EXCLUDED FROM THE SLICE by the pins "
                               f"({'; '.join(excluded_by_pin)}), which is why its region "
                               f"came back EMPTY on {', '.join(empty)}. ⛔ This is NOT a "
                               f"failure to certify: this path's own counterexample does "
                               f"not satisfy the pins, so the path was never in the slice "
                               f"being generalised. For the ABI-value gate path of a "
                               f"non-payable unit that is exactly what auto-pinning "
                               f"msg.value costs, and it is announced when the pin is "
                               f"applied. Counting it against the certification rate "
                               f"prices a stated design cost as a search result")
                continue
            # Sibling subtraction can invert a box even though this path's
            # enumerated counterexample is a known member. Keep that witness
            # as a non-empty singleton seed, then use the normal certification
            # loop below; this does not turn the witness into a proof.
            point = {n: (ce[n], ce[n]) for n in box if n in ce}
            point_bad = ce_in_region(point, holes, ce)
            missing = sorted(set(box) - set(point))
            if not missing and not point_bad:
                print(f"[certify enc={enc}] inverted outer region recovered "
                      "as the enumerated counterexample singleton; normal "
                      "ESBMC certification is still required")
                box, holes, empty = point, {}, []
            else:
                failed[enc] = (f"region is EMPTY on {', '.join(empty)} (lo > hi) under the "
                               f"current pins, so this path has no domain in this slice; "
                               f"certifying it would hold vacuously. The path's own "
                               f"counterexample DOES satisfy every pin, so the emptiness is "
                               f"not attributable to them -- it came out of the subtraction")
                failed[enc] += run_single_point_witness_check(
                    enc, depth, ce, xpins,
                    dict(
                        structural_seed_establishes.get(enc)
                        or structural_region_establishes.get(enc) or {}))
                continue
        if enc in warned:
            # Not fatal: certification is the arbiter. But say it, because a
            # region that a cut could not separate is EXPECTED to be refuted.
            print(f"[certify enc={enc}] region overlaps an unseparated sibling; "
                  f"certifying anyway, the query is what decides")
        if enc in structural_region_source:
            key = (enc, 1)
            ok[key] = dict(box)
            ok_holes[key] = copy_holes(holes)
            ok_source[key] = "structural-simple-decision"
            witness_check[enc] = "STRUCTURAL"
            print(f"[certify enc={enc}] {structural_region_source[enc]}. "
                  f"No ESBMC certification query is started for this path.")
            continue
        structural = structural_abi_gate_certificate(path_decisions.get(enc), box, holes, ce)
        if structural:
            print(f"[certify enc={enc}] structural ABI gate recognized: "
                  f"{structural}. The normal ESBMC k-induction and non-vacuity "
                  "query is still required.")
        # THE WITNESS AND THE BOX IT WAS SOLVED UNDER TRAVEL TOGETHER.
        #
        # They must, and getting this wrong produced a FALSE POSITIVE on real
        # input that I very nearly built a diagnosis on. `box` advances at the
        # bottom of this loop; `last_wit` is the witness of the round BEFORE the
        # advance. Comparing that witness against the final `box` is comparing it
        # against an assumption it was never solved under -- and since each
        # shrink cuts at the witness, the witness is reliably just OUTSIDE the
        # next box. So the trust check reported "the witness value contradicts
        # the bound this query assumed" on every budget-exhausted path, always,
        # from arithmetic rather than from anything about the model.
        #
        # Measured: EscrowSrc.cancel, last shrink (0, 268214519) -> (0,
        # 134127735), witness 134127736. Inside the box it was actually solved
        # under; outside the box it was checked against. Four paths, four
        # spurious contradictions, and the anti-collapse branch fired for a wrong
        # reason -- which is worse than not firing, because its whole purpose is
        # to say the payload could not be compared.
        #
        # The independent check that caught it: pinning FACTORY to a point
        # reports that point, and bounding it to [0,100] reports 52. The bound
        # binds and the harvest is faithful, so "reported outside the bound" had
        # to be the checker's error, not the model's.
        # ---- S3: a path's region is a LIST of boxes, not one box ----
        #
        # The queue holds pieces still to certify. It starts as the single
        # measured region and only ever grows when a refutation's cut discards a
        # side AND the piece budget allows that side to be pursued. At
        # --max-region-pieces 1 nothing is ever enqueued, so this is the old
        # single-box loop with the same prints and the same reasons.
        #
        # `has_ce` travels with each piece because it decides which guarantee
        # applies. The piece holding the path's own counterexample is checked by
        # C2 (a known member must survive). A piece that does not hold it has NO
        # known member, so its guarantee is the tool's non-vacuity witness alone
        # -- which is a real guarantee (the query is refuted exactly when the
        # box admits an execution walking the path) but a different one, and the
        # report must not present the two as the same thing.
        # ---- WHAT THE HARNESS CHOSE, FIXED AT THIS PATH'S OWN VALUE ----
        #
        # PER PATH, not global like `pins`. The sibling paths of one call site
        # differ in exactly this quantity -- measured on
        # notes/coverage/poc/B5_ExtcallInCallee, enc=6 carries success=0 and
        # enc=7 carries success=1 -- so a single dict shared across paths would
        # certify one of them about the other's slice.
        if xpins:
            print(f"[certify enc={enc}] --pin-extcall: fixing " +
                  ", ".join(f"{n}=={v}" for n, v in sorted(xpins.items())) +
                  " at this path's counterexample value. These are NOT call "
                  "arguments: the region below holds of the executions in "
                  "which the callee behaved this way, and a test rendering it "
                  "has to realise that some other way")
        queue = [(dict(box), copy_holes(holes), True, False)]
        piece_no, piece_fail = 0, []
        while queue:
            box, holes, has_ce, refinement_used = queue.pop(0)
            piece_no += 1
            tag = (f"enc={enc}" if args.max_region_pieces <= 1 else f"enc={enc} piece {piece_no}")
            last_wit, last_wit_box = {}, dict(box)
            # C3: |R| may only ever get NARROWER across shrink rounds. Seeded
            # with the piece as measured, then compared after every accepted
            # cut. Per piece: a sibling piece is a different set and comparing
            # sizes across them would compare two regions that were never in a
            # narrowing relation at all.
            prev_size = region_size(box, holes)
            reason = None
            # PARTIAL GENERALISATION, per piece. Coordinates this piece gave up
            # and pinned at their x_pi value rather than losing the whole path.
            # Per piece and not per path: two pieces of one path are different
            # sets and may retreat on different coordinates.
            retreated = dict(
                structural_seed_retreats.get(enc) or structural_region_retreats.get(enc) or {})
            established = dict(
                structural_seed_establishes.get(enc) or structural_region_establishes.get(enc)
                or {})
            tiny_safety_cut_coord, tiny_safety_cut_streak = None, 0
            for _ in range(args.shrink_rounds):
                v, nb, wit, punches, unexp, unknown_why = certify(
                    args.esbmc,
                    args.sol,
                    args.contract,
                    query_unit,
                    enc,
                    depth,
                    box,
                    ce,
                    dict(query_pins(), **xpins),
                    args.max_tx,
                    args.timeout,
                    cwd,
                    ast=args.ast,
                    focus=focus,
                    memlimit=args.memlimit,
                    holes=holes,
                    esbmc_args=args.esbmc_arg,
                    state_structs=args.state_struct_fields,
                    establish=established,
                    param_types=enumeration_param_types,
                    state_types=enumeration_state_types,
                    extcall_coord_specs=extcall_length_specs)
                # ---- A COORDINATE THE QUERY CANNOT EXPRESS: DROP AND RETRY ----
                #
                # Not a shrink round. The tool declined to ATTEMPT the query, so
                # nothing was measured and consuming budget for it would let one
                # unexpressible pin exhaust the whole path. Bounded by
                # construction: each pass removes at least one name from a
                # finite set or stops.
                #
                # Dropping is sound in the only direction that matters -- an
                # unmentioned coordinate is universally quantified, so the
                # certificate gets STRONGER, not wider. It is announced anyway,
                # because it changes what the region is a statement about.
                while unexp:
                    # `xpins` is in the list because the merged dict is what
                    # reaches the spec: a refusal naming one of these names
                    # would otherwise fall into the "this driver holds no pin
                    # under that spelling" hard stop, which is true of `pins`
                    # and false of the query that was actually sent.
                    gone = [n for n in unexp if n in pins or n in box or n in holes or n in xpins]
                    if not gone:
                        # THE TOOL REFUSED AND THIS DRIVER CANNOT ACT ON IT --
                        # a name under a spelling we do not hold. Falling
                        # through here would carry the outstanding refusal into
                        # the normal verdict handling: at best it lands on
                        # UNKNOWN and reports "ESBMC printed neither SUCCESSFUL
                        # nor FAILED", the exact true-and-useless sentence this
                        # whole change exists to remove; at worst the run also
                        # printed a whole-line VERIFICATION SUCCESSFUL and a
                        # query the tool DECLINED TO ATTEMPT is recorded as a
                        # certified region. Neither is acceptable, so this is a
                        # hard stop with the names in it.
                        reason = ("the certification query was REFUSED on " + ", ".join(unexp) +
                                  ", and this driver holds no pin, bound or hole "
                                  "under that spelling, so it could not drop them "
                                  "and re-query. NOTHING WAS MEASURED for this "
                                  "path -- this is a refusal of the query, not a "
                                  "property of the path")
                        break
                    for n in gone:
                        pins.pop(n, None)
                        xpins.pop(n, None)
                        box.pop(n, None)
                        holes.pop(n, None)
                    # RECORDED, because `pins` is GLOBAL across paths: only the
                    # first path that hits the refusal prints this line, and
                    # every later path's region silently no longer carries the
                    # pin with nothing per-path to say so. It also leaves C5's
                    # coordinate accounting -- which ran once, before any query
                    # -- no longer true: the name is now in no bucket at all.
                    # Re-reported under the regions, where a reader of the final
                    # block can see it.
                    dropped_by_certify.update(gone)
                    print(f"[certify {tag}] DROPPED " + ", ".join(gone) +
                          " — the certification query cannot express "
                          "it (a mapping, a dynamic array or a `constant` is "
                          "lowered to a contract-scope global, not a "
                          "component of the contract object), and it refuses "
                          "the WHOLE query on one such name. Without this the "
                          "unit returns no verdict on every path. Every "
                          "region below therefore holds for ALL values of it, "
                          "which is a STRONGER statement than the slice that "
                          "was asked for. Re-querying")
                    prev_size = region_size(box, holes)
                    v, nb, wit, punches, unexp, unknown_why = certify(
                        args.esbmc,
                        args.sol,
                        args.contract,
                        query_unit,
                        enc,
                        depth,
                        box,
                        ce,
                        dict(query_pins(), **xpins),
                        args.max_tx,
                        args.timeout,
                        cwd,
                        ast=args.ast,
                        focus=focus,
                        memlimit=args.memlimit,
                        holes=holes,
                        # ⛔ THIS RE-QUERY DROPPED --esbmc-arg, and that
                        # contradicts the flag's own stated rule: "APPLIED TO
                        # EVERY INVOCATION on purpose: a bound that differs
                        # between the round that measured a region and the query
                        # that certifies it is two measurements wearing one
                        # name." A path that hits the drop-and-retry branch was
                        # silently re-certified under the DEFAULT unwind while
                        # every other query used the caller's. Found while
                        # threading state_structs through the same two call
                        # sites; fixed here rather than left in a line this
                        # commit already touches.
                        esbmc_args=args.esbmc_arg,
                        state_structs=args.state_struct_fields,
                        establish=established,
                        param_types=enumeration_param_types,
                        state_types=enumeration_state_types,
                        extcall_coord_specs=extcall_length_specs)
                if reason is not None:
                    # Set only by the un-droppable-refusal branch above. Leaving
                    # the for-loop here is what keeps a refused query out of the
                    # SUCCESSFUL / VACUOUS / UNKNOWN handling below.
                    break
                if wit:
                    last_wit = wit
                    last_wit_box = dict(box)
                if v == "SUCCESSFUL":
                    # C2, BEFORE the region is recorded as certified. A region
                    # that excludes this path's own counterexample is certified
                    # about a set the path does not have, and the CE is the one
                    # member of the domain we know for certain.
                    #
                    # APPLIED ONLY TO THE PIECE THAT HOLDS THE CE. On any other
                    # piece the CE is outside BY CONSTRUCTION -- that is what
                    # made it a separate piece -- so running C2 there would
                    # reject every piece S3 exists to keep. The guarantee that
                    # replaces it is the tool's own non-vacuity witness, which
                    # this run has already passed to reach SUCCESSFUL.
                    if has_ce:
                        # ---- THE PINS MUST BE IN THE PICTURE ----
                        #
                        # `ce_in_region(box, ...)` cannot see a pin: pins never
                        # enter `box`, they are folded in only when `certify`
                        # builds the spec. So the half of C2 its own docstring
                        # claimed -- "a --pin the caller supplies can conflict
                        # with the CE outright" -- was structurally dead.
                        #
                        # S10 made it live. `pins["msg.value"] = 0` is applied to
                        # EVERY path of a non-payable unit, including the ABI
                        # gate path whose entire domain is msg.value != 0, i.e.
                        # the one path whose counterexample the pin is
                        # GUARANTEED to exclude. Until now that was caught only
                        # by something external -- the tool answering VACUOUS,
                        # or the outer round inverting an interval.
                        #
                        # It is reported SEPARATELY from a C2 violation, and the
                        # distinction is the whole point. A CE outside the BOX
                        # means a cut carved into the real domain: a defect. A
                        # CE outside a PIN means the path is excluded from the
                        # slice the caller asked about: not a defect, and the
                        # honest statement about it. Merging them would file
                        # S10's stated cost as a bug in the subtraction.
                        excluded = ce_in_region({n: (v, v) for n, v in pins.items()}, {}, ce)
                        if excluded:
                            reason = ("this path is EXCLUDED FROM THE SLICE by the "
                                      "pins (" + "; ".join(excluded) + "). Its own "
                                      "counterexample does not satisfy them, so the "
                                      "region certified here is not about this path's "
                                      "domain. For the ABI-value gate path under a "
                                      "non-payable unit this is exactly what "
                                      "auto-pinning msg.value costs, and it is a "
                                      "statement about the slice, not a defect in the "
                                      "subtraction")
                            break
                        missing = ce_in_region(box, holes, ce)
                        if missing:
                            reason = ("CERTIFIED region does NOT contain this path's "
                                      "own counterexample (" + "; ".join(missing) +
                                      "). The CE is a known member of the domain -- "
                                      "the enumeration witnessed the path with it -- "
                                      "so the region has been cut into the real "
                                      "domain and the certificate is about a "
                                      "different set. Refusing to report it as "
                                      "certified")
                            break
                    else:
                        print(f"[certify {tag}] certified WITHOUT a known "
                              f"member: this piece does not contain the path's "
                              f"counterexample, so C2 does not apply and the "
                              f"non-emptiness guarantee is the tool's "
                              f"non-vacuity witness alone")
                    ok[(enc, piece_no)] = box
                    # DEEP-COPIED ON STORE for the same reason it is deep-copied
                    # on enqueue: `holes` keeps being mutated by later rounds of
                    # sibling pieces, and a stored region that changes after it
                    # was certified is a report about a query nobody issued.
                    ok_holes[(enc, piece_no)] = copy_holes(holes)
                    # WHAT WAS GIVEN UP travels with what was certified. A
                    # region reported without naming its retreated coordinates
                    # reads as a full generalisation, and the emitter's
                    # parameterized/concrete decision depends on the
                    # difference.
                    ok_retreated[(enc, piece_no)] = dict(retreated)
                    ok_refinement_used[(enc, piece_no)] = bool(refinement_used)
                    ok_established[(enc, piece_no)] = dict(established)
                    ok_extcall[(enc, piece_no)] = dict(xpins)
                    if retreated:
                        print(f"[certify {tag}] certified with "
                              f"{len(retreated)} coordinate(s) PINNED at x_pi "
                              f"(partial generalisation): " +
                              ", ".join(f"{n}=={v}" for n, v in sorted(retreated.items())))
                    break
                if v == "VACUOUS":
                    # The box admits NO execution that walks this path. Neither
                    # accepting nor shrinking is defensible: accepting certifies
                    # a region containing no input, and shrinking responds to an
                    # empty box by making it emptier. Recorded as its own
                    # reason, because the cause is upstream -- the region came
                    # from a subtraction or a pin that excluded this path from
                    # the slice entirely -- and naming it as a refutation would
                    # send the reader looking at the solver instead.
                    #
                    # ON A SPLIT PIECE THIS IS THE EXPECTED OUTCOME, not a
                    # defect: the discarded side of a cut may hold no domain at
                    # all, and that is precisely the question S3 pays a query to
                    # ask instead of assuming the answer either way.
                    reason = ("region is VACUOUS: the certification query witnessed "
                              "NO execution admitted by it that walks this path, so "
                              "every exit assert held for want of an execution. "
                              "Before the non-vacuity witness existed this printed as "
                              "a certificate")
                    break
                if v == "UNDECIDED_TRUNCATED":
                    # THE BOUND MAY HAVE MANUFACTURED THE ANSWER.
                    #
                    # Handled EXPLICITLY and next to VACUOUS, because that is
                    # the verdict it replaces: the tool would have said VACUOUS
                    # and the loop would have recorded "this path has no domain
                    # in this slice" -- a statement about the REGION -- when the
                    # truth was a statement about the unwind bound. Recording it
                    # as vacuous is how a perfectly good region gets dropped;
                    # recording it as UNKNOWN loses the one thing that makes it
                    # different, namely that it names its own repair.
                    #
                    # Not shrunk and not accepted, for the same reason VACUOUS
                    # is neither: there is nothing to cut (no witness was
                    # obtained) and nothing to certify (no execution was
                    # explored on this path).
                    reason = ("the certification query returned "
                              "UNDECIDED-TRUNCATED: a loop was cut at the unwind "
                              "bound while unwinding assertions were disabled, so "
                              "the executions that would witness this path may have "
                              "been ASSUMED AWAY rather than shown not to exist. "
                              "This is NOT 'the region is vacuous' -- the region may "
                              "be perfectly non-empty and the bound is what could "
                              "not see it" + (f" ({unknown_why})" if unknown_why else "") +
                              ". Re-run this path with a larger --unwind, or "
                              "--unwindset/--unwindsetname on the loop(s) named, "
                              "to get a verdict")
                    break
                if v == "UNKNOWN":
                    # No verdict at all -- ESBMC crashed, was killed, or
                    # produced neither line. Shrinking here would treat "we
                    # never found out" as "refuted" and would quietly hand back
                    # a NARROWER box that nothing ever checked.
                    # NAME THE CAUSE. On the first full corpus sweep this was
                    # the second largest failure bucket -- 22 paths -- and the
                    # message said only that neither verdict line was printed,
                    # which cannot distinguish a timeout from a crash from an
                    # unresolvable coordinate. `round_failure_reason` already
                    # separates those three and was only ever applied to
                    # outer-box rounds.
                    reason = ("no verdict from the certification query "
                              "(ESBMC printed neither SUCCESSFUL nor FAILED)" +
                              (f" — {unknown_why}"
                               if unknown_why else ". Its output names no timeout, no bad exit "
                               "code and no unresolvable coordinate, so the "
                               "cause is NOT one of the three this driver "
                               "knows how to name"))
                    break
                # ---- S4: PREFER THE PUNCH, under a stated budget ----
                #
                # A hole removes ONE value; a side cut removes a whole side
                # chosen by whichever counterexample the solver returned. So
                # where a punch is available it is both the larger surviving
                # region and the one that does not depend on a value nobody
                # picked.
                #
                # BUDGETED, because it is not strictly better: punching
                # converges only when the excluded set is a few points, and
                # against a boundary that is an interval it would punch forever,
                # one value per round, while a side cut crosses it at once.
                # `--max-holes` is that policy, it is stated rather than
                # inferred, and when it is exhausted the loop falls back to the
                # side cut it always used. MEASURED on the S4 fixture: with a
                # budget of 2 the punched region ended WIDER than the unpunched
                # one, which is that caveat happening rather than being argued.
                #
                # Applied ONLY when the tool actually suggested one, so a log
                # carrying only a SHRINK line drives exactly the path it did
                # before this existed -- which is what the must-flip test pins.
                usable = [(c, val) for c, val in punches if len(holes.get(c, ())) < args.max_holes]
                if usable:
                    refinement_used = True
                    tiny_safety_cut_coord, tiny_safety_cut_streak = None, 0
                    for c, val in usable:
                        holes.setdefault(c, [])
                        if val not in holes[c]:
                            holes[c].append(val)
                            holes[c].sort()
                    new_size = region_size(box, holes)
                    if new_size > prev_size:
                        reason = (f"INVARIANT VIOLATED: a PUNCH widened the region "
                                  f"(|R| {prev_size} -> {new_size}), which no hole "
                                  f"can do")
                        break
                    prev_size = new_size
                    print(f"[punch {tag}] " + ", ".join(f"{c} != {val}"
                                                        for c, val in usable) + f"  |R| {new_size}")
                    continue
                # ---- §Certification DECIDES WHAT A REFUTATION MEANS --------
                #
                # `nb` above is the TOOL's first suggestion, applied unread.
                # Under the default policy it is discarded and the cut is
                # derived here from the two counterexamples, which is what the
                # method specifies and what `refutation_response` documents.
                if args.cut_policy == "spec":
                    _rng = assumed_ranges(last_wit_box, pins)
                    _ahs = assumed_holes(holes, pins)
                    kind, payload = refutation_response(box, holes, ce, last_wit, pins, _rng, _ahs)
                    if kind == "coords-gate":
                        # NOT another round. The method routes this to the
                        # coordinate gate and names the responsible quantity;
                        # `divergence_text` is what names it, from the report's
                        # own extraction caveats rather than by inference.
                        reason = (
                            "REFERRED TO THE COORDINATE GATE (method "
                            "§Coordinates), not retried: the refuting witness "
                            "and this path's counterexample agree on every "
                            "coordinate, so what separates them is a quantity "
                            "no test can set. §Certification sends such a path "
                            "to the coordinate gate INSTEAD of to another "
                            "shrink round, because no cut on any coordinate "
                            "could answer it" +
                            divergence_text(dict(ce, **path_extras.get(enc, {})), last_wit,
                                            set(last_wit_box) | set(pins), caveats, _rng, _ahs))
                        break
                    if kind in ("untrusted", "no-payload"):
                        reason = (
                            "refuted, and the refutation could NOT be compared "
                            "against this path's counterexample, so no cut is "
                            "derivable -- this is not the agree-on-everything "
                            "case" +
                            divergence_text(dict(ce, **path_extras.get(enc, {})), last_wit,
                                            set(last_wit_box) | set(pins), caveats, _rng, _ahs))
                        break
                    if kind == "no-retreat":
                        # THE RETREAT DOES NOT APPLY TO THIS PIECE.
                        #
                        # Reported as its own reason and NOT as the coordinate
                        # gate, because the two say opposite things about where
                        # the limitation is. The gate means the witness and
                        # x_pi agree on everything the payload carries, i.e.
                        # ESBMC did not harvest the separating quantity. This
                        # means they DIFFER, and the differences sit on
                        # coordinates whose x_pi value this piece was built to
                        # exclude -- a fact about S3's split, not about the
                        # model. Filing it as the gate would inflate the
                        # harvesting bucket with our own bookkeeping.
                        reason = (
                            "refuted, and §Certification's retreat does not "
                            "apply to this PIECE: every coordinate the "
                            "refutation points at (" + ", ".join(payload) +
                            ") has its x_pi value OUTSIDE this piece's own "
                            "interval. A piece produced by a split excludes "
                            "x_pi by construction, so pinning at x_pi would "
                            "not narrow the piece -- it would replace it "
                            "with a point that is not in it, and every piece "
                            "of this path would collapse onto the same set. "
                            "⛔ This is NOT the coordinate gate: the witness "
                            "and x_pi DO differ here" +
                            divergence_text(dict(ce, **path_extras.get(enc, {})), last_wit,
                                            set(last_wit_box) | set(pins), caveats, _rng, _ahs))
                        break
                    if kind == "pin":
                        tiny_safety_cut_coord, tiny_safety_cut_streak = None, 0
                        # PARTIAL GENERALISATION. The method: "A region with
                        # some coordinates pinned and others left to range says
                        # less than one in which all of them range, more than a
                        # test at a single point, and it is the common
                        # outcome." Applied to `box` and not to `pins`: `pins`
                        # is global across paths and is the slice the CALLER
                        # asked about, while this retreat is this path's own.
                        applied = {n: v for n, v in payload.items() if box.get(n) != (v, v)}
                        if applied:
                            refinement_used = True
                            for n, v in applied.items():
                                box[n] = (v, v)
                            retreated.update(applied)
                            prev_size = region_size(box, holes)
                            print(f"[retreat {tag}] PINNED " +
                                  ", ".join(f"{n}=={v}" for n, v in sorted(applied.items())) +
                                  f" at its x_pi value and carrying on with "
                                  f"the others  |R| {prev_size}")
                            continue
                        # Nothing moved: every coordinate the refutation points
                        # at is already a point. There is no further retreat.
                        reason = (
                            "refuted, and every coordinate the refutation "
                            "points at is ALREADY pinned at its x_pi value, so "
                            "the retreat of §Certification has nothing left to "
                            "give up" +
                            divergence_text(dict(ce, **path_extras.get(enc, {})), last_wit,
                                            set(last_wit_box) | set(pins), caveats, _rng, _ahs))
                        break
                    _c, _clo, _chi, _removed = payload
                    if (unknown_why == "UNSAFE" and _removed == 1
                            and args.safety_retreat_after_tiny_cuts > 0):
                        if tiny_safety_cut_coord == _c:
                            tiny_safety_cut_streak += 1
                        else:
                            tiny_safety_cut_coord = _c
                            tiny_safety_cut_streak = 1
                        retreat = tiny_safety_cut_retreat(box, _c, _removed, ce,
                                                          tiny_safety_cut_streak,
                                                          args.safety_retreat_after_tiny_cuts)
                        if retreat:
                            applied = {n: v for n, v in retreat.items() if box.get(n) != (v, v)}
                            if applied:
                                refinement_used = True
                                for n, v in applied.items():
                                    box[n] = (v, v)
                                retreated.update(applied)
                                prev_size = region_size(box, holes)
                                tiny_safety_cut_coord = None
                                tiny_safety_cut_streak = 0
                                print(f"[retreat {tag}] PINNED " +
                                      ", ".join(f"{n}=={v}" for n, v in sorted(applied.items())) +
                                      " at its x_pi value after repeated "
                                      "one-value safety cuts; the product "
                                      "region cannot spell the relational "
                                      "checked-arithmetic guard, so carrying "
                                      "on with the remaining wide "
                                      f"coordinate(s)  |R| {prev_size}")
                                continue
                    else:
                        tiny_safety_cut_coord, tiny_safety_cut_streak = None, 0
                    nb = dict(box)
                    nb[_c] = (_clo, _chi)
                    print(f"[cut {tag}] {_c} -> [{_clo}, {_chi}], removing "
                          f"{_removed} value(s) -- the fewest of the "
                          f"coordinate(s) this refutation offers")
                if nb is None or nb == box:
                    reason = ("refuted with no single-coordinate cut available" + divergence_text(
                        dict(ce, **path_extras.get(enc, {})), last_wit,
                        set(last_wit_box) | set(pins), caveats, assumed_ranges(last_wit_box, pins),
                        assumed_holes(holes, pins)))
                    break
                # C3: a shrink that does not shrink is a defect, not a slow
                # round. A region that GREW would mean the cut moved a bound
                # outwards, which no legal cut can do and which nothing
                # downstream would ever notice -- the loop would simply certify
                # a wider region than the one it measured.
                #
                # CHECKED ON THE UNCLAMPED `nb`, deliberately. `split_on_cut`
                # below clamps the suggestion to the current interval, and if
                # that clamping ran first a suggestion reaching outside the box
                # would be silently trimmed instead of caught here.
                new_size = region_size(nb, holes)
                if new_size > prev_size:
                    reason = (f"INVARIANT VIOLATED: the shrink WIDENED the region "
                              f"(|R| {prev_size} -> {new_size}). A cut may only ever "
                              f"make the region narrower; a wider one would certify "
                              f"inputs that were never measured. Refusing to continue "
                              f"this path")
                    break
                # ---- S3: KEEP THE DISCARDED SIDE, under a stated budget ----
                #
                # Enqueued BEFORE `box` advances, and derived from the box the
                # cut was applied to, so the pieces partition exactly the set
                # that was about to be narrowed. `has_ce` is computed rather
                # than assumed: the tool's cut is supposed to keep the CE, but
                # on a piece that never held one there is nothing to keep, and
                # `ce_in_region` answers it exactly.
                coord = cut_of(box, nb)
                # THE SUGGESTION MUST LIE INSIDE THE INTERVAL IT CUTS, and C3
                # alone does not enforce it. `split_on_cut` CLAMPS, so `rest` is
                # computed from the clamped bounds while `box` advances to the
                # UNCLAMPED `nb`; on box [10,100] with a suggestion [5,50] the
                # complement is [51,100] and the kept side is [5,50], whose
                # union is [5,100] -- covering [5,9], which was never in the
                # measured region. |R| does not catch it (46 < 91, so the
                # narrowing check passes). Checked explicitly rather than
                # inferred from the size.
                if coord is not None and coord in box:
                    olo, ohi = box[coord]
                    nlo, nhi = nb[coord]
                    if nlo < olo or nhi > ohi:
                        reason = (f"INVARIANT VIOLATED: the suggested cut on {coord} "
                                  f"[{nlo}, {nhi}] reaches OUTSIDE the interval it "
                                  f"cuts [{olo}, {ohi}]. A cut may only narrow; a "
                                  f"union built from this would certify inputs the "
                                  f"region never contained, and |R| cannot see it "
                                  f"because the result is still smaller")
                        break
                if coord is not None and args.max_region_pieces > 1:
                    _, rest = split_on_cut(box, coord, *nb[coord])
                    for r in rest:
                        if piece_no + len(queue) >= args.max_region_pieces:
                            print(f"[split {tag}] piece budget "
                                  f"{args.max_region_pieces} reached; "
                                  f"{coord} in [{r[coord][0]}, {r[coord][1]}] "
                                  f"is DISCARDED UNMEASURED -- it is not known "
                                  f"to be outside the domain, only unexamined")
                            continue
                        queue.append((r, copy_holes(holes),
                                      not ce_in_region(r, holes, ce), True))
                        print(f"[split {tag}] keeping the discarded side "
                              f"{coord} in [{r[coord][0]}, {r[coord][1]}] as a "
                              f"separate piece")
                prev_size = new_size
                print(f"[shrink {tag}] {box} -> {nb}  |R| {new_size}")
                box = nb
                refinement_used = True
            else:
                reason = (
                    "shrink round budget exhausted" +
                    divergence_text(dict(ce, **path_extras.get(enc, {})), last_wit,
                                    set(last_wit_box) | set(pins), caveats,
                                    assumed_ranges(last_wit_box, pins), assumed_holes(holes, pins)))
            if reason is not None:
                piece_fail.append(reason)
        # A path FAILS only when NO piece of it certified. Reported with the
        # FIRST piece's reason, which at --max-region-pieces 1 is the only one
        # and reproduces the old message verbatim; the others are appended so a
        # multi-piece failure does not read as a single-box one.
        if not any(k[0] == enc for k in ok):
            failed[enc] = piece_fail[0] if piece_fail else "no piece was measured"
            if len(piece_fail) > 1:
                failed[enc] += (f" (and {len(piece_fail) - 1} further piece(s) of this "
                                f"path also failed: " + "; ".join(piece_fail[1:]) + ")")
            # ---- §Certification's FLOOR: THE SINGLE POINT IS STILL A QUERY ----
            #
            # Verbatim from the method:
            #
            #     Where the rounds run out on every coordinate, R_pi = {x_pi},
            #     and a region of one point leaves nothing for a test to range
            #     over. That the witness follows pi needs no argument, since the
            #     path domains are pairwise disjoint and x_pi lies in D_pi. THE
            #     INSERTED CHECKS ARE ANOTHER MATTER, because the enumeration of
            #     Section enum left them out and a witness may trip one. The same
            #     question is therefore put on the single point, where every
            #     quantity is fixed and the answer is a matter of evaluation. A
            #     witness that fails it receives no test and is reported with the
            #     path.
            #
            # THE DRIVER SKIPPED IT ENTIRELY and fell straight through to "this
            # path falls back to its concrete counterexample test". That is not
            # a missing refinement, it is the one route by which this method can
            # deliver a RED test: enumeration deliberately keeps the
            # compiler-inserted arithmetic and bounds checks OUT of a path's
            # identity (§Decision Points), certification turns them back ON, and
            # a witness that overflows was therefore never asked about. The
            # concrete replay test built from it runs on the real contract and
            # reverts.
            #
            # It is one query and every quantity in it is fixed, which is why
            # the method calls the answer "a matter of evaluation" -- this is
            # not a second search.
            failed[enc] += run_single_point_witness_check(
                enc, depth, ce, xpins,
                dict(
                    structural_seed_establishes.get(enc) or structural_region_establishes.get(enc)
                    or {}))

    # HARD CHECK, not a warning. Two certified regions that share a point mean
    # an input would have to walk two different paths. Reporting them and
    # carrying on would be shipping a contradiction.
    # KEYED BY (enc, piece) SINCE S3, and the check is deliberately run over ALL
    # pairs rather than only across distinct paths. Two pieces of the SAME path
    # are carved as complements of one another, so they are disjoint by
    # construction -- which makes an intersection between them a defect in the
    # splitting, exactly the kind this function exists to catch, and skipping
    # them would leave the new code as the only part of the loop with no
    # partition check on it.
    overlap = certified_overlap(ok, ok_holes, ok_established, ok_extcall)
    if overlap:
        print("\n=== INVARIANT VIOLATED: certified regions intersect ===")

        def _k(k):
            return f"enc={k[0]}" + (f" piece {k[1]}" if k[1] > 1 else "")

        for e1, e2 in overlap:
            print(f"  {_k(e1)} and {_k(e2)} share at least one point:")
            print(f"    {_k(e1)}: {ok[e1]}")
            print(f"    {_k(e2)}: {ok[e2]}")
        print("Path domains partition the input space, so this cannot be true "
              "of any correct output. Refusing to report these as certified. "
              "This is the check that would have caught the certification gate "
              "returning true on every input -- that was spotted by eye, from "
              "exactly this symptom.")
        return 1

    print("\n=== CERTIFIED REGIONS ===")
    # Grouped by path, so a region reported as several boxes reads as ONE
    # statement about that path (their union) rather than as several paths. The
    # "N of M" label appears only when a path really has more than one piece, so
    # a single-piece run prints exactly the line it always printed.
    by_enc = {}
    for key in ok:
        by_enc.setdefault(key[0], []).append(key)
    for enc in sorted(by_enc):
        keys = sorted(by_enc[enc])
        for i, key in enumerate(keys, 1):
            box = ok[key]
            pin_txt = "".join(f", {n} == {v}" for n, v in pins.items())
            hs = ok_holes.get(key) or {}

            def _one(n, lo, hi, hs=hs):
                # The punched set is printed WITH its interval, never as a
                # footnote: `[0, 2^160-1]` and `[0, 2^160-1] \ {255}` are
                # different regions, and the second is the one that was
                # certified. A reader who quotes the interval alone would be
                # quoting a region the query refuted.
                v = sorted(hs.get(n, ()))
                return (f"{n} in [{lo}, {hi}]" +
                        (" \\ {" + ", ".join(str(x) for x in v) + "}" if v else ""))

            # THE REAL PIECE NUMBER, not a re-enumeration of the certified ones.
            # `[split enc=6 piece 1]` and `[shrink enc=6 piece 3]` in the log
            # have to be findable in this report; renumbering 1..N over the
            # pieces that happened to certify makes cross-referencing a sweep
            # log against its own report guesswork.
            label = (f"enc={enc}"
                     if len(keys) == 1 else f"enc={enc} piece {key[1]} ({i} of {len(keys)} "
                     f"certified)")
            print(f"  {label}: " + ", ".join(_one(n, lo, hi)
                                             for n, (lo, hi) in box.items()) + pin_txt)
        if len(keys) > 1:
            print(f"  enc={enc}: the region of this path is the UNION of the "
                  f"{len(keys)} boxes above. Each was certified by its own "
                  f"query; the union is certified because each member is, and "
                  f"they are pairwise disjoint (checked above)")
    for enc, why in sorted(failed.items()):
        if enc in pre_failed:
            if "extcall." in why or "external-call behavior" in why:
                suffix = ("; no concrete counterexample test is emitted "
                          "without a deterministic external-call fixture")
            else:
                suffix = ("; no concrete counterexample test is emitted "
                          "without a deterministic fixture for the "
                          "uncontrolled decision source")
        else:
            suffix = "; this path falls back to its concrete counterexample test"
        print(f"  enc={enc}: NOT CERTIFIED — {why}{suffix}")
    if dropped_by_certify:
        # C5 ran ONCE, before any query, and every one of these names was in a
        # bucket then. The drop removed them afterwards, so as the run ends they
        # are in NO bucket -- exactly the "a name vanished between the
        # counterexample and the region" condition C5 exists to catch. It is not
        # an error (dropping widens the quantification, which is sound), but a
        # name that leaves the accounting must not leave it silently.
        print("\n  COORDINATE ACCOUNTING, amended: " + ", ".join(sorted(dropped_by_certify)) +
              " left every bucket during certification — the query could not "
              "express them, so the pins were dropped. Every region above is "
              "therefore a statement holding for ALL their values, which is "
              "STRONGER than the slice originally asked for. They appear in "
              "no pin list above because the list is printed after the drop")

    # ---- THE RESULT, MACHINE-READABLE. Everything above is PROSE ----
    #
    # Until this existed the certified region was PRINTED and nowhere else. The
    # only JSON the loop left behind was `cert.json`, which is the certification
    # QUERY's input spec and is OVERWRITTEN per attempt -- so after a run it
    # holds the LAST path tried, which is typically one that did NOT certify.
    # MEASURED on FeeVault.setDiscount: enc=6 certified with `bps in [251,
    # 65535]`, and the only cert.json on disk described enc=7, uncertified.
    #
    # That forces the next stage to parse this script's stdout, which is the
    # exact antipattern this project keeps paying for: two ledgers of one fact,
    # the second one a regex over prose that silently stops matching when a
    # sentence is reworded. A TestPlan built that way would be a plan whose
    # provenance nothing can check.
    #
    # WHAT IS WRITTEN IS WHAT WAS CERTIFIED, not a re-derivation: `ok`,
    # `ok_holes`, `pins` and `failed` are the same objects the block above
    # prints, read once, here. A consumer and this report cannot disagree.
    #
    # The per-path counterexample travels with it because the downstream plan
    # needs a KNOWN MEMBER of the domain -- for the concrete-replay fallback, and
    # for the C2 check that a region contains it.
    ce_by_enc = {e: ce for e, _d, ce in all_paths}
    depth_by_enc = {e: d for e, d, _ce in all_paths}
    enumeration_report_path = (args.enumeration_report or enumeration_report_snapshot_path(cwd))
    out = {
        "schema":
        "path-generalise-result/1",
        "contract":
        args.contract,
        "unit":
        args.unit,
        "path_function":
        args.path_function,
        "max_tx":
        args.max_tx,
        # THE ALPHABET TRAVELS WITH THE LENGTH. A region measured under one
        # scope may not be quoted into another's table, and until this field
        # existed the artefact recorded only half of the configuration -- so
        # two results that disagree because one was focused and one was not
        # looked like two results that simply disagree.
        "scope":
        scope_label,
        "extcall_length_coordinates":
        extcall_length_specs,
        "enumeration_source": {
            "mode": "imported-stage-1" if args.enumeration_report else "direct",
            "index": file_identity(args.enumeration_index),
            "report": file_identity(enumeration_report_path),
            "salvage": read_enumeration_salvage(cwd),
        },
        # The slice every region below is a statement ABOUT. A region quoted
        # without its pins is a region quoted wrong.
        "pins": {
            n: str(v)
            for n, v in sorted(pins.items())
        },
        "state_dependency_filter":
        state_dependency_filter,
        "path_decisions": {
            str(enc): {
                "abi_gate_class": abi_gate_class(decisions),
                "decisions": decisions
            }
            for enc, decisions in sorted(path_decisions.items())
        },
        "dropped_by_certify":
        sorted(dropped_by_certify),
        "certified": [
            {
                "enc":
                key[0],
                "piece":
                key[1],
                "depth":
                depth_by_enc.get(key[0]),
                "verdict":
                "CERTIFIED",
                # ⛔ THE OLD COMMENT HERE STATED THE FLOOR TEST WRONG, and the
                # method says so outright. It read: "`hi >= lo` and a width > 1
                # on at least one coordinate is what separates a PUT from a
                # concrete replay". §From a Region to a Test:
                #
                #     A test is parameterized when at least one coordinate IT
                #     RENDERS is left more than one value to take. A region
                #     wider than a point does not settle this on its own, since
                #     the coordinates the omission rule leaves out are not
                #     rendered and a region can be wide only on those.
                #
                # So width over the whole box is NOT the test; width over the
                # RENDERED set is, and the rendered set is the emitter's to
                # decide. A box wide only on omitted coordinates would be
                # emitted as a PUT under the old reading and is a concrete
                # replay under the method's. That decision therefore stays with
                # `solidity_path_put.py`, and what this file owes it is the
                # numbers plus which coordinates were pinned by the retreat.
                "retreated": {
                    n: str(v)
                    for n, v in sorted((ok_retreated.get(key) or {}).items())
                },
                "refinement_used": bool(ok_refinement_used.get(key, False)),
                "established": [{
                    "target": target,
                    "source": source
                } for target, source in sorted((ok_established.get(key) or {}).items())],
                # THE HARNESS-CHOSEN VALUES THIS REGION WAS CERTIFIED UNDER.
                # Empty unless --pin-extcall was passed. A consumer that
                # renders the region as a test and ignores this field emits a
                # test that claims more than was certified.
                "extcall_pins": {
                    n: str(v)
                    for n, v in sorted((ok_extcall.get(key) or {}).items())
                },
                "extcall_length_coordinates":
                [spec for spec in extcall_length_specs if spec.get("coord") in ok[key]],
                "certification_source":
                ok_source.get(key, "esbmc-certify"),
                "box": [{
                    "name": n,
                    "lo": str(lo),
                    "hi": str(hi),
                    "holes": [str(h) for h in sorted((ok_holes.get(key) or {}).get(n, ()))]
                } for n, (lo, hi) in sorted(ok[key].items())],
                "ce": {
                    n: str(v)
                    for n, v in sorted(ce_by_enc.get(key[0], {}).items())
                },
            } for key in sorted(ok)
        ],
        # NOT omitted. A path that did not certify is a REPORTABLE OUTCOME with
        # a named reason, and leaving it out would let a consumer read the file
        # as "these are all the paths" when it is "these are the ones that
        # certified".
        "not_certified": [
            {
                "enc": e,
                "depth": depth_by_enc.get(e),
                "verdict": "NOT_CERTIFIED",
                "reason": why,
                "concrete_fallback": concrete_fallback_cleared(e),
                # §Certification's floor. "SUCCESSFUL" clears this path's concrete
                # replay test; "FAILED" means it gets NO test; anything else is
                # undecided and clears nothing. A path MISSING from this map was
                # never asked (--no-witness-check), which is not the same as any
                # of the three -- so the key is emitted as null rather than
                # omitted, and a consumer must not read null as "fine".
                "witness_check": witness_check.get(e),
                "ce": {
                    n: str(v)
                    for n, v in sorted(ce_by_enc.get(e, {}).items())
                }
            } for e, why in sorted(failed.items())
        ],
        "enumerated": [{
            "enc": e,
            "depth": d
        } for e, d, _ in all_paths],
    }
    result_path = os.path.join(cwd, "generalise-result.json")
    with open(result_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    write_generalise_progress(
        cwd,
        "complete",
        certified=len(out["certified"]),
        not_certified=len(out["not_certified"]),
        witnessed=len(out["enumerated"]),
        result=os.path.basename(result_path),
    )
    print(f"\n[result] machine-readable result written to {result_path}: "
          f"{len(out['certified'])} certified region(s), "
          f"{len(out['not_certified'])} not certified, over "
          f"{len(out['enumerated'])} witnessed path(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
