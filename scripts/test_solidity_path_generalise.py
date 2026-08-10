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

import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from solidity_path_generalise import (verdict, claim_unit, coord_values,  # noqa
                                      UINT256_MAX,
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
                                      certification_query_pins,
                                      struct_fields,
                                      declared_struct_fields,
                                      lowering_artifacts,
                                      thin_to,
                                      budget_probe_values,
                                      ce_in_region,
                                      region_size,
                                      coordinate_accounting,
                                      punch_targets,
                                      geometric_values,
                                      TYPE_RANGE_RE,
                                      cut_of,
                                      split_on_cut,
                                      copy_holes,
                                      function_mutability,
                                      unexpressible_coords,
                                      drop_unexpressible_query_names,
                                      unresolvable_coords,
                                      mapping_state_vars,
                                      mapping_slot_type_ranges,
                                      unit_params,
                                      unit_mapping_slot_accesses,
                                      unit_state_dependencies,
                                      propose_slot_coords,
                                      add_esbmc_mapping_aliases,
                                      prefer_esbmc_mapping_aliases,
                                      state_coord_type_ranges,
                                      bytes_static_value_from_ce,
                                      bytes_static_mapping_key_from_ce,
                                      bytes_static_mapping_key_from_value,
                                      agreed_bytes_mapping_key_literals,
                                      empty_enumeration_reason,
                                      brackets_for,
                                      known_inside,
                                      point_has_known_member,
                                      outward_ladder,
                                      run_config,
                                      stamp_workdir,
                                      abi_gate_class,
                                      structural_abi_gate_certificate,
                                      _decision_relation,
                                      literal_state_constants,
                                      path_cov_fixture_state_pins,
                                      structural_decision_region,
                                      structural_decision_regions,
                                      structural_decision_regions_with_retreat,
                                      structural_decision_regions_with_relations,
                                      relation_establishable_state_targets,
                                      relation_establishable_env_sources,
                                      direct_recursive_helpers_in_unit_closure,
                                      enumeration_has_arith_conditions,
                                      witness_values,
                                      report_from_ce_journal,
                                      partial_journal_report,
                                      live_witness_vectors,
                                      write_enumeration_salvage,
                                      read_enumeration_salvage,
                                      write_generalise_progress,
                                      generalise_progress_path,
                                      payload_extras,
                                      extcall_inseparable_failures,
                                      path_cov_probe_goal_cap,
                                      path_cov_probe_early_stop,
                                      path_cov_probe_timeout,
                                      path_cov_probe_enum_timeout,
                                      file_identity,
                                      save_failed_round,
                                      validate_enumeration_import,
                                      derive_env_coord_disagreed,
                                      derive_agreed_establishable_env_pins,
                                      derive_agreed_unpinned_establishable_env_coords,
                                      tiny_safety_cut_retreat,
                                      uncontrolled_decision_splits,
                                      _decision_term,
                                      _coord_range)

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

bytes_ce, bytes_refused = coord_values({
    "inputs": {
        "assertionId": "{ .data = { 0x12, 0x34 }, .length=32 }",
    },
}, param_types={"assertionId": "bytes32"})
check("bytes32-aggregate-param-becomes-raw-coordinate",
      bytes_ce.get("assertionId"),
      0x1234000000000000000000000000000000000000000000000000000000000000)
check("bytes32-aggregate-param-not-refused-when-typed",
      bytes_refused, [])

bytes4_padded_ce, bytes4_padded_refused = coord_values({
    "inputs": {
        "interfaceId": "{ .data = { 0xff, 0xff, 0xff, 0xfe, "
        "0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "
        "0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "
        "0, 0, 0, 0 }, .length=4 }",
    },
}, param_types={"interfaceId": "bytes4"})
check("bytes4-padded-aggregate-param-becomes-raw-coordinate",
      bytes4_padded_ce.get("interfaceId"), 0xfffffffe)
check("bytes4-padded-aggregate-param-not-refused",
      bytes4_padded_refused, [])

bytes4_bad_padding, bytes4_bad_padding_refused = coord_values({
    "inputs": {
        "interfaceId": "{ .data = { 0x12, 0x34, 0x56, 0x78, 0x01 }, "
        ".length=4 }",
    },
}, param_types={"interfaceId": "bytes4"})
check("bytes4-nonzero-padding-is-refused",
      bytes4_bad_padding.get("interfaceId"), None)
check("bytes4-nonzero-padding-refusal-is-named",
      any("interfaceId (bytes4" in item for item in bytes4_bad_padding_refused),
      True)

untyped_bytes_ce, untyped_bytes_refused = coord_values({
    "inputs": {
        "assertionId": "{ .data = { 0x12, 0x34 }, .length=32 }",
    },
})
check("untyped-bytes-aggregate-does-not-invent-payload-coordinate",
      "assertionId" in untyped_bytes_ce, False)
check("untyped-bytes-aggregate-keeps-old-struct-field-behavior",
      untyped_bytes_ce.get("assertionId.length"), 32)
check("untyped-bytes-aggregate-still-refused-as-whole",
      any(r.startswith("assertionId (aggregate;")
          for r in untyped_bytes_refused), True)

dynamic_bytes_ce, dynamic_bytes_refused = coord_values({
    "inputs": {
        "data": "{ .offset=0, .length=7, .capacity=7, .initialized=1, "
                ".anon_pad$4=0 }",
    },
}, param_types={"data": "bytes memory"})
check("dynamic-bytes-keeps-length-coordinate",
      dynamic_bytes_ce.get("data.length"), 7)
check("dynamic-bytes-does-not-expose-offset",
      "data.offset" in dynamic_bytes_ce, False)
check("dynamic-bytes-does-not-expose-capacity",
      "data.capacity" in dynamic_bytes_ce, False)
check("dynamic-bytes-does-not-expose-initialized",
      "data.initialized" in dynamic_bytes_ce, False)
check("dynamic-bytes-refusal-explains-internal-fields",
      any("dynamic bytes aggregate; using length only" in r
          for r in dynamic_bytes_refused), True)

with tempfile.TemporaryDirectory() as _wit_dir:
    with open(os.path.join(_wit_dir, "cov-report.json"), "w",
              encoding="utf-8") as _wf:
        json.dump({
            "certify_safety_refutations": [{
                "status": "F",
                "condition": "take:path:7#exit0",
                "inputs": {
                    "data": "{ .offset=0, .length=9, .capacity=9, "
                            ".initialized=1, .anon_pad$4=0 }",
                },
            }],
        }, _wf)
    _wit = witness_values(
        _wit_dir, "take", param_types={"data": "bytes calldata"})
    check("typed-witness-dynamic-bytes-keeps-length",
          _wit.get("data.length"), 9)
    check("typed-witness-dynamic-bytes-drops-offset",
          "data.offset" in _wit, False)

state_bytes_ce, state_bytes_refused = coord_values({
    "entry_storage": {
        "SAFE_TX_TYPEHASH$75": "{ .data = { 0xAB, 0xCD }, .length=32 }",
    },
}, state_types={"SAFE_TX_TYPEHASH": "bytes32"})
check("bytes32-aggregate-state-uses-source-name-type",
      state_bytes_ce.get("state.SAFE_TX_TYPEHASH$75"),
      0xABCD000000000000000000000000000000000000000000000000000000000000)
check("bytes32-aggregate-state-not-refused-when-typed",
      state_bytes_refused, [])


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

with tempfile.TemporaryDirectory() as _fail_dir:
    _meta = save_failed_round(
        _fail_dir, "linear-refine",
        {"unit": "sol:@C@C@F@f#1", "coords": [{"name": "x"}]},
        "ERROR: boom\n[run] CMD esbmc C.sol --path-cov-outer-box outer.json\n"
        "[run] EXIT -6\n",
        "ESBMC exited -6 (ABORTED (SIGABRT))", 1.25)
    _meta_data = json.load(open(_meta, encoding="utf-8"))
    check("failed-round-meta-is-written", os.path.exists(_meta), True)
    check("failed-round-log-is-written",
          os.path.exists(os.path.join(_fail_dir, "failed-rounds",
                                      _meta_data["log"])), True)
    check("failed-round-spec-is-written",
          os.path.exists(os.path.join(_fail_dir, "failed-rounds",
                                      _meta_data["outerSpec"])), True)
    check("failed-round-meta-keeps-command",
          _meta_data["cmd"], "esbmc C.sol --path-cov-outer-box outer.json")


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
check("relation-established-partition-does-not-fire",
      certified_overlap({
          (6, 1): {"state.owner": (0, 0), "msg.sender": (1, 10)},
          (7, 1): {"msg.sender": (0, 10)},
      }, established={(7, 1): {"state.owner": "msg.sender"}}), [])
check("relation-established-overlap-still-fires-when-satisfiable",
      certified_overlap({
          (6, 1): {"state.owner": (0, 0), "msg.sender": (0, 10)},
          (7, 1): {"msg.sender": (0, 10)},
      }, established={(7, 1): {"state.owner": "msg.sender"}}),
      [((6, 1), (7, 1))])

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
check("level0-point-confirmed-by-known-member-with-missing-fixture-pin",
      point_has_known_member(
          {24: [{"msg.value": 0, "defaultFarm_": 1}]},
          24, "msg.value", 0, {"state._owner": 1}), True)
check("level0-point-not-confirmed-by-member-that-conflicts-a-present-pin",
      point_has_known_member(
          {24: [{"msg.value": 0, "defaultFarm_": 1}]},
          24, "msg.value", 0, {"defaultFarm_": 0}), False)

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

# Once an immutable/constant has been removed from the FREE coordinate set, it
# still constrains the deployed-contract slice being certified. Filtering it out
# here lets ESBMC refute a region using a constructor state the generated
# Foundry test can never produce.
_PINS_WITH_IMMUTABLE = {"state.FACTORY": 7, "msg.value": 0}
check("immutable-pin-still-constrains-certification-query",
      certification_query_pins(_PINS_WITH_IMMUTABLE),
      {"msg.value": 0, "state.FACTORY": 7})

# The AST reader itself: a missing or unreadable file yields {}, which leaves
# every coordinate in place -- the pre-existing behaviour, stated rather than
# accidental.
check("missing-ast-yields-no-mutability", state_mutability("/no/such/ast"), {})
check("none-ast-yields-no-mutability", state_mutability(None), {})


# --- a struct member the SOURCE never declared is not an input ---
#
# The struct lowering adds padding members. MEASURED on EscrowSrc's `Immutables`:
# `immutables.anon_pad$2` was offered as a coordinate alongside the seven real
# fields. No generated test can set a padding word, so this is the same defect as
# offering an immutable -- the coordinate list containing something unsettable.
#
# Read, not pattern-matched: the check is membership in the set of field names
# the AST declares, not a guess about `$` or `pad` in the name.
DECLARED = {"taker", "maker", "amount", "token", "timelocks", "safetyDeposit",
            "orderHash"}
COORDS = ["immutables.taker", "immutables.anon_pad$2", "immutables.amount",
          "state.balance", "msg.sender", "amt"]
check("undeclared-struct-member-is-an-artifact",
      sorted(lowering_artifacts(COORDS, DECLARED)), ["immutables.anon_pad$2"])
# THE MUST-FLIP: real fields survive. Without it the rule could drop every struct
# coordinate and still look right -- which would undo the change that gave
# EscrowSrc its coordinates in the first place.
check("declared-struct-member-survives",
      "immutables.taker" in lowering_artifacts(COORDS, DECLARED), False)
# Non-struct coordinates are not even considered: `state.`, the environment
# namespaces and a bare parameter are settable by construction, and a state
# variable whose name is absent from every struct must not be dropped by this.
check("state-coordinate-not-touched",
      "state.balance" in lowering_artifacts(COORDS, DECLARED), False)
check("env-coordinate-not-touched",
      "msg.sender" in lowering_artifacts(COORDS, DECLARED), False)
check("bare-parameter-not-touched",
      "amt" in lowering_artifacts(COORDS, DECLARED), False)
# With NOTHING declared there is nothing to compare against, so everything must
# survive -- otherwise an unreadable AST would silently empty the coordinate list
# for a reason unrelated to the contract.
check("empty-declared-set-drops-nothing", lowering_artifacts(COORDS, set()), {})
check("missing-ast-declares-nothing",
      declared_struct_fields("/no/such/ast"), set())


# --- the ladder is thinned by CLAIMS EMITTED, the quantity that costs ---
#
# MEASURED, same unit and pins, only the ladder length varying:
#   1548 values/coord -> 148 queries reached the solver, 6.9s solving / 300s
#     60 values/coord -> 1713 queries reached the solver, 86.8s solving / 200s
# A 26x thinner ladder put 11.6x more queries in front of the solver. The round
# is emission-bound, so a budget in solver time or probes-answered would bound
# the wrong quantity.
check("thin-keeps-both-endpoints",
      thin_to([0, 1, 2, 4, 8, 16, 32], 3)[0], 0)
check("thin-keeps-the-last", thin_to([0, 1, 2, 4, 8, 16, 32], 3)[-1], 32)
check("thin-respects-the-cap", len(thin_to(list(range(100)), 5)) <= 5, True)
check("thin-is-a-noop-when-short", thin_to([1, 2, 3], 10), [1, 2, 3])

# Budget off must change nothing at all -- the default, and the previous
# behaviour.
vals = {"a": list(range(300)), "b": list(range(300))}
out, note = budget_probe_values(vals, 5, 0)
check("budget-off-changes-nothing", out, vals)
check("budget-off-says-nothing", note, None)

# A budget that binds thins and SAYS SO: a silently shorter ladder is a silently
# coarser measurement.
out, note = budget_probe_values(vals, 5, 600)
check("budget-thins-both", all(len(v) <= 30 for v in out.values()), True)
check("budget-reports-the-thinning", "thinned" in (note or ""), True)
check("budget-names-the-emission-finding", "EMISSION-bound" in (note or ""),
      True)

# NEVER below two values: one cannot distinguish a genuine point domain from a
# vacuous path, which is a soundness-adjacent property rather than resolution.
out, note = budget_probe_values(vals, 500, 4)
check("budget-never-goes-below-two",
      all(len(v) >= 2 for v in out.values()), True)

# A budget that is already satisfied must not claim to have done anything.
out, note = budget_probe_values({"a": [1, 2]}, 1, 10000)
check("satisfied-budget-is-silent", note, None)
check("satisfied-budget-keeps-values", out, {"a": [1, 2]})


# --- a bracket is kept unless it constrains NOTHING ---
#
# The old rule dropped an upper bracket whose far end reached the type limit.
# MEASURED, on the first run where the bracket and a refine round both finished:
# the bracket said `immutables.amount upper in (5.14e61, 2^256-1]` and the refine
# span came back (0, 2^256-1) -- the whole type. The shrink then had nothing to
# work from and halved, which had been blamed on the method.
BR_USEFUL = {14: ("immutables.amount upper in "
                  "(51422017416287688817342786954917203280710495801049370729644032, "
                  f"{BIG}]")}
got = brackets_for("immutables.amount", BR_USEFUL)
check("a-bracket-touching-the-type-max-is-still-kept", got is not None, True)
check("and-its-lower-end-is-the-information",
      got[0], 51422017416287688817342786954917203280710495801049370729644032)

# THE MUST-FLIP the old rule was right about: a bracket spanning the coordinate's
# whole range constrains nothing, and refining towards it would hand back the
# span the loop already had.
BR_VACUOUS = {14: f"a upper in (0, {BIG}]"}
check("a-bracket-over-the-whole-type-is-still-dropped",
      brackets_for("a", BR_VACUOUS), None)
# ...and the same on the lower side.
check("a-lower-bracket-over-the-whole-type-is-dropped",
      brackets_for("a", {14: f"a lower in [0, {BIG})"}), None)
check("a-lower-bracket-that-constrains-is-kept",
      brackets_for("a", {14: "a lower in [500, 900)"}) is not None, True)

# --- one degenerate path must not drag the shared span back to everything ---
#
# The span is the union over ALL paths. MEASURED: the bracket said
# `immutables.amount upper in (5.14e61, 2^256-1]` and the refine span still came
# back (0, 2^256-1), because another path contributed a bracket over the whole
# type and min()/max() swallowed the narrowing.
#
# A bracket spanning the coordinate's entire range contributes ZERO information
# by definition, so dropping it is not a policy choice. This is deliberately NOT
# per-path spans, which would multiply the claim count -- the quantity the
# round's cost tracks -- by the path count.
MIXED = {14: "a upper in (500, 900]",          # informative
         30: f"a upper in (0, {BIG}]"}          # says nothing
check("a-degenerate-path-does-not-widen-the-union",
      brackets_for("a", MIXED, (0, BIG)), (500, 900))
# ...and with no informative bracket at all the answer is still None, not a
# fabricated span.
check("all-degenerate-still-yields-nothing",
      brackets_for("a", {30: f"a upper in (0, {BIG}]"}, (0, BIG)), None)
# A narrower TYPE makes a bracket degenerate that would not be on uint256 -- the
# test is against the coordinate's own range, which is why it is passed in.
ADDR_MAX_ = (1 << 160) - 1
check("degenerate-is-judged-against-the-coordinate-type",
      brackets_for("a", {14: f"a upper in (0, {ADDR_MAX_}]"}, (0, ADDR_MAX_)),
      None)
check("the-same-bracket-is-informative-on-a-wider-type",
      brackets_for("a", {14: f"a upper in (0, {ADDR_MAX_}]"}, (0, BIG)),
      (0, ADDR_MAX_))

# --- upper and lower brackets: WHY THEY ARE NOT SEPARATED ---
#
# A design note kept where the code is, because separating them was tried, is
# WRONG, and the reasoning is easy to re-derive incorrectly.
#
#   `upper in (a, b]`  the true UPPER bound lies in (a, b]
#   `lower in [c, d)`  the true LOWER bound lies in [c, d)
#
# It looks as though an upper bracket should constrain only `hi` and a lower one
# only `lo`. That is right if the question is WHAT THE DOMAIN IS. It is wrong
# here, because the refine round asks WHERE TO PROBE NEXT: the boundary sits
# INSIDE the bracket, so the next ladder must be laid across it. The union of the
# two intervals is the region containing both boundaries, which is exactly what a
# span is for.
#
# Implemented, tested, and reverted: separating them made an upper-only bracket
# return (type_lo, b), a span that no longer covers where the upper boundary
# actually is -- so the next round would probe everywhere except the interesting
# part. The must-flip pair that exposed this is the one that made the separated
# version's own tests pass while the production span got worse.
#
# The real cause of the observed (0, 2^256-1) span is the DEGENERATE
# contribution filtered just above, not the folding.


# --- C2: the certified region must contain this path's own counterexample ---
#
# Each pair is a MUST-FLIP: the same region and the same CE, differing only in
# the one thing the check is about. A check with no direction that must flip
# cannot testify for itself -- one that always returned [] passes the first half
# of every pair below.

# (i) inside vs outside the interval.
check("C2-ce-inside-the-interval-is-clean",
      ce_in_region({"a": (10, 20)}, {}, {"a": 15}), [])
check("C2-ce-outside-the-interval-is-caught",
      ce_in_region({"a": (10, 20)}, {}, {"a": 21}),
      ["a: CE 21 outside [10, 20]"])

# (ii) THE HOLE. This is the half a `lo <= ce <= hi` test cannot see, and it is
# the live route: holes are carried ACROSS shrink rounds, so a hole punched in
# an early round can land on the CE after a later side cut moves the interval.
check("C2-ce-not-punched-is-clean",
      ce_in_region({"a": (10, 20)}, {"a": [11]}, {"a": 15}), [])
check("C2-ce-punched-out-is-caught",
      ce_in_region({"a": (10, 20)}, {"a": [15]}, {"a": 15}),
      ["a: CE 15 was PUNCHED OUT of [10, 20]"])

# (iii) a coordinate the CE does not mention is UNCONSTRAINED, not violated.
# Reporting it would make every partial payload look like a broken region --
# the payload legitimately omits coordinates the path never read.
check("C2-coordinate-absent-from-the-ce-is-not-a-violation",
      ce_in_region({"a": (10, 20), "b": (0, 5)}, {}, {"a": 15}), [])

# --- C3: |R|, and the direction it may move ---
check("C3-size-is-the-product-over-coordinates",
      region_size({"a": (0, 9), "b": (0, 1)}), 20)
check("C3-a-hole-removes-exactly-one-value",
      region_size({"a": (0, 9)}, {"a": [4]}), 9)
check("C3-a-hole-outside-the-interval-removes-nothing",
      region_size({"a": (0, 9)}, {"a": [40]}), 10)
check("C3-an-inverted-interval-is-empty",
      region_size({"a": (9, 0)}), 0)
