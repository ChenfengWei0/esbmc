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
import time

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


def struct_fields(text):
    """Top-level scalar fields of a rendered struct, as {field: int}.

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
            i = j
        i += 1
    return out


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
                refused.append(
                    f"{n} (aggregate; {len(fields)} scalar field(s) used "
                    f"instead: " + ", ".join(sorted(fields)) + ")")
            else:
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
            if (n.get("nodeType") == "VariableDeclaration"
                    and n.get("stateVariable")):
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


def lowering_artifacts(coords, declared):
    """Struct-field coordinates the SOURCE never declared.

    Only `base.field` coordinates are considered, and only when the declared set
    is non-empty -- with nothing to compare against, everything would look
    undeclared and the whole coordinate list would vanish for a reason that has
    nothing to do with the contract.
    """
    if not declared:
        return {}
    out = {}
    for c in coords:
        if "." not in c or c.startswith(("state.", "msg.", "tx.", "block.")):
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
        if not c.startswith("state."):
            continue
        mu = mutability.get(c[6:])
        if mu in ("immutable", "constant"):
            out[c] = mu
    return out


def geometric_values(limit):
    """Round-1 ladder: magnitude-independent, one run."""
    vals, v = [0], 1
    while v <= limit:
        vals.append(v)
        v *= 2
    vals.append(limit)
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
    idx = sorted({0, n - 1} |
                 {round(i * (n - 1) / (k - 1)) for i in range(k)})
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


def level0_candidates(paths, coords):
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
    """
    out = {}
    for c in coords:
        vals = sorted({ce[c] for _, _, ce in paths if c in ce})
        if vals:
            out[c] = vals
    return out


def single_point_coords(box):
    """Coordinates this path's box has collapsed to one value."""
    return sorted(n for n, (lo, hi) in box.items() if lo == hi)


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


BOX_RE = re.compile(
    r"path enc=(\d+) depth=\d+ OUTER box \(D_path is CONTAINED in it\): (.*)")
BRACKET_RE = re.compile(r"path enc=(\d+) BRACKET \(refine[^)]*\): (.*)")
REGION_RE = re.compile(
    r"path enc=(\d+) CERTIFIED region after subtracting sibling outer boxes "
    r"\(zero queries\): ([^—]*)(— WARNING.*)?")
SHRINK_RE = re.compile(r"retry with (\S+) in \[(\d+), (\d+)\]")
# The tool publishes each coordinate's own type range. The driver chooses the
# ladder and cannot choose it correctly without this: laying probes over the
# whole 256-bit range on a 160-bit `address` puts most of them OUTSIDE the type,
# where they wrap and measure a different number. Measured -- that is how an
# impossible-looking bracket (`lower in [2^255, 1)`) arose, and the inverted
# span it produced killed the loop.
TYPE_RANGE_RE = re.compile(
    r"coordinate '([^']+)' has TYPE RANGE \[(\d+), (\d+)\]")


# `name in [lo, hi]`, optionally followed by Definition 5's punched set
# `\ {v, w}`. One regex for both so the hole can never be read as belonging to
# the NEXT coordinate: they are captured in the same match as their interval.
INTERVAL_RE = re.compile(
    r"(\S+) in \[(\d+), (\d+)\](?: \\ \{([0-9, ]+)\})?")


def parse_intervals(text):
    # Scanned, not split: an interval contains ", " itself, so splitting on it
    # cuts every interval in half and silently yields nothing.
    return {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in INTERVAL_RE.finditer(text)}


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
            out[m.group(1)] = sorted(
                {int(v) for v in m.group(4).split(",") if v.strip()})
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
                re.escape(coord) + r" (upper|lower) in [\[(](\d+), (\d+)[\])]",
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


