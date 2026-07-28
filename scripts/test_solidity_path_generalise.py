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
                                      empty_coords, shrink_target, is_env)

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


if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print("  " + f)
    sys.exit(1)
print("solidity_path_generalise: all checks passed")