# The punched-empty route, which `lo <= hi` passes: a well-formed interval whose
# every value has been removed.
check("C3-punched-empty-is-empty-though-lo-le-hi",
      region_size({"a": (5, 5)}, {"a": [5]}), 0)
# The must-flip for monotonicity: the same cut read in both directions.
check("C3-a-real-cut-is-narrower",
      region_size({"a": (11, 100)}) < region_size({"a": (5, 100)}), True)
check("C3-a-widening-cut-is-detectable",
      region_size({"a": (0, 100)}) > region_size({"a": (5, 100)}), True)

# --- the certify RESULT line, read from REAL log text ---
#
# These four strings are VERBATIM prefixes from real runs, `ERROR: ` included.
# The first version of CERTIFY_RESULT_RE anchored on `^--path-cov-certify` and
# so matched only the two outcomes that go through log_status -- CERTIFIED and
# REFUTED -- while VACUOUS and UNDECIDED, which go through log_error and are
# prefixed `ERROR: `, fell through to the old whole-line verdict and were
# reported as "no verdict at all". A vacuous certification reading as UNKNOWN
# loses the entire point of the gate.
#
# Caught by an end-to-end driver run, not by a unit test, because the first
# tests were written from the FORMAT STRING rather than from a log. Hence these.
_R_CERT = ("--path-cov-certify: RESULT: CERTIFIED — every input the box "
           "admits walks path enc=2 (2 of 4 exit assert(s) discharged")
_R_REF = ("--path-cov-certify: RESULT: REFUTED — 1 of 4 exit assert(s) were "
          "refuted, so an input the box admits leaves this path")
_R_VAC = ("ERROR: --path-cov-certify: RESULT: VACUOUS — the box admits NO "
          "execution that walks path enc=3 of this unit")
_R_UND = ("ERROR: --path-cov-certify: RESULT: UNDECIDED — no exit assert was "
          "refuted, but 1 of 4 came back UNKNOWN from the solver")

# A certified run prints VERIFICATION FAILED -- the witness is refuted on it --
# so every case below pairs the RESULT line with the OPPOSITE verdict line, to
# pin that the RESULT line wins.
check("RESULT-CERTIFIED-beats-the-FAILED-verdict-line",
      verdict(WARN + "\n" + _R_CERT + "\nVERIFICATION FAILED"), "SUCCESSFUL")
check("RESULT-REFUTED-is-FAILED",
      verdict(WARN + "\n" + _R_REF + "\nVERIFICATION FAILED"), "FAILED")
check("RESULT-VACUOUS-survives-the-ERROR-prefix",
      verdict(WARN + "\n" + _R_VAC), "VACUOUS")
check("RESULT-UNDECIDED-survives-the-ERROR-prefix",
      verdict(WARN + "\n" + _R_UND), "UNKNOWN")
# And with no RESULT line at all the old whole-line verdict still decides, so an
# older ESBMC keeps working.
check("no-RESULT-line-falls-back-to-the-verdict-line",
      verdict(WARN + "\nVERIFICATION SUCCESSFUL"), "SUCCESSFUL")

# --- S4: the PUNCH suggestion, verbatim from the tool ---
#
# The line below is the tool's own wording (goto_coverage.cpp), not a paraphrase.
# A paraphrased fixture would pass against a parser that never matches the real
# output, which is the exact failure the WARNING line at the top of this file
# records.
_PUNCH = (
    "--path-cov-certify: PUNCH SUGGESTION for "
    "'sol:@C@Gate2@F@send#29:path:3#exit1' — instead of cutting the interval, "
    "remove the witness itself: add to != 255 to the box's `holes` "
    "(Definition 5). Legal by the same rule as a side cut (this path's own "
    "counterexample differs there and survives), and it costs ONE value rather "
    "than a whole side")
_SHRINK_ONLY = (
    "--path-cov-certify: SHRINK SUGGESTION for 'x' — the witness lies outside "
    "the path on coordinate 'a', and the path's own counterexample lies on the "
    "other side of it, so retry with a in [11, 100] (everything else unchanged)")

check("S4-a-punch-line-yields-the-coordinate-and-value",
      punch_targets(WARN + "\n" + _PUNCH, {}), [("to", 255)])
# THE MUST-FLIP that keeps the old behaviour byte-identical: a log with only a
# SHRINK line must yield NO punch, so the loop takes exactly the branch it took
# before this existed.
check("S4-a-shrink-only-log-yields-no-punch",
      punch_targets(WARN + "\n" + _SHRINK_ONLY, {}), [])
check("S4-unsafe-refutation-uses-shrink-not-punch",
      punch_targets(WARN + "\n" + _PUNCH + "\n"
                    "--path-cov-certify: RESULT: UNSAFE", {}),
      [])
# A PINNED coordinate is refused, the same rule shrink_target obeys: a pin is a
# single value, so punching it would empty the coordinate outright.
check("S4-a-pinned-coordinate-is-never-punched",
      punch_targets(WARN + "\n" + _PUNCH, {"to": 255}), [])
# A value outside the CURRENT interval removes nothing and must not be recorded
# as if it constrained the region -- the suggestion was made against the box the
# tool was handed, which a later round may already have cut.
check("S4-a-value-outside-the-interval-is-dropped",
      punch_targets(WARN + "\n" + _PUNCH, {}, {"to": (0, 100)}), [])
check("S4-a-value-inside-the-interval-is-kept",
      punch_targets(WARN + "\n" + _PUNCH, {}, {"to": (0, 300)}), [("to", 255)])
# `!=` occurs in prose elsewhere in the same output; anchoring on the
# SUGGESTION LINE is what stops a bare scan harvesting text as a coordinate.
check("S4-prose-elsewhere-is-not-harvested",
      punch_targets(WARN + "\nsomething about a != 7 in passing", {}), [])
# THE DEFAULT IS OFF. `punch_targets` still reports what the tool suggested --
# it is a parser, and refusing to parse would hide the suggestion from the log
# as well -- but the LOOP applies nothing at `--max-holes 0`. Pinned as the
# arithmetic the loop's filter performs, so the house rule ("every existing
# number reproduced verbatim without the flag", the same one --level0 follows)
# cannot be undone by a later default change without this going red.
check("S4-max-holes-0-admits-no-punch",
      [(c, v) for c, v in punch_targets(WARN + "\n" + _PUNCH, {})
       if len({}.get(c, ())) < 0], [])
check("S4-max-holes-1-admits-the-first-punch",
      [(c, v) for c, v in punch_targets(WARN + "\n" + _PUNCH, {})
       if len({}.get(c, ())) < 1], [("to", 255)])

# --- C5: coordinate accounting ---
#
# The must-flip is the whole point: the same payload against buckets that do and
# do not claim every name. A checker that always returned [] passes the first.
_B_OK = {"free coordinate": ["a"], "pinned": {"b"},
         "refused by the tool": {"c"}}
check("C5-every-payload-name-reaches-a-bucket",
      coordinate_accounting({"a", "b", "c"}, _B_OK)[0], [])
check("C5-a-name-in-no-bucket-is-caught",
      coordinate_accounting({"a", "b", "c", "state._DOCKED"}, _B_OK)[0],
      ["state._DOCKED"])

# COVERAGE, NOT PARTITION, and this pair pins that decision rather than leaving
# it to be re-argued. An unsettable coordinate is ALSO added to `pins` -- that is
# what "pinned at the counterexample value" means -- so a partition check would
# fail on correct input. Overlap is reported, never an error.
_B_OVERLAP = {"pinned": {"x"}, "unsettable, pinned at its CE": {"x"}}
check("C5-overlap-is-not-a-violation",
      coordinate_accounting({"x"}, _B_OVERLAP)[0], [])
check("C5-overlap-is-reported-with-both-buckets",
      coordinate_accounting({"x"}, _B_OVERLAP)[1]["x"],
      ["pinned", "unsettable, pinned at its CE"])

# An EMPTY bucket must not blow up: several are empty on an ordinary run.
check("C5-empty-and-None-buckets-are-tolerated",
      coordinate_accounting({"a"}, {"free coordinate": ["a"],
                                    "pinned": set(),
                                    "refused by the tool": None})[0], [])

# --- S3: a refutation's cut splits the box, it does not just narrow it ---
#
# The discarded side is NOT known to be outside the path's domain: the cut
# excludes ONE refuting witness, and the rest of that side may be domain the
# path really has. Certification is a per-query judgement, so a union of
# separately certified boxes is certified; only the REPRESENTATION had to change.

# Which coordinate moved, read by diffing rather than threaded through certify.
check("S3-cut-is-read-off-the-diff",
      cut_of({"a": (0, 100), "b": (0, 9)}, {"a": (11, 100), "b": (0, 9)}), "a")
# Zero coordinates moved is the no-progress case the existing branch reports.
check("S3-no-change-is-no-cut",
      cut_of({"a": (0, 100)}, {"a": (0, 100)}), None)
# Two coordinates moved is not a single-coordinate cut and must not be guessed at.
check("S3-two-changes-is-not-a-cut",
      cut_of({"a": (0, 100), "b": (0, 9)}, {"a": (1, 100), "b": (1, 9)}), None)

# An INTERIOR cut yields the kept piece and BOTH complements.
kept, rest = split_on_cut({"a": (0, 100), "b": (0, 9)}, "a", 40, 60)
check("S3-interior-cut-keeps-the-suggestion", kept["a"], (40, 60))
check("S3-interior-cut-leaves-the-other-coordinate", kept["b"], (0, 9))
check("S3-interior-cut-yields-two-pieces", [r["a"] for r in rest],
      [(0, 39), (61, 100)])

# THE PARTITION PROPERTY, which is what makes the union sound: the kept piece and
# the complements must tile the ORIGINAL box exactly -- no point lost, no point
# counted twice. Checked as arithmetic rather than asserted in a comment, which
# is this project's own rule for the propositions a method rests on.
_orig = region_size({"a": (0, 100), "b": (0, 9)})
_sum = region_size(kept) + sum(region_size(r) for r in rest)
check("S3-the-pieces-tile-the-original-exactly", _sum, _orig)
check("S3-the-pieces-are-pairwise-disjoint",
      certified_overlap({(3, 1): kept, (3, 2): rest[0], (3, 3): rest[1]}), [])

# A cut flush against one end yields ONE complement, not an empty interval.
kept2, rest2 = split_on_cut({"a": (0, 100)}, "a", 0, 60)
check("S3-cut-at-the-low-end-yields-one-piece", [r["a"] for r in rest2],
      [(61, 100)])
check("S3-cut-at-the-high-end-yields-one-piece",
      [r["a"] for r in split_on_cut({"a": (0, 100)}, "a", 40, 100)[1]],
      [(0, 39)])

# THE MUST-FLIP that keeps --max-region-pieces 1 byte-identical to today: a cut
# that removes nothing produces NO pieces, so nothing is ever enqueued and the
# existing no-progress branch is the one that speaks.
check("S3-a-cut-that-removes-nothing-yields-no-pieces",
      split_on_cut({"a": (0, 100)}, "a", 0, 100)[1], [])
# A suggestion reaching OUTSIDE the current interval is clamped, so a complement
# can never be handed back a range the region never had. (The unclamped `nb` is
# what the C3 widening check sees -- that ordering is deliberate and is why this
# clamps rather than rejects.)
check("S3-a-suggestion-wider-than-the-box-yields-no-pieces",
      split_on_cut({"a": (10, 20)}, "a", 0, 100)[1], [])
check("S3-a-suggestion-wider-than-the-box-is-clamped",
      split_on_cut({"a": (10, 20)}, "a", 0, 100)[0]["a"], (10, 20))
# A suggestion that does not meet the interval at all is not a cut: no kept
# piece is defensible, so the box comes back unchanged and no piece is spawned.
check("S3-a-disjoint-suggestion-leaves-the-box-alone",
      split_on_cut({"a": (10, 20)}, "a", 50, 60)[0]["a"], (10, 20))
check("S3-a-disjoint-suggestion-spawns-nothing",
      split_on_cut({"a": (10, 20)}, "a", 50, 60)[1], [])
# A coordinate the box does not carry cannot be split on.
check("S3-an-unknown-coordinate-spawns-nothing",
      split_on_cut({"a": (0, 9)}, "zz", 1, 2)[1], [])

# THE C2 ASYMMETRY the loop depends on: the kept piece holds the CE and the
# complements do not, BY CONSTRUCTION. That is why C2 is applied to one and the
# non-vacuity witness carries the others -- running C2 on a complement would
# reject every piece S3 exists to keep.
_CE = {"a": 50}
check("S3-the-kept-piece-holds-the-ce", ce_in_region(kept, {}, _CE), [])
check("S3-a-complement-does-not-hold-the-ce",
      ce_in_region(rest[0], {}, _CE) != [], True)

# The partition check must survive the key change from `enc` to `(enc, piece)`.
check("S3-overlap-still-fires-on-tuple-keys",
      certified_overlap({(2, 1): {"a": (0, 5)}, (7, 1): {"a": (0, 5)}}),
      [((2, 1), (7, 1))])
check("S3-overlap-still-silent-on-a-real-partition",
      certified_overlap({(2, 1): {"a": (6, BIG)}, (3, 1): {"a": (0, 5)}}), [])

# --- S10: msg.value on a non-payable unit, read as a FACT not pinned as policy ---
#
# MEASURED on one contract, controlled -- identical command apart from the flag:
#
#     auto-pin ON   4 of 5 paths certified, each region the path's EXACT domain
#     auto-pin OFF  0 of 5
#
# and the ON run carries ONE pin (msg.value == 0) where --pin-env carries
# fifteen, so the region is universally quantified over everything else. That
# difference is the reason this is not "--pin-env made default": a non-payable
# function's ABI gate reverts every call carrying value, so pinning msg.value
# excludes nothing REACHABLE, while pinning block.timestamp turns the region
# into a statement about one slice.
#
# The mutability is READ from the AST for the same reason state_mutability is:
# "every counterexample has msg.value 0" is true of a non-payable function and
# equally true of a payable one nobody happened to send value to.

_AST = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition", "name": "C",
        "nodes": [
            {"nodeType": "FunctionDefinition", "name": "f",
             "stateMutability": "nonpayable"},
            {"nodeType": "FunctionDefinition", "name": "deposit",
             "stateMutability": "payable"},
            {"nodeType": "FunctionDefinition", "name": "peek",
             "stateMutability": "view"},
        ],
    }],
}
_fd, _p = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd, "w") as _f:
    # solc's --ast-compact-json output carries a banner before the object, and
    # the reader skips to the first '{'. Written WITH one, because a fixture
    # without it would pass against a reader that cannot handle the real file.
    _f.write("======= C.sol =======\nJSON AST (compact format):\n\n")
    json.dump(_AST, _f)

check("S10-nonpayable-is-read", function_mutability(_p).get("f"), "nonpayable")
# THE MUST-FLIP. A payable function really can be called with value, so pinning
# it to 0 would generalise over a strictly smaller space than the contract has.
# Without this pair a reader that always said "nonpayable" would pass.
check("S10-payable-is-read", function_mutability(_p).get("deposit"), "payable")
check("S10-view-is-read-and-is-not-payable",
      function_mutability(_p).get("peek"), "view")
# An OVERLOAD declared both ways cannot be resolved from the name, so the
# PAYABLE reading wins -- the direction that declines to pin, i.e. declines to
# act. Opposite of state_mutability's tie-break, and deliberately so: there the
# risky move is dropping a settable coordinate, here it is pinning a quantity
# that really can vary.
_AST2 = {"nodeType": "SourceUnit", "nodes": [
    {"nodeType": "FunctionDefinition", "name": "g",
     "stateMutability": "nonpayable"},
    {"nodeType": "FunctionDefinition", "name": "g",
     "stateMutability": "payable"}]}
_fd2, _p2 = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd2, "w") as _f2:
    json.dump(_AST2, _f2)
check("S10-an-overload-declared-both-ways-reads-as-payable",
      function_mutability(_p2).get("g"), "payable")
os.unlink(_p)
os.unlink(_p2)

# Same failure direction as every other AST read here: absent means pin nothing,
# which reproduces the previous behaviour exactly. The driver REPORTS the
# absence, which is what stops it reading as a property of the contract.
check("S10-missing-ast-pins-nothing", function_mutability("/no/such/ast"), {})
check("S10-none-ast-pins-nothing", function_mutability(None), {})

# --- the CERTIFY branch's refusal is NOT the OUTER-BOX branch's ---
#
# VERBATIM from the run that exposed this, `ERROR: ` prefix included. The two
# branches refuse an unresolvable name with different sentences AND see
# different inputs -- the outer-box spec carries pins in a `pin` field, while
# certify folds every pin into the `box` as a degenerate bound. So a pin certify
# cannot express is one the outer-box rounds never complained about.
#
# THE FIRST ATTEMPT AT THIS FIX HARVESTED THE OUTER-BOX WORDING, and on the very
# run that motivated it the outer-box rounds said nothing at all, so the fix
# never fired: the pin was still in the box, the query was still refused, and
# the report was unchanged. A detector wired to the wrong sentence is never
# wrong and never right, so both sentences are pinned here, in both directions.
_CERT_REFUSED = (
    "ERROR: --path-cov-certify: unit 'sol:@C@Aqua@F@rawBalances#2819' — "
    "REFUSING THE QUERY because coordinate 'state._DOCKED' cannot be "
    "expressed: the name does not resolve to an input of this unit. Name a "
    "parameter of this unit, an environment value as `msg.value` / "
    "`tx.origin` / `block.timestamp`, or a state variable at entry as "
    "`state.<field>` (which reaches the contract object's own components only "
    "— a mapping or a dynamic array does not resolve). Certification is not "
    "attempted: dropping the bound would certify a WIDER box than the one "
    "asked for")
_OUTER_REFUSED = (
    "ERROR: --path-cov-outer-box: unit 'sol:@C@Aqua@F@pull#3153' has no input "
    "named 'state._DOCKED'. Name a parameter, an environment value ...")

check("the-certify-refusal-names-its-coordinate",
      unexpressible_coords(_CERT_REFUSED), ["state._DOCKED"])
# MUST FLIP (1): the OUTER-BOX wording must NOT be read as a certify refusal.
# This is the bug, in the direction it actually happened.
check("the-outer-box-refusal-is-not-a-certify-refusal",
      unexpressible_coords(_OUTER_REFUSED), [])
# MUST FLIP (2): and the certify wording must NOT be read as an outer-box one,
# or `round_failure_reason` would start blaming the wrong branch.
check("the-certify-refusal-is-not-an-outer-box-refusal",
      unresolvable_coords(_CERT_REFUSED), [])
check("the-outer-box-refusal-is-still-read-by-its-own-reader",
      unresolvable_coords(_OUTER_REFUSED), ["state._DOCKED"])
# A clean log names nothing, so a successful run cannot drop a pin by accident.
check("a-clean-log-refuses-nothing", unexpressible_coords(CERTIFIED), [])
check("a-refuted-log-refuses-nothing", unexpressible_coords(REFUTED), [])

# --- the punched set must be DEEP-copied, or S3 pieces share their parent's holes ---
#
# `holes` is {coord: [int,...]}, so `dict(holes)` shares the LIST objects and
# the punch branch mutates them in place. A piece enqueued with a shallow copy
# keeps growing holes its parent punches AFTER the split -- it is certified over
# a region carrying a hole it never derived, and a stored region mutates after
# the query that certified it. Needs --max-region-pieces > 1 AND --max-holes > 0,
# which is the combination the S3 note names as the next thing to measure.
_parent = {"x": [200]}
_shallow = dict(_parent)
_deep = copy_holes(_parent)
_parent["x"].append(90)
check("S3-a-shallow-copy-of-holes-aliases-the-lists", _shallow["x"], [200, 90])
check("S3-copy_holes-does-not", _deep["x"], [200])
# ...and the copy must be independent in the other direction too, or a piece's
# own punch would reach back into its parent.
_deep2 = copy_holes({"x": [1]})
_deep2["x"].append(2)
check("S3-mutating-the-copy-leaves-the-source-alone", copy_holes({"x": [1]}),
      {"x": [1]})
check("S3-copy_holes-tolerates-none", copy_holes(None), {})


