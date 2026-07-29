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
                                      extraction_caveats)

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
check("struct-param-refused", "immutables" in ce, False)
check("symbolic-slot-refused", "state.PROXY_BYTECODE_HASH" in ce, False)
check("refusals-are-reported", sorted(refused),
      ["immutables", "state.PROXY_BYTECODE_HASH"])

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


if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print("  " + f)
    sys.exit(1)
print("solidity_path_generalise: all checks passed")