def outer_round(esbmc, sol, contract, unit, paths, coords, pins, probes,
                max_tx, timeout, cwd, spans=None, geometric=False,
                ast=None, focus=None, memlimit="8g", values_by_coord=None,
                extra_values=None, type_ranges=None,
                claim_budget=0):
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
    for c in coords:
        if c in values_by_coord:
            spec_coords.append(
                {"name": c,
                 "values": [str(v) for v in values_by_coord[c]]})
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
            limit = (type_ranges or {}).get(c, (0, UINT256_MAX))[1]
            vals = [str(v) for v in geometric_values(limit)]
            geo[c] = sorted(set(vals + extra), key=int)
            spec_coords.append({"name": c, "values": None})
        else:
            lo, hi = spans[c]
            spec = {"name": c, "lo": str(lo), "hi": str(hi)}
            if extra:
                spec["values"] = extra
            spec_coords.append(spec)
    spec = {"unit": unit, "probes": probes, "coords": spec_coords,
            "pin": [{"name": n, "value": str(v)} for n, v in pins.items()],
            "paths": [{"enc": e, "depth": d,
                       "ce": {k: str(v) for k, v in ce.items() if k in coords}}
                      for e, d, ce in paths]}
    if geo:
        geo, note = budget_probe_values(geo, len(paths), claim_budget)
        if note:
            print(f"[round] LADDER THINNED: {note}")
        for sc in spec_coords:
            if sc.get("values") is None:
                sc["values"] = geo[sc["name"]]
    path = os.path.join(cwd, "outer.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    # WALL CLOCK PER ROUND, printed. The bracket round's cost is a number the
    # evaluation needs and has never had: the only figures ever collected for it
    # came from runs that were ALSO hitting the type-wrap defect, so "did not
    # finish" could not be separated from "too slow". Those are different
    # claims and only one of them is about the method. Timed here, at the single
    # place a round is issued, so no caller can report a cost it did not
    # measure. ("did not finish" above is deliberately not called "too slow".)
    _t0 = time.time()
    log = run(esbmc, sol, contract, ["--path-cov-outer-box", path],
              max_tx, timeout, cwd, ast=ast, focus=focus, memlimit=memlimit)
    _wall = time.time() - _t0
    n_probe = sum(len(c.get("values", [])) or (probes + 2) for c in spec_coords)
    kind = ("level-0" if values_by_coord else
            ("geometric-bracket" if geometric else "linear-refine"))
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
    failure = round_failure_reason(log)
    if failure:
        print(f"[outer-box] ROUND MEASURED NOTHING — {failure}")
    boxes, brackets, regions, warned = {}, {}, {}, set()
    region_holes, type_ranges = {}, {}
    for line in log.splitlines():
        m = TYPE_RANGE_RE.search(line)
        if m:
            type_ranges[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        m = BOX_RE.search(line)
        if m:
            boxes[int(m.group(1))] = parse_intervals(m.group(2))
        m = BRACKET_RE.search(line)
        if m:
            brackets[int(m.group(1))] = m.group(2)
        m = REGION_RE.search(line)
        if m:
            regions[int(m.group(1))] = parse_intervals(m.group(2))
            # An OUTER box never carries holes -- it is a containment statement,
            # and punching is a subtraction step. Only the region is read for
            # them, so a hole cannot arrive from a line that cannot produce one.
            region_holes[int(m.group(1))] = parse_holes(m.group(2))
            if m.group(3):
                warned.add(int(m.group(1)))
    return boxes, brackets, regions, warned, failure, region_holes, type_ranges


CERTIFY_RESULT_RE = re.compile(
    r"^--path-cov-certify: RESULT: (CERTIFIED|REFUTED|VACUOUS|UNDECIDED)\b")


def verdict(log):
    """'SUCCESSFUL' / 'FAILED' / 'VACUOUS' / 'UNKNOWN'.

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
        # UNDECIDED means the solver was asked and could not answer, which is
        # the same actionable state as no verdict at all: stop, do NOT shrink.
        return {"CERTIFIED": "SUCCESSFUL", "REFUTED": "FAILED",
                "VACUOUS": "VACUOUS", "UNDECIDED": "UNKNOWN"}[result]
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
        punched = {v for v in set(a_holes.get(n, ())) | set(b_holes.get(n, ()))
                   if olo <= v <= ohi}
        if len(punched) >= ohi - olo + 1:
            return False
    return True


def certified_overlap(ok, holes=None):
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
    encs = sorted(ok)
    for i, e1 in enumerate(encs):
        for e2 in encs[i + 1:]:
            if boxes_intersect(ok[e1], ok[e2],
                               holes.get(e1), holes.get(e2)):
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


def divergence_text(path_ce, wit_ce, bounded, caveats=None, ranges=None,
                    holes=None):
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
    only_path = sorted(set(path_ce) - set(wit_ce))
    only_wit = sorted(set(wit_ce) - set(path_ce))
    asym = ""
    if only_path or only_wit:
        asym = ("; NOTE: the two payloads do not carry the same keys, so the "
                "comparison above covers only the shared ones"
                + (f" -- only in this path's: {', '.join(only_path)}"
                   if only_path else "")
                + (f" -- only in the witness's: {', '.join(only_wit)}"
                   if only_wit else ""))
    diff_all = [(n, path_ce[n], wit_ce[n])
                for n in sorted(set(path_ce) & set(wit_ce))
                if path_ce[n] != wit_ce[n]]
    # SPLIT OFF the differences whose witness value contradicts the bound this
    # very query assumed. Reporting those as "the witness differs on X" is how a
    # diagnosis went wrong: EscrowSrc.cancel was read as "the divergence lives in
    # an unpinned msg.sender", giving that its own failure cell, when the sender
    # WAS pinned and the reported value simply was not the entry-time one.
    # They are not dropped -- an unexplained contradiction between the report and
    # the query is worth saying out loud -- but they must not be offered as the
    # discriminating quantity.
    untrusted = [t for t in diff_all
                 if outside_assumed(t[0], t[2], ranges, holes)]
    diff = [t for t in diff_all if t not in untrusted]
    untrusted_note = ""
    if untrusted:
        def _assumed(n):
            txt = f"[{ranges[n][0]}, {ranges[n][1]}]"
            hs = sorted((holes or {}).get(n, ()))
            return txt + (" \\ {" + ", ".join(str(h) for h in hs) + "}"
                          if hs else "")
        untrusted_note = (
            "; NOTE: the witness value reported for "
            + ", ".join(f"{n} (={wv}, assumed in {_assumed(n)})"
                        for n, _, wv in untrusted)
            + " lies OUTSIDE the bound this query assumed, so it is NOT the "
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
                "scalar the two payloads have in common" + asym
                + untrusted_note)
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
    return ("; the witness differs from this path's counterexample on: "
            + ", ".join(
                f"{n} (path={pv}, witness={wv})"
                + ("" if n in bounded else " [NOT a bounded coordinate]")
                for n, pv, wv in diff)
            + asym + untrusted_note)


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
            max_tx, timeout, cwd, ast=None, focus=None, memlimit="8g",
            holes=None):
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

    spec = {"unit": unit, "enc": enc, "depth": depth,
            "ce": {k: str(v) for k, v in ce.items()},
            "box": [bound(n, lo, hi) for n, (lo, hi) in box.items()] +
                   [{"name": n, "lo": str(v), "hi": str(v)}
                    for n, v in pins.items()]}
    path = os.path.join(cwd, "cert.json")
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
    ap.add_argument("--claim-budget", type=int, default=0,
                    help="cap the number of CLAIMS an outer-box round emits, "
                         "thinning each coordinate's ladder evenly to fit. "
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
                         "default."
                    )
    ap.add_argument("--level0", action="store_true",
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
    artifacts = lowering_artifacts(coords, declared_struct_fields(args.ast))
    if artifacts:
        coords = [c for c in coords if c not in artifacts]
        print("[coords] DROPPED as struct-lowering artifact(s), not source "
              "fields: " + ", ".join(f"{c} ({w})"
                                     for c, w in sorted(artifacts.items()))
              + ". The struct lowering introduces padding members the source "
                "never declared; no generated test can set one, so offering it "
                "as a coordinate is the same defect as offering an immutable")

    unsettable = unsettable_coords(coords, state_mutability(args.ast))
    if unsettable:
        for c in sorted(unsettable):
            v = next((ce[c] for _, _, ce in paths if c in ce), None)
            if v is not None:
                pins.setdefault(c, v)
        coords = [c for c in coords if c not in unsettable]
        print("[coords] NOT SETTABLE by any generated test, pinned at the "
              "counterexample value instead of generalised: "
              + ", ".join(f"{c} ({unsettable[c]}, =={pins[c]})"
                          for c in sorted(unsettable))
              + ". An immutable is fixed at construction and a constant is in "
                "the code; neither is an input, so generalising over one asks "
                "the verifier about inputs no test can produce")
    elif args.ast:
        print("[coords] every state coordinate is a MUTABLE state variable "
              "(checked against the AST), so none was excluded")
    else:
        print("[coords] no --ast given, so state-variable mutability could NOT "
              "be checked: an immutable or constant coordinate would be "
              "generalised over as if a test could set it. Pass --ast to have "
              "them pinned instead")

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
        payload_names,
        {"free coordinate": coords,
         "pinned": set(pins),
         "environment (unconstrained)": set(env_names),
         "dropped lowering artifact": set(artifacts),
         "unsettable, pinned at its CE": set(unsettable),
         "refused by the tool": set(refused or ())})
    if unaccounted:
        print("[coords] ACCOUNTING VIOLATED — "
              + f"{len(unaccounted)} payload name(s) reached NO bucket: "
              + ", ".join(unaccounted)
              + ". Each is a quantity the counterexample carries that the "
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
            why.append(
                f"{len(unsettable)} coordinate(s) are fixed at deployment "
                f"(immutable/constant) and no test can set them: "
                + ", ".join(sorted(unsettable)))
        if refused:
            why.append(
                f"{len(refused)} name(s) were refused as UNSUPPORTED because "
                f"the coordinate kinds cannot express them (struct, mapping, "
                f"non-scalar): " + ", ".join(refused))
        if pins and not why:
            why.append("every coordinate was pinned by request")
        print("[coords] NO GENERALISABLE COORDINATE — "
              + "; ".join(why)
              + ". This is a COORDINATE-KIND result, not a search result: the "
                "paths were witnessed and their region is a point, so each "
                "falls back to its concrete counterexample test. Widening the "
                "ladder or the shrink budget cannot change it")
        return 1
    print(f"[coords] {', '.join(coords)}"
          + (f"   [pinned: {pins}]" if pins else ""))

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
    # Learned from the tool, round by round, and never guessed. Empty until a
    # round has published one, so the FIRST ladder falls back to the full 256-bit
    # range exactly as before -- there is nothing to know it from yet.
    type_ranges = {}
    if args.level0:
        cand = level0_candidates(paths, coords)
        l0_boxes, _, _, _, l0_failure, _, tr_new = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, values_by_coord=cand,
            ast=args.ast, focus=focus, memlimit=args.memlimit)
        # Level 0 lays no ladder, but it DOES publish every coordinate's type
        # range -- so the geometric bracket that follows can be bounded by the
        # type instead of by 2^256. That ordering is why the fix costs no extra
        # run: the information is already on the way past.
        type_ranges.update(tr_new)
        if l0_failure:
            # Not "no equality coordinates". Say which it was, here, where it is
            # known -- the same rule the rest of this file follows.
            print(f"[level0] round measured NOTHING — {l0_failure}; "
                  f"descending to the geometric ladder for every coordinate")
        else:
            eq = equality_coords(l0_boxes, coords, len(paths))
            for enc, b in sorted(l0_boxes.items()):
                pts = single_point_coords(b)
                print(f"[level0] enc={enc} single-point on: "
                      + (", ".join(f"{n}=={b[n][0]}" for n in pts)
                         if pts else "(none)"))
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
                blind = [n for n in pts if len(cand.get(n, ())) < 2]
                if blind:
                    print(f"[level0] ⚠ enc={enc}: the point(s) on "
                          + ", ".join(blind)
                          + " came from a ONE-VALUE candidate list, which "
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

    # Round 1: geometric bracket.
    if args.skip_bracket:
        brackets, regions, warned, round_failure = {}, {}, set(), None
        region_holes = {}
        print("[bracket] SKIPPED (--skip-bracket): refining from each "
              "coordinate's full type range, which is the same fallback the "
              "code takes when the bracket measures nothing")
    else:
        (_, brackets, regions, warned, round_failure, region_holes,
         tr_new) = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, geometric=True,
            ast=args.ast, focus=focus, memlimit=args.memlimit,
            values_by_coord=eq_values, extra_values=cand,
            type_ranges=type_ranges, claim_budget=args.claim_budget)
        type_ranges.update(tr_new)
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
        lo, hi = (brackets_for(c, brackets, type_ranges.get(c))
                  or (0, UINT256_MAX))
        tlo, thi = type_ranges.get(c, (0, UINT256_MAX))
        return (max(lo, tlo), min(hi, thi))
    spans = {c: _span(c) for c in coords}
    for r in range(args.refine_rounds):
        (_, brackets, regions, warned, round_failure, region_holes,
         tr_new) = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, spans=spans,
            ast=args.ast, focus=focus, memlimit=args.memlimit,
            values_by_coord=eq_values, extra_values=cand,
            type_ranges=type_ranges)
        type_ranges.update(tr_new)
        last_failure = round_failure or last_failure
        print(f"[refine {r+1}] spans={spans} regions={regions}"
              + (f" holes={ {k: v for k, v in region_holes.items() if v} }"
                 if any(region_holes.values()) else "")
              + (f" UNSEPARATED={sorted(warned)}" if warned else ""))
        new = {c: (brackets_for(c, brackets, type_ranges.get(c))
                   or spans[c]) for c in coords}
        if new == spans:
            break
        spans = new

    # Certify every candidate, shrinking on the witness when refuted.
    ok, failed, ok_holes = {}, {}, {}
    for enc, depth, ce in paths:
        box = regions.get(enc)
        if box is None:
            failed[enc] = (last_failure or
                           "no fully bounded region was measured")
            continue
        # The punched set travels WITH the box through the whole shrink loop.
        # A side cut applied below narrows the interval, and a hole outside the
        # narrowed interval removes nothing -- but it is also harmless to keep,
        # and dropping it here would need its own justification, so it stays and
        # the tool's own emptiness check is the arbiter.
        holes = dict(region_holes.get(enc) or {})
        empty = empty_coords(box, holes)
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
        last_wit, last_wit_box = {}, dict(box)
        # C3: |R| may only ever get NARROWER across shrink rounds. Seeded with
        # the region as measured, then compared after every accepted cut.
        prev_size = region_size(box, holes)
        for _ in range(args.shrink_rounds):
            v, nb, wit = certify(args.esbmc, args.sol, args.contract, args.unit,
                                 enc, depth, box, ce, pins, args.max_tx,
                                 args.timeout, cwd, ast=args.ast, focus=focus,
                                 memlimit=args.memlimit, holes=holes)
            if wit:
                last_wit = wit
                last_wit_box = dict(box)
            if v == "SUCCESSFUL":
                # C2, BEFORE the region is recorded as certified. A region that
                # excludes this path's own counterexample is certified about a
                # set the path does not have, and the CE is the one member of
                # the domain we know for certain.
                missing = ce_in_region(box, holes, ce)
                if missing:
                    failed[enc] = (
                        "CERTIFIED region does NOT contain this path's own "
                        "counterexample (" + "; ".join(missing) + "). The CE is "
                        "a known member of the domain -- the enumeration "
                        "witnessed the path with it -- so the region has been "
                        "cut into the real domain and the certificate is about "
                        "a different set. Refusing to report it as certified")
                    break
                ok[enc] = box
                ok_holes[enc] = holes
                break
            if v == "VACUOUS":
                # The box admits NO execution that walks this path. Neither
                # accepting nor shrinking is defensible: accepting certifies a
                # region containing no input, and shrinking responds to an empty
                # box by making it emptier. Recorded as its own reason, because
                # the cause is upstream -- the region came from a subtraction or
                # a pin that excluded this path from the slice entirely -- and
                # naming it as a refutation would send the reader looking at the
                # solver instead.
                failed[enc] = (
                    "region is VACUOUS: the certification query witnessed NO "
                    "execution admitted by it that walks this path, so every "
                    "exit assert held for want of an execution. Before the "
                    "non-vacuity witness existed this printed as a certificate")
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
                    + divergence_text(ce, last_wit,
                                      set(last_wit_box) | set(pins),
                                      caveats,
                                      assumed_ranges(last_wit_box, pins),
                                      assumed_holes(holes, pins)))
                break
            # C3: a shrink that does not shrink is a defect, not a slow round.
            # A region that GREW would mean the cut moved a bound outwards,
            # which no legal cut can do and which nothing downstream would ever
            # notice -- the loop would simply certify a wider region than the
            # one it measured.
            new_size = region_size(nb, holes)
            if new_size > prev_size:
                failed[enc] = (
                    f"INVARIANT VIOLATED: the shrink WIDENED the region "
                    f"(|R| {prev_size} -> {new_size}). A cut may only ever make "
                    f"the region narrower; a wider one would certify inputs "
                    f"that were never measured. Refusing to continue this path")
                break
            prev_size = new_size
            print(f"[shrink enc={enc}] {box} -> {nb}  |R| {new_size}")
            box = nb
        else:
            failed[enc] = (
                "shrink round budget exhausted"
                + divergence_text(ce, last_wit, set(last_wit_box) | set(pins),
                                  caveats,
                                  assumed_ranges(last_wit_box, pins),
                                  assumed_holes(holes, pins)))

    # HARD CHECK, not a warning. Two certified regions that share a point mean
    # an input would have to walk two different paths. Reporting them and
    # carrying on would be shipping a contradiction.
    overlap = certified_overlap(ok, ok_holes)
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
        hs = ok_holes.get(enc) or {}

        def _one(n, lo, hi, hs=hs):
            # The punched set is printed WITH its interval, never as a footnote:
            # `[0, 2^160-1]` and `[0, 2^160-1] \ {255}` are different regions,
            # and the second is the one that was certified. A reader who quotes
            # the interval alone would be quoting a region the query refuted.
            v = sorted(hs.get(n, ()))
            return (f"{n} in [{lo}, {hi}]"
                    + (" \\ {" + ", ".join(str(x) for x in v) + "}" if v
                       else ""))
        print(f"  enc={enc}: "
              + ", ".join(_one(n, lo, hi) for n, (lo, hi) in box.items())
              + pin_txt)
    for enc, why in sorted(failed.items()):
        print(f"  enc={enc}: NOT CERTIFIED — {why}; this path falls back to its "
              f"concrete counterexample test")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