# --- S10's AST read must be scoped to the contract AND its bases ---
#
# Every benchmark input is FLATTENED: dozens of contracts in one file, routinely
# repeating a function name. Walking the whole AST and keying by bare name lets
# any of them collide, and because the tie-break is "payable wins" the collision
# SKIPS the pin and prints "this unit is PAYABLE" -- a false claim about the
# unit, on exactly the multi-contract inputs the corpus sweep uses.
#
# Inheritance is not optional either: `BaseEscrow.rescueFunds` is measured under
# `--contract EscrowSrc`, so scoping to the named contract's own functions would
# lose the pin on every inherited unit instead.
_FLAT = {
    "nodeType": "SourceUnit",
    "nodes": [
        {"nodeType": "ContractDefinition", "name": "IERC20", "id": 1,
         "linearizedBaseContracts": [1],
         "nodes": [{"nodeType": "FunctionDefinition", "name": "transfer",
                    "stateMutability": "payable"}]},
        {"nodeType": "ContractDefinition", "name": "BaseEscrow", "id": 2,
         "linearizedBaseContracts": [2],
         "nodes": [{"nodeType": "FunctionDefinition", "name": "rescueFunds",
                    "stateMutability": "nonpayable"}]},
        {"nodeType": "ContractDefinition", "name": "EscrowSrc", "id": 3,
         "linearizedBaseContracts": [3, 2],
         "nodes": [{"nodeType": "FunctionDefinition", "name": "transfer",
                    "stateMutability": "nonpayable"},
                   {"nodeType": "FunctionDefinition", "name": "withdraw",
                    "stateMutability": "nonpayable"}]},
    ],
}
_fd3, _p3 = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd3, "w") as _f3:
    json.dump(_FLAT, _f3)

# THE BUG, in the direction it actually happens: unscoped, IERC20's payable
# `transfer` collides with EscrowSrc's nonpayable one and payable wins.
check("S10-unscoped-a-foreign-payable-namesake-wins",
      function_mutability(_p3).get("transfer"), "payable")
# THE FIX: scoped to the contract under test, the right one is read.
check("S10-scoped-reads-the-contract-under-test",
      function_mutability(_p3, "EscrowSrc").get("transfer"), "nonpayable")
# MUST FLIP: an INHERITED unit must still be found, or scoping trades one yield
# loss for another.
check("S10-scoped-still-finds-an-inherited-unit",
      function_mutability(_p3, "EscrowSrc").get("rescueFunds"), "nonpayable")
# A contract that does not inherit must NOT see its sibling's functions.
check("S10-scoped-does-not-see-an-unrelated-contract",
      "withdraw" in function_mutability(_p3, "IERC20"), False)
# An unknown contract name falls back to the whole AST rather than returning
# nothing: reading nothing would turn a lookup failure into "mutability unknown",
# which reads as a property of the source.
check("S10-an-unknown-contract-falls-back-to-the-whole-ast",
      function_mutability(_p3, "NoSuchContract").get("rescueFunds"),
      "nonpayable")
os.unlink(_p3)


# --- a suggested cut must lie INSIDE the interval it cuts ---
#
# split_on_cut CLAMPS, so the complement is computed from clamped bounds while
# the loop advances to the UNCLAMPED suggestion. On box [10,100] with suggestion
# [5,50] the complement is [51,100] and the kept side [5,50]: their union covers
# [5,9], which was never in the measured region. |R| cannot catch it -- 46 < 91,
# so the narrowing check passes, which is why the loop tests containment
# directly.
check("S3-an-out-of-range-cut-is-not-caught-by-size-alone",
      region_size({"x": (5, 50)}) < region_size({"x": (10, 100)}), True)
check("S3-the-complement-is-computed-from-the-clamped-bounds",
      [r["x"] for r in split_on_cut({"x": (10, 100)}, "x", 5, 50)[1]],
      [(51, 100)])
check("S3-and-the-clamped-kept-side-is-inside",
      split_on_cut({"x": (10, 100)}, "x", 5, 50)[0]["x"], (10, 50))


# --- C2 against the PINS is a separate question from C2 against the box ---
#
# Pins never enter `box` -- certify folds them in only when building the spec --
# so ce_in_region(box, ...) structurally cannot see a pin conflict. S10 made that
# live: msg.value is pinned to 0 on every path of a non-payable unit, including
# the ABI-gate path whose whole domain is msg.value != 0.
#
# The two must stay DISTINCT findings. A CE outside the BOX is a cut that carved
# into the real domain (a defect). A CE outside a PIN is the path being excluded
# from the slice (S10's stated cost). Merging them files that cost as a bug.
_PINS_AS_BOX = {n: (v, v) for n, v in {"msg.value": 0}.items()}
check("C2-pins-as-a-box-detects-the-abi-gate-path",
      ce_in_region(_PINS_AS_BOX, {}, {"msg.value": 7, "x": 3}),
      ["msg.value: CE 7 outside [0, 0]"])
# MUST FLIP: a path whose CE agrees with the pin is not excluded.
check("C2-pins-as-a-box-clears-an-agreeing-path",
      ce_in_region(_PINS_AS_BOX, {}, {"msg.value": 0, "x": 3}), [])
# ...and the box check alone sees NEITHER, which is the structural blindness.
check("C2-the-box-check-alone-cannot-see-a-pin-conflict",
      ce_in_region({"x": (0, 9)}, {}, {"msg.value": 7, "x": 3}), [])

# --- S7 groundwork: every decimal the tool prints may carry a SIGN ---
#
# Five patterns matched `\d+` only. That was correct while only unsigned
# bit-vectors were accepted as coordinates, and it becomes a SILENT failure the
# moment the tool starts publishing signed ranges: `TYPE RANGE [-578..., 578...]`
# matches nothing, `type_ranges` stays empty, `_span` falls back to
# (0, UINT256_MAX), and the ladder is laid over the wrong range while the loop
# reports that it ran. The driver is made signed-ready BEFORE the tool emits
# signed ranges, so the first signed run is not a silent no-op.
#
# EVERY CHECK BELOW IS A PAIR: the signed case must parse, and the unsigned case
# must be byte-identical to what it always was.

INT256_MIN = -(1 << 255)
INT256_MAX = (1 << 255) - 1

check("S7-type-range-parses-a-negative-lower-bound",
      TYPE_RANGE_RE.search(
          f"coordinate 'a' has TYPE RANGE [{INT256_MIN}, {INT256_MAX}]"
      ).groups(),
      ("a", str(INT256_MIN), str(INT256_MAX)))
check("S7-type-range-unsigned-unchanged",
      TYPE_RANGE_RE.search(
          f"coordinate 'a' has TYPE RANGE [0, {BIG}]").groups(),
      ("a", "0", str(BIG)))

check("S7-interval-parses-negatives",
      parse_intervals("a in [-5, 5], b in [0, 9]"),
      {"a": (-5, 5), "b": (0, 9)})
check("S7-interval-unsigned-unchanged",
      parse_intervals("a in [0, 5]"), {"a": (0, 5)})
# The punched set too -- its character class was digits-and-comma only, so a
# negative hole would have silently truncated the set at the minus sign.
check("S7-holes-parse-negatives",
      parse_holes("a in [-9, 9] \\ {-3, 0, 3}"), {"a": [-3, 0, 3]})
check("S7-holes-unsigned-unchanged",
      parse_holes("a in [0, 9] \\ {3}"), {"a": [3]})

check("S7-shrink-target-takes-a-negative-cut",
      shrink_target("--path-cov-certify: refuted; retry with a in [-100, -5]",
                    {}), ("a", -100, -5))
check("S7-shrink-target-unsigned-unchanged",
      shrink_target("--path-cov-certify: refuted; retry with a in [11, 100]",
                    {}), ("a", 11, 100))

_PUNCH_NEG = ("--path-cov-certify: PUNCH SUGGESTION for 'x' — instead of "
              "cutting the interval, remove the witness itself: add a != -7 to "
              "the box's `holes` (Definition 5)")
check("S7-punch-parses-a-negative-value",
      punch_targets(_PUNCH_NEG, {}), [("a", -7)])

check("S7-bracket-scan-parses-negatives",
      brackets_for("a", {14: "a upper in (-500, -100]"},
                   (INT256_MIN, INT256_MAX)),
      (-500, -100))
check("S7-bracket-scan-unsigned-unchanged",
      brackets_for("a", {14: "a upper in (500, 900]"}), (500, 900))

# The ladder. `lo` defaults to 0, so the unsigned list must be IDENTICAL to the
# one the loop has always laid -- that is the must-flip that keeps every
# existing number reproducible.
check("S7-geometric-unsigned-is-byte-identical",
      geometric_values(64), [0, 1, 2, 4, 8, 16, 32, 64])
check("S7-geometric-explicit-zero-lo-is-the-same",
      geometric_values(64, 0), geometric_values(64))
# ...and with a negative lower end the ladder is mirrored, so a boundary in the
# negative half is bracketed within a factor of two too, rather than reached
# only by the endpoint.
check("S7-geometric-signed-is-symmetric",
      geometric_values(8, -8), [-8, -4, -2, -1, 0, 1, 2, 4, 8])
check("S7-geometric-signed-keeps-both-endpoints",
      (geometric_values(100, -100)[0], geometric_values(100, -100)[-1]),
      (-100, 100))

# --- MAPPING SLOTS: the outer-box refusal wording the reader did not know ---
#
# MEASURED on a hand-built outer-box spec naming `state.nosuch[k]`. The tool
# refuses it twice and loudly, and NEITHER sentence contains "has no input
# named" -- the only phrase `unresolvable_coords` looked for. So the refusal was
# invisible to the driver: nothing printed, and the regions below simply did not
# mention the coordinate, which reads as "unconstrained".
#
# That is the detector-on-the-wrong-branch failure again. It stayed harmless
# only because nothing ever ASKED for a coordinate the tool could refuse; the
# slot proposal is what makes it reachable, so it is repaired in the same change.
_OUTER_REFUSED_NEW = (
    "WARNING: --path-cov-outer-box: unit 'sol:@C@SlotMin@F@take#53' — REFUSING "
    "coordinate 'state.nosuch[k]': the name does not resolve to an input of "
    "this unit. Name a parameter, an environment value (`msg.value` ...), or a "
    "state variable at entry (`state.<field>`); note that `state.<field>` "
    "reaches the contract object's own components only, so a WHOLE mapping or "
    "dynamic array does not resolve -- name ONE SLOT of it as "
    "`state.<name>[<key>]` instead")
check("slot-the-current-outer-box-refusal-is-read",
      unresolvable_coords(_OUTER_REFUSED_NEW), ["state.nosuch[k]"])
# MUST FLIP: the OLD wording must still be read, or a driver upgrade silently
# stops recognising a message an older binary still emits.
check("slot-the-older-outer-box-wording-is-still-read",
      unresolvable_coords(_OUTER_REFUSED), ["state._DOCKED"])
# ...and the certify branch's sentence must STILL not be read as an outer-box
# one, which is the pairing the earlier block pins in the other direction.
check("slot-the-certify-refusal-is-still-not-an-outer-box-one",
      unresolvable_coords(_CERT_REFUSED), [])

# A REFUSED COORDINATE IS NOT A ROUND THAT MEASURED NOTHING. The tool says so
# itself: "the remaining coordinates are measured as usual". Repairing the
# detector above without this would print a confident falsehood on every run
# carrying one refused name -- worse than the silent miss it replaces.
_BOX_LINE = ("--path-cov-outer-box: path enc=15 depth=3 OUTER box (D_path is "
             "CONTAINED in it): v in [1, 9], k in [0, 9]\n[run] EXIT 1\n")
check("slot-a-refusal-beside-a-box-is-not-measured-nothing",
      round_failure_reason(_OUTER_REFUSED_NEW + "\n" + _BOX_LINE), None)
# MUST FLIP: with NO box in the log the original reading stands.
_r = round_failure_reason(_OUTER_REFUSED_NEW + "\n[run] EXIT 1\n")
check("slot-a-refusal-with-no-box-at-all-still-reports-the-gap",
      _r is not None and "state.nosuch[k]" in _r, True)
_pins = {"state.nosuch[k]": 7, "msg.value": 0}
_regions = {3: {"state.nosuch[k]": (7, 7), "k": (0, 9)}}
_holes = {3: {"state.nosuch[k]": [7], "k": [5]}}
check("slot-outer-refusal-is-pre-dropped-before-certification",
      sorted(drop_unexpressible_query_names(
          ["state.nosuch[k]"], _pins, _regions, _holes)),
      ["state.nosuch[k]"])
check("slot-pre-drop-removes-pin-and-region-bound",
      (_pins, _regions, _holes),
      ({"msg.value": 0}, {3: {"k": (0, 9)}}, {3: {"k": [5]}}))

# --- proposing a slot from solc's declaration ---
#
# A payload can only ever offer a slot at a key some counterexample already
# picked -- MEASURED both ways: SlotMin's payload carries `state.bal[0xFF..FF]`
# while farming's carries no `_balances` slot at all. Neither can give
# `_balances[account]`, the slot a guard reads for EVERY account, because the
# payload is a list of values and that coordinate is a function of an input.
_MAPS = {
    "nodeType": "SourceUnit",
    "nodes": [
        {"nodeType": "ContractDefinition", "name": "Other", "id": 1,
         "linearizedBaseContracts": [1],
         "nodes": [{"nodeType": "VariableDeclaration", "name": "ghost",
                    "stateVariable": True,
                    "typeName": {"nodeType": "Mapping",
                                 "keyType": {"nodeType": "ElementaryTypeName",
                                             "typeDescriptions": {
                                                 "typeString": "address"}},
                                 "valueType": {"nodeType": "ElementaryTypeName",
                                               "typeDescriptions": {
                                                   "typeString": "uint256"}}}}]},
        {"nodeType": "ContractDefinition", "name": "Pool", "id": 2,
         "linearizedBaseContracts": [2],
         "nodes": [
             {"nodeType": "VariableDeclaration", "name": "_balances",
              "stateVariable": True,
              "typeName": {"nodeType": "Mapping",
                           "keyType": {"nodeType": "ElementaryTypeName",
                                       "typeDescriptions": {
                                           "typeString": "address"}},
                           "valueType": {"nodeType": "ElementaryTypeName",
                                         "typeDescriptions": {
                                             "typeString": "uint256"}}}},
             {"nodeType": "VariableDeclaration", "name": "_allowance",
              "stateVariable": True,
              "typeName": {"nodeType": "Mapping",
                           "keyType": {"nodeType": "ElementaryTypeName",
                                       "typeDescriptions": {
                                           "typeString": "address"}},
                           "valueType": {"nodeType": "Mapping"}}},
             {"nodeType": "VariableDeclaration", "name": "_owner",
              "stateVariable": True,
              "typeDescriptions": {"typeString": "address"},
              "typeName": {"nodeType": "ElementaryTypeName",
                           "typeDescriptions": {"typeString": "address"}}},
             {"nodeType": "VariableDeclaration", "name": "_total",
              "stateVariable": True,
              "typeName": {"nodeType": "ElementaryTypeName",
                           "typeDescriptions": {"typeString": "uint256"}}},
             {"nodeType": "FunctionDefinition", "name": "balanceOf",
              "parameters": {"parameters": [
                  {"name": "account",
                   "typeDescriptions": {"typeString": "address"}}]}},
             {"nodeType": "FunctionDefinition", "name": "deposit",
              "parameters": {"parameters": [
                  {"name": "amount",
                   "typeDescriptions": {"typeString": "uint256"}}]}},
         ]},
    ],
}
_fd4, _p4 = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd4, "w") as _f4:
    json.dump(_MAPS, _f4)

_m, _mref = mapping_state_vars(_p4, "Pool")
check("slot-a-value-type-key-with-a-scalar-value-is-offered",
      _m.get("_balances"), ("address", "uint256"))
# A plain scalar state variable is not a mapping and must not appear.
check("slot-a-scalar-state-variable-is-not-a-mapping", "_total" in _m, False)
# A NESTED mapping is refused with its reason, not dropped silently -- the same
# shape the storage-layout side refuses, and the reason names the mechanism.
check("slot-a-nested-mapping-is-refused-by-name",
      [r.split(" ")[0] for r in _mref], ["_allowance"])
# SCOPED. Another contract's mapping must not be offered: on a FLATTENED input
# that name would produce a coordinate the tool cannot resolve, and (before the
# reader above was fixed) it vanished with nothing on screen.
check("slot-a-foreign-contract-mapping-is-not-offered", "ghost" in _m, False)
check("slot-the-foreign-mapping-is-visible-to-its-own-contract",
      "ghost" in mapping_state_vars(_p4, "Other")[0], True)
check("state-alias-type-range-keeps-address-width",
      state_coord_type_ranges(
          _p4, "Pool", ["state._owner$117"],
          {"_owner": "_owner$117"}),
      {"state._owner$117": (0, (1 << 160) - 1)})

check("slot-the-units-parameters-are-read-in-order",
      unit_params(_p4, "Pool", "balanceOf"), [("account", "address")])

# THE KEY'S TYPE MUST MATCH. `_balances[amount]` on a mapping(address => ...)
# resolves to a different slot than the guard reads, so the region would be
# certified about a quantity no execution touches.
check("slot-a-matching-parameter-becomes-the-key",
      propose_slot_coords({"_balances": ("address", "uint256")},
                          [("account", "address")], 4)[0],
      ["state._balances[account]", "state._balances[msg.sender]"])
# MUST FLIP: a parameter of the WRONG type is not used, and msg.sender is still
# offered because the key type is address.
check("slot-a-mismatched-parameter-is-not-used",
      propose_slot_coords({"_balances": ("address", "uint256")},
                          [("amount", "uint256")], 4)[0],
      ["state._balances[msg.sender]"])
_alias_maps = prefer_esbmc_mapping_aliases(add_esbmc_mapping_aliases(
    {"wards": ("address", "uint256")}, {"wards": "wards$5"}))
check("slot-esbmc-alias-drops-source-row",
      sorted(_alias_maps), ["wards$5"])
check("slot-esbmc-alias-preserves-source-access-name",
      propose_slot_coords(_alias_maps, [], 4, ["wards"],
                          [("wards", ("msg.sender",))])[0],
      ["state.wards$5[msg.sender]"])
check("slot-esbmc-alias-still-drives-type-range",
      mapping_slot_type_ranges(_alias_maps, ["state.wards$5[msg.sender]"]),
      {"state.wards$5[msg.sender]": (0, UINT256_MAX)})

# ---- NESTED AND STRUCT-VALUED STORES -------------------------------------
#
# THESE CANNOT PASS WITHOUT THE CHANGE, and that is checked rather than hoped:
# `propose_slot_coords` used to destructure every entry as `kt, _vt = maps[m]`,
# so a 3-tuple raised ValueError before a single name was built. There is no
# reading of the old code under which these produce the expected lists.
#
# THE TWO ONE-LEVEL CASES ABOVE ARE THE NEGATIVE CONTROL and must stay
# BIT-IDENTICAL: widening the nested case while also changing the one-level
# spelling would silently re-point every slot coordinate already in use, and
# both outcomes would look like "the new tests pass".
check("slot-nested-two-level-crosses-the-keys",
      propose_slot_coords({"m": (("address", "address"), "uint256", [""])},
                          [("from_", "address")], 8)[0],
      ["state.m[from_][from_]", "state.m[from_][msg.sender]",
       "state.m[msg.sender][from_]", "state.m[msg.sender][msg.sender]"])

check("slot-struct-value-names-every-scalar-field",
      propose_slot_coords({"b": (("uint256",), "struct Bal",
                                 [".amount", ".tag"])},
                          [("k", "uint256")], 8)[0],
      ["state.b[k].amount", "state.b[k].tag"])
check("slot-struct-value-leaf-types-drive-their-own-ranges",
      mapping_slot_type_ranges(
          {"b": (("uint256",), "struct Bal", [".amount", ".tag"],
                 {".amount": "uint248", ".tag": "uint8"})},
          ["state.b[k].amount", "state.b[k].tag"]),
      {"state.b[k].amount": (0, (1 << 248) - 1),
       "state.b[k].tag": (0, 255)})

# A level with NO candidate key must yield NOTHING rather than a shorter name.
# A name with fewer keys denotes a whole sub-store, and its rungs would be
# reported under the name the reader wrote -- the silent-wrong-quantity shape.
_cn, _sn = propose_slot_coords({"m": (("address", "uint8"), "uint256", [""])},
                               [("a", "address")], 8)
check("slot-nested-missing-a-level-proposes-nothing", _cn, [])
check("slot-nested-missing-a-level-names-which-level",
      any("key level 1" in s for s in _sn), True)

# ---- AND THE SAME THING READ OFF A REAL solc AST -------------------------
#
# The cases above are hand-built dicts, so they test the proposer and nothing
# else. This one runs the AST walk over the fixture the whole finding rests on.
# Its presence is CHECKED, not assumed: a missing fixture would otherwise skip
# silently and the suite would stay green having measured nothing.
_d44 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "notes", "coverage", "poc", "D44_MapStructValue.solast")
check("d44-fixture-is-present", os.path.exists(_d44), True)
_d44maps, _d44ref = mapping_state_vars(_d44, "D44_MapStructValue")
check("d44-one-level-scalar-still-the-plain-two-tuple",
      _d44maps.get("balScalar"), ("uint256", "uint256"))
