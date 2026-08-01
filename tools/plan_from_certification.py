#!/usr/bin/env python3
"""generalise-result.json + cov-report.json  ->  testplan.json

The stage that was structurally missing. Before it, `foundry.cpp` rendered a
test from a COUNTEREXAMPLE, inside ESBMC, while certification ran afterwards in
another process -- so no emitted test could ever carry a certified region. This
reverses that order:

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

    # The Solidity type of each parameter. NOT guessed from the value: an
    # address and a uint256 both arrive as integers, and rendering an address as
    # uint256 changes what `bound` produces.
    #
    # ⛔ NOT AVAILABLE HERE, and this is the honest stopping point rather than a
    # place to guess. The certification result carries coordinate NAMES and
    # bounds; the parameter TYPES live in the unit's signature. Wiring them
    # through is a change to the result schema, not something to infer.
    raise Refuse(
        "STOPPED, and the reason is the point of this run.\n\n"
        f"The verifier certified a real region for {contract}.{unit} "
        f"enc={a.enc} (exit={exit_kind}): "
        + ", ".join(f"{p['name']} in [{p['lo']}, {p['hi']}]" for p in params)
        + (f", concrete {concrete_env}" if concrete_env else "")
        + (f", state {state_pins}" if state_pins else "")
        + (f", points {points}" if points else "")
        + ".\n\nTwo things are missing before a plan can be written, and "
          "NEITHER may be filled in by this script:\n"
          "  (1) the Solidity TYPE of each parameter. `generalise-result.json` "
          "carries coordinate names and integer bounds; the types live in the "
          "unit's signature and must be threaded through the result schema. "
          "Guessing (an address and a uint256 are both integers here) changes "
          "what bound() produces.\n"
          "  (2) an ASSERTION. The deliverable is defined as carrying at least "
          "one proved oracle. The certification query proves WHICH PATH the "
          "region walks, not what the call leaves behind -- that is stage 3 "
          "(assertion synthesis), which the paper leaves as a section header "
          "with no content and which does not exist in the implementation "
          "either. Writing an assertion here would make `manual: false` false.\n\n"
          "So B is not 0 because of wiring any more: the wiring now reaches "
          "this point with a certified region in hand. It is 0 because STAGE 3 "
          "DOES NOT EXIST.")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Refuse as e:
        sys.stderr.write(f"plan_from_certification: REFUSED — {e}\n")
        sys.exit(2)
