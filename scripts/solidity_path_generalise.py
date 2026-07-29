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
import json
import os
import re
import subprocess
import sys
import tempfile

UINT256_MAX = (1 << 256) - 1


def run(esbmc, sol, contract, extra, max_tx, timeout, cwd, ast=None, focus=None,
        memlimit="8g"):
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
    cmd = [esbmc]
    if ast:
        cmd.append(os.path.abspath(ast))
    cmd += ["--sol", os.path.abspath(sol), "--contract", contract,
            "--solidity-path-coverage", "--solidity-max-tx", str(max_tx),
            "--result-only", "--memlimit", memlimit]
    if focus:
        cmd += ["--focus-function", focus]
    cmd += extra
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # A timeout is an OUTCOME of this pipeline, not a crash of it. Measured:
        # one outer-box round on a real contract unit (5 paths, 2 coordinates,
        # 4 probes) does not finish in 540s, so on real input this is the common
        # case rather than the exceptional one -- and it used to surface as a
        # Python traceback in the middle of a benchmark. Return the partial
        # output with an explicit marker; since it carries NEITHER verdict line,
        # every caller reads it as UNKNOWN, which is what it is.
        # `text=True` does NOT apply to the exception's captured output: both
        # attributes come back as bytes. Decode each side BEFORE concatenating,
        # or the handler itself raises -- which is what it did on its first real
        # use, turning a handled timeout into a TypeError inside the handler.
        def _txt(b):
            if b is None:
                return ""
            return b.decode(errors="replace") if isinstance(b, bytes) else b
        out = _txt(e.stdout) + _txt(e.stderr)
        return out + f"\n[run] TIMEOUT after {timeout}s: {' '.join(cmd)}\n"
    # RECORD THE EXIT CODE, and let callers judge on it rather than on message
    # text. ESBMC uses 0 for SUCCESSFUL and 1 for FAILED; anything else means it
    # did not finish -- 6 for a conversion error, 134 for an abort, and so on.
    #
    # This replaces a whitelist of two known failure messages, which was wrong
    # in the way this file keeps being wrong: a THIRD cause (an abort on a
    # string-typed state coordinate, `Projecting from non-tuple based AST`)
    # matched neither pattern, so the round came back with no regions and was
    # reported downstream as "no fully bounded region was measured" -- a
    # property of the path, for what was a crash. A whitelist of failures is
    # open at the bottom; an exit code is not.
    return p.stdout + p.stderr + f"\n[run] EXIT {p.returncode}\n"


def parse_int(s):
    s = s.strip()
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


ENV_PREFIXES = ("msg.", "tx.", "block.")


def is_env(name):
    """Is this coordinate an EVM environment quantity rather than an input?

    Same namespace rule the tool uses to resolve coordinate names. It matters
    for POLICY, not just naming: environment quantities are never made free
    coordinates here, because the ladder cost is multiplicative in the number of
    coordinates and there are fifteen of them.
    """
    return name.startswith(ENV_PREFIXES)


def coord_values(c):
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
    for n, v in (c.get("env") or {}).items():
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
    for n, v in (c.get("inputs") or {}).items():
        try:
            ce[n] = parse_int(v)
        except ValueError:
            refused.append(n)
    for n, v in (c.get("entry_storage") or {}).items():
        try:
            ce["state." + n] = parse_int(v)
        except ValueError:
            refused.append("state." + n)
    return ce, refused