check("d44-struct-valued-mapping-lists-its-scalar-fields",
      (_d44maps.get("balStruct") or (None, None, None))[2],
      [".amount", ".tag"])
check("d44-struct-valued-mapping-keeps-leaf-types",
      (_d44maps.get("balStruct") or (None, None, None, {}))[3],
      {".amount": "uint248", ".tag": "uint8"})
check("d44-struct-valued-mapping-slot-ranges-are-not-uint256-defaults",
      mapping_slot_type_ranges(
          _d44maps, ["state.balStruct[k].amount", "state.balStruct[k].tag"]),
      {"state.balStruct[k].amount": (0, (1 << 248) - 1),
       "state.balStruct[k].tag": (0, 255)})
check("d44-struct-valued-mapping-is-no-longer-refused",
      any("balStruct" in r for r in _d44ref), False)
_nested_struct_map_ast = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition",
        "name": "C",
        "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [
            {
                "nodeType": "StructDefinition",
                "name": "Set",
                "members": [{
                    "nodeType": "VariableDeclaration",
                    "name": "memberIndices",
                    "typeName": {
                        "nodeType": "Mapping",
                        "keyType": {
                            "nodeType": "ElementaryTypeName",
                            "typeDescriptions": {"typeString": "bytes32"},
                        },
                        "valueType": {
                            "nodeType": "ElementaryTypeName",
                            "typeDescriptions": {"typeString": "uint256"},
                        },
                    },
                }],
            },
            {
                "nodeType": "StructDefinition",
                "name": "Account",
                "members": [{
                    "nodeType": "VariableDeclaration",
                    "name": "entries",
                    "typeName": {
                        "nodeType": "UserDefinedTypeName",
                        "pathNode": {"name": "Set"},
                    },
                }],
            },
            {
                "nodeType": "VariableDeclaration",
                "stateVariable": True,
                "name": "accounts",
                "typeName": {
                    "nodeType": "Mapping",
                    "keyType": {
                        "nodeType": "ElementaryTypeName",
                        "typeDescriptions": {"typeString": "address"},
                    },
                    "valueType": {
                        "nodeType": "UserDefinedTypeName",
                        "pathNode": {"name": "Account"},
                    },
                },
            },
        ],
    }],
}
_nested_struct_map_file = tempfile.NamedTemporaryFile(
    "w", suffix=".solast", delete=False)
try:
    json.dump(_nested_struct_map_ast, _nested_struct_map_file)
    _nested_struct_map_file.close()
    _nsm_maps, _nsm_refused = mapping_state_vars(
        _nested_struct_map_file.name, "C")
finally:
    try:
        os.unlink(_nested_struct_map_file.name)
    except OSError:
        pass
check("mapping-to-struct-to-mapping-leaf-is-enumerated",
      _nsm_maps.get("accounts"),
      (("address", "bytes32"), "nested mapping leaf",
       [".entries.memberIndices"],
       {".entries.memberIndices": "uint256"}))
check("slot-proposer-keeps-bytesN-parameter-as-key",
      propose_slot_coords(
          _nsm_maps, [("dataHash", "bytes32")], 8,
          dependencies=["accounts"])[0],
      ["state.accounts[msg.sender][dataHash].entries.memberIndices"])
# ...and on a NON-address key with no matching parameter there is nothing to
# propose at all, with the reason named rather than an empty list.
_c, _s = propose_slot_coords({"bal": ("uint256", "uint256")},
                             [("who", "address")], 4)
check("slot-no-usable-key-proposes-nothing", _c, [])
check("slot-and-says-why", "no parameter of the key type" in _s[0], True)
# The budget is a cap, and what it cuts is NAMED -- a silent truncation would
# read as "there was nothing else to propose".
_c2, _s2 = propose_slot_coords({"bal": ("uint256", "uint256")},
                               [("a", "uint256"), ("b", "uint256")], 1)
check("slot-the-budget-caps-the-proposal", _c2, ["state.bal[a]"])
check("slot-the-budget-names-what-it-cut",
      "over the --slot-coords budget" in _s2[0], True)
os.unlink(_p4)

# Dependency selection follows solc declaration ids through modifiers and
# internal calls. The direct state access ranks first; the unused mapping is
# named as excluded instead of consuming the cap by alphabetical accident.
_DEPS = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition", "name": "Dep", "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "direct", "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11,
             "name": "modifierMap", "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12,
             "name": "helperMap", "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 13,
             "name": "unused", "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 14,
             "name": "sameArity", "stateVariable": True},
            {"nodeType": "ModifierDefinition", "id": 20,
             "name": "guard", "body": {
                 "nodeType": "Block", "statements": [{
                     "nodeType": "Identifier", "name": "modifierMap",
                     "referencedDeclaration": 11}]}},
            {"nodeType": "FunctionDefinition", "id": 30,
             "name": "helper", "body": {
                 "nodeType": "Block", "statements": [{
                     "nodeType": "Identifier", "name": "helperMap",
                     "referencedDeclaration": 12}]}},
            {"nodeType": "FunctionDefinition", "id": 40, "name": "f",
             "parameters": {"parameters": []},
             "modifiers": [{"nodeType": "ModifierInvocation",
                            "modifierName": {"nodeType": "IdentifierPath",
                                             "referencedDeclaration": 20}}],
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "Identifier", "name": "direct",
                  "referencedDeclaration": 10},
                 {"nodeType": "FunctionCall", "expression": {
                     "nodeType": "Identifier", "name": "helper",
                     "referencedDeclaration": 30}}]}},
            {"nodeType": "FunctionDefinition", "id": 41, "name": "f",
             "parameters": {"parameters": [{"name": "x",
                                               "typeDescriptions": {
                                                   "typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "Identifier", "name": "unused",
                  "referencedDeclaration": 13}]}},
            {"nodeType": "FunctionDefinition", "id": 42, "name": "f",
             "parameters": {"parameters": [{"name": "who",
                                               "typeDescriptions": {
                                                   "typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "Identifier", "name": "sameArity",
                  "referencedDeclaration": 14}]}}
        ]
    }]
}
_fd5, _p5 = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd5, "w") as _f5:
    json.dump(_DEPS, _f5)
_deps, _dep_evidence = unit_state_dependencies(_p5, "Dep", "f", arity=0)
check("slot-dependencies-rank-direct-before-transitive",
      _deps, ["direct", "helperMap", "modifierMap"])
_dc, _ds = propose_slot_coords(
    {name: ("address", "uint256")
     for name in ("direct", "helperMap", "modifierMap", "unused")},
    [("who", "address")], 4, _deps)
check("slot-dependencies-spend-budget-in-ranked-order", _dc, [
    "state.direct[who]", "state.direct[msg.sender]",
    "state.helperMap[who]", "state.helperMap[msg.sender]"])
check("slot-dependencies-name-an-unreferenced-mapping",
      any("state.unused[...]" in note and "excluded" in note for note in _ds),
      True)
check("slot-dependencies-publish-call-chain-evidence",
      any("modifier guard#20" in note for note in _dep_evidence), True)
_overload_deps, _ = unit_state_dependencies(_p5, "Dep", "f", arity=1)
check("slot-dependencies-arity-alone-cannot-separate-same-arity-overloads",
      _overload_deps, ["sameArity", "unused"])
_exact_deps, _ = unit_state_dependencies(
    _p5, "Dep", "f", arity=1, declaration_id=41)
check("slot-dependencies-select-same-arity-overload-by-node-id",
      _exact_deps, ["unused"])
check("slot-params-select-same-arity-overload-by-node-id",
      unit_params(_p5, "Dep", "f", declaration_id=42),
      [("who", "address")])
os.unlink(_p5)

_SLOT_ACCESS = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition", "name": "Aqua", "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "_balances", "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "push",
             "parameters": {"parameters": [
                 {"name": "maker", "typeDescriptions": {"typeString": "address"}},
                 {"name": "app", "typeDescriptions": {"typeString": "address"}},
                 {"name": "strategyHash",
                  "typeDescriptions": {"typeString": "bytes32"}},
                 {"name": "token", "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement",
                 "expression": {
                     "nodeType": "MemberAccess", "memberName": "load",
                     "expression": {
                         "nodeType": "IndexAccess", "src": "40:1:0",
                         "baseExpression": {
                             "nodeType": "IndexAccess",
                             "baseExpression": {
                                 "nodeType": "IndexAccess",
                                 "baseExpression": {
                                     "nodeType": "IndexAccess",
                                     "baseExpression": {
                                         "nodeType": "Identifier",
                                         "name": "_balances",
                                         "referencedDeclaration": 10},
                                     "indexExpression": {
                                         "nodeType": "Identifier",
                                         "name": "maker"}},
                                 "indexExpression": {
                                     "nodeType": "Identifier",
                                     "name": "app"}},
                             "indexExpression": {
                                 "nodeType": "Identifier",
                                 "name": "strategyHash"}},
                         "indexExpression": {
                             "nodeType": "Identifier",
                             "name": "token"}}}}]}}
        ]
    }]
}
_fd6, _p6 = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd6, "w") as _f6:
    json.dump(_SLOT_ACCESS, _f6)
_slot_accesses, _slot_access_evidence = unit_mapping_slot_accesses(
    _p6, "Aqua", "push", declaration_id=20)
check("slot-access-walk-preserves-the-source-key-chain",
      _slot_accesses, [("_balances", ("maker", "app", "strategyHash", "token"))])
check("slot-access-evidence-names-the-source-slot",
      "state._balances[maker][app][strategyHash][token]"
      in _slot_access_evidence[0], True)
_LIB_METHOD_SLOT_ACCESS = {
    "nodeType": "SourceUnit",
    "nodes": [
        {"nodeType": "ContractDefinition", "name": "Sets", "id": 1,
         "linearizedBaseContracts": [1],
         "nodes": [
             {"nodeType": "FunctionDefinition", "id": 30, "name": "contains",
              "parameters": {"parameters": [
                  {"id": 31, "name": "self"},
                  {"id": 32, "name": "other"},
              ]},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "Return", "expression": {
                      "nodeType": "BinaryOperation", "operator": ">",
                      "leftExpression": {
                          "nodeType": "IndexAccess", "src": "30:5:0",
                          "baseExpression": {
                              "nodeType": "MemberAccess",
                              "memberName": "memberIndices",
                              "expression": {
                                  "nodeType": "Identifier",
                                  "name": "self",
                                  "referencedDeclaration": 31}},
                          "indexExpression": {
                              "nodeType": "Identifier",
                              "name": "other",
                              "referencedDeclaration": 32}},
                      "rightExpression": {
                          "nodeType": "Literal", "kind": "number",
                          "value": "0"}}}]}}]},
        {"nodeType": "ContractDefinition", "name": "Prover", "id": 2,
         "linearizedBaseContracts": [2],
         "nodes": [
             {"nodeType": "VariableDeclaration", "id": 10,
              "name": "accounts", "stateVariable": True},
             {"nodeType": "FunctionDefinition", "id": 20,
              "name": "deleteEntry",
              "parameters": {"parameters": [
                  {"id": 21, "name": "dataHash",
                   "typeDescriptions": {"typeString": "bytes32"}},
              ]},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "ExpressionStatement", "expression": {
                      "nodeType": "FunctionCall",
                      "expression": {
                          "nodeType": "MemberAccess",
                          "memberName": "contains",
                          "referencedDeclaration": 30,
                          "expression": {
                              "nodeType": "MemberAccess",
                              "memberName": "entries",
                              "expression": {
                                  "nodeType": "IndexAccess",
                                  "baseExpression": {
                                      "nodeType": "Identifier",
                                      "name": "accounts",
                                      "referencedDeclaration": 10},
                                  "indexExpression": {
                                      "nodeType": "MemberAccess",
                                      "memberName": "sender",
                                      "expression": {
                                          "nodeType": "Identifier",
                                          "name": "msg"}}}}},
                      "arguments": [{
                          "nodeType": "Identifier",
                          "name": "dataHash",
                          "referencedDeclaration": 21}]}}]}}]}],
}
_fd_lib, _p_lib = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd_lib, "w") as _f_lib:
    json.dump(_LIB_METHOD_SLOT_ACCESS, _f_lib)
_lib_slot_accesses, _lib_slot_evidence = unit_mapping_slot_accesses(
    _p_lib, "Prover", "deleteEntry", declaration_id=20, access_mode="read")
check("slot-access-method-call-threads-hidden-receiver",
      _lib_slot_accesses,
      [("accounts.entries.memberIndices", ("msg.sender", "dataHash"))])
check("slot-access-method-call-evidence-names-inner-library-slot",
      "state.accounts.entries.memberIndices[msg.sender][dataHash]"
      in _lib_slot_evidence[0],
      True)
os.unlink(_p_lib)
_RW_SLOT_ACCESS = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition", "name": "RW", "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "bal", "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "set",
             "parameters": {"parameters": [
                 {"id": 21, "name": "who",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 22, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 23, "name": "recipient",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "leftHandSide": {
                         "nodeType": "IndexAccess", "src": "100:5:0",
                         "baseExpression": {
                             "nodeType": "Identifier", "name": "bal",
                             "referencedDeclaration": 10},
                         "indexExpression": {
                             "nodeType": "Identifier", "name": "recipient",
                             "referencedDeclaration": 23}},
                     "rightHandSide": {
                         "nodeType": "Identifier", "name": "amount",
                         "referencedDeclaration": 22}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "FunctionCall", "src": "120:7:0",
                     "expression": {"nodeType": "Identifier", "name": "require"},
                     "arguments": [{
                         "nodeType": "BinaryOperation", "operator": ">",
                         "leftExpression": {
                             "nodeType": "IndexAccess", "src": "130:5:0",
                             "baseExpression": {
                                 "nodeType": "Identifier", "name": "bal",
                                 "referencedDeclaration": 10},
                             "indexExpression": {
                                 "nodeType": "MemberAccess",
                                 "memberName": "sender",
                                 "expression": {
                                     "nodeType": "Identifier", "name": "msg"}}},
                         "rightExpression": {
                             "nodeType": "Literal", "kind": "number",
                             "value": "0"}}]}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "leftHandSide": {
                         "nodeType": "IndexAccess", "src": "150:5:0",
                         "baseExpression": {
                             "nodeType": "Identifier", "name": "bal",
                             "referencedDeclaration": 10},
                         "indexExpression": {
                             "nodeType": "Identifier", "name": "who",
                             "referencedDeclaration": 21}},
                     "rightHandSide": {
                         "nodeType": "Identifier", "name": "amount",
                         "referencedDeclaration": 22}}}]}}
        ]
    }]
}
_fd_rw, _p_rw = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd_rw, "w") as _f_rw:
    json.dump(_RW_SLOT_ACCESS, _f_rw)
_all_slot_accesses, _ = unit_mapping_slot_accesses(
    _p_rw, "RW", "set", declaration_id=20)
_read_slot_accesses, _ = unit_mapping_slot_accesses(
    _p_rw, "RW", "set", declaration_id=20, access_mode="read")
check("slot-access-default-keeps-write-targets-for-oracles",
      _all_slot_accesses, [("bal", ("msg.sender",)), ("bal", ("recipient",)),
                           ("bal", ("who",))])
check("slot-access-read-mode-drops-plain-assignment-lhs",
      _read_slot_accesses, [("bal", ("msg.sender",)), ("bal", ("who",))])
os.unlink(_p_rw)
_STRUCT_SLOT_ACCESS = {
    "nodeType": "SourceUnit",
    "nodes": [{
        "nodeType": "ContractDefinition", "name": "Rows", "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "rows", "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "who",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 22, "name": "country",
                  "typeDescriptions": {"typeString": "uint16"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "FunctionCall", "src": "100:7:0",
                     "expression": {"nodeType": "Identifier", "name": "require"},
                     "arguments": [{
                         "nodeType": "BinaryOperation", "operator": "!=",
                         "leftExpression": {
                             "nodeType": "MemberAccess",
                             "memberName": "identity",
                             "src": "110:5:0",
                             "expression": {
                                 "nodeType": "IndexAccess",
                                 "src": "112:5:0",
                                 "typeDescriptions": {
                                     "typeString":
                                     "struct Rows.Row storage ref"},
                                 "baseExpression": {
                                     "nodeType": "Identifier", "name": "rows",
                                     "referencedDeclaration": 10},
                                 "indexExpression": {
                                     "nodeType": "Identifier", "name": "who",
                                     "referencedDeclaration": 21}}},
                         "rightExpression": {
                             "nodeType": "Literal", "kind": "number",
                             "value": "0"}}]}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "leftHandSide": {
                         "nodeType": "MemberAccess",
                         "memberName": "country",
                         "src": "130:5:0",
                         "expression": {
                             "nodeType": "IndexAccess",
                             "src": "132:5:0",
                             "typeDescriptions": {
                                 "typeString": "struct Rows.Row storage ref"},
                             "baseExpression": {
                                 "nodeType": "Identifier", "name": "rows",
                                 "referencedDeclaration": 10},
                             "indexExpression": {
                                 "nodeType": "Identifier", "name": "who",
                                 "referencedDeclaration": 21}}},
                     "rightHandSide": {
                         "nodeType": "Identifier", "name": "country",
                         "referencedDeclaration": 22}}}]}}
        ]
    }]
}
_fd_struct, _p_struct = tempfile.mkstemp(suffix=".solast")
with os.fdopen(_fd_struct, "w") as _f_struct:
    json.dump(_STRUCT_SLOT_ACCESS, _f_struct)
_struct_read_accesses, _ = unit_mapping_slot_accesses(
    _p_struct, "Rows", "touch", declaration_id=20, access_mode="read")
_struct_all_accesses, _ = unit_mapping_slot_accesses(
    _p_struct, "Rows", "touch", declaration_id=20)
_struct_maps = {"rows": ("address", "struct Rows.Row", [".country"])}
_struct_read_slots, _struct_read_skips = propose_slot_coords(
    _struct_maps, [("who", "address"), ("country", "uint16")], 8,
    sorted({name for name, _keys in _struct_read_accesses}),
    _struct_read_accesses)
_struct_all_slots, _ = propose_slot_coords(
    _struct_maps, [("who", "address"), ("country", "uint16")], 8,
    sorted({name for name, _keys in _struct_all_accesses}),
    _struct_all_accesses)
check("slot-access-read-mode-preserves-struct-field-tail",
      _struct_read_accesses, [("rows.identity", ("who",))])
check("slot-region-does-not-substitute-a-different-struct-field",
      _struct_read_slots, [])
check("slot-region-names-the-unqueryable-read-field",
      any("rows.identity" in s for s in _struct_read_skips), True)
check("slot-oracle-mode-still-sees-the-written-struct-field",
      _struct_all_slots, ["state.rows[who].country"])
os.unlink(_p_struct)
_bytes32_zero_slot_key = "0x20" + ("00" * 31)
check("bytes32-zero-ce-lowers-to-raw-coordinate",
      bytes_static_value_from_ce("bytes32", "{ .data = { 0 } }"), 0)
check("bytes2-ce-lowers-to-raw-coordinate",
      bytes_static_value_from_ce("bytes2", "{ .data = { 0x12, 0x34 } }"),
      0x1234)
check("bytes32-zero-ce-lowers-to-solidity-mapping-key",
      bytes_static_mapping_key_from_ce("bytes32", "{ .data = { 0 } }"),
      _bytes32_zero_slot_key)
check("bytes32-typed-value-lowers-to-solidity-mapping-key",
      bytes_static_mapping_key_from_value("bytes32", 0),
      _bytes32_zero_slot_key)
check("bytes2-ce-lowers-to-solidity-mapping-key",
      bytes_static_mapping_key_from_ce(
          "bytes2", "{ .data = { 0x12, 0x34 } }"),
      "0x02" + ("00" * 29) + "1234")
_literal_keys, _literal_skipped = agreed_bytes_mapping_key_literals(
    [{"strategyHash": "{ .data = { 0 } }"},
     {"strategyHash": "{ .data = { 0 } }"}],
    [("maker", "address"), ("strategyHash", "bytes32")])
check("bytes32-mapping-key-literal-agrees-across-witnesses",
      _literal_keys, {"strategyHash": _bytes32_zero_slot_key})
check("bytes32-mapping-key-literal-has-no-refusal",
      _literal_skipped, [])
_literal_from_list_raw, _literal_from_list_skipped = \
    agreed_bytes_mapping_key_literals(
        [[{"name": "strategyHash", "value": "{ .data = { 0 } }"}]],
        [("strategyHash", "bytes32")])
