#!/usr/bin/env python3
"""generalise-result.json + cov-report.json  ->  testplan.json

⚠ NOT THE SHIPPING ROUTE, and an earlier version of this header said it was.
The route that produces a PUT with a proved oracle end to end is
`scripts/solidity_path_put.py`; see the RETRACTION near the end of this file for
what was claimed here and why it was wrong. This script targets the RICHER
TestPlan shape (named accounts, a pre-state established by real transactions,
semantic assertions over locals and return values) and refuses, naming which
producer each missing part would have to come from.

The order both routes share, and which `foundry.cpp` alone cannot follow --
inside ESBMC a test is rendered from a COUNTEREXAMPLE while certification runs
afterwards in another process, so no emitted test can carry a certified region:

    witness -> region certification -> [HERE] -> emitter -> forge

⛔ IT MUST NOT INVENT ANYTHING. Every field it writes is either copied from the
certification result or refused. A generator that fills a gap with a plausible
value produces a plan whose `manual: false` is a lie, and `manual: false` is
exactly the field an auditor would otherwise trust.

THREE CONSTRAINTS, EACH MEASURED RATHER THAN ASSUMED (FeeVault.setDiscount):

  1. A PIN MUST BE A VALUE THE ENUMERATION WITNESSED. Pinning `state.owner` to a
     clean fixture address (4096) killed ALL THREE paths -- every one was
     "EXCLUDED FROM THE SLICE by the pins", because C2 requires the region to
     contain the path's own counterexample and no CE had owner == 4096. The
     fixture is not free to choose; it has to land on a witnessed point.
  2. `msg.sender` HAS TO BE A COORDINATE for any unit guarded by a caller check.
     The driver treats msg./tx./block. as environment and never makes them free
     coordinates, so `setDiscount`'s success path -- guarded by
     `msg.sender == owner` -- reported "the witness differs on msg.sender [NOT a
     bounded coordinate]" and could not certify. With `--env-coord msg.sender`
     and `owner` pinned it certifies: `bps in [0, 250]`, `u` over the whole
     address space. (Pinning owner is also what turns `msg.sender == owner` from
     a CROSS-COORDINATE relation -- out of scope by Definition 6 -- into
     coordinate-equals-constant, which level 0 handles.)
  3. A SINGLE-POINT COORDINATE IS NOT A FUZZ PARAMETER. `[v, v]` is one value;
     declaring it as a fuzz argument and then `bound`ing it to a point is a
     concrete test wearing a PUT's syntax. Points are rendered as concrete
     values, and the count of coordinates with width > 1 is what decides whether
     a PUT is possible at all.

Usage:
  plan_from_certification.py <generalise-result.json> <cov-report.json>
                             --enc N --import PATH [--out FILE]
"""
import json
import os
import sys
import argparse

ENV_PREFIXES = ("msg.", "tx.", "block.")


class Refuse(Exception):
    pass


def width(lo, hi, holes):
    return (hi - lo + 1) - len([h for h in holes if lo <= h <= hi])


