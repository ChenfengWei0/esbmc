// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// THE SMALLEST CONTRACT THAT HAS THE DEFECT: one unit, no source decision, so
// the ONLY decision in the whole run is the synthetic ABI value gate.
//
// WHAT IT ISOLATES. 55 of the 63 payload-vs-path contradictions found across
// the PoC set are one shape, and it appears once per unit in every contract:
//
//     <unit>:path:2   decisions: msg.value == 0 [fall-through / ABI gate]
//                     env:       msg.value = 0
//
// `path:2` is depth 1 and exits `revert`: it is the path taken when value is
// sent to a NONPAYABLE entry, so it requires `msg.value != 0`. Its own payload
// states the condition under which it is not taken.
//
// WHY IT MATTERS MORE THAN A MISSING FIELD. The emitter renders this path and
// its sibling as the SAME call, and labels one test case with BOTH path ids --
// 37 of 161 cases across the set. So a path that no emitted test can reach is
// counted as rendered, in the numerator of the ratio this pipeline exists to
// report.
//
// WHAT THE ANSWER MUST BE, and why the question is well-posed. The claim is
// `assert(tr != enc || cnt != depth)` and it is REFUTED, so an execution exists
// that walks path:2 -- and any such execution has `msg.value != 0`. The model
// therefore HAS the value. The question this contract makes cheap to answer is
// whether it is lost on the way into `env`, or whether the gate is modelled on
// something other than `msg.value`.
//
// EXPECTED, written before running: 2 paths, both F, and `path:2` must carry a
// NONZERO `env["msg.value"]`. Anything else is the defect, reproduced in six
// lines with a log short enough to read whole.
//
// ---- MEASURED, AND THE ANSWER IS THE FIRST ONE ----
//
// The model HAS the value and the report takes a DIFFERENT VERSION OF THE SAME
// SYMBOL. From the `set:path:2` counterexample, verbatim:
//
//     State 11  solidity_blockchain.c line 31
//       msg_value = 0
//     State 51  solidity_misc.c line 171  function _sol_per_tx_reseed
//       msg_value = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
//     State 72  D09_ValueGate.sol line 36  function set
//       path_tr$0 = 2
//     Violated property: ...:path:2   path_tr$0 != 2 || path_cnt$1 != 1
//
// State 11 is the DECLARATION-time nondet, before the harness runs. State 51 is
// `_sol_per_tx_reseed`, the last write before the transaction enters the unit,
// and it is nonzero exactly as path:2 requires. `cov-report.json` reports
// `env["msg.value"] = 0` -- the State 11 value.
//
// Compare the sibling `set:path:3` (the gate-PASSED path): there State 51 sets
// `msg_value = 0`, `path_tr$0 = 3`, and 0 is correct. So the harvest is right
// half the time by coincidence: it always reports the initial value, which
// happens to be the reseeded one whenever the reseed chose 0.
//
// ---- WHY IT IS THE SAME DEFECT AS THE msg.sender CASES ----
//
// `_sol_per_tx_reseed` reseeds msg_sender at State 50 by the same mechanism, and
// the 6 `msg.sender == owner` contradictions across the PoC set are that. In
// those the EMITTER renders `vm.prank(address(uint160(4294967294)))` -- the
// reseeded value -- while the report says 0, so the two disagree about the same
// execution and the emitter is the one that is right.
//
// So entry conditions 1 and 2 of the 63 payload/path contradictions are ONE
// root cause: the env payload is harvested from the FIRST assignment to each
// environment symbol rather than the LAST ONE BEFORE THE UNIT IS ENTERED.
//
// The consequence is not a missing field. Both arms of the gate then render as
// the same call, so one emitted case is labelled with both path ids -- 37 of 161
// cases across the set -- and a path no test can reach is counted as covered.
contract D09_ValueGate {
    uint256 public x;

    function set(uint256 v) external {
        x = v;
    }
}