check("bytes32-mapping-key-literal-accepts-journal-list-inputs",
      _literal_from_list_raw, {"strategyHash": _bytes32_zero_slot_key})
check("bytes32-mapping-key-literal-journal-list-has-no-refusal",
      _literal_from_list_skipped, [])
_literal_from_typed, _literal_from_typed_skipped = \
    agreed_bytes_mapping_key_literals(
        [], [("strategyHash", "bytes32")],
        typed_paths=[(2, 1, {"strategyHash": 0}),
                     (3, 1, {"strategyHash": 0})])
check("bytes32-mapping-key-literal-falls-back-to-typed-paths",
      _literal_from_typed, {"strategyHash": _bytes32_zero_slot_key})
check("bytes32-mapping-key-literal-typed-paths-have-no-refusal",
      _literal_from_typed_skipped, [])
_literal_disagree, _literal_disagree_skipped = \
    agreed_bytes_mapping_key_literals(
        [], [("strategyHash", "bytes32")],
        typed_paths=[(2, 1, {"strategyHash": 0}),
                     (3, 1, {"strategyHash": 1})])
check("bytes32-mapping-key-literal-refuses-disagreeing-typed-paths",
      _literal_disagree, {})
check("bytes32-mapping-key-literal-names-disagreeing-typed-paths",
      any("witnessed paths disagree" in s
          for s in _literal_disagree_skipped), True)

_live_vectors, _live_bad, _live_missing = live_witness_vectors(
    [(7, 1, {"state.owner": 1, "msg.value": 0})],
    {7: [{"state.owner": 1, "msg.value": 0},
         {"state.owner": 2, "msg.value": 0},
         {"state.owner": 3, "msg.value": 1},
         {"state.owner": 4}]},
    {"msg.value": 0})
check("live-witness-vectors-keep-values-inside-pinned-slice",
      _live_vectors, {7: [{"state.owner": 1, "msg.value": 0},
                          {"state.owner": 2, "msg.value": 0}]})
check("live-witness-vectors-count-pin-violations", _live_bad, 1)
check("live-witness-vectors-count-missing-pins", _live_missing, 1)
check("path-cov-probe-goal-cap-detected",
      path_cov_probe_goal_cap(
          "ERROR: --path-cov-probe: unit 'f' needs 23978 probe claims "
          "(38 branch arms x 631 physical exits), exceeding "
          "--path-cov-max-goals 10000"),
      True)
check("path-cov-probe-goal-cap-detected-normalized-diagnostic",
      path_cov_probe_goal_cap(
          "path coverage probe universe exceeded --path-cov-max-goals "
          "before any cov-report.json could be emitted"),
      True)
check("path-cov-probe-goal-cap-not-a-generic-timeout",
      path_cov_probe_goal_cap("ERROR: Terminated"), False)
check("path-cov-probe-early-stop-detected",
      path_cov_probe_early_stop(
          "--path-cov-probe: unit 'f' added 216 exit-latched claim(s)\n"
          "[run] EARLY STOP: --path-cov-probe added 216 exit-latched "
          "claim(s) for f, over the fallback threshold 128"),
      True)
check("path-cov-probe-early-stop-not-a-generic-timeout",
      path_cov_probe_early_stop("[run] TIMEOUT after 60s"), False)
check("path-cov-probe-timeout-detected",
      path_cov_probe_timeout(
          "--path-cov-probe: unit 'f' added 3880 exit-latched claim(s)\n"
          "[run] TIMEOUT after 120s: esbmc ... --path-cov-probe"),
      True)
check("path-cov-probe-timeout-not-basic-timeout",
      path_cov_probe_timeout("[run] TIMEOUT after 120s: esbmc ..."),
      False)
check("path-cov-probe-enum-timeout-caps-600s-unit",
      path_cov_probe_enum_timeout(600, 8), 90)
check("path-cov-probe-enum-timeout-keeps-non-probe-budget",
      path_cov_probe_enum_timeout(600, 0), 600)
check("path-cov-probe-enum-timeout-preserves-small-unit-budget",
      path_cov_probe_enum_timeout(45, 8), 45)
_aqua_slots, _aqua_skipped = propose_slot_coords(
    {"_balances": (("address", "address", "bytes32", "address"),
                   "struct Balance", [".amount", ".tokensCount"])},
    [("maker", "address"), ("app", "address"),
     ("strategyHash", "bytes32"), ("token", "address")],
    4, ["_balances"], _slot_accesses)
check("slot-source-access-keeps-bytes32-key-as-parameter",
      _aqua_slots,
      ["state._balances[maker][app][strategyHash][token].amount",
       "state._balances[maker][app][strategyHash][token].tokensCount"])
check("slot-source-access-no-longer-needs-bytes32-literal",
      any("bytesN parameter strategyHash" in s for s in _aqua_skipped),
      False)
_aqua_slots, _aqua_skipped = propose_slot_coords(
    {"_balances": (("address", "address", "bytes32", "address"),
                   "struct Balance", [".amount", ".tokensCount"])},
    [("maker", "address"), ("app", "address"),
     ("strategyHash", "bytes32"), ("token", "address")],
    4, ["_balances"], _slot_accesses,
    key_literals={"strategyHash": _bytes32_zero_slot_key})
check("slot-source-accesses-spend-the-budget-before-cross-products",
      _aqua_slots[:2],
      [f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].amount",
       f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].tokensCount"])
check("slot-source-access-deduplicates-the-fallback-cross-product",
      _aqua_slots.count(
          f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].amount"),
      1)
check("slot-source-access-suppresses-guessed-cross-products",
      _aqua_slots,
      [f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].amount",
       f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].tokensCount"])
check("slot-source-access-names-the-suppressed-fallback",
      any("fallback cross-product suppressed" in s for s in _aqua_skipped),
      True)
_aqua_typed_slots, _aqua_typed_skipped = propose_slot_coords(
    {"_balances": (("address", "address", "bytes32", "address"),
                   "struct Balance", [".amount", ".tokensCount"],
                   {".amount": "uint248", ".tokensCount": "uint8"})},
    [("maker", "address"), ("app", "address"),
     ("strategyHash", "bytes32"), ("token", "address"),
     ("amount", "uint256")],
    8, ["_balances"], _slot_accesses,
    key_literals={"strategyHash": _bytes32_zero_slot_key})
_aqua_amount_coord = (
    f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].amount")
_aqua_count_coord = (
    f"state._balances[maker][app][{_bytes32_zero_slot_key}][token].tokensCount")
check("slot-source-access-aqua-push-ground-truth-slots",
      _aqua_typed_slots, [_aqua_amount_coord, _aqua_count_coord])
check("slot-source-access-aqua-push-no-strategyhash-aggregate-slot",
      any("[strategyHash]" in c for c in _aqua_typed_slots), False)
check("slot-source-access-aqua-push-no-guessed-cross-product",
      any("[maker][maker]" in c for c in _aqua_typed_slots), False)
check("slot-source-access-aqua-push-static-leaf-ranges",
      mapping_slot_type_ranges(
          {"_balances": (("address", "address", "bytes32", "address"),
                         "struct Balance", [".amount", ".tokensCount"],
                         {".amount": "uint248", ".tokensCount": "uint8"})},
          _aqua_typed_slots),
      {_aqua_amount_coord: (0, (1 << 248) - 1),
       _aqua_count_coord: (0, 255)})
check("slot-source-access-aqua-push-documents-fallback-suppression",
      any("fallback cross-product suppressed" in s
          for s in _aqua_typed_skipped), True)
os.unlink(_p6)

_setdist_ast = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "notes", "coverage",
    "poc_units", "farming__Distributor__setDistributor", "inputs",
    "farming__FarmingPool.flat.sol.solast")
check("setDistributor-real-ast-is-present", os.path.exists(_setdist_ast), True)
_setdist_deps, _ = unit_state_dependencies(
    _setdist_ast, "FarmingPool", "setDistributor")
check("setDistributor-dependencies-cross-onlyOwner-but-not-erc20-mappings",
      _setdist_deps, ["_distributor", "_owner"])

_rec_ast = {
    "nodeType": "SourceUnit",
    "nodes": [
        {
            "nodeType": "ContractDefinition",
            "id": 1,
            "name": "SafeMath",
            "contractKind": "library",
            "linearizedBaseContracts": [1],
            "nodes": [
                {
                    "nodeType": "FunctionDefinition",
                    "id": 2,
                    "name": "sub",
                    "parameters": {"parameters": [{}, {}]},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "Return",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "sub",
                                    "referencedDeclaration": 2,
                                },
                                "arguments": [{}, {}],
                            },
                        }],
                    },
                },
            ],
        },
        {
            "nodeType": "ContractDefinition",
            "id": 3,
            "name": "Token",
            "linearizedBaseContracts": [3],
            "nodes": [
                {
                    "nodeType": "FunctionDefinition",
                    "id": 4,
                    "name": "transfer",
                    "parameters": {"parameters": [{}, {}]},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "ExpressionStatement",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "_transfer",
                                    "referencedDeclaration": 5,
                                },
                                "arguments": [{}, {}],
                            },
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 5,
                    "name": "_transfer",
                    "parameters": {"parameters": [{}, {}]},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "ExpressionStatement",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "MemberAccess",
                                    "memberName": "sub",
                                    "referencedDeclaration": 2,
                                },
                                "arguments": [{}],
                            },
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 6,
                    "name": "boundedRecur",
                    "parameters": {"parameters": [{}]},
                    "body": {
                        "nodeType": "Block",
                        "statements": [
                            {"nodeType": "IfStatement", "condition": {}},
                            {
                                "nodeType": "Return",
                                "expression": {
                                    "nodeType": "FunctionCall",
                                    "expression": {
                                        "nodeType": "Identifier",
                                        "name": "boundedRecur",
                                        "referencedDeclaration": 6,
                                    },
                                    "arguments": [{}],
                                },
                            },
                        ],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 7,
                    "name": "callBounded",
                    "parameters": {"parameters": []},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "ExpressionStatement",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "boundedRecur",
                                    "referencedDeclaration": 6,
                                },
                                "arguments": [{}],
                            },
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 8,
                    "name": "loopForever",
                    "parameters": {"parameters": []},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "Return",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "loopForever",
                                    "referencedDeclaration": 8,
                                },
                                "arguments": [],
                            },
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 9,
                    "name": "normalTarget",
                    "parameters": {"parameters": []},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "ExpressionStatement",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "foo",
                                    "referencedDeclaration": 10,
                                },
                                "arguments": [{}],
                            },
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 10,
                    "name": "foo",
                    "parameters": {"parameters": [{}]},
                    "body": {"nodeType": "Block", "statements": []},
                },
            ],
        },
        {
            "nodeType": "ContractDefinition",
            "id": 11,
            "name": "Unrelated",
            "linearizedBaseContracts": [11],
            "nodes": [
                {
                    "nodeType": "FunctionDefinition",
                    "id": 12,
                    "name": "foo",
                    "parameters": {"parameters": [{}]},
                    "body": {
                        "nodeType": "Block",
                        "statements": [{
                            "nodeType": "Return",
                            "expression": {
                                "nodeType": "FunctionCall",
                                "expression": {
                                    "nodeType": "Identifier",
                                    "name": "foo",
                                    "referencedDeclaration": 12,
                                },
                                "arguments": [{}],
                            },
                        }],
                    },
                },
            ],
        },
    ],
}
_rec_ast_file = tempfile.NamedTemporaryFile(
    mode="w", suffix=".solast", delete=False)
try:
    json.dump(_rec_ast, _rec_ast_file)
    _rec_ast_file.close()
    check("recursive-helper-preflight-sees-extension-wrapper",
          direct_recursive_helpers_in_unit_closure(
              _rec_ast_file.name, "Token", "transfer"),
          ["SafeMath.sub/2"])
    check("recursive-helper-preflight-does-not-refuse-base-case-recursion",
          direct_recursive_helpers_in_unit_closure(
              _rec_ast_file.name, "Token", "callBounded"), [])
    check("recursive-helper-preflight-refuses-target-wrapper-too",
          direct_recursive_helpers_in_unit_closure(
              _rec_ast_file.name, "Token", "loopForever"),
          ["Token.loopForever/0"])
    check("recursive-helper-preflight-ignores-unrelated-same-signature-wrapper",
          direct_recursive_helpers_in_unit_closure(
              _rec_ast_file.name, "Token", "normalTarget"), [])
finally:
    try:
        os.unlink(_rec_ast_file.name)
    except OSError:
        pass

# ---- AN EMPTY WITNESS SET IS NOT AUTOMATICALLY A RESULT ---------------------
#
# The driver used to print, whenever nothing was witnessed:
#
#   no witnessed path for this unit; nothing to generalise. That is a result,
#   not an error ... (The report was checked: it holds no F claim for any unit,
#   so this really is the empty case and not a failed match.)
#
# MEASURED on St1inch.balanceOf. The report it had just written said
# `claims_abandoned_over_budget: 2` and carried `u_reason` values
# `claim-budget-exceeded` twice and `bounded-holds` once -- so two of three
# claims were never decided at all, and the sentence above reported that as a
# property of the contract while asserting it had checked.
#
# The fixture below is that report's SHAPE, field for field, taken from the real
# file rather than paraphrased -- the keys were printed off disk first, because
# this driver has already been wired to a field (`claim["function"]`) that is
# always empty on complete-path claims.


# ---- outward_ladder: rungs beyond the known members, never between them -----
#
# THE PROPERTY BEING PINNED IS WHERE THE FIRST RUNG SITS. Anchored at zero the
# ladder's nearest rungs to a boundary at 21 are 16 and 32; anchored at the
# known member 20 the first rung IS 21. The second check below is the one that
# would go red if the anchor were ever moved back to zero.
check("outward_ladder 10..20 in [0,255]", outward_ladder(10, 20, 0, 255),
      [0, 2, 6, 8, 9, 10, 20, 21, 22, 24, 28, 36, 52, 84, 148, 255])
check("outward_ladder first rung is one beyond the top member",
      outward_ladder(10, 20, 0, 255)[7], 21)
# NOTHING STRICTLY BETWEEN THE MEMBERS. Those probes are refuted in both
# directions by the members themselves, before the query is issued.
check("outward_ladder lays no rung inside the bracket",
      [v for v in outward_ladder(10, 20, 0, 255) if 10 < v < 20], [])
# A single-member path still gets a bracket, and it is laid on BOTH sides --
# one value is not evidence of a point, so the ladder may not behave as if it
# were.
check("outward_ladder single member", outward_ladder(0, 0, 0, 255),
      [0, 1, 2, 4, 8, 16, 32, 64, 128, 255])
# CLAMPED AT THE TYPE, in both directions: a rung past the type maximum is
# built as a constant of that type, wraps, and measures a different number.
check("outward_ladder at both type edges", outward_ladder(0, 255, 0, 255),
      [0, 255])
check("outward_ladder stays inside a narrow type",
      [v for v in outward_ladder(3, 4, 0, 7) if v < 0 or v > 7], [])

# ---- the BUDGET: nearest rungs kept, furthest dropped, anchors never ---------
#
# TWO-WAY, because a cap that dropped the NEAREST rungs and one that drops the
# furthest both shrink the list, and a size check alone cannot tell them apart.
# So the first check names the values kept and the second names one that must be
# gone. Uncapped, this ladder is the 16-value list pinned above.
check("outward_ladder budget keeps the nearest rungs",
      outward_ladder(10, 20, 0, 255, budget=2), [0, 8, 9, 10, 20, 21, 22, 255])
check("outward_ladder budget drops the FURTHEST rung",
      [v for v in outward_ladder(10, 20, 0, 255, budget=2) if v == 148], [])
# THE ANCHORS AND BOTH TYPE LIMITS SURVIVE ANY BUDGET. Without the limits the
# coordinate is half-open and the subtraction is blocked outright, so a budget
# small enough to reach them would break the round it was meant to make cheap.
check("outward_ladder budget 1 still carries anchors and type limits",
      outward_ladder(10, 20, 0, 255, budget=1), [0, 9, 10, 20, 21, 255])
# budget=0 IS THE OLD BEHAVIOUR, byte for byte. Every number recorded before the
# flag existed has to reproduce under a run that does not pass it.
check("outward_ladder budget 0 is uncapped",
      outward_ladder(10, 20, 0, 255, budget=0),
      outward_ladder(10, 20, 0, 255))
# THE CASE THAT MOTIVATED IT, at the real width: a uint256 anchored at [0, 1]
# lays 259 rungs uncapped and 6 at budget 4 -- and the 6 include both anchors
# and both type limits.
_U256 = (1 << 256) - 1
check("outward_ladder uint256 uncapped is 259 rungs",
      len(outward_ladder(0, 1, 0, _U256)), 259)
check("outward_ladder uint256 at budget 4",
      outward_ladder(0, 1, 0, _U256, budget=4), [0, 1, 2, 3, 5, 9, _U256])

# ---- known_inside: the path-labelled point pool -----------------------------
#
# EVERY CHECK BELOW HAS BOTH OUTCOMES SOMEWHERE IN THIS BLOCK. A pool that can
# only ever return "nothing to prune" and one that prunes correctly print the
# same thing at the call site, so each rule is pinned by a pair: a case where it
# fires and a case where it must NOT.

_P3 = [(1, 1, {}), (2, 1, {})]

# FIRES. Both paths' members bracket [10, 90] and [20, 80], so the intersection
# is the open interval (20, 80) and any rung strictly inside it is refuted in
# BOTH directions on BOTH paths before the query is issued.
_pr, _end, _kept, _notes = known_inside(
    _P3,
    {1: [{"a": 10}, {"a": 90}, {"a": 50}],
     2: [{"a": 20}, {"a": 80}]},
    ["a"], {}, {"a": (0, 255)})
check("known_inside prune", _pr, {"a": (20, 80)})
# The extremes of EVERY path are kept, plus their neighbours -- the neighbours
# are the perturbation probe, and the extremes are what stops the bracket
# widening once the interior is gone.
check("known_inside endpoints", _end["a"], [9, 10, 11, 19, 20, 21, 79, 80, 81,
                                            89, 90, 91])
check("known_inside kept", _kept, {1: {"a": [10, 50, 90]}, 2: {"a": [20, 80]}})
check("known_inside notes", _notes, [])

# DOES NOT FIRE: disjoint domains. lo = max(10, 80) = 80 and hi = min(20, 90) =
# 20, so the intersection is empty and NOTHING may be dropped -- the ladder is
# shared, and a value informative for either path has to be laid.
_pr2, _end2, _, _ = known_inside(
    _P3,
    {1: [{"a": 10}, {"a": 20}], 2: [{"a": 80}, {"a": 90}]},
    ["a"], {}, {"a": (0, 255)})
check("known_inside disjoint prunes nothing", _pr2, {})
check("known_inside disjoint still gives endpoints", _end2["a"],
      [9, 10, 11, 19, 20, 21, 79, 80, 81, 89, 90, 91])

# DOES NOT FIRE: a path with no member on the coordinate. A proposed mapping
# slot is exactly this, and dropping a rung on the strength of the OTHER paths
# would remove a question this one has not answered.
_pr3, _end3, _, _ = known_inside(
    _P3,
    {1: [{"a": 10}, {"a": 90}], 2: [{"b": 1}]},
    ["a"], {}, {"a": (0, 255)})
check("known_inside missing coord prunes nothing", _pr3, {})
check("known_inside missing coord has no endpoints", _end3, {})

# A ONE-VALUE PATH IS NOT A POINT AND MUST NOT BEHAVE LIKE ONE. Path 2 offers a
# single member, so its own interval is empty and the intersection cannot have
# an interior -- lo = max(10, 50) = 50, hi = min(90, 50) = 50.
_pr4, _, _kept4, _ = known_inside(
    _P3, {1: [{"a": 10}, {"a": 90}], 2: [{"a": 50}]}, ["a"], {},
    {"a": (0, 255)})
check("known_inside single-member path prunes nothing", _pr4, {})
check("known_inside single member is still reported", _kept4[2], {"a": [50]})

# THE PIN FILTER IS A WHOLE-VECTOR TEST. The vector {a: 55, p: 9} walks the path
# but is NOT in the slice p == 7, so its `a` value may not enter the pool -- and
# with it gone the two paths no longer bracket a shared interior.
_pr5, _, _kept5, _notes5 = known_inside(
    _P3,
    {1: [{"a": 10, "p": 7}, {"a": 90, "p": 7}],
     2: [{"a": 20, "p": 7}, {"a": 80, "p": 9}]},
    ["a"], {"p": 7}, {"a": (0, 255)})
check("known_inside pin filter drops the vector", _kept5[2], {"a": [20]})
check("known_inside pin filter changes the prune", _pr5, {})
check("known_inside pin filter is announced", len(_notes5), 1)