def exit_kind_of(report, unit, enc):
    """`normal` or `revert`, read from the ENUMERATION report, not inferred.

    The exit is a property the path enumeration already published per claim
    (`exit_kind`). Deriving it here from the decision sequence would be a second
    ledger of one fact, and the two would drift.
    """
    for c in report.get("claims", []):
        cond = c.get("condition") or ""
        if cond.split(":", 1)[0] != unit:
            continue
        if str(c.get("path_id")) == str(enc):
            ek = c.get("exit_kind")
            if ek:
                return ek
    raise Refuse(
        f"the enumeration report carries no claim for {unit} path_id {enc}, so "
        f"its exit kind is unknown. A test cannot be rendered without it: a "
        f"reverting path needs vm.expectRevert and a normal one must be emitted "
        f"bare, and guessing either way asserts something nothing proved")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("report")
    ap.add_argument("--enc", type=int, required=True)
    ap.add_argument("--import", dest="imp", required=True,
                    help="the import path the emitted test uses for the source")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv[1:])

    with open(a.result) as fh:
        res = json.load(fh)
    with open(a.report) as fh:
        rep = json.load(fh)

    if res.get("schema") != "path-generalise-result/1":
        raise Refuse(f"unknown result schema {res.get('schema')!r}")

    cert = [c for c in (res.get("certified") or []) if c["enc"] == a.enc]
    if not cert:
        why = next((n["reason"] for n in (res.get("not_certified") or [])
                    if n["enc"] == a.enc), None)
        raise Refuse(
            f"enc={a.enc} is not among the CERTIFIED regions"
            + (f". It is recorded as NOT CERTIFIED: {why}" if why else
               ". It is not in the result at all")
            + ". A plan may only be built from a region the verifier "
              "discharged; building one from an uncertified path would emit a "
              "test whose assumption nothing proved")
    if len(cert) > 1:
        raise Refuse(
            f"enc={a.enc} certified as {len(cert)} pieces. The region is their "
            f"UNION, which one `bound()` per coordinate cannot express -- that "
            f"is Definition 6, a region is a product of per-coordinate sets. "
            f"Emit one test per piece instead; refusing to silently pick one")
    c = cert[0]
    unit = res["unit"]
    contract = res["contract"]
    pins = {k: int(v) for k, v in (res.get("pins") or {}).items()}
    exit_kind = exit_kind_of(rep, unit, a.enc)

    # ---- split the certified box by WHAT KIND of quantity each coordinate is --
    params, concrete_env, state_pins, points = [], {}, {}, []
    for b in c["box"]:
        n = b["name"]
        lo, hi = int(b["lo"]), int(b["hi"])
        holes = [int(h) for h in (b.get("holes") or [])]
        w = width(lo, hi, holes)
        if w <= 0:
            raise Refuse(f"coordinate {n} has an EMPTY certified interval "
                         f"[{lo}, {hi}] minus {holes}; nothing can be drawn "
                         f"from it")
        if n.startswith(ENV_PREFIXES):
            if w != 1:
                raise Refuse(
                    f"environment coordinate {n} certified as a RANGE "
                    f"[{lo}, {hi}]. A test sets the caller and the value with "
                    f"one concrete call to vm.prank / a value option, not with "
                    f"a fuzzed range, so rendering this faithfully is not "
                    f"supported yet. Refusing rather than pinning it silently "
                    f"to one end, which would emit a test narrower than what "
                    f"was certified")
            concrete_env[n] = lo
            continue
        if n.startswith("state."):
            if w != 1:
                raise Refuse(
                    f"state coordinate {n} certified as a RANGE [{lo}, {hi}]. "
                    f"Establishing a range of storage values needs the mutable "
                    f"slot work; refusing rather than picking a point, which "
                    f"would emit a test narrower than what was certified")
            state_pins[n[len("state."):]] = lo
            continue
        if w == 1:
            # NOT a fuzz parameter. See constraint 3 in the module docstring.
            points.append({"name": n, "value": lo})
            continue
        params.append({"name": n, "lo": str(lo), "hi": str(hi),
                       "holes": [str(h) for h in holes],
                       "sol_type": None})     # filled below from the AST/report

    if not params:
        raise Refuse(
            "the certified region has NO coordinate of width > 1, so every "
            "input it admits is a single point. That is a concrete replay test "
            "with extra syntax, not a parameterized unit test -- emit it "
            "through the replay path instead")

    # ---- RETRACTION, kept visible rather than quietly edited out -----------
    #
    # This script used to stop here and print "STAGE 3 DOES NOT EXIST", naming
    # that as the reason the pipeline emits no PUT with an oracle. THAT CLAIM
    # WAS FALSE, and it was false about this repository, not about the method:
    #
    #   * stage 3 is `--path-cov-assert <spec.json>` inside ESBMC. It prints a
    #     POST-STATE ASSERTION LADDER (post ==/!=/>=/<=/>/< pre, and delta
    #     bounds) with a HOLDS/REFUTED/no-verdict per rung, and it has thirteen
    #     `solidity_path_cov_assert_*` regressions, `_r1_pair_written` and
    #     `_r1_pair_unchanged` among them;
    #   * stage 4 is `scripts/solidity_path_put.py`, 1151 lines, which already
    #     runs the emitter for the preamble, runs the ladder, reads the storage
    #     layout from `forge inspect`, reads the parameter types from the solc
    #     AST, and writes a PUT.
    #
    # The two facts this script said were unobtainable -- parameter types and a
    # proved assertion -- were both already being obtained, a directory away.
    # The claim was written from memory instead of by opening the scripts, and
    # it is recorded here rather than deleted because a retraction that leaves
    # no trace is how the same wrong premise gets re-derived.
    #
    # WHAT THIS SCRIPT IS FOR NOW. `solidity_path_put.py` establishes the entry
    # state with `vm.store` and its oracle is the ladder's own pre/post
    # relations. The TestPlan schema (`bench/FeeVault/schema/testplan.json`)
    # describes a STRICTLY RICHER artefact: a fixture of named accounts, a
    # pre-state established by real transactions (`via.kind == "call"`), locals,
    # and semantic assertions such as `net == amount - fee`. Nothing in the
    # certification result or the ladder produces those, so this route still
    # ends in a refusal -- but the refusal is now about the RICHER shape, and it
    # names what would have to produce each part.
    raise Refuse(
        "the certified region is real and reaches this point, and the TestPlan "
        "shape is what cannot be filled from it.\n\n"
        f"CERTIFIED for {contract}.{unit} enc={a.enc} (exit={exit_kind}): "
        + ", ".join(f"{p['name']} in [{p['lo']}, {p['hi']}]" for p in params)
        + (f", concrete {concrete_env}" if concrete_env else "")
        + (f", state {state_pins}" if state_pins else "")
        + (f", points {points}" if points else "")
        + "\n\nFor a PUT over exactly this region, with an oracle proved by "
          "ESBMC's assertion ladder, use the route that ships:\n\n"
          f"  scripts/solidity_path_put.py --contract {contract} "
          f"--unit {unit} --enc {a.enc} \\\n"
          "      --sol <flat.sol> --ast <flat.solast> --forge-project <proj> "
          "\\\n      --region '<the box above, as JSON>' --pin <each pin>\n\n"
          "What THIS route additionally needs, and where each part would have "
          "to come from -- none of it may be invented here:\n"
          "  (1) `pre_state[].via` -- the TRANSACTION SEQUENCE that puts the "
          "contract in the entry state. The certification result names the "
          "entry state as VALUES (`state.owner == 0`); it does not name a "
          "sequence of calls that reaches them, and `solidity_path_put.py` "
          "sidesteps the question with `vm.store`. Deriving a sequence is a "
          "reachability problem nothing in this pipeline solves today.\n"
          "  (2) semantic assertions over LOCALS and RETURN VALUES (`net == "
          "amount - fee`). The ladder speaks about STATE VARIABLES, pre versus "
          "post. A return value is not a state variable, so no rung is about "
          "it, and writing one here would make `manual: false` a lie.\n"
          "  (3) named fixture ACCOUNTS. `alice` is a modelling choice a human "
          "made; the region says `msg.sender in [0, 0]`.")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Refuse as e:
        sys.stderr.write(f"plan_from_certification: REFUSED — {e}\n")
        sys.exit(2)
