// A --focus-function name that matches nothing must be a HARD FAILURE, never an
// empty measurement.
//
// This became worth pinning when --focus-function started narrowing what is
// INSTRUMENTED. Before that, a focus naming nothing still enumerated the whole
// contract, so the run printed a full report and the mistake was survivable.
// Now it would enumerate nothing, and
//
//     Complete Paths : 0 / No complete path enumerated
//
// is byte-compatible with a contract that genuinely has no externally-callable
// function. A per-method sweep would then record a clean, plausible zero for a
// name it simply got wrong -- the same class of failure as the certify/assert
// "matched NO enumerated unit" routes, which are hard failures for exactly this
// reason.
//
// ---- WHICH LAYER THIS PINS, AND WHY THAT ONE ----
//
// There are two guards, and this test deliberately pins the one that fires.
//
//   FIRST, the frontend validator (solidity_convert.cpp, at the top of
//   convert()): the name must be a public/external, non-constructor,
//   non-receive/fallback method of the target contract. It fails the CONVERSION,
//   so the run dies with exit 6 before any GOTO program exists -- which is why
//   the expectation below is a conversion error and not a coverage message.
//
//   SECOND, a `units_enumerated == 0` gate at the end of
//   solidity_path_coverage(). It is defence in depth and it has NO REPRODUCER
//   TODAY: the validator closes the misspelling route, and the "right name,
//   wrong scope" route does not exist either, because Solidity inheritance is
//   merge-by-copy -- measured with `contract D is B`, `--contract D
//   --focus-function basefn` enumerates `sol:@C@D@F@basefn#23`, attributed to D
//   and in scope. That gate is documented as unreachable at its own site rather
//   than being given a contrived test here; a test that pinned a manufactured
//   path to it would report on the fixture, not on the tool.
//
// So what is pinned is the observable contract -- "a focus name matching nothing
// stops the run, loudly, with the offending name and the contract it was looked
// up in" -- at the layer that enforces it. `contract.sol` has exactly one
// public function so that the error cannot be about ambiguity, and the focus
// name below differs from it in more than a typo's worth of characters so the
// test cannot accidentally start matching.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function pub(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