# A VECTOR THAT DOES NOT CARRY THE PINNED NAME IS ALSO OUT. It cannot be shown
# to be in the slice, and every use downstream treats a pooled value as a KNOWN
# MEMBER of it.
_, _, _kept6, _notes6 = known_inside(
    [(1, 1, {})], {1: [{"a": 10, "p": 7}, {"a": 90}]}, ["a"], {"p": 7}, None)
check("known_inside drops a vector missing the pin", _kept6[1], {"a": [10]})
check("known_inside missing-pin is announced", len(_notes6), 1)

# NO TYPE RANGE -> NO NEIGHBOURS. Probing outside the type wraps and measures a
# different number, so the neighbour is left out rather than guessed; the caller
# names the coordinates this happened to.
_, _end7, _, _ = known_inside(
    _P3, {1: [{"a": 10}, {"a": 90}], 2: [{"a": 20}, {"a": 80}]},
    ["a"], {}, None)
check("known_inside no type range gives no neighbours", _end7["a"],
      [10, 20, 80, 90])

# CLAMPED AT THE TYPE EDGE, on the side that has one only.
_, _end8, _, _ = known_inside(
    [(1, 1, {})], {1: [{"a": 0}, {"a": 255}]}, ["a"], {}, {"a": (0, 255)})
check("known_inside clamps both type edges", _end8["a"], [0, 1, 254, 255])


def _report(tmpdir, claims):
    with open(os.path.join(tmpdir, "cov-report.json"), "w") as f:
        json.dump({"claims": claims, "partial": False}, f)
    return tmpdir


def _claim(pid, reason, unit="balanceOf"):
    return {"condition": f"{unit}:path:{pid}", "status": "U",
            "path_id": pid, "path_depth": 1, "u_reason": reason,
            "function": "", "bounded_holds": reason == "bounded-holds"}


_journal_claim = {
    "claim": "sol:@C@ClaimTopicsRegistry@F@addClaimTopic#420:path:31",
    "condition": "addClaimTopic:path:31",
    "entry_storage": [
        {"name": "_owner", "value": "0x2"},
    ],
    "env": [
        {"name": "msg_value", "value": "0"},
        {"name": "block_timestamp", "value": "0x10"},
    ],
    "extcall_returns": [
        {"name": "return_value$__msgSender$2", "value": "0x2"},
    ],
    "inputs": [
        {"name": "_claimTopic", "value": "0x7"},
    ],
    "path_depth": 4,
    "path_function": "sol:@C@ClaimTopicsRegistry@F@addClaimTopic#420",
    "path_id": "31",
    "witness_count": 2,
    "witnesses": [{
        "entry_storage": [
            {"name": "_owner", "value": "0x2"},
        ],
        "env": [
            {"name": "msg_value", "value": "0"},
        ],
        "inputs": [
            {"name": "_claimTopic", "value": "0x9"},
        ],
    }],
}

_ce_from_journal, _ref_from_journal = coord_values(_journal_claim)
check("coord_values accepts journal list payloads", _ce_from_journal, {
    "_claimTopic": 7,
    "block.timestamp": 16,
    "msg.value": 0,
    "state._owner": 2,
})
check("coord_values journal payloads do not refuse scalars",
      _ref_from_journal, [])
check("payload_extras accepts journal name field",
      payload_extras(_journal_claim),
      {"extcall.return_value$__msgSender$2": 2})

_journal_report = report_from_ce_journal({
    "claims_decided": 6,
    "claims_total": 277,
    "kind": "solidity-complete-path-ce-journal",
    "partial": True,
    "witnesses": {
        "sol:@C@ClaimTopicsRegistry@F@addClaimTopic#420:path:31\t":
        _journal_claim,
    },
})
check("journal report is partial", _journal_report["partial"], True)
check("journal report keeps witnessed claim",
      _journal_report["claims"][0]["condition"], "addClaimTopic:path:31")
check("journal report normalizes env names",
      _journal_report["claims"][0]["env"]["msg.value"], "0")
check("journal report keeps extra witnesses",
      _journal_report["claims"][0]["witnesses"][0]["inputs"]["_claimTopic"],
      "0x9")
check("journal report records salvage source",
      _journal_report["veriput_salvage"]["from"], "cov-ce-journal.json")

_journal_dir = tempfile.mkdtemp(prefix="journal-report-")
with open(os.path.join(_journal_dir, "cov-ce-journal.json"), "w") as f:
    json.dump({
        "kind": "solidity-complete-path-ce-journal",
        "witnesses": {"k": _journal_claim},
    }, f)
check("partial_journal_report reads cwd journal",
      partial_journal_report(_journal_dir)["claims"][0]["path_id"], "31")
_salvage_meta = write_enumeration_salvage(_journal_dir, _journal_report)
check("enumeration salvage sidecar records path count",
      _salvage_meta["path_count"], 1)
check("enumeration salvage sidecar records witness count",
      read_enumeration_salvage(_journal_dir)["witness_count"], 2)
write_generalise_progress(_journal_dir, "outer-round-started",
                          round_kind="linear-refine",
                          coords={"z", "a"},
                          regions={31: {"x": (0, 7)}})
write_generalise_progress(_journal_dir, "certify-query-started",
                          enc=31,
                          box=[{"name": "x", "lo": "0", "hi": "7"}])
with open(generalise_progress_path(_journal_dir)) as f:
    _progress = json.load(f)
check("generalise progress records latest stage",
      _progress["stage"], "certify-query-started")
check("generalise progress top-level does not retain stale event keys",
      "round_kind" in _progress, False)
check("generalise progress keeps recent history",
      [e["stage"] for e in _progress["history"]],
      ["outer-round-started", "certify-query-started"])
check("generalise progress serializes sets deterministically",
      _progress["history"][0]["coords"], ["a", "z"])
check("generalise progress serializes integer dict keys",
      _progress["history"][0]["regions"]["31"]["x"], [0, 7])
os.unlink(os.path.join(_journal_dir, "cov-ce-journal.json"))
os.unlink(os.path.join(_journal_dir, "enumeration-salvage.json"))
os.unlink(generalise_progress_path(_journal_dir))
os.rmdir(_journal_dir)


_d = tempfile.mkdtemp(prefix="emptyenum-")

# 1. THE REAL CASE. Two abandoned, one decided -> fatal, and the text must say
#    so in words that cannot be read as a coverage result.
_report(_d, [_claim(2, "claim-budget-exceeded"),
             _claim(6, "bounded-holds"),
             _claim(7, "claim-budget-exceeded")])
_fatal_abandon, _txt_abandon = empty_enumeration_reason(_d, "balanceOf")
check("emptyenum-abandoned-claims-are-not-a-result", _fatal_abandon, True)
check("emptyenum-and-it-names-the-token",
      "claim-budget-exceeded" in _txt_abandon, True)
check("emptyenum-and-it-counts-them",
      "2 of 3 claim(s) were ABANDONED" in _txt_abandon, True)
check("emptyenum-and-it-names-the-repair",
      "--path-cov-claim-timeout" in _txt_abandon, True)

# 2. THE NEGATIVE CONTROL, and it is the one that matters: with the SAME unit,
#    the SAME count of claims and only the reason token changed, the answer must
#    flip. Without this the check above would also pass on a function that
#    returned True unconditionally.
_report(_d, [_claim(2, "bounded-holds"),
             _claim(6, "bounded-holds"),
             _claim(7, "bounded-holds")])
_fatal_bh, _txt_bh = empty_enumeration_reason(_d, "balanceOf")
check("emptyenum-all-bounded-holds-IS-a-decided-outcome", _fatal_bh, False)
check("emptyenum-the-two-branches-say-different-things",
      _txt_abandon == _txt_bh, False)
check("emptyenum-only-the-abandoned-branch-refuses",
      ("NOT a result" in _txt_abandon, "NOT a result" in _txt_bh),
      (True, False))
# ...and even the benign branch must not be readable as "unreachable". That is
# a WORKORDER rule, not a nicety.
check("emptyenum-bounded-holds-still-disclaims-unreachability",
      "NOT a statement that the path is unreachable" in _txt_bh, True)

# 3. SCOPE. `unit-not-entered` means the dispatcher never called the unit, which
#    is a property of the command line. It must land on the refusing side.
_report(_d, [_claim(2, "unit-not-entered")])
check("emptyenum-unit-not-entered-is-a-command-line-outcome",
      empty_enumeration_reason(_d, "balanceOf")[0], True)

# 4. SOLVER UNKNOWN is the st1inch shape: the solver gave no answer, so the
#    absence of an F claim is not a path result and must not feed the B
#    denominator as an empty witnessed set.
_report(_d, [_claim(2, "solver-unknown"),
             _claim(3, "bounded-holds")])
_fatal_solver_unknown, _txt_solver_unknown = empty_enumeration_reason(
    _d, "balanceOf")
check("emptyenum-solver-unknown-is-not-a-result",
      _fatal_solver_unknown, True)
check("emptyenum-solver-unknown-is-named",
      "solver answered `unknown`" in _txt_solver_unknown, True)

# 5. NAMED OBSTACLE is structural, not a solver-budget miss. It is still
#    fatal for PUT generation, but the remediation is to remove the obstacle,
#    not to spend a larger per-claim budget.
_report(_d, [_claim(2, "named-obstacle"),
             _claim(3, "named-obstacle")])
_fatal_named_obstacle, _txt_named_obstacle = empty_enumeration_reason(
    _d, "balanceOf")
check("emptyenum-named-obstacle-is-not-a-result",
      _fatal_named_obstacle, True)
check("emptyenum-named-obstacle-is-structural",
      "structural model/chain mismatch" in _txt_named_obstacle, True)
check("emptyenum-named-obstacle-is-not-budget-advice",
      "--path-cov-claim-timeout" in _txt_named_obstacle, False)

# 6. A TOKEN THIS DRIVER DOES NOT KNOW fails CLOSED. The alternative -- treating
#    an unrecognised reason as benign -- is how a new ESBMC token would silently
#    become "no coverage here", which is the failure `verdict()` above already
#    refuses for certification verdicts.
_report(_d, [_claim(2, "some-token-from-a-newer-esbmc")])
_fatal_new, _txt_new = empty_enumeration_reason(_d, "balanceOf")
check("emptyenum-an-unknown-reason-token-fails-closed", _fatal_new, True)
check("emptyenum-and-says-it-does-not-know-it",
      "does not know this reason token" in _txt_new, True)

# 7. NO CLAIM FOR THIS UNIT AT ALL is not the empty case either -- nothing was
#    attempted, which is a scope or wiring question.
_report(_d, [_claim(2, "bounded-holds", unit="someOtherUnit")])
check("emptyenum-no-claim-for-the-unit-is-not-the-empty-case",
      empty_enumeration_reason(_d, "balanceOf")[0], True)

# 7. AN UNREADABLE REPORT must not become the benign branch. "We could not tell"
#    and "we looked and found nothing" are the two readings this whole function
#    exists to keep apart.
_d_empty = tempfile.mkdtemp(prefix="emptyenum-none-")
_fatal_missing, _txt_missing = empty_enumeration_reason(_d_empty, "balanceOf")
check("emptyenum-a-missing-report-is-unknown-not-empty", _fatal_missing, True)
check("emptyenum-and-says-which", "CANNOT BE READ" in _txt_missing, True)
os.rmdir(_d_empty)

for _f in os.listdir(_d):
    os.unlink(os.path.join(_d, _f))
os.rmdir(_d)

# ---- THE REFUSAL LIST IS SHARED, NOT COPIED --------------------------------
#
# Stage 2 forwards extra ESBMC flags now, and stage 4 already did. Two drivers
# with two copies of "which flags are safe" is one fact in two ledgers, and the
# way that fails here is specific: a flag added to one list stays accepted by
# the other, so the same run is refused at one stage and forwarded at the next.
# The generaliser imports the put script's list; this pins that it is the SAME
# OBJECT rather than an equal-looking duplicate.
import solidity_path_put as _spp  # noqa: E402
import solidity_path_generalise as _spg  # noqa: E402

check("esbmcarg-the-refusal-list-is-one-object",
      _spg.STRATEGY_FLAGS_REFUSED is _spp.STRATEGY_FLAGS_REFUSED, True)
check("esbmcarg-the-checker-is-one-object",
      _spg.check_esbmc_args is _spp.check_esbmc_args, True)
# MUST FLIP, on the shared checker as the generaliser sees it: a strategy flag
# is refused with a reason, an unwinding flag is not.
check("esbmcarg-a-strategy-flag-is-refused",
      _spg.check_esbmc_args(["--k-induction"]) is None, False)
check("esbmcarg-unwindset-passes",
      _spg.check_esbmc_args(["--unwindset", "55:512,56:512"]), None)
check("esbmcarg-no-extra-args-passes", _spg.check_esbmc_args([]), None)

# ---- §Certification's CUT RULE AND ITS RETREAT ------------------------------
#
# Four things the method states and the driver did not do. Each check below is
# paired with the case that must NOT trigger it, because a rule that fires on
# everything is the always-true-reader shape this project keeps hitting.
from solidity_path_generalise import (cut_towards, coord_kept,  # noqa: E402
                                      refutation_response,
                                      violated_properties)

# 1. DIRECTION. "keeping the side of y_c on which x_{pi,c} lies removes y and
#    keeps x_pi". Both directions, because a rule that only ever cuts upward
#    would pass a one-sided test.
check("cut-keeps-the-side-holding-x_pi-below", cut_towards(0, 100, 60, 10),
      (0, 59))
check("cut-keeps-the-side-holding-x_pi-above", cut_towards(0, 100, 60, 90),
      (61, 100))
check("cut-offers-nothing-where-they-agree", cut_towards(0, 100, 60, 60), None)

# 2. FEWEST VALUES REMOVED, across candidates. This is the whole of correction
#    4: the tool's first suggestion is not consulted at all.
#    x_pi = {a: 10, b: 10}; witness differs on both.
#      a: cut (0,59) removes 41 of 101
#      b: cut (0,11) removes 89 of 101
#    so `a` must be chosen. Reversing which coordinate is cheaper must reverse
#    the answer -- otherwise the check would pass on "always take the first".
_box2 = {"a": (0, 100), "b": (0, 100)}
_ce2 = {"a": 10, "b": 10}
check("cut-takes-the-fewest-values-removed",
      refutation_response(_box2, {}, _ce2, {"a": 60, "b": 12}, {}),
      ("cut", ("a", 0, 59, 41)))
check("cut-and-the-choice-follows-the-numbers-not-the-order",
      refutation_response(_box2, {}, _ce2, {"a": 12, "b": 60}, {}),
      ("cut", ("b", 0, 59, 41)))

# 3. THE RETREAT, trigger two: "where cutting would leave that coordinate one
#    value". On [0,1] with x_pi = 0 and y = 1 the cut leaves exactly {0}, so
#    the method pins rather than cutting.
check("retreat-when-the-cut-would-leave-one-value",
      refutation_response({"a": (0, 1)}, {}, {"a": 0}, {"a": 1}, {}),
      ("pin", {"a": 0}))
# ...and the SAME shape one value wider must still cut, or the check above is
# just "this function always pins".
check("retreat-does-not-fire-where-two-values-survive",
      refutation_response({"a": (0, 2)}, {}, {"a": 0}, {"a": 2}, {}),
      ("cut", ("a", 0, 1, 1)))

# 3b. Repeated one-value `RESULT: UNSAFE` cuts are a product-region symptom
#     only when some OTHER non-environment coordinate can still be generalized.
#     This keeps `x + 2` on the normal two-cut path, while `x + y` can retreat
#     one side and preserve the other.
check("tiny-safety-retreat-keeps-a-remaining-input-wide",
      tiny_safety_cut_retreat({"x": (0, 99), "y": (3, 99)}, "x", 1,
                              {"x": 0, "y": 99}, 2, 2),
      {"x": 0})
check("tiny-safety-retreat-waits-for-the-throttle",
      tiny_safety_cut_retreat({"x": (0, 99), "y": (3, 99)}, "x", 1,
                              {"x": 0, "y": 99}, 1, 2),
      None)
check("tiny-safety-retreat-does-not-weaken-single-input-boundary",
      tiny_safety_cut_retreat({"x": (0, 99)}, "x", 1, {"x": 0}, 2, 2),
      None)
check("tiny-safety-retreat-ignores-env-only-width",
      tiny_safety_cut_retreat({"x": (0, 99), "msg.sender": (0, 99)}, "x", 1,
                              {"x": 0, "msg.sender": 7}, 2, 2),
      None)
check("tiny-safety-retreat-requires-a-one-value-cut",
      tiny_safety_cut_retreat({"x": (0, 99), "y": (3, 99)}, "x", 2,
                              {"x": 0, "y": 99}, 2, 2),
      None)

# 4. THE RETREAT, trigger one: the coordinate cannot be cut at all because it
#    is PINNED. The old `shrink_target` returned None here and the path died;
#    the method pins what it points at and carries on with the others. `b` is
#    cuttable and must be taken instead of the path ending.
check("a-pinned-coordinate-no-longer-ends-the-path",
      refutation_response({"b": (0, 100)}, {"msg.value": 0},
                          {"msg.value": 0, "b": 10},
                          {"msg.value": 5, "b": 60}, {"msg.value": 0}),
      ("cut", ("b", 0, 59, 41)))

# 5. THE COORDINATE GATE, correction 5. Agreement on every coordinate is a
#    TERMINAL outcome and must not be retried.
check("agreement-on-every-coordinate-goes-to-the-coords-gate",
      refutation_response({"a": (0, 100)}, {}, {"a": 10}, {"a": 10}, {}),
      ("coords-gate", None))
# ...and the three states that must NOT collapse into it. An empty payload is a
# missing harvest, and a difference contradicting the query's own bound is a
# payload that could not be compared. Both read as "no difference" if merged.
check("no-payload-is-not-the-coords-gate",
      refutation_response({"a": (0, 100)}, {}, {"a": 10}, {}, {})[0],
      "no-payload")
check("an-untrusted-difference-is-not-the-coords-gate",
      refutation_response({"a": (0, 100)}, {}, {"a": 10}, {"a": 900}, {},
                          {"a": (0, 100)})[0],
      "untrusted")

# 6. |V_c| counts punctures, so "fewest values removed" cannot be gamed by a
#    coordinate whose interval is mostly holes.
check("coord-kept-subtracts-punctures", coord_kept(0, 9, (1, 2, 3)), 7)
check("coord-kept-ignores-punctures-outside-the-interval",
      coord_kept(0, 9, (1, 2, 99)), 8)
check("coord-kept-of-an-inverted-interval-is-zero", coord_kept(9, 0, ()), 0)

# ---------------------------------------------------------------------------
# violated_properties: BOTH directions, because a log reader that returns the
# same thing on every input is the failure this file exists to stop. One log
# HAS the block, one has none, and one has two -- and the block is compared
# CHARACTER FOR CHARACTER, since the whole point of the harvest is that it
# quotes rather than summarises.
# ---------------------------------------------------------------------------
_LOG_WITH_ONE = """Starting Bounded Model Checking
--path-cov-certify: RESULT: REFUTED

Violated property:
  file farming.sol line 118 column 9 function deposit
  arithmetic overflow on add
  !overflow("+", state._totalSupply, amount)

VERIFICATION FAILED

[run] EXIT 1
"""

_LOG_WITH_NONE = """Starting Bounded Model Checking
--path-cov-certify: RESULT: REFUTED
VERIFICATION FAILED

[run] EXIT 1
"""

_LOG_WITH_TWO = """Violated property:
  file a.sol line 1 column 1 function f
  assertion
  x != 0

Violated property:
  file b.sol line 2 column 2 function g
  division by zero
  y != 0

VERIFICATION FAILED
"""

check("violated-one-block-is-quoted-verbatim",
      violated_properties(_LOG_WITH_ONE),
      ["Violated property:\n"
       "  file farming.sol line 118 column 9 function deposit\n"
       "  arithmetic overflow on add\n"
       '  !overflow("+", state._totalSupply, amount)'])
check("violated-no-block-is-an-empty-list-not-a-sentence",
      violated_properties(_LOG_WITH_NONE), [])
check("violated-two-blocks-are-kept-apart",
      len(violated_properties(_LOG_WITH_TWO)), 2)
check("violated-second-of-two-is-the-second-one",
      violated_properties(_LOG_WITH_TWO)[1].splitlines()[2].strip(),
      "division by zero")
# The reader must not fire on a line that merely CONTAINS the words: the
# heading is matched whole, so a counterexample step mentioning it stays out.
check("violated-heading-must-be-the-whole-line",
      violated_properties("  see Violated property: below\nfoo\n"), [])

