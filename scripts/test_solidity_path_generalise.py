#!/usr/bin/env python3
"""Regression for the stage-2 generalisation driver's report/verdict reading.

WHY THIS FILE EXISTS. The driver had two defects at once, and each hid the
other:

  * its unit filter read a report field that is always empty, so it matched zero
    paths on every input ever tried and the loop never reached the point where a
    region would be printed;
  * its certification test was `"VERIFICATION SUCCESSFUL" in log`, and ESBMC
    opens every bounded Solidity run with a WARNING that CONTAINS that phrase --
    so every certification came back true.

Certification is the only soundness gate in the pipeline. A gate that is
unconditionally green does not weaken the method, it removes it. The second
defect was therefore invisible for exactly as long as the first one kept the
loop from ever exercising it.

These are pure-function tests: no ESBMC, no solver, no clock. The log strings
below are VERBATIM captures from real runs, warning line included -- a
paraphrased fixture would have passed against the old code too, which is the
whole failure mode being pinned here.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from solidity_path_generalise import (verdict, claim_unit, coord_values,  # noqa
                                      empty_coords, shrink_target, is_env,
                                      round_failure_reason, boxes_intersect,
                                      certified_overlap, divergence_text,
                                      extraction_caveats, level0_candidates,
                                      single_point_coords, equality_coords,
                                      assumed_ranges, outside_assumed,
                                      parse_intervals, parse_holes,
                                      assumed_holes,
                                      round_accounting,
                                      state_mutability,
                                      unsettable_coords,
                                      struct_fields)

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


# The warning line is the one that made every certification read as certified.
# It is quoted exactly; shortening it would defeat the point of the test.
WARN = (
    "WARNING: Solidity harness: transaction sequence bounded to 1 tx "
    "(default). A VERIFICATION SUCCESSFUL result is bounded -- it means no "
    "violation within 1 transaction(s), NOT an unbounded proof; bugs requiring "
    "more transactions are not explored. Use --solidity-precise (or "
    "--solidity-max-tx 0) for an unbounded proof."
)

REFUTED = "\n".join([
    "Target: 64-bit little-endian x86_64-unknown-linux with esbmclibc",
    WARN,
    "Solving claim 'plain:path:7#exit0 at' with solver Bitwuzla 0.8.2",
    "✗ FAILED: 'plain:path:7#exit0 at'",
    "",
    "VERIFICATION FAILED",
    "ESBMC version 8.2.0 64-bit x86_64 linux",
])

CERTIFIED = "\n".join([
    "Target: 64-bit little-endian x86_64-unknown-linux with esbmclibc",
    WARN,
    "Solving claim 'plain:path:3#exit0 at' with solver Bitwuzla 0.8.2",
    "✓ PASSED: 'plain:path:3#exit0 at'",
    "",
    "VERIFICATION SUCCESSFUL",
    "ESBMC version 8.2.0 64-bit x86_64 linux",
])

# ESBMC dying inside the SMT layer. Observed for real: pinning a coordinate on a
# contract with an interface-typed storage slot trips
# `Assertion 'ta != nullptr && "Tuple AST mismatch"'`. NEITHER verdict line is
# printed. This must not read as "refuted": the loop would then respond to a
# crash by shrinking the box, i.e. by turning "we never found out" into "no".
CRASHED = "\n".join([
    "Target: 64-bit little-endian x86_64-unknown-linux with esbmclibc",
    WARN,
    "Solving claim 'plain:path:7#ub_a_0 at' with solver Bitwuzla 0.8.2",
    "esbmc: src/solvers/smt/tuple/smt_tuple_node_ast.h:72: "
    "const tuple_node_smt_ast* to_tuple_node_ast(smt_astt): "
    "Assertion `ta != nullptr && \"Tuple AST mismatch\"' failed.",
])

# --- the must-flip pair. Neither direction alone is evidence. ---
check("refuted-is-not-certified", verdict(REFUTED), "FAILED")
check("certified-is-certified", verdict(CERTIFIED), "SUCCESSFUL")
# and the third state, which is not a shade of either
check("crash-is-unknown", verdict(CRASHED), "UNKNOWN")

# The warning ALONE -- no verdict at all -- is the exact input that used to
# return "certified". If this ever goes back to SUCCESSFUL the gate is gone.
check("warning-alone-is-not-certified", verdict(WARN), "UNKNOWN")

# A killed run. On real input this is the COMMON case, not the exceptional one:
# one outer-box round on a real contract unit does not finish in 540s. It must
# read as UNKNOWN for the same reason a crash does.
TIMED_OUT = "\n".join([
    "Target: 64-bit little-endian x86_64-unknown-linux with esbmclibc",
    WARN,
    "Starting Bounded Model Checking",
    "[run] TIMEOUT after 100s: esbmc --path-cov-outer-box outer.json",
])
check("timeout-is-unknown", verdict(TIMED_OUT), "UNKNOWN")


# --- unit identification ---
# `function` is present and empty on every complete-path claim; the plain name
# lives in the `condition` prefix. A claim keyed the old way matched nothing.
check("unit-from-condition",
      claim_unit({"condition": "withdraw:path:14", "function": ""}),
      "withdraw")
check("unit-from-condition-toy",
      claim_unit({"condition": "plain:path:7", "function": ""}),
      "plain")
check("unit-missing-condition", claim_unit({"function": ""}), "")
check("unit-no-colon", claim_unit({"condition": "weird"}), "")


# --- coordinate refusal ---
# Struct-typed parameters and symbolic storage values are UNSUPPORTED and must
# be REFUSED, not crashed on. Before this the driver called int() on them and
# died with a ValueError partway through a benchmark.
ce, refused = coord_values({
    "inputs": {
        "a": "4",
        "immutables": "{ .orderHash={ .data=nil }, .taker=0, .amount=0 }",
    },
    "entry_storage": {
        "s": "0",
        "PROXY_BYTECODE_HASH": "_ESBMC_aux_Escrow.PROXY_BYTECODE_HASH",
    },
})
check("scalar-param-kept", ce.get("a"), 4)
check("scalar-slot-kept", ce.get("state.s"), 0)
check("struct-param-itself-refused", "immutables" in ce, False)
check("symbolic-slot-refused", "state.PROXY_BYTECODE_HASH" in ce, False)
# ...but its SCALAR FIELDS are coordinates now that the tool resolves
# `param.field`. Refusing the whole aggregate is what left every EscrowSrc unit
# with nothing to generalise: 23 witnessed paths across five units, zero
# coordinates, and not one of them a search failure.
check("struct-scalar-field-becomes-a-coordinate", ce.get("immutables.taker"), 0)
check("struct-second-field-too", ce.get("immutables.amount"), 0)
# The nested aggregate is NOT flattened here -- `immutables.orderHash.data` is
# reachable if something asks for it, but inventing the name unasked would put a
# coordinate in the list that nobody requested.
check("nested-aggregate-not-flattened",
      any(k.startswith("immutables.orderHash") for k in ce), False)
check("refusal-names-the-fields-used-instead",
      any(r.startswith("immutables (aggregate; 2 scalar field(s)")
          for r in refused), True)
check("symbolic-slot-still-plainly-refused",
      "state.PROXY_BYTECODE_HASH" in refused, True)

# The parser on its own, against the VERBATIM report rendering.
check("struct-fields-verbatim",
      struct_fields("{ .orderHash={ .data=nil }, .taker=0, .amount=0 }"),
      {"taker": 0, "amount": 0})
check("struct-fields-hex-value",
      struct_fields("{ .a=0xFF, .b=3 }"), {"a": 255, "b": 3})
# `nil` is not a value a test can produce, so it is skipped for the same reason a
# symbolic storage slot is refused.
check("struct-fields-skip-nil", struct_fields("{ .a=nil, .b=1 }"), {"b": 1})
# Depth matters: a field of a NESTED struct must not be lifted to the top level,
# or `orderHash.data` would silently become the coordinate `data`.
check("struct-fields-do-not-lift-nested",
      struct_fields("{ .outer={ .inner=7 }, .x=1 }"), {"x": 1})
check("struct-fields-non-struct-text", struct_fields("0"), {})
check("struct-fields-empty", struct_fields("{ }"), {})

# Hex is a value form the report really uses (block.number, large parameters).
ce2, refused2 = coord_values({"inputs": {"a": "0xFF"}, "entry_storage": {}})
check("hex-param-kept", ce2.get("a"), 255)
check("hex-not-refused", refused2, [])



# --- an empty box certifies vacuously and must never be certified ---
# The values are the ones a real run produced: with the environment pinned, the
# ABI-gate revert path's subtraction yielded lo > hi and the run reported it as
# a certified region.
BIG = 115792089237316195423570985008687907853269984665640564039457584007913129639935
check("empty-box-detected",
      empty_coords({"a": (23158417847463239084714197001737581570653996933128112807891516801582625927987, 0),
                    "state.s": (BIG, BIG)}),
      ["a"])
check("point-interval-is-not-empty", empty_coords({"a": (5, 5)}), [])
check("normal-interval-is-not-empty", empty_coords({"a": (0, 5)}), [])
check("every-empty-coord-named",
      empty_coords({"a": (7, 3), "b": (0, 1), "c": (9, 8)}), ["a", "c"])


# --- a refutation may not cut a pinned coordinate ---
# NOTE: unlike the verdict fixtures above, this line is RECONSTRUCTED from
# SHRINK_RE rather than captured verbatim -- a real refutation was observed
# cutting `a` from [0,5] to [0,3], and a real refutation was observed suggesting
# block.number while it was pinned, but the exact line text of the latter was
# not kept. What is pinned here is the pinned-coordinate REFUSAL, which does not
# depend on the surrounding prose.
SHRINK_LOG = ("--path-cov-certify: refuted; retry with block.number in "
              "[1, 115792089237316195423570985008687907853269984665640564039457584007913129639935]")
check("cut-taken-when-free",
      shrink_target(SHRINK_LOG, {}), ("block.number", 1, BIG))
check("cut-refused-when-pinned",
      shrink_target(SHRINK_LOG, {"block.number": BIG}), None)
check("no-cut-in-log", shrink_target("nothing here", {}), None)


# --- environment namespace ---
check("msg-is-env", is_env("msg.value"), True)
check("block-is-env", is_env("block.timestamp"), True)
check("tx-is-env", is_env("tx.origin"), True)
check("state-is-not-env", is_env("state.s"), False)
check("param-is-not-env", is_env("a"), False)

# env values are harvested unprefixed, because that is the name the tool resolves
ce3, _ = coord_values({"env": {"msg.value": "0", "block.number": "1"},
                       "inputs": {}, "entry_storage": {}})
check("env-harvested-unprefixed", sorted(ce3), ["block.number", "msg.value"])



# --- a round that measured nothing must say WHY, in the right words ---
# Verbatim from a real aqua run: the harvest reports state._DOCKED in
# entry_storage, the coordinate resolver does not accept it, and the tool
# refuses rather than silently widening the box.
UNRESOLVED = (
    "--path-cov-outer-box: OUTER-BOX BATCH for unit 'pull' ...\n"
    "ERROR: --path-cov-outer-box: unit 'sol:@C@Aqua@F@pull#3153' has no input "
    "named 'state._DOCKED'. Name a parameter, an environment value "
    "(`msg.value` ...), or a state variable at entry (`state.<field>`). "
    "Dropping it would silently produce a box with one fewer constraint, i.e. "
    "a WIDER region than the one measured.\n"
)
r = round_failure_reason(UNRESOLVED)
check("unresolved-names-the-coordinate", "state._DOCKED" in (r or ""), True)
check("unresolved-is-not-called-budget", "BUDGET" in (r or ""), False)
check("unresolved-says-support-gap", "COORDINATE-SUPPORT" in (r or ""), True)

TIMED = "Starting Bounded Model Checking\n[run] TIMEOUT after 100s: esbmc ...\n"
t = round_failure_reason(TIMED)
check("timeout-is-called-budget", "BUDGET" in (t or ""), True)
check("timeout-is-not-support-gap", "COORDINATE-SUPPORT" in (t or ""), False)

# A round that RAN and simply separated nothing must not be given either
# excuse -- that is the case where "no region" really is about the path.
OK_ROUND = ("--path-cov-outer-box: 12 of 12 ladder probe(s) reached the solver\n"
            "--path-cov-outer-box: path enc=7 depth=2 OUTER box "
            "(D_path is CONTAINED in it): a in [0, 5]\n")
check("clean-round-has-no-excuse",
      round_failure_reason(OK_ROUND + "\n[run] EXIT 1\n"), None)
check("successful-round-has-no-excuse",
      round_failure_reason(OK_ROUND + "\n[run] EXIT 0\n"), None)

# THE THIRD CAUSE, which the message whitelist missed. Verbatim from a
# FarmingPool run: a string-typed state coordinate (state._name) aborts the
# solver, the round returns nothing, and before this it was reported as "no
# fully bounded region was measured" -- a property of the path, for a crash.
# The exit code decides, so a FOURTH cause needs no new pattern.
ABORTED = ("Solving claim 'deposit:path:3623#ub_state._name_0 at' ...\n"
           "ERROR: Projecting from non-tuple based AST\n"
           "[run] EXIT 134\n")
a = round_failure_reason(ABORTED)
check("abort-is-detected", "134" in (a or ""), True)
check("abort-says-aborted", "ABORTED" in (a or ""), True)
check("abort-is-not-called-budget", "BUDGET" in (a or ""), False)
check("abort-is-not-called-support-gap", "COORDINATE-SUPPORT" in (a or ""),
      False)
# subprocess reports a signal-killed child as NEGATIVE, and that is the form the
# live run actually produced -- -6, not 134. Both must name SIGABRT rather than
# falling through to the generic wording.
neg = round_failure_reason("ERROR: Projecting from non-tuple based AST\n"
                           "[run] EXIT -6\n")
check("negative-signal-detected", "-6" in (neg or ""), True)
check("negative-signal-named-sigabrt", "SIGABRT" in (neg or ""), True)
# An exit code nobody has seen yet must still be caught, which is the whole
# point of not enumerating causes.
u = round_failure_reason("something new\n[run] EXIT 42\n")
check("unknown-exit-code-still-caught", "42" in (u or ""), True)
# The two NAMED causes keep their specific wording even though a bad exit code
# accompanies them -- more specific beats more general.
check("timeout-wording-survives-exit-code",
      "BUDGET" in (round_failure_reason(TIMED + "\n[run] EXIT 124\n") or ""),
      True)
check("unresolved-wording-survives-exit-code",
      "COORDINATE-SUPPORT" in
      (round_failure_reason(UNRESOLVED + "\n[run] EXIT 134\n") or ""), True)



# --- two certified regions may never intersect ---
# The positive fixture is the ACTUAL output that exposed the always-green gate:
# enc=2 and enc=7 were both reported certified over a in [0, 5]. A human noticed
# the contradiction; this is the code noticing it instead.
GREEN_GATE_OUTPUT = {
    2: {"a": (0, 5), "state.s": (0, BIG)},
    7: {"a": (0, 5), "state.s": (0, BIG)},
}
check("the-bug-that-happened-is-caught",
      certified_overlap(GREEN_GATE_OUTPUT), [(2, 7)])

# The negative control is the real certified output from the payable contract:
# disjoint on `a`, and together the whole type. It must NOT fire.
REAL_PARTITION = {
    2: {"a": (6, BIG), "state.s": (0, BIG)},
    3: {"a": (0, 5), "state.s": (0, BIG)},
}
check("a-real-partition-does-not-fire", certified_overlap(REAL_PARTITION), [])

# One disjoint coordinate separates two boxes even when every other overlaps --
# a box is a conjunction.
check("one-disjoint-coord-separates",
      boxes_intersect({"a": (0, 5), "b": (0, BIG)},
                      {"a": (6, BIG), "b": (0, BIG)}), False)
check("touching-at-one-point-intersects",
      boxes_intersect({"a": (0, 5)}, {"a": (5, 9)}), True)
# A coordinate constrained in one box and absent from the other is
# unconstrained there, so it cannot separate them.
check("absent-coord-does-not-separate",
      boxes_intersect({"a": (0, 5), "b": (7, 9)}, {"a": (0, 5)}), True)
check("three-way-overlap-lists-all-pairs",
      certified_overlap({1: {"a": (0, 9)}, 2: {"a": (0, 9)}, 3: {"a": (0, 9)}}),
      [(1, 2), (1, 3), (2, 3)])


# --- the reach gate must NAME the quantity, and its three outcomes must differ ---
#
# "Refuted with no single-coordinate cut available" is the number the evaluation
# leans on, and on its own it says only that the witness agrees on every BOUNDED
# coordinate. Reading the missing coordinate CLASS off it is an inference from
# having seen nothing else -- the inference this project has got wrong five
# times. All three outcomes below have to stay distinguishable, and the middle
# one is the one that would otherwise read as "nothing to report".

# (1) A named difference, with the bounded/unbounded split. This is the shape the
# evaluation's coordinate table needs in order to explain the reach-gate bucket
# by measurement rather than by argument.
d1 = divergence_text({"a": 4, "state._DOCKED": 255, "block.timestamp": 1},
                     {"a": 4, "state._DOCKED": 0, "block.timestamp": 1},
                     {"a"})
check("divergence-names-the-quantity", "state._DOCKED" in d1, True)
check("divergence-gives-both-values",
      "path=255" in d1 and "witness=0" in d1, True)
check("divergence-flags-unbounded", "NOT a bounded coordinate" in d1, True)
check("divergence-omits-agreeing-quantities", "block.timestamp" in d1, False)
check("divergence-omits-agreeing-bounded", "a (" in d1, False)

# A difference ON a bounded coordinate is reported WITHOUT the unbounded flag --
# it should not normally happen (a bounded difference yields a cut), so if it
# ever appears it must be visible as the anomaly it is rather than mislabelled.
d1b = divergence_text({"a": 4}, {"a": 9}, {"a"})
check("bounded-difference-not-flagged-unbounded",
      "NOT a bounded coordinate" in d1b, False)
check("bounded-difference-still-named", "a (path=4, witness=9)" in d1b, True)

# (2) MEASURED on aqua: the witness agreed with the path's counterexample on
# every scalar in the payload -- all four coordinates and all fifteen environment
# quantities. The discriminating quantity is not in the payload at all. This is
# a finding, and it must not render as an empty list.
d2 = divergence_text({"a": 4, "state._DOCKED": 255},
                     {"a": 4, "state._DOCKED": 255}, {"a"})
check("no-difference-is-stated-explicitly", "not in the payload at all" in d2,
      True)
check("no-difference-is-called-unknown-bucket",
      "explicit unknown bucket" in d2, True)

# (3) NO payload at all. Distinct from (2): one says "we looked and they agree",
# the other says "we could not look". Collapsing them is this file's recurring
# failure-as-result bug.
d3 = divergence_text({"a": 4}, {}, {"a"})
check("missing-payload-is-not-no-difference",
      "NOT a finding of 'no difference'" in d3, True)
check("missing-payload-differs-from-agreement", d3 == d2, False)

# (4) The empty-difference case must be NARROWED by what the report already
# said. `ce_extraction` names each family the harvest could not render, with the
# mechanism -- it has been in every report this driver ever read, and the driver
# never looked. Verbatim from an aqua run.
EXTRACTION = {
    "ce_extraction": {
        "compact_trace": False,
        "extcall_returns_unavailable_reason":
            "not implemented yet. The value an external call returns to the "
            "contract reaches the user's variable through a tuple-field "
            "extraction, which get_nondet_symbol does not traverse, so that "
            "trace step is skipped before classification.",
    }
}
cav = extraction_caveats([EXTRACTION])
check("caveat-family-extracted", sorted(cav), ["extcall_returns"])
check("caveat-keeps-the-mechanism", "tuple-field extraction" in
      cav["extcall_returns"], True)
check("caveat-ignores-non-reason-keys", "compact_trace" in cav, False)

d4 = divergence_text({"a": 4}, {"a": 4}, {"a"}, cav)
check("empty-diff-quotes-the-caveat", "extcall_returns" in d4, True)
check("empty-diff-keeps-the-unknown-bucket",
      "explicit unknown bucket" in d4, True)
# Quoting a named candidate is NOT concluding it is the answer, and the wording
# has to keep saying so -- that distinction is the whole reason this file exists.
check("named-candidate-is-not-a-conclusion",
      "NAMED candidate, not a conclusion" in d4, True)
# With no caveats the message must be exactly the un-narrowed one, so a missing
# ce_extraction cannot quietly look like a narrowed answer.
check("no-caveats-leaves-message-unnarrowed",
      divergence_text({"a": 4}, {"a": 4}, {"a"}, {}) == d2, True)

# (5) ASYMMETRIC PAYLOADS. Comparing only the shared keys and then saying the
# witness "agrees on EVERY scalar quantity" is false as soon as one side carries
# a key the other does not -- the intersection drops it and the sentence still
# claims total agreement. Overclaiming is the one thing this function must not
# do, since saying precisely what was compared is its whole job.
d5 = divergence_text({"a": 4}, {"a": 4, "b": 7}, {"a", "b"})
check("asymmetric-does-not-claim-every-scalar",
      "EVERY scalar quantity in the payload" in d5, False)
check("asymmetric-says-only-the-shared-ones",
      "only the shared ones" in d5, True)
check("asymmetric-names-the-extra-key", "only in the witness's: b" in d5, True)
# ...and it is NOT the unknown-bucket message either: "they agree on everything"
# and "they agree on everything comparable" are different findings.
check("asymmetric-is-not-the-unknown-bucket",
      "not in the payload at all" in d5, False)

# The other direction, and a difference PLUS an asymmetry: the named difference
# must still be reported, with the coverage caveat appended rather than either
# one silently replacing the other.
d6 = divergence_text({"a": 4, "c": 1}, {"a": 9}, {"a"})
check("asym-with-diff-still-names-the-difference",
      "a (path=4, witness=9)" in d6, True)
check("asym-with-diff-still-flags-coverage",
      "only in this path's: c" in d6, True)


# --- LEVEL 0: the candidate comes from the sibling, and "point" must be able
#     to come back false ---
#
# The descent is single point -> small set -> interval and this driver started
# at the interval, which on an equality-constrained coordinate means bisecting
# toward a point: measured on state.FACTORY, round after round of halving
# (2923...595 -> 429496731 -> 214748363 -> 107374179 -> 53687087 -> 26843535 ->
# 13421759), about 160 rounds away from the answer on 2^160.

ADDR_MAX = (1 << 160) - 1

# (1) PROVENANCE. The candidate must be the SIBLING'S OWN counterexample value
# (proposition 9), never a constant this file knows about. The observation that
# can flip: change the counterexample, and the candidate has to change with it.
# Without this check a hardcoded {0, 1, MAX} table would pass every other test
# here, and that table is the third red line.
P_A = [(2, 1, {"to": 0, "amt": 7}), (3, 1, {"to": ADDR_MAX, "amt": 7})]
P_B = [(2, 1, {"to": 255, "amt": 7}), (3, 1, {"to": ADDR_MAX, "amt": 7})]
check("candidates-come-from-the-counterexamples",
      level0_candidates(P_A, ["to", "amt"]),
      {"to": [0, ADDR_MAX], "amt": [7]})
check("candidates-follow-a-changed-counterexample",
      level0_candidates(P_B, ["to", "amt"])["to"], [255, ADDR_MAX])
check("candidates-are-deduplicated", level0_candidates(P_A, ["amt"]),
      {"amt": [7]})
# A coordinate absent from every payload yields no candidate list at all rather
# than an empty one, so it falls through to the ladder instead of being probed
# with nothing.
check("absent-coordinate-gets-no-candidate-list",
      "nope" in level0_candidates(P_A, ["to", "nope"]), False)

# (2) POSITIVE. A box that came back [v, v] is the single-point case.
check("point-box-is-single-point",
      single_point_coords({"to": (255, 255), "amt": (0, 99)}), ["to"])
check("range-box-is-not-single-point",
      single_point_coords({"amt": (0, 99)}), [])

# (3) MUST FLIP -- the projection is NOT a single point.
#     enc=3's domain is every address except one, so its box on `to` is the
#     whole type. `to` is therefore NOT equality-type and must still go through
#     the ladder. Building only the positive case would give a rule that files
#     anything into the hole, which is the failure this pair exists to prevent.
BOXES_MIXED = {2: {"to": (255, 255), "amt": (0, 99)},
               3: {"to": (0, ADDR_MAX), "amt": (0, 99)}}
check("mixed-projection-is-not-equality-type",
      equality_coords(BOXES_MIXED, ["to", "amt"], 2), [])

# ...and the positive control on the same shape: when EVERY path pins it,
# it is equality-type. This is the state.FACTORY case -- a validation check that
# forces one address on every path.
BOXES_ALL_POINTS = {2: {"to": (255, 255), "amt": (0, 99)},
                    3: {"to": (255, 255), "amt": (0, 99)}}
check("all-paths-pinned-is-equality-type",
      equality_coords(BOXES_ALL_POINTS, ["to", "amt"], 2), ["to"])
# Two paths pinned to DIFFERENT points is still equality-type: each path's own
# projection is a point, which is exactly what the batch needs in order to lay
# candidates instead of a ladder.
check("different-points-per-path-still-equality-type",
      equality_coords({2: {"to": (1, 1)}, 3: {"to": (2, 2)}}, ["to"], 2),
      ["to"])

# (4) MUST FLIP -- missing measurements are not evidence.
#     If a path produced no box, "every path is a point" is a statement about
#     the paths that happened to report. Treating that as equality-type would
#     skip the ladder on the strength of a measurement that was never made --
#     the "saw nothing, therefore not X" inference this project has got wrong
#     five times.
check("a-missing-path-blocks-the-conclusion",
      equality_coords({2: {"to": (255, 255)}}, ["to"], 2), [])
check("no-boxes-at-all-is-not-equality-type",
      equality_coords({}, ["to"], 2), [])
# A coordinate missing from one path's box is unconstrained there, not a point.
check("coordinate-absent-from-one-box-blocks-it",
      equality_coords({2: {"to": (5, 5)}, 3: {"amt": (0, 9)}}, ["to"], 2), [])


# --- a witness value that contradicts the query's OWN assumption is not a
#     divergence, and must not be reported as one ---
#
# This is here because a diagnosis was drawn from exactly that. EscrowSrc.cancel
# was read as "the divergence lives in an unpinned msg.sender", and that reading
# got its own failure cell -- while the sender WAS pinned and the reported value
# simply was not the entry-time one. Measured afterwards on three fixtures: the
# bound does bind (same path, pin [255,255] SUCCESSFUL vs [0,0] FAILED); the
# report matches the pin when the path reads the quantity and makes no nested
# call; and it can contradict the pin when the path never reads it, or when a
# call wrapper has overwritten msg.sender with the callee's identity.
#
# The check needs none of those mechanisms. It compares what was reported against
# what was assumed -- a proposition the pipeline already relies on, made into a
# runtime check.

check("assumed-ranges-fold-pins-in",
      assumed_ranges({"a": (0, 5)}, {"msg.sender": 7}),
      {"a": (0, 5), "msg.sender": (7, 7)})
check("inside-the-assumption-is-trusted",
      outside_assumed("a", 3, {"a": (0, 5)}), False)
check("outside-the-assumption-is-not",
      outside_assumed("a", 9, {"a": (0, 5)}), True)
check("unbounded-coordinate-is-trusted",
      outside_assumed("b", 9, {"a": (0, 5)}), False)
check("no-ranges-at-all-is-trusted", outside_assumed("a", 9, None), False)

# (1) POSITIVE. A difference whose witness value is INSIDE the assumed box is a
#     genuine divergence and stays reported.
t1 = divergence_text({"a": 4, "s": 1}, {"a": 4, "s": 3}, {"a", "s"}, None,
                     {"a": (0, 5), "s": (0, 9)})
check("in-range-difference-still-reported", "s (path=1, witness=3)" in t1, True)
check("in-range-difference-not-flagged-untrusted",
      "OUTSIDE the bound" in t1, False)

# (2) MUST FLIP. The same difference, with the witness value outside the bound
#     the query assumed, is excluded from the divergence and named as untrusted.
t2 = divergence_text({"a": 4, "s": 1}, {"a": 9, "s": 3}, {"a", "s"}, None,
                     {"a": (0, 5), "s": (0, 9)})
check("out-of-range-difference-excluded", "a (path=4, witness=9)" in t2, False)
check("out-of-range-difference-named", "OUTSIDE the bound" in t2, True)
check("out-of-range-note-gives-the-assumption", "assumed in [0, 5]" in t2, True)
check("other-difference-survives", "s (path=1, witness=3)" in t2, True)

# (3) THE ANTI-COLLAPSE CHECK, and the reason this is not just a filter. When
#     EVERY observed difference is untrusted, the result must NOT fall through
#     to "the witness agrees on every scalar" -- that message is the explicit
#     empty-divergence bucket the reach-gate number is built from, and reaching
#     it from a measurement problem would manufacture a reach-gate hit.
t3 = divergence_text({"a": 4}, {"a": 9}, {"a"}, None, {"a": (0, 5)})
check("all-untrusted-is-not-the-empty-divergence-case",
      "not in the payload at all" in t3, False)
check("all-untrusted-says-it-could-not-be-compared",
      "could not be compared" in t3, True)
check("all-untrusted-still-names-the-quantity", "OUTSIDE the bound" in t3, True)

# (4) CLOSED BY DEFAULT. With no ranges supplied the wording is byte-identical
#     to before, so every existing caller and fixture is unaffected.
check("no-ranges-leaves-wording-unchanged",
      divergence_text({"a": 4}, {"a": 9}, {"a"}),
      divergence_text({"a": 4}, {"a": 9}, {"a"}, None, None))
check("no-ranges-reports-the-difference",
      "a (path=4, witness=9)" in divergence_text({"a": 4}, {"a": 9}, {"a"}),
      True)


# --- PUNCHED INTERVALS (Definition 5): `[lo, hi] \ {v}` ---
#
# The subtraction now removes a single-point sibling by excluding that value
# instead of keeping one SIDE of it. Measured on one address coordinate: the old
# behaviour answered `[256, 2^160-1]` or `[0, 254]` depending only on which
# counterexample the solver happened to return for the sibling -- both correct,
# 5.7e45 apart. The hole makes both cases answer `[0, 2^160-1] \ {255}`.
#
# The driver has to read, carry and re-send the hole. Dropping it anywhere means
# certifying a WIDER region than the one reported, which the query then refutes,
# which hands the entire gain straight back.

REGION_LINE = ("--path-cov-outer-box: path enc=3 CERTIFIED region after "
               "subtracting sibling outer boxes (zero queries): "
               "to in [0, 1461501637330902918203684832716283019655932542975] "
               "\\ {255}, amt in [0, 99]")
check("region-intervals-still-parse",
      parse_intervals(REGION_LINE),
      {"to": (0, ADDR_MAX), "amt": (0, 99)})
check("region-holes-parsed", parse_holes(REGION_LINE), {"to": [255]})
# A coordinate WITHOUT a punched set must get no entry at all -- an empty list
# and "no holes" would be indistinguishable to a consumer that tests truthiness,
# and the interval that follows must not inherit the previous one's holes.
check("unpunched-coordinate-has-no-hole-entry",
      "amt" in parse_holes(REGION_LINE), False)
check("no-holes-anywhere-parses-empty",
      parse_holes("to in [0, 5], amt in [0, 99]"), {})
check("several-holes-on-one-coordinate",
      parse_holes("a in [0, 9] \\ {3, 5, 7}"), {"a": [3, 5, 7]})

# THE FALSE ALARM. This is not a refinement of the overlap check, it is a live
# bug without it: the partition check EXITS on an intersection, so reading these
# two correct regions without the hole would kill a correct run. Both were
# independently certified by the query on the fixture this pair comes from.
PUNCHED_PARTITION = {2: {"to": (255, 255)}, 3: {"to": (0, ADDR_MAX)}}
PUNCHED_HOLES = {2: {}, 3: {"to": [255]}}
check("without-holes-the-partition-check-false-alarms",
      certified_overlap(PUNCHED_PARTITION), [(2, 3)])
check("with-holes-the-same-regions-are-disjoint",
      certified_overlap(PUNCHED_PARTITION, PUNCHED_HOLES), [])
# ...and the hole must only separate where it actually covers the overlap. A
# hole somewhere else in the interval leaves the two genuinely intersecting, so
# the check must still fire -- otherwise it would have been traded for silence.
check("a-hole-outside-the-overlap-does-not-separate",
      boxes_intersect({"a": (0, 9)}, {"a": (4, 6)}, {"a": [1]}, None), True)
check("a-hole-covering-the-whole-overlap-separates",
      boxes_intersect({"a": (0, 9)}, {"a": (5, 5)}, {"a": [5]}, None), False)
check("a-hole-covering-part-of-the-overlap-does-not",
      boxes_intersect({"a": (0, 9)}, {"a": (5, 6)}, {"a": [5]}, None), True)

# EMPTINESS has a second route once the interval can be punched, and `lo > hi`
# cannot see it. Same consequence as an inverted interval: an unsatisfiable
# assumption certifies vacuously.
check("punched-out-point-is-empty", empty_coords({"a": (5, 5)}, {"a": [5]}),
      ["a"])
check("partly-punched-interval-is-not-empty",
      empty_coords({"a": (0, 9)}, {"a": [5]}), [])
check("hole-outside-the-interval-does-not-empty-it",
      empty_coords({"a": (0, 9)}, {"a": [50]}), [])
check("empty-coords-unchanged-without-holes",
      empty_coords({"a": (5, 5)}), [])

# The trust check must treat a PUNCHED value as outside the assumption too. The
# query said `c != h`, so a report claiming `c == h` contradicts it exactly as a
# value beyond the endpoints does -- and reading only the endpoints would let
# through the one value the query most explicitly excluded.
check("punched-value-is-outside-the-assumption",
      outside_assumed("a", 5, {"a": (0, 9)}, {"a": [5]}), True)
check("unpunched-value-inside-is-still-trusted",
      outside_assumed("a", 4, {"a": (0, 9)}, {"a": [5]}), False)
check("holes-omitted-behaves-as-before",
      outside_assumed("a", 5, {"a": (0, 9)}), False)
check("assumed-holes-drops-pinned-coordinates",
      assumed_holes({"a": [1], "msg.sender": [2]}, {"msg.sender": 7}),
      {"a": [1]})

# ...and end to end through divergence_text: a witness value equal to a punched
# point is excluded from the divergence and named, with the punched set shown in
# the assumption it contradicts.
tp = divergence_text({"a": 4, "s": 1}, {"a": 5, "s": 3}, {"a", "s"}, None,
                     {"a": (0, 9), "s": (0, 9)}, {"a": [5]})
check("punched-witness-value-excluded", "a (path=4, witness=5)" in tp, False)
check("punched-witness-value-named", "OUTSIDE the bound" in tp, True)
check("punched-note-shows-the-punched-set", "[0, 9] \\ {5}" in tp, True)
check("other-difference-survives-punching", "s (path=1, witness=3)" in tp, True)
# CLOSED BY DEFAULT: with no holes the wording is byte-identical to before.
check("no-holes-leaves-divergence-wording-unchanged",
      divergence_text({"a": 4}, {"a": 9}, {"a"}, None, {"a": (0, 5)}),
      divergence_text({"a": 4}, {"a": 9}, {"a"}, None, {"a": (0, 5)}, None))


# --- the witness must be judged against the box it was SOLVED under ---
#
# This pins a FALSE POSITIVE that reached real input. In the shrink loop the box
# advances each round, and the cut is placed AT the witness -- so the witness of
# round N is reliably just outside the box of round N+1. Checking it against the
# final box therefore reported "the witness value contradicts the bound this
# query assumed" on EVERY budget-exhausted path, from arithmetic alone, saying
# nothing whatever about the model. Measured on EscrowSrc.cancel: last shrink
# (0, 268214519) -> (0, 134127735) with witness 134127736 -- inside the box it
# was solved under, outside the one it was compared against. Four paths, four
# spurious contradictions, and the anti-collapse branch fired for a wrong reason,
# which is worse than not firing at all: its entire job is to say the payload
# could not be compared.
WIT = {"state.FACTORY": 134127736}
PATH_CE = {"state.FACTORY": 0}
# The box the witness was actually solved under: 134127736 is INSIDE it, so this
# is a genuine divergence and must be reported as one.
right = divergence_text(PATH_CE, WIT, {"state.FACTORY"}, None,
                        {"state.FACTORY": (0, 268214519)})
check("witness-inside-its-own-box-is-a-real-divergence",
      "state.FACTORY (path=0, witness=134127736)" in right, True)
check("witness-inside-its-own-box-not-flagged-untrusted",
      "OUTSIDE the bound" in right, False)
# The NEXT box, which it was never solved under. Reported as untrusted -- which
# is correct behaviour for this input and precisely why passing it was a bug.
wrong = divergence_text(PATH_CE, WIT, {"state.FACTORY"}, None,
                        {"state.FACTORY": (0, 134127735)})
check("witness-against-the-next-box-looks-untrusted",
      "OUTSIDE the bound" in wrong, True)
check("the-two-differ", right == wrong, False)
# ...and that mistake does not merely add a note, it CHANGES THE FINDING: with
# the only difference untrusted, the result becomes the anti-collapse message
# instead of a named divergence. That is the shape that made it dangerous.
check("wrong-box-collapses-to-could-not-be-compared",
      "could not be compared" in wrong, True)
check("right-box-does-not", "could not be compared" in right, False)


# --- a round may not report cost without reporting what was decided ---
LADDER_LOG = "\n".join([
    "--path-cov-outer-box: 417 of 420 ladder probe(s) reached the solver. ...",
    "Runtime decision procedure: 0.002s",
    "Runtime decision procedure: 0.001s",
    "Runtime decision procedure: 88.400s",
    "✓ PASSED: 'send:path:2#ub_a_0 at'",
    "✗ FAILED: 'send:path:3#ub_a_0 at'",
])
acc = round_accounting(LADDER_LOG)
check("accounting-reports-decided-over-total",
      "417 of 420 probe(s) reached the solver" in acc, True)
check("accounting-reports-the-max", "max=88.400s" in acc, True)
check("accounting-reports-the-median", "median=" in acc, True)
check("accounting-reports-the-verdict-mix",
      "PASSED=1 FAILED=1" in acc, True)
# A round that produced NO decision time at all must say so rather than report
# zero -- "the solver answered nothing" and "the solver answered instantly" are
# opposite findings and a 0.0 would read as the second.
empty = round_accounting("--path-cov-outer-box: 0 of 420 ladder probe(s) "
                         "reached the solver")
check("no-decision-time-is-not-zero",
      "NO query reported a decision time" in empty, True)
check("no-decision-time-still-reports-the-ratio",
      "0 of 420" in empty, True)


# --- a coordinate no generated test can set must not be generalised over ---
#
# MEASURED on EscrowSrc: `cancel`'s only two free coordinates are
# `state.FACTORY` and `state.RESCUE_DELAY`, and BOTH are `immutable` -- the
# contract declares twelve state variables and not one of them is mutable. The
# loop was ranging over quantities fixed at deployment, which hands the verifier
# an input space wider than reality, so its 0-of-4 certification result was never
# a search-power problem and no ladder would have fixed it.
#
# The fact is READ, not inferred. "Constant across every counterexample" is true
# of an immutable and equally true of ordinary storage that happens not to vary,
# so a heuristic here would be the exact inference this project keeps getting
# wrong. solc states `mutability` outright on every VariableDeclaration.
MUT = {"FACTORY": "immutable", "RESCUE_DELAY": "immutable",
       "balance": "mutable", "_LOW_160_BIT_MASK": "constant"}
check("immutable-state-coordinate-is-unsettable",
      unsettable_coords(["state.FACTORY"], MUT), {"state.FACTORY": "immutable"})
check("constant-state-coordinate-is-unsettable",
      unsettable_coords(["state._LOW_160_BIT_MASK"], MUT),
      {"state._LOW_160_BIT_MASK": "constant"})
# THE MUST-FLIP: a mutable state variable is settable and must survive. Without
# this the rule could disqualify every state coordinate and still look right.
check("mutable-state-coordinate-survives",
      unsettable_coords(["state.balance"], MUT), {})
# Parameters and environment quantities are settable by construction and are not
# even considered -- a parameter that happens to share a name with an immutable
# must not be disqualified by it.
check("parameter-sharing-a-name-is-not-disqualified",
      unsettable_coords(["FACTORY"], MUT), {})
check("env-coordinate-is-not-disqualified",
      unsettable_coords(["msg.sender"], MUT), {})
# A state variable the AST says nothing about stays in: silence is not evidence
# of immutability, and dropping on absence would narrow the generalisation for a
# reason that has nothing to do with the contract.
check("unknown-state-coordinate-stays",
      unsettable_coords(["state.mystery"], MUT), {})
check("empty-mutability-map-excludes-nothing",
      unsettable_coords(["state.FACTORY", "state.balance"], {}), {})

# The AST reader itself: a missing or unreadable file yields {}, which leaves
# every coordinate in place -- the pre-existing behaviour, stated rather than
# accidental.
check("missing-ast-yields-no-mutability", state_mutability("/no/such/ast"), {})
check("none-ast-yields-no-mutability", state_mutability(None), {})


if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print("  " + f)
    sys.exit(1)
print("solidity_path_generalise: all checks passed")