def enumerate_paths(esbmc, sol, contract, unit, max_tx, timeout, cwd,
                    ast=None, focus=None, memlimit="8g", path_function=None):
    """Step 1. Returns (paths, refused) where paths = [(enc, depth, ce)]."""
    log = run(esbmc, sol, contract, ["--cov-report-json"], max_tx, timeout, cwd,
              ast=ast, focus=focus, memlimit=memlimit)
    report = os.path.join(cwd, "cov-report.json")
    if not os.path.exists(report):
        # Do NOT let this surface as a FileNotFoundError about a JSON file.
        # ESBMC has already said what went wrong -- a solc version mismatch, a
        # parse error, a missing contract -- and throwing that output away turns
        # an actionable message into a stack trace about the wrong subject.
        raise SystemExit(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            + log)
    with open(report) as f:
        rep = json.load(f)

    claims = [c for c in rep.get("claims", [])
              if claim_unit(c) == unit and "path_id" in c
              and "path_depth" in c]
    if path_function:
        claims = [c for c in claims
                  if c.get("path_function") == path_function]

    # OVERLOADS. Two functions sharing a name are two units with two independent
    # path-id spaces, and a stage-2 query identifies a path by (enc, depth)
    # alone. Merging them would hand the certification query an `enc` from the
    # wrong space -- a wrong answer, not an error. Refuse and name the
    # candidates instead of picking one.
    pfs = sorted({c.get("path_function") for c in claims})
    if len(pfs) > 1:
        raise SystemExit(
            f"[enumerate] '{unit}' names {len(pfs)} overloads; their path-id "
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
            raise SystemExit(
                f"[enumerate] no F claim matched unit '{unit}', but the report "
                f"holds {len(any_f)} F claim(s) for: {', '.join(units)}. "
                f"That is a wiring failure, not a result.")

    out, refused = [], set()
    for c in witnessed:
        ce, ref = coord_values(c)
        refused.update(ref)
        out.append((int(c["path_id"]), int(c["path_depth"]), ce))
    # Same enc can appear once per transaction instance; keep one of each.
    seen, uniq = set(), []
    for enc, depth, ce in out:
        if enc in seen:
            continue
        seen.add(enc)
        uniq.append((enc, depth, ce))
    return uniq, sorted(refused), extraction_caveats(witnessed)


def geometric_values(limit):
    """Round-1 ladder: magnitude-independent, one run."""
    vals, v = [0], 1
    while v <= limit:
        vals.append(v)
        v *= 2
    vals.append(limit)
    return sorted(set(vals))


BOX_RE = re.compile(
    r"path enc=(\d+) depth=\d+ OUTER box \(D_path is CONTAINED in it\): (.*)")
BRACKET_RE = re.compile(r"path enc=(\d+) BRACKET \(refine[^)]*\): (.*)")
REGION_RE = re.compile(
    r"path enc=(\d+) CERTIFIED region after subtracting sibling outer boxes "
    r"\(zero queries\): ([^—]*)(— WARNING.*)?")
SHRINK_RE = re.compile(r"retry with (\S+) in \[(\d+), (\d+)\]")


def parse_intervals(text):
    # Scanned, not split: an interval contains ", " itself, so splitting on it
    # cuts every interval in half and silently yields nothing.
    return {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"(\S+) in \[(\d+), (\d+)\]", text)}


def brackets_for(coord, brackets):
    """Where the SEPARATION boundary still is, per the bracket report.

    A bracket that runs into the type limit (upper ending at 2^256-1, or lower
    starting at 0) is not a separation point -- it says "no bound was found
    inside the type", i.e. the bound IS the type limit. Refining towards it
    keeps the span at the full type range and the loop never narrows, which is
    exactly what it did before this was excluded.
    """
    lo, hi = None, None
    for txt in brackets.values():
        for m in re.finditer(
                re.escape(coord) + r" (upper|lower) in [\[(](\d+), (\d+)[\])]",
                txt):
            a, b = int(m.group(2)), int(m.group(3))
            if m.group(1) == "upper" and b >= UINT256_MAX:
                continue
            if m.group(1) == "lower" and a <= 0:
                continue
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    return (lo, hi) if lo is not None else None