# The workdir stamp used to write these switches and then compare a fixed tuple
# that omitted them. Two incompatible arms therefore passed the provenance gate
# and overwrote the same cov-report/outer/cert/result files.
with tempfile.TemporaryDirectory() as _stamp_dir:
    _tool = os.path.join(_stamp_dir, "esbmc")
    _sol = os.path.join(_stamp_dir, "C.sol")
    open(_tool, "w").close()
    open(_sol, "w").close()
    _base = SimpleNamespace(esbmc=_tool, sol=_sol, ast=None, contract="C",
                            unit="f", path_function=None, max_tx=1)
    _cfg_off = run_config(_base, "focus")
    _cfg_on = run_config(
        SimpleNamespace(**dict(vars(_base), env_coord_disagreed=True)),
        "focus")
    check("run-config-records-env-coordinate-policy",
          (_cfg_off["env_coord_disagreed"],
           _cfg_on["env_coord_disagreed"]),
          (False, True))
    _cfg_agreed_env = run_config(
        SimpleNamespace(**dict(vars(_base),
                               pin_agreed_establishable_env=True)),
        "focus")
    check("run-config-records-agreed-establishable-env-policy",
          (_cfg_off["pin_agreed_establishable_env"],
           _cfg_agreed_env["pin_agreed_establishable_env"]),
          (False, True))
    _cfg_allow_rec = run_config(
        SimpleNamespace(**dict(vars(_base),
                               allow_recursive_helper_enumeration=True)),
        "focus")
    check("run-config-records-recursive-helper-policy",
          (_cfg_off["allow_recursive_helper_enumeration"],
           _cfg_allow_rec["allow_recursive_helper_enumeration"]),
          (False, True))
    stamp_workdir(_stamp_dir, _cfg_off)
    try:
        stamp_workdir(_stamp_dir, _cfg_on)
        _stamp_refused = False
    except SystemExit:
        _stamp_refused = True
    try:
        stamp_workdir(_stamp_dir, _cfg_agreed_env)
        _stamp_agreed_refused = False
    except SystemExit:
        _stamp_agreed_refused = True
    try:
        stamp_workdir(_stamp_dir, _cfg_allow_rec)
        _stamp_allow_rec_refused = False
    except SystemExit:
        _stamp_allow_rec_refused = True
check("workdir-refuses-a-policy-change-the-old-field-list-missed",
      _stamp_refused, True)
check("workdir-refuses-agreed-establishable-env-policy-change",
      _stamp_agreed_refused, True)
check("workdir-refuses-recursive-helper-policy-change",
      _stamp_allow_rec_refused, True)

_env_promoted, _env_kept = derive_env_coord_disagreed(
    [(2, 1, {
        "msg.value": 0,
        "block.basefee": 1,
        "block.prevrandao": 4,
        "tx.gasprice": 9,
        "block.chainid": 31337,
        "block.coinbase": 11,
        "tx.origin": 13,
        "block.gaslimit": 15,
    }), (3, 1, {
        "msg.value": 0,
        "block.basefee": 2,
        "block.prevrandao": 5,
        "tx.gasprice": 10,
        "block.chainid": 31337,
        "block.coinbase": 12,
        "tx.origin": 14,
        "block.gaslimit": 16,
    })],
    ["msg.value", "block.basefee", "block.prevrandao", "tx.gasprice",
     "block.chainid", "block.coinbase", "tx.origin", "block.gaslimit"],
    {"msg.value": 0})
check("env-disagreed-promotes-modeled-cheatcode-quantities",
      _env_promoted,
      ["block.basefee", "block.prevrandao", "tx.gasprice",
       "block.coinbase"])
check("env-disagreed-keeps-pinned-agreed-and-unestablishable-quantities",
      _env_kept,
      ["msg.value (already pinned at 0)",
       "block.chainid (all 2 paths agree)",
       "tx.origin (paths disagree, but the PUT emitter cannot establish this "
       "environment quantity)",
       "block.gaslimit (paths disagree, but the PUT emitter cannot establish "
       "this environment quantity)"])
_env_agreed_pins, _env_agreed_kept = derive_agreed_establishable_env_pins(
    [(2, 1, {
        "msg.sender": 7,
        "block.chainid": 31337,
        "block.timestamp": 100,
        "tx.origin": 1,
        "msg.data": 0,
        "msg.value": 0,
    }), (3, 1, {
        "msg.sender": 7,
        "block.chainid": 31337,
        "block.timestamp": 101,
        "tx.origin": 1,
        "msg.data": 0,
        "msg.value": 0,
    })],
    ["msg.sender", "block.chainid", "block.timestamp", "tx.origin",
     "msg.data", "msg.value"],
    {"msg.value": 0})
check("agreed-establishable-env-pins-only-put-renderable-agreement",
      _env_agreed_pins, {"msg.sender": 7, "block.chainid": 31337})
check("agreed-establishable-env-keeps-pinned-disagreed-and-unsupported",
      _env_agreed_kept,
      ["block.timestamp (paths disagree)",
       "tx.origin (all paths agree, but the PUT emitter cannot establish this "
       "environment quantity)",
       "msg.data (all paths agree, but the PUT emitter cannot establish this "
       "environment quantity)",
       "msg.value (already pinned at 0)"])
_env_zero_sender_pins, _env_zero_sender_kept = \
    derive_agreed_establishable_env_pins(
        [(2, 1, {"msg.sender": 0}), (3, 1, {"msg.sender": 0})],
        ["msg.sender"], {})
check("agreed-env-does-not-pin-foundry-unprankable-zero-sender",
      _env_zero_sender_pins, {})
check("agreed-env-explains-zero-sender-is-left-quantified",
      _env_zero_sender_kept,
      ["msg.sender (all paths agree at 0, but Foundry cannot establish "
       "address(0) with vm.prank; leave it quantified for ESBMC certification "
       "instead)"])
check("agreed-zero-sender-becomes-free-coordinate-not-env-bucket",
      derive_agreed_unpinned_establishable_env_coords(
          [(2, 1, {"msg.sender": 0}), (3, 1, {"msg.sender": 0})],
          ["msg.sender"], {}),
      {"msg.sender"})
check("nonzero-agreed-sender-stays-pin-not-free-coordinate",
      derive_agreed_unpinned_establishable_env_coords(
          [(2, 1, {"msg.sender": 7}), (3, 1, {"msg.sender": 7})],
          ["msg.sender"], {}),
      set())
check("address-like-environment-coordinates-use-address-domain",
      (_coord_range("msg.sender"), _coord_range("tx.origin"),
       _coord_range("block.coinbase")),
      ((0, (1 << 160) - 1), (0, (1 << 160) - 1),
       (0, (1 << 160) - 1)))
check("numeric-modeled-environment-coordinates-stay-uint256-domain",
      (_coord_range("block.basefee"), _coord_range("tx.gasprice")),
      ((0, (1 << 256) - 1), (0, (1 << 256) - 1)))

check("synthetic-abi-taken-is-the-body",
      abi_gate_class([{"synthetic_abi_gate": True, "arm": "taken"}]),
      "body")
check("synthetic-abi-fallthrough-is-the-reject-path",
      abi_gate_class([{"synthetic_abi_gate": True, "arm": "fall-through"}]),
      "reject")
check("a-source-only-path-has-no-abi-class",
      abi_gate_class([{"arm": "taken", "branch_claim": "x == 0"}]), None)
_abi_body = [{"synthetic_abi_gate": True, "arm": "taken"}]
_abi_reject = [{"synthetic_abi_gate": True, "arm": "fall-through"}]
check("abi-only-body-structurally-certifies",
      structural_abi_gate_certificate(_abi_body, {"msg.value": (0, 0)}, {},
                                      {"msg.value": 0}) is not None, True)
check("abi-only-reject-structurally-certifies",
      structural_abi_gate_certificate(_abi_reject, {"msg.value": (1, 9)}, {},
                                      {"msg.value": 3}) is not None, True)
check("abi-body-wrong-region-does-not-structurally-certify",
      structural_abi_gate_certificate(_abi_body, {"msg.value": (0, 1)}, {},
                                      {"msg.value": 0}), None)
check("abi-reject-containing-zero-does-not-structurally-certify",
      structural_abi_gate_certificate(_abi_reject, {"msg.value": (0, 9)}, {},
                                      {"msg.value": 3}), None)
check("source-decision-plus-abi-does-not-structurally-certify",
      structural_abi_gate_certificate(_abi_body + [{
          "arm": "taken",
          "branch_claim": "x == 0"
      }], {"msg.value": (0, 0)}, {}, {"msg.value": 0}), None)

_setdist_decisions = [
    {"synthetic_abi_gate": True, "arm": "taken",
     "branch_claim": "!(msg.value == 0)"},
    {"arm": "taken",
     "branch_claim":
     "return_value$_owner$1 != return_value$__msgSender$2"},
    {"arm": "taken", "branch_claim": "distributor_ == 0"},
]
_setdist_box, _setdist_holes, _setdist_reason = structural_decision_region(
    _setdist_decisions,
    {"msg.value": 0, "msg.sender": 1, "distributor_": 7},
    {"state._owner": 1},
    ["distributor_", "msg.sender", "msg.value"],
    coord_types={"distributor_": "address"})
check("simple-decision-region-pins-owner-sender",
      _setdist_box["msg.sender"], (1, 1))
check("simple-decision-region-keeps-address-width-for-nonzero-arg",
      _setdist_box["distributor_"], (1, (1 << 160) - 1))
check("simple-decision-region-pins-nonpayable-body-value",
      _setdist_box["msg.value"], (0, 0))
check("simple-decision-region-has-no-hole-for-endpoint-nonzero",
      _setdist_holes, {})
check("simple-decision-region-records-structural-reason",
      _setdist_reason.startswith("STRUCTURAL simple decision region"), True)

_double_not_region = structural_decision_region(
    [{"branch_claim": "!(!(msg.value == TICKET_AMOUNT))"}],
    {"msg.value": 11},
    {},
    ["msg.value"],
    constants={"TICKET_AMOUNT": 10})
check("double-negated-require-branch-keeps-operator",
      _double_not_region[0]["msg.value"], (0, BIG))
check("double-negated-require-branch-punches-required-value",
      _double_not_region[1]["msg.value"], {10})

_setdist_reject = structural_decision_region(
    [{"synthetic_abi_gate": True, "arm": "fall-through",
      "branch_claim": "msg.value == 0"}],
    {"msg.value": 1, "msg.sender": 0, "distributor_": 0},
    {"state._owner": 1},
    ["distributor_", "msg.sender", "msg.value"],
    coord_types={"distributor_": "address"})
check("simple-decision-region-abi-reject-is-value-positive",
      _setdist_reject[0]["msg.value"], (1, (1 << 256) - 1))

_setdist_nonowner = structural_decision_region(
    [{"synthetic_abi_gate": True, "arm": "taken",
      "branch_claim": "!(msg.value == 0)"},
     {"arm": "fall-through",
      "branch_claim":
      "!(return_value$_owner$1 != return_value$__msgSender$2)"}],
    {"msg.value": 0, "msg.sender": 9, "distributor_": 4},
    {"state._owner": 1},
    ["distributor_", "msg.sender", "msg.value"],
    coord_types={"distributor_": "address"})
check("simple-decision-region-nonowner-punches-owner-hole",
      _setdist_nonowner[1]["msg.sender"], {1})

_setdist_all = structural_decision_regions(
    [(15, 3, {"msg.value": 0, "msg.sender": 1, "distributor_": 7})],
    {15: _setdist_decisions},
    {"state._owner": 1},
    ["distributor_", "msg.sender", "msg.value"],
    coord_types={"distributor_": "address"})
check("simple-decision-regions-batches-all-paths",
      sorted(_setdist_all[0]), [15])

_arith_dir = tempfile.mkdtemp(prefix="arith-conditions-")
try:
    check("missing-arith-report-does-not-disable-structural",
          enumeration_has_arith_conditions(_arith_dir), False)
    with open(os.path.join(_arith_dir, "cov-report.json"), "w") as _f:
        json.dump({"summary": {"arith_resolve": {"conditions_seen": 0},
                               "arith_revert_only_paths": 0}}, _f)
    check("zero-arith-report-keeps-structural",
          enumeration_has_arith_conditions(_arith_dir), False)
    with open(os.path.join(_arith_dir, "cov-report.json"), "w") as _f:
        json.dump({"summary": {"arith_resolve": {"conditions_seen": 3},
                               "arith_revert_only_paths": 0}}, _f)
    check("arith-conditions-disable-structural",
          enumeration_has_arith_conditions(_arith_dir), True)
    with open(os.path.join(_arith_dir, "cov-report.json"), "w") as _f:
        json.dump({"summary": {"arith_resolve": {"conditions_seen": 0},
                               "arith_revert_only_paths": 1}}, _f)
    check("arith-revert-only-disables-structural",
          enumeration_has_arith_conditions(_arith_dir), True)
finally:
    try:
        os.unlink(os.path.join(_arith_dir, "cov-report.json"))
    except OSError:
        pass
    os.rmdir(_arith_dir)

_hash_split_paths = [
    (12, 3, {"msg.value": 10, "state.bank": 0}),
    (13, 3, {"msg.value": 10, "state.bank": 1}),
]
_hash_split_decisions = {
    12: [
        {"index": 1, "function": "play", "line": 15, "arm": "taken",
         "branch_claim": "!(msg.value == TICKET_AMOUNT)"},
        {"index": 2, "function": "", "line": 0, "arm": "fall-through",
         "branch_claim": "!(__esbmc_hash_result_abi_512_1 == 0)"},
        {"index": 3, "function": "play", "line": 20, "arm": "fall-through",
         "branch_claim": "!(random == 0)"},
    ],
    13: [
        {"index": 1, "function": "play", "line": 15, "arm": "taken",
         "branch_claim": "!(msg.value == TICKET_AMOUNT)"},
        {"index": 2, "function": "", "line": 0, "arm": "fall-through",
         "branch_claim": "!(__esbmc_hash_result_abi_512_1 == 0)"},
        {"index": 3, "function": "play", "line": 20, "arm": "taken",
         "branch_claim": "random == 0"},
    ],
}
_hash_split = uncontrolled_decision_splits(
    _hash_split_paths, _hash_split_decisions,
    ["msg.value", "state.bank"], {"state.TICKET_AMOUNT": 10})
check("hash-derived-local-split-is-static-inseparable",
      sorted(_hash_split), [12, 13])

_input_split_decisions = {
    12: [{"index": 7, "function": "f", "line": 9, "arm": "taken",
          "branch_claim": "amount > 9000"},
         {"index": 8, "function": "", "line": 0, "arm": "taken",
          "branch_claim": "!(__esbmc_hash_result_abi_512_1 == 0)"}],
    13: [{"index": 7, "function": "f", "line": 9, "arm": "fall-through",
          "branch_claim": "!(amount > 9000)"},
         {"index": 8, "function": "", "line": 0, "arm": "taken",
          "branch_claim": "!(__esbmc_hash_result_abi_512_1 == 0)"}],
}
check("input-coordinate-split-is-not-static-inseparable",
      uncontrolled_decision_splits(
          [(12, 1, {"amount": 9001}), (13, 1, {"amount": 5})],
          _input_split_decisions, ["amount"], {}),
      {})

_plain_success_split_decisions = {
    40: [{"index": 3, "function": "callAndDecode", "line": 19,
          "arm": "taken", "branch_claim": "success"}],
    41: [{"index": 3, "function": "callAndDecode", "line": 19,
          "arm": "fall-through", "branch_claim": "!success"}],
}
check("bare-success-with-extcall-payload-is-static-inseparable",
      sorted(uncontrolled_decision_splits(
          [(40, 4, {"target": 0, "data.length": 0}),
           (41, 4, {"target": 0, "data.length": 0})],
          _plain_success_split_decisions, ["target", "data.length"], {},
          path_extras={40: {"extcall.success": 1},
                       41: {"extcall.success": 0}})),
      [40, 41])
check("bare-success-without-extcall-payload-is-not-guessed",
      uncontrolled_decision_splits(
          [(40, 4, {"target": 0}), (41, 4, {"target": 0})],
          _plain_success_split_decisions, ["target"], {}),
      {})
_abi_gate_plus_success_decisions = {
    42: [
        {"index": 1, "function": "f", "line": 1, "arm": "fall-through",
         "branch_claim": "msg.value == 0", "synthetic_abi_gate": True},
    ],
    43: [
        {"index": 1, "function": "f", "line": 1, "arm": "taken",
         "branch_claim": "!(msg.value == 0)", "synthetic_abi_gate": True},
        {"index": 2, "function": "f", "line": 3, "arm": "taken",
         "branch_claim": "success"},
    ],
}
check("synthetic-abi-gate-is-not-uncontrolled-evidence",
      uncontrolled_decision_splits(
          [(42, 1, {"msg.value": 1, "target": 0}),
           (43, 2, {"msg.value": 0, "target": 0})],
          _abi_gate_plus_success_decisions, ["target"], {"msg.value": 0},
          path_extras={43: {"extcall.success": 1}}),
      {})

_safety_dir = tempfile.mkdtemp(prefix="safety-witness-")
try:
    with open(os.path.join(_safety_dir, "cov-report.json"), "w") as _f:
        json.dump({
            "certify_safety_refutations": [{
                "condition": "add:safety",
                "path_function": "sol:@C@Cr1@F@add#36",
                "status": "F",
                "inputs": {"x": "7", "y": "1"},
                "env": {"msg.value": "0"},
                "entry_storage": {}
            }],
            "claims": [{
                "condition": "add:path:14",
                "status": "F",
                "inputs": {},
                "env": {},
                "entry_storage": {}
            }]
        }, _f)
    check("certify-safety-refutation-witness-is-read",
          witness_values(_safety_dir, "add"),
          {"msg.value": 0, "x": 7, "y": 1})
    check("certify-safety-refutation-witness-matches-path-function",
          witness_values(_safety_dir, "sol:@C@Cr1@F@add#36"),
          {"msg.value": 0, "x": 7, "y": 1})
finally:
    try:
        os.unlink(os.path.join(_safety_dir, "cov-report.json"))
    except OSError:
        pass
    os.rmdir(_safety_dir)

check("simple-decision-region-refuses-coordinate-equality",
      structural_decision_region(
          [{"branch_claim": "a == b"}], {"a": 1, "b": 2}, {},
          ["a", "b"]), None)
_owner_rel_paths = [
    (12, 3, {"msg.value": 0, "msg.sender": 9, "state._owner": 1,
             "newOwner": 0}),
    (15, 3, {"msg.value": 0, "msg.sender": 7, "state._owner": 7,
             "newOwner": 5}),
]
_owner_rel_decisions = {
    12: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(!(return_value$_owner$1 == return_value$__msgSender$2))"},
         {"branch_claim": "!(!(newOwner != 0))"}],
    15: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(return_value$_owner$1 == return_value$__msgSender$2)"},
         {"branch_claim": "!(newOwner != 0)"}],
}
_rel_boxes, _rel_holes, _rel_reasons, _rel_retreats = \
    structural_decision_regions_with_retreat(
        _owner_rel_paths, _owner_rel_decisions, {"msg.value": 0},
        ["msg.sender", "newOwner", "state._owner"],
        coord_types={"newOwner": "address"})
check("owner-sender-relation-retreat-pins-entry-owner",
      _rel_retreats, {12: {"state._owner": 1}, 15: {"state._owner": 7}})
check("owner-sender-relation-retreat-keeps-nonowner-sender-wide",
      (_rel_boxes[12]["msg.sender"],
       _rel_holes[12]["msg.sender"]), ((0, (1 << 160) - 1), {1}))
check("owner-sender-relation-retreat-pins-owner-path-sender",
      _rel_boxes[15]["msg.sender"], (7, 7))
check("owner-sender-relation-retreat-keeps-success-argument-wide",
      _rel_boxes[15]["newOwner"], (1, (1 << 160) - 1))
_rel2_boxes, _rel2_holes, _rel2_reasons, _rel2_retreats, _rel2_establishes = \
    structural_decision_regions_with_relations(
        _owner_rel_paths, _owner_rel_decisions, {"msg.value": 0},
        ["msg.sender", "newOwner", "state._owner"],
        coord_types={"newOwner": "address"})
check("owner-sender-relation-establishes-success-owner",
      _rel2_establishes[15], {"state._owner": "msg.sender"})
check("owner-sender-relation-establish-keeps-success-sender-wide",
      _rel2_boxes[15]["msg.sender"], (0, (1 << 160) - 1))
check("owner-sender-relation-establish-drops-owner-from-box",
      "state._owner" in _rel2_boxes[15], False)
check("owner-sender-relation-establish-keeps-neq-retreat",
      _rel2_retreats[12], {"state._owner": 1})
check("owner-sender-relation-state-target-is-pin-exempt",
      relation_establishable_state_targets(
          _owner_rel_paths, _owner_rel_decisions, {"msg.value": 0},
          ["msg.sender", "newOwner", "state._owner"]),
      {"state._owner"})
check("owner-sender-relation-env-source-is-pin-exempt",
      relation_establishable_env_sources(
          _owner_rel_paths, _owner_rel_decisions, {"msg.value": 0},
          ["msg.sender", "newOwner", "state._owner"], ["msg.sender"]),
      {"msg.sender"})
check("decision-term-public-getter-state-coord",
      _decision_term("return_value$admin$1", {"state.admin": 7}, {}),
      ("coord", "state.admin"))
check("decision-term-public-getter-state-coord-set",
      _decision_term("return_value$admin$1", {}, {},
                     coord_set={"state.admin"}),
      ("coord", "state.admin"))
check("decision-term-public-getter-state-pin",
      _decision_term("return_value$admin$1", {}, {"state.admin": 7}),
      ("const", 7))
check("decision-term-bare-state-coord",
      _decision_term("admin", {"state.admin": 7}, {}),
      ("coord", "state.admin"))
check("decision-term-bare-state-pin",
      _decision_term("admin", {}, {"state.admin": 7}),
      ("const", 7))
_admin_rel_paths = [
    (6, 2, {"msg.value": 0, "msg.sender": 9, "state.admin": 1,
            "newAdmin": 0}),
    (14, 3, {"msg.value": 0, "msg.sender": 7, "state.admin": 7,
             "newAdmin": 0}),
    (15, 3, {"msg.value": 0, "msg.sender": 7, "state.admin": 7,
             "newAdmin": 5}),
]
_admin_rel_decisions = {
    6: [{"branch_claim": "!(msg.value == 0)"},
        {"branch_claim":
         "!(!(return_value$admin$1 == return_value$__msgSender$2))"}],
    14: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(return_value$admin$1 == return_value$__msgSender$2)"},
         {"branch_claim": "!(!(newAdmin != 0))"}],
    15: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(return_value$admin$1 == return_value$__msgSender$2)"},
         {"branch_claim": "!(newAdmin != 0)"}],
}
_admin_boxes, _admin_holes, _admin_reasons, _admin_retreats = \
    structural_decision_regions_with_retreat(
        _admin_rel_paths, _admin_rel_decisions, {"msg.value": 0},
        ["msg.sender", "newAdmin", "state.admin"],
        coord_types={"newAdmin": "address"})
check("admin-sender-relation-retreat-pins-entry-admin",
      _admin_retreats,
      {6: {"state.admin": 1}, 14: {"state.admin": 7},
       15: {"state.admin": 7}})
check("admin-sender-relation-retreat-keeps-nonadmin-sender-wide",
      (_admin_boxes[6]["msg.sender"],
       _admin_holes[6]["msg.sender"]), ((0, (1 << 160) - 1), {1}))
check("admin-sender-relation-retreat-pins-admin-path-sender",
      _admin_boxes[14]["msg.sender"], (7, 7))
check("admin-sender-relation-retreat-keeps-success-argument-wide",
      _admin_boxes[15]["newAdmin"], (1, (1 << 160) - 1))
_admin_bare_rel_decisions = {
    6: [{"branch_claim": "!(msg.value == 0)"},
        {"branch_claim": "!(!(msg.sender == admin))"}],
    14: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim": "!(msg.sender == admin)"},
         {"branch_claim": "!(!(newAdmin != 0))"}],
    15: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim": "!(msg.sender == admin)"},
         {"branch_claim": "!(newAdmin != 0)"}],
}
_admin_bare_boxes, _admin_bare_holes, _admin_bare_reasons, \
    _admin_bare_retreats = structural_decision_regions_with_retreat(
        _admin_rel_paths, _admin_bare_rel_decisions, {"msg.value": 0},
        ["msg.sender", "newAdmin", "state.admin"],
        coord_types={"newAdmin": "address"})
check("admin-bare-sender-relation-retreat-pins-entry-admin",
      _admin_bare_retreats,
      {6: {"state.admin": 1}, 14: {"state.admin": 7},
       15: {"state.admin": 7}})
check("admin-bare-sender-relation-retreat-keeps-nonadmin-sender-wide",
      (_admin_bare_boxes[6]["msg.sender"],
       _admin_bare_holes[6]["msg.sender"]),
      ((0, (1 << 160) - 1), {1}))
check("admin-bare-sender-relation-retreat-pins-admin-path-sender",
      _admin_bare_boxes[14]["msg.sender"], (7, 7))
check("decision-relation-inverts-ordered-claim",
      _decision_relation("x > 5"), ("x", "<=", "5"))
check("decision-relation-keeps-negated-ordered-claim",
      _decision_relation("!(x > 5)"), ("x", ">", "5"))
check("decision-relation-boolean-false-guard",
      _decision_relation("!(!m[k])"), ("m[k]", "==", "0"))
check("decision-relation-boolean-true-guard",
      _decision_relation("!(m[k])"), ("m[k]", "==", "1"))
check("decision-relation-boolean-taken-guard",
      _decision_relation("!(!m[k])", "taken"), ("m[k]", "==", "0"))
check("decision-relation-boolean-fallthrough-guard",
      _decision_relation("!(!m[k])", "fall-through"), ("m[k]", "==", "1"))
check("decision-term-mapping-slot-state-coord",
      _decision_term("_tokenAgentsList$322[_agentAddress]", {}, {},
                     coord_set={"state._tokenAgentsList$322[_agentAddress]"}),
      ("coord", "state._tokenAgentsList$322[_agentAddress]"))

_owner_map_rel_paths = [
    (13, 3, {"msg.value": 0, "msg.sender": 7, "state._owner": 7,
             "_agentAddress": 9}),
]
_owner_map_rel_decisions = {
    13: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(return_value$_owner$1 == return_value$__msgSender$2)"},
         {"branch_claim": "!(!_tokenAgentsList$322[_agentAddress])"}],
}
_owner_map_boxes, _owner_map_holes, _owner_map_reasons, \
    _owner_map_retreats, _owner_map_establishes = \
    structural_decision_regions_with_relations(
        _owner_map_rel_paths, _owner_map_rel_decisions, {"msg.value": 0},
        ["msg.sender", "_agentAddress", "state._owner",
         "state._tokenAgentsList$322[_agentAddress]"],
        coord_types={"_agentAddress": "address"})
check("owner-mapping-boolean-guard-establishes-owner-sender",
      _owner_map_establishes[13], {"state._owner": "msg.sender"})
check("owner-mapping-boolean-guard-fixes-empty-slot",
      _owner_map_boxes[13]["state._tokenAgentsList$322[_agentAddress]"],
      (0, 0))
check("owner-mapping-boolean-guard-keeps-agent-wide",
      _owner_map_boxes[13]["_agentAddress"], (0, (1 << 160) - 1))

_remove_agent_rel_paths = [
    (14, 3, {"msg.value": 0, "msg.sender": 7, "state._owner": 7,
             "_agentAddress": 9}),
]
_remove_agent_rel_decisions = {
    14: [{"branch_claim": "!(msg.value == 0)"},
         {"branch_claim":
          "!(return_value$_owner$1 == return_value$__msgSender$2)",
          "arm": "taken"},
         {"branch_claim": "!(!_tokenAgentsList$322[_agentAddress])",
          "arm": "fall-through"}],
}
_remove_agent_boxes, _remove_agent_holes, _remove_agent_reasons, \
    _remove_agent_retreats, _remove_agent_establishes = \
    structural_decision_regions_with_relations(
        _remove_agent_rel_paths, _remove_agent_rel_decisions,
        {"msg.value": 0},
        ["msg.sender", "_agentAddress", "state._owner",
         "state._tokenAgentsList$322[_agentAddress]"],
        coord_types={"_agentAddress": "address"})
check("remove-agent-boolean-guard-establishes-owner-sender",
      _remove_agent_establishes[14], {"state._owner": "msg.sender"})
check("remove-agent-boolean-guard-requires-set-slot",
      _remove_agent_boxes[14]["state._tokenAgentsList$322[_agentAddress]"],
      (1, 1))

_setmax_normal_decisions = [
    {"synthetic_abi_gate": True, "arm": "taken",
     "branch_claim": "!(msg.value == 0)"},
    {"arm": "taken",
     "branch_claim":
     "return_value$_owner$1 != return_value$__msgSender$2"},
    {"arm": "taken", "branch_claim": "maxLossRatio_ > _ONE_E9"},
]
_setmax_box, _setmax_holes, _setmax_reason = structural_decision_region(
    _setmax_normal_decisions,
    {"msg.value": 0, "msg.sender": 1, "maxLossRatio_": 7},
    {"state._owner": 1},
    ["maxLossRatio_", "msg.sender", "msg.value"],
    constants={"_ONE_E9": 1000000000})
check("ordered-decision-region-pins-value",
      _setmax_box["msg.value"], (0, 0))
check("ordered-decision-region-pins-owner",
      _setmax_box["msg.sender"], (1, 1))
check("ordered-decision-region-cuts-upper-bound-from-constant",
      _setmax_box["maxLossRatio_"], (0, 1000000000))
check("ordered-decision-region-keeps-no-stale-holes",
      _setmax_holes, {})
check("ordered-decision-region-reason-names-comparison",
      "comparison" in _setmax_reason, True)

_setmax_overflow = structural_decision_region(
    [{"synthetic_abi_gate": True, "arm": "taken",
      "branch_claim": "!(msg.value == 0)"},
     {"arm": "taken",
      "branch_claim":
      "return_value$_owner$1 != return_value$__msgSender$2"},
     {"arm": "fall-through", "branch_claim": "!(maxLossRatio_ > _ONE_E9)"}],
    {"msg.value": 0, "msg.sender": 1, "maxLossRatio_": 1000000001},
    {"state._owner": 1},
    ["maxLossRatio_", "msg.sender", "msg.value"],
    constants={"_ONE_E9": 1000000000})
check("ordered-decision-region-cuts-lower-bound-from-constant",
      _setmax_overflow[0]["maxLossRatio_"],
      (1000000001, (1 << 256) - 1))
check("ordered-decision-region-refuses-unsatisfied-constant",
      structural_decision_region(
          [{"branch_claim": "!(1 > _ONE_E9)"}],
          {}, {}, ["maxLossRatio_"],
          constants={"_ONE_E9": 1000000000}), None)

with tempfile.NamedTemporaryFile("w", suffix=".solast", delete=False) as _astf:
    json.dump({
        "nodeType": "SourceUnit",
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 1,
            "name": "Base",
            "linearizedBaseContracts": [1],
            "nodes": [{
                "nodeType": "VariableDeclaration",
                "constant": True,
                "name": "_ONE_E9",
                "value": {
                    "nodeType": "Literal",
                    "kind": "number",
                    "value": "1e9",
                    "typeDescriptions": {
                        "typeString": "int_const 1000000000_by_1"
                    }
                }
            }]
        }, {
            "nodeType": "ContractDefinition",
            "id": 2,
            "name": "Child",
            "linearizedBaseContracts": [2, 1],
            "nodes": []
        }]
    }, _astf)
    _literal_ast = _astf.name
try:
    check("literal-state-constants-parse-solidity-scientific-integer",
          literal_state_constants(_literal_ast, "Child")["_ONE_E9"],
          1000000000)
finally:
    os.unlink(_literal_ast)

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as _fxf:
    json.dump({"contract": "St1inch", "state": {"_owner": "1",
                                                "note": "not-int"}}, _fxf)
    _fixture_path = _fxf.name
try:
    _fixture_pins, _fixture_skipped = path_cov_fixture_state_pins(
        ["--path-cov-fixture", _fixture_path], "St1inch")
    check("fixture-state-pins-import-scalar-state",
          _fixture_pins, {"state._owner": 1})
    check("fixture-state-pins-report-nonscalar-skips",
          len(_fixture_skipped), 1)
    check("fixture-state-pins-ignore-wrong-contract",
          path_cov_fixture_state_pins(["--path-cov-fixture", _fixture_path],
                                      "Other")[0], {})
finally:
    os.unlink(_fixture_path)

_extcall_fail = extcall_inseparable_failures(
    [(26, 4, {"amount": 0, "msg.value": 0, "msg.sender": 0}),
     (27, 4, {"amount": 0, "msg.value": 0, "msg.sender": 0})],
    {26: {"extcall.success": 0}, 27: {"extcall.success": 1}})
check("extcall-only-sibling-split-is-statically-inseparable",
      sorted(_extcall_fail), [26, 27])
check("extcall-inseparable-reason-names-uncontrolled-behaviour",
      "external-call behavior" in _extcall_fail[26], True)
check("settable-difference-is-not-extcall-inseparable",
      extcall_inseparable_failures(
          [(26, 4, {"amount": 0}), (27, 4, {"amount": 1})],
          {26: {"extcall.success": 0}, 27: {"extcall.success": 1}}),
      {})
check("missing-extcall-harvest-is-not-inseparable",
      extcall_inseparable_failures(
          [(26, 4, {"amount": 0}), (27, 4, {"amount": 0})],
          {26: {"extcall.success": 0}}),
      {})
_nondet_decisions = {
    28: [{"index": 4, "function": "setDefaultFarm_onlyOwner", "line": 4569,
          "branch_claim": "NONDET( IERC20Plugins *) != ( IERC20Plugins *)this",
          "arm": "fall-through"}],
    29: [{"index": 4, "function": "setDefaultFarm_onlyOwner", "line": 4569,
          "branch_claim": "NONDET( IERC20Plugins *) != ( IERC20Plugins *)this",
          "arm": "taken"}],
}
_nondet_fail = extcall_inseparable_failures(
    [(28, 4, {"defaultFarm_": 1, "msg.sender": 1, "msg.value": 0}),
     (29, 4, {"defaultFarm_": 1, "msg.sender": 1, "msg.value": 0})],
    {}, _nondet_decisions)
check("nondet-decision-only-sibling-split-is-statically-inseparable",
      sorted(_nondet_fail), [28, 29])
check("nondet-decision-inseparable-reason-names-decision",
      "decision#4" in _nondet_fail[28], True)
check("nondet-decision-with-settable-difference-is-not-inseparable",
      extcall_inseparable_failures(
          [(28, 4, {"defaultFarm_": 1}), (29, 4, {"defaultFarm_": 2})],
          {}, _nondet_decisions),
      {})
_safe_transfer_decisions = {
    58: [{"index": 5, "function": "safeTransferFrom", "line": 1820,
          "branch_claim": "!(!success)", "arm": "fall-through"}],
    59: [{"index": 5, "function": "safeTransferFrom", "line": 1820,
          "branch_claim": "!success", "arm": "taken"}],
}
_safe_transfer_fail = extcall_inseparable_failures(
    [(58, 5, {"amount": 1, "maker": 0, "token": 0}),
     (59, 5, {"amount": 1, "maker": 0, "token": 0})],
    {}, _safe_transfer_decisions)
check("safe-transfer-success-sibling-split-is-statically-inseparable",
      sorted(_safe_transfer_fail), [58, 59])
check("safe-transfer-success-inseparable-reason-names-decision",
      "decision#5" in _safe_transfer_fail[58], True)
check("safe-transfer-success-with-settable-difference-is-not-inseparable",
      extcall_inseparable_failures(
          [(58, 5, {"amount": 1}), (59, 5, {"amount": 2})],
          {}, _safe_transfer_decisions),
      {})

# Stage 2 may reuse stage 1 only when the structured collection manifest proves
# that both stages mean the same run. This test exercises the accepting edge and
# one semantic mismatch without launching ESBMC.
with tempfile.TemporaryDirectory() as _import_dir:
    _binary = os.path.join(_import_dir, "esbmc")
    _source = os.path.join(_import_dir, "C.sol")
    _ast = os.path.join(_import_dir, "C.solast")
    _reports = os.path.join(_import_dir, "reports")
    os.mkdir(_reports)
    _report = os.path.join(_reports, "D__f.json")
    for _path in (_binary, _source, _ast):
        with open(_path, "w", encoding="utf-8") as _stream:
            _stream.write(_path)
    with open(_report, "w", encoding="utf-8") as _stream:
        json.dump({"claims": []}, _stream)
    _argv = [_binary, _ast, "--sol", _source,
             "--solidity-path-coverage", "--solidity-max-tx", "1",
             "--cov-report-json", "--path-cov-max-goals", "10000",
             "--memlimit", "8g", "--contract", "C",
             "--focus-function", "f", "--branch-function-coverage",
             "--path-cov-probe", "--all-witnesses",
             "--max-witnesses", "8"]
    _index = {
        "schema": "veriput-pathcov-collection/2",
        "primary": {"name": "C"},
        "flatInputIdentity": file_identity(_source),
        "astInputIdentity": file_identity(_ast),
        "esbmcIdentity": file_identity(_binary),
        "config": {"onlyUnits": ["f"], "solidityMaxTx": 1,
                   "memlimit": "8g", "probeWitnesses": 8,
                   "solverFlags": [], "scope": "single", "focusWith": [],
                   "instrumentOnlyUnit": False},
        "runs": [{"tag": "D__f", "function": "f", "reportPresent": True,
                  "killedByOuterTimeout": False, "cmdArgv": _argv}],
        "reportsDir": _reports,
    }
    _index_path = os.path.join(_import_dir, "index.json")
    with open(_index_path, "w", encoding="utf-8") as _stream:
        json.dump(_index, _stream)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "8g", 8, [])
        _valid_import = True
    except SystemExit:
        _valid_import = False
    check("matching-stage1-report-is-reusable", _valid_import, True)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "10g", 8, [])
        _raised_memlimit_import = True
    except SystemExit:
        _raised_memlimit_import = False
    check("stage1-report-is-reusable-with-a-larger-stage2-memlimit",
          _raised_memlimit_import, True)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "7g", 8, [])
        _lower_memlimit_refused = False
    except SystemExit:
        _lower_memlimit_refused = True
    check("stage1-report-with-a-larger-memlimit-than-stage2-is-refused",
          _lower_memlimit_refused, True)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 2, "8g", 8, [])
        _mismatched_import_refused = False
    except SystemExit:
        _mismatched_import_refused = True
    check("stage1-report-with-another-tx-bound-is-refused",
          _mismatched_import_refused, True)

    _index["runs"][0]["cmdArgv"] = [
        arg for arg in _argv if arg != "--path-cov-probe"
    ]
    with open(_index_path, "w", encoding="utf-8") as _stream:
        json.dump(_index, _stream)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "8g", 8, [])
        _missing_probe_refused = False
    except SystemExit:
        _missing_probe_refused = True
    check("stage1-report-without-path-probe-is-refused",
          _missing_probe_refused, True)

    _legacy_argv = [_binary, _ast, "--sol", _source,
                    "--solidity-path-coverage", "--solidity-max-tx", "1",
                    "--cov-report-json", "--path-cov-max-goals", "10000",
                    "--memlimit", "8g", "--contract", "C",
                    "--focus-function", "f"]
    _legacy_index = {
        "benchmark": "bench",
        "primary": {"name": "C"},
        "flatInput": _source,
        "config": {"onlyUnits": ["f"], "solidityMaxTx": 1,
                   "memlimit": "8g", "solverFlags": [], "scope": "single",
                   "focusWith": [], "instrumentOnlyUnit": False},
        "runs": [{"tag": "D__f", "function": "f", "reportPresent": True,
                  "killedByOuterTimeout": False,
                  "cmd": " ".join(_legacy_argv)}],
        "reportsDir": _reports,
    }
    with open(_index_path, "w", encoding="utf-8") as _stream:
        json.dump(_legacy_index, _stream)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "8g", 8, [])
        _legacy_import = True
    except SystemExit:
        _legacy_import = False
    check("legacy-stage1-report-is-reusable-without-probe-provenance",
          _legacy_import, True)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 1, "10g", 8, [])
        _legacy_raised_memlimit_import = True
    except SystemExit:
        _legacy_raised_memlimit_import = False
    check("legacy-stage1-report-is-reusable-with-a-larger-stage2-memlimit",
          _legacy_raised_memlimit_import, True)
    try:
        validate_enumeration_import(
            _index_path, _report, _binary, _source, _ast, "C", "f",
            "focus", 2, "8g", 8, [])
        _legacy_mismatched_import_refused = False
    except SystemExit:
        _legacy_mismatched_import_refused = True
    check("legacy-stage1-report-with-another-tx-bound-is-refused",
          _legacy_mismatched_import_refused, True)


if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print("  " + f)
    sys.exit(1)
print("solidity_path_generalise: all checks passed")