def outer_round(esbmc, sol, contract, unit, paths, coords, pins, probes,
                max_tx, timeout, cwd, spans=None, geometric=False,
                ast=None, focus=None, memlimit="8g"):
    """Steps 2-4: one batch. Returns (boxes, brackets, regions, warned)."""
    spec_coords = []
    for c in coords:
        if geometric:
            spec_coords.append(
                {"name": c, "values": [str(v)
                                       for v in geometric_values(UINT256_MAX)]})
        else:
            lo, hi = spans[c]
            spec_coords.append({"name": c, "lo": str(lo), "hi": str(hi)})
    spec = {"unit": unit, "probes": probes, "coords": spec_coords,
            "pin": [{"name": n, "value": str(v)} for n, v in pins.items()],
            "paths": [{"enc": e, "depth": d,
                       "ce": {k: str(v) for k, v in ce.items() if k in coords}}
                      for e, d, ce in paths]}
    path = os.path.join(cwd, "outer.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    log = run(esbmc, sol, contract, ["--path-cov-outer-box", path],
              max_tx, timeout, cwd, ast=ast, focus=focus, memlimit=memlimit)
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
    failure = round_failure_reason(log)
    if failure:
        print(f"[outer-box] ROUND MEASURED NOTHING — {failure}")
    boxes, brackets, regions, warned = {}, {}, {}, set()
    for line in log.splitlines():
        m = BOX_RE.search(line)
        if m:
            boxes[int(m.group(1))] = parse_intervals(m.group(2))
        m = BRACKET_RE.search(line)
        if m:
            brackets[int(m.group(1))] = m.group(2)
        m = REGION_RE.search(line)
        if m:
            regions[int(m.group(1))] = parse_intervals(m.group(2))
            if m.group(3):
                warned.add(int(m.group(1)))
    return boxes, brackets, regions, warned, failure


def verdict(log):
    """'SUCCESSFUL' / 'FAILED' / 'UNKNOWN', read as a LINE, never a substring.

    This function exists because of a measured, total failure of the soundness
    gate. The test used to be:

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
    """
    seen = "UNKNOWN"
    for line in log.splitlines():
        s = line.strip()
        if s == "VERIFICATION SUCCESSFUL":
            seen = "SUCCESSFUL"
        elif s == "VERIFICATION FAILED":
            seen = "FAILED"
    return seen


def boxes_intersect(a, b):
    """Do two boxes share at least one point?

    Two boxes intersect iff they overlap on EVERY coordinate: a box is a
    conjunction, so one disjoint coordinate separates them entirely. A
    coordinate present in one box and absent from the other is unconstrained
    there, hence overlapping on it.
    """
    for n, (lo, hi) in a.items():
        if n not in b:
            continue
        blo, bhi = b[n]
        if hi < blo or bhi < lo:
            return False
    return True


def certified_overlap(ok):
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
    encs = sorted(ok)
    for i, e1 in enumerate(encs):
        for e2 in encs[i + 1:]:
            if boxes_intersect(ok[e1], ok[e2]):
                bad.append((e1, e2))
    return bad


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
    unresolved = sorted(set(re.findall(r"has no input named '([^']+)'", log)))
    if unresolved:
        return ("the outer-box round rejected coordinate(s) "
                + ", ".join(unresolved)
                + " as unresolvable, so nothing was measured (a "
                  "COORDINATE-SUPPORT gap, not a property of the path)")
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
        why = {"6": "conversion error",
               "124": "killed on timeout", "-15": "killed (SIGTERM)",
               "-9": "killed (SIGKILL)",
               "134": "ABORTED (SIGABRT)", "-6": "ABORTED (SIGABRT)",
               "139": "crashed (SIGSEGV)", "-11": "crashed (SIGSEGV)",
               }.get(code, "did not complete")
        return (f"ESBMC exited {code} ({why}), so the round measured nothing "
                f"(a TOOL outcome, not a property of the path). The last "
                f"ERROR line in its output names the cause")
    return None


def empty_coords(box):
    """Coordinates whose interval is EMPTY (lo > hi).

    An empty box certifies VACUOUSLY: `assume(lo <= x <= hi)` with lo > hi is
    `assume(false)`, so `assert(tr == pi)` holds for want of any execution and
    the query answers SUCCESSFUL. The tool's own non-vacuity argument -- put the
    assert on EVERY exit -- addresses a different vacuity and does not cover an
    unsatisfiable assumption.

    Not hypothetical: with the environment pinned, the ABI-gate revert path's
    domain is empty, the sibling subtraction duly produced lo > hi, and the run
    reported it as a certified region. The pin had excluded the path; the honest
    statement is that, not a certificate.
    """
    return sorted(n for n, (lo, hi) in box.items() if lo > hi)


def witness_values(cwd, unit):
    """The REFUTING input's payload, harvested from the certification run.

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
    for c in rep.get("claims", []):
        if c.get("status") == "F" and claim_unit(c) == unit:
            ce, _ = coord_values(c)
            return ce
    return {}


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
                out.setdefault(k[: -len("_unavailable_reason")], v)
    return out


def divergence_text(path_ce, wit_ce, bounded, caveats=None):
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
    diff = [(n, path_ce[n], wit_ce[n])
            for n in sorted(set(path_ce) & set(wit_ce))
            if path_ce[n] != wit_ce[n]]
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
        return msg
    return ("; the witness differs from this path's counterexample on: "
            + ", ".join(
                f"{n} (path={pv}, witness={wv})"
                + ("" if n in bounded else " [NOT a bounded coordinate]")
                for n, pv, wv in diff))


def shrink_target(log, pins):
    """The cut a refutation suggests, as (coord, lo, hi), or None.

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


def certify(esbmc, sol, contract, unit, enc, depth, box, ce, pins,
            max_tx, timeout, cwd, ast=None, focus=None, memlimit="8g"):
    """Step 5. Returns (verdict, suggested_box_or_None)."""
    spec = {"unit": unit, "enc": enc, "depth": depth,
            "ce": {k: str(v) for k, v in ce.items()},
            "box": [{"name": n, "lo": str(lo), "hi": str(hi)}
                    for n, (lo, hi) in box.items()] +
                   [{"name": n, "lo": str(v), "hi": str(v)}
                    for n, v in pins.items()]}
    path = os.path.join(cwd, "cert.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    log = run(esbmc, sol, contract,
              ["--path-cov-certify", path, "--cov-report-json"],
              max_tx, timeout, cwd, ast=ast, focus=focus, memlimit=memlimit)
    v = verdict(log)
    if v != "FAILED":
        # SUCCESSFUL: certified. UNKNOWN: no verdict at all -- the caller must
        # not shrink on it, so no box is suggested either.
        return v, None, {}
    # Harvested on every refutation, not only when the shrink fails: the caller
    # needs it in the budget-exhausted branch too, and by then this run's report
    # has been overwritten by the next one.
    wit = witness_values(cwd, unit)
    cut = shrink_target(log, pins)
    if cut is None:
        return v, None, wit
    coord, lo, hi = cut
    nb = dict(box)
    nb[coord] = (lo, hi)
    return v, nb, wit


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esbmc", default="esbmc")
    ap.add_argument("--sol", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--probes", type=int, default=16)
    ap.add_argument("--refine-rounds", type=int, default=3)
    ap.add_argument("--shrink-rounds", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--ast", default=None,
                    help="prebuilt .solast, passed positionally. Needed for "
                         "any source whose pragma pins a solc this machine "
                         "does not have -- every flattened benchmark input "
                         "does.")
    ap.add_argument("--focus", action="store_true",
                    help="narrow the harness dispatcher to --unit. Does NOT "
                         "change the enumeration (verified by comparing "
                         "content-addressed path key sets, not just counts); "
                         "it is a pure scope control, and on a real contract "
                         "it is the difference between finishing in seconds "
                         "and exceeding a 900s budget with nothing to show.")
    ap.add_argument("--memlimit", default="8g",
                    help="passed to ESBMC. Keep it at or below whatever the "
                         "caller computed for the machine; this used to be "
                         "hardcoded, so a caller's limit was a line nobody "
                         "read.")
    ap.add_argument("--env-coord", action="append", default=[],
                    help="promote one environment quantity (e.g. "
                         "block.timestamp) to a FREE coordinate instead of a "
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
    ap.add_argument("--skip-bracket", action="store_true",
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
    ap.add_argument("--pin-env", action="store_true",
                    help="pin each msg./tx./block. quantity on which every "
                         "witnessed path agrees, at that value. Off by default "
                         "because it changes what every region MEANS -- each "
                         "becomes a statement about that environment slice, "
                         "which is printed with it. Measured effect: without "
                         "it a non-payable function certifies nothing, because "
                         "its ABI gate is a decision on an unconstrained "
                         "msg.value.")
    ap.add_argument("--path-function", default=None,
                    help="disambiguate overloads: the exact mangled "
                         "path_function to generalise.")
    ap.add_argument("--pin", action="append", default=[],
                    help="coord=value, e.g. state.bal=50. Pinned coordinates "
                         "are NOT generalised; every region reported is a "
                         "statement about that slice and carries the pin.")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    pins = {}
    for p in args.pin:
        n, _, v = p.partition("=")
        pins[n] = parse_int(v)

    cwd = args.workdir or tempfile.mkdtemp(prefix="pathgen-")
    os.makedirs(cwd, exist_ok=True)
    print(f"[workdir] {cwd}")

    focus = args.unit if args.focus else None
    paths, refused, caveats = enumerate_paths(
        args.esbmc, args.sol, args.contract, args.unit, args.max_tx,
        args.timeout, cwd, ast=args.ast, focus=focus, memlimit=args.memlimit,
        path_function=args.path_function)
    if not paths:
        print("[enumerate] no witnessed path for this unit; nothing to "
              "generalise. That is a result, not an error: a path with no "
              "counterexample has no known member of its domain to keep, so "
              "there is nothing to grow a region around. (The report was "
              "checked: it holds no F claim for any unit, so this really is "
              "the empty case and not a failed match.)")
        return 1
    print(f"[enumerate] {len(paths)} witnessed path(s): "
          + ", ".join(f"enc={e} depth={d}" for e, d, _ in paths))
    if refused:
        # Say it. Every region printed below is a statement about the SLICE
        # through these, not about the whole input space.
        print(f"[coords] UNSUPPORTED, refused as coordinates (not scalar): "
              f"{', '.join(refused)}. Every region below is a statement about "
              f"the slice through whatever values they took in the "
              f"counterexample, and does NOT generalise over them.")

    # ---- EVM environment: pin where every path agrees, never probe ----
    #
    # Environment quantities are never made FREE coordinates: the ladder cost is
    # multiplicative in the coordinate count and there are fifteen of them, and
    # the bracket round is already the binding cost on real input.
    #
    # They are pinned only where EVERY witnessed path's counterexample agrees on
    # the value. A pin that contradicts some path's own counterexample would
    # place that path's known domain member OUTSIDE the box, and "keep a known
    # member" is the invariant that stops the subtraction cut from carving away
    # the real region. Where the paths disagree the quantity is left
    # unconstrained -- which is the status quo, and is reported rather than
    # passed over, because an unconstrained gate is exactly what refuses
    # certification.
    env_names = sorted({k for _, _, ce in paths for k in ce if is_env(k)}
                       - set(args.env_coord))
    if args.env_coord:
        print(f"[env] probed as free coordinate(s): "
              f"{', '.join(sorted(args.env_coord))}")
    if args.pin_env and env_names:
        agreed, disagreed = {}, []
        for n in env_names:
            vals = {ce.get(n) for _, _, ce in paths}
            if len(vals) == 1 and None not in vals:
                agreed[n] = vals.pop()
            else:
                disagreed.append(n)
        for n, v in agreed.items():
            pins.setdefault(n, v)          # an explicit --pin always wins
        if agreed:
            print(f"[env] pinned (all {len(paths)} paths agree): "
                  + ", ".join(f"{n}={v}" for n, v in sorted(agreed.items())))
        if disagreed:
            print(f"[env] NOT pinned, paths disagree on the witnessed value: "
                  f"{', '.join(disagreed)}. Left unconstrained, so a path "
                  f"guarded by one of these cannot certify.")
    elif env_names:
        print(f"[env] {len(env_names)} environment quantity(s) left "
              f"unconstrained (--pin-env is off). A non-payable function has an "
              f"ABI-level decision on msg.value, so its paths cannot certify "
              f"while it is unconstrained.")

    coords = sorted({k for _, _, ce in paths for k in ce}
                    - set(pins) - set(env_names))
    if not coords:
        print("[coords] no generalisable coordinate: "
              + ("every coordinate is pinned" if pins else
                 "every coordinate was refused as UNSUPPORTED")
              + "; nothing to generalise")
        return 1
    print(f"[coords] {', '.join(coords)}"
          + (f"   [pinned: {pins}]" if pins else ""))

    # Round 1: geometric bracket.
    if args.skip_bracket:
        brackets, regions, warned, round_failure = {}, {}, set(), None
        print("[bracket] SKIPPED (--skip-bracket): refining from each "
              "coordinate's full type range, which is the same fallback the "
              "code takes when the bracket measures nothing")
    else:
        _, brackets, regions, warned, round_failure = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, geometric=True,
            ast=args.ast, focus=focus, memlimit=args.memlimit)
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
    spans = {c: (brackets_for(c, brackets) or (0, UINT256_MAX))
             for c in coords}
    for r in range(args.refine_rounds):
        _, brackets, regions, warned, round_failure = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, spans=spans,
            ast=args.ast, focus=focus, memlimit=args.memlimit)
        last_failure = round_failure or last_failure
        print(f"[refine {r+1}] spans={spans} regions={regions}"
              + (f" UNSEPARATED={sorted(warned)}" if warned else ""))
        new = {c: (brackets_for(c, brackets) or spans[c]) for c in coords}
        if new == spans:
            break
        spans = new

    # Certify every candidate, shrinking on the witness when refuted.
    ok, failed = {}, {}
    for enc, depth, ce in paths:
        box = regions.get(enc)
        if box is None:
            failed[enc] = (last_failure or
                           "no fully bounded region was measured")
            continue
        empty = empty_coords(box)
        if empty:
            failed[enc] = (
                f"region is EMPTY on {', '.join(empty)} (lo > hi) under the "
                f"current pins, so this path has no domain in this slice; "
                f"certifying it would hold vacuously")
            continue
        if enc in warned:
            # Not fatal: certification is the arbiter. But say it, because a
            # region that a cut could not separate is EXPECTED to be refuted.
            print(f"[certify enc={enc}] region overlaps an unseparated sibling; "
                  f"certifying anyway, the query is what decides")
        last_wit = {}
        for _ in range(args.shrink_rounds):
            v, nb, wit = certify(args.esbmc, args.sol, args.contract, args.unit,
                                 enc, depth, box, ce, pins, args.max_tx,
                                 args.timeout, cwd, ast=args.ast, focus=focus,
                                 memlimit=args.memlimit)
            if wit:
                last_wit = wit
            if v == "SUCCESSFUL":
                ok[enc] = box
                break
            if v == "UNKNOWN":
                # No verdict at all -- ESBMC crashed, was killed, or produced
                # neither line. Shrinking here would treat "we never found out"
                # as "refuted" and would quietly hand back a NARROWER box that
                # nothing ever checked.
                failed[enc] = ("no verdict from the certification query "
                               "(ESBMC printed neither SUCCESSFUL nor FAILED)")
                break
            if nb is None or nb == box:
                failed[enc] = (
                    "refuted with no single-coordinate cut available"
                    + divergence_text(ce, last_wit, set(box) | set(pins), caveats))
                break
            print(f"[shrink enc={enc}] {box} -> {nb}")
            box = nb
        else:
            failed[enc] = (
                "shrink round budget exhausted"
                + divergence_text(ce, last_wit, set(box) | set(pins), caveats))

    # HARD CHECK, not a warning. Two certified regions that share a point mean
    # an input would have to walk two different paths. Reporting them and
    # carrying on would be shipping a contradiction.
    overlap = certified_overlap(ok)
    if overlap:
        print("\n=== INVARIANT VIOLATED: certified regions intersect ===")
        for e1, e2 in overlap:
            print(f"  enc={e1} and enc={e2} share at least one point:")
            print(f"    enc={e1}: {ok[e1]}")
            print(f"    enc={e2}: {ok[e2]}")
        print("Path domains partition the input space, so this cannot be true "
              "of any correct output. Refusing to report these as certified. "
              "This is the check that would have caught the certification gate "
              "returning true on every input -- that was spotted by eye, from "
              "exactly this symptom.")
        return 1

    print("\n=== CERTIFIED REGIONS ===")
    for enc, box in sorted(ok.items()):
        pin_txt = "".join(f", {n} == {v}" for n, v in pins.items())
        print(f"  enc={enc}: "
              + ", ".join(f"{n} in [{lo}, {hi}]" for n, (lo, hi) in box.items())
              + pin_txt)
    for enc, why in sorted(failed.items()):
        print(f"  enc={enc}: NOT CERTIFIED — {why}; this path falls back to its "
              f"concrete counterexample test")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
