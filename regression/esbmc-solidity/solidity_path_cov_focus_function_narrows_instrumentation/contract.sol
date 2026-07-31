// `--focus-function` narrows the ENUMERATION, and it is meant to.
//
// THIS DIRECTORY PREVIOUSLY PINNED THE OPPOSITE, and that expectation is now
// false BY DESIGN rather than broken. The test was called
// `solidity_path_cov_focus_function_same_enumeration` and required
// `instrumented 8 complete path(s) across 2 unit(s)` -- byte-identical to its
// sibling `internal_call_expands`, which runs the same contract under the FULL
// dispatcher. Its stated reason was:
//
//     "enumeration is a static DFS over the goto program at instrumentation
//      time, whereas --focus-function only changes which entry the harness
//      invokes"
//
// That was a true description of the old implementation and a bad property. It
// made every focused run publish CONTRACT-level numbers under a unit-level
// label. MEASURED on aqua `--focus-function dock`:
//
//     Complete Paths : 2846    Reached : 2    Path Coverage: 0.07%
//
// 2783 of those 2846 paths belong to units the dispatcher CANNOT ENTER in that
// run, so no exploration could ever witness them -- they are in the denominator
// by construction and unreachable by construction. Against `dock`'s own 63
// paths the honest figure is 3.17%: the published metric was wrong by 45x, and
// `summary.paths_total` carried the same contract-level number into
// cov-report.json, where it has already been misread as the unit's.
//
// So the contract is now: --focus-function narrows what is INSTRUMENTED, not
// only what is ENTERED.
//
// ---- WHICH ASSERTIONS MOVED BECAUSE OF THAT CHANGE, AND WHICH DID NOT ----
//
// This matters because these lines went red once already for an unrelated
// reason (a seventh `U Reasons` slot, `claim-budget-exceeded`), and the
// enumeration assertions were checked then and found UNMOVED. So their movement
// here is an unambiguous signal of this change and of nothing else.
//
//   MOVED, and only by this change:
//     instrumented       8 across 2 unit(s)   ->  3 across 1 unit(s)
//     distribution       8 total, max 5 in caller, mean 4.0, 3 before, 2.67x
//                        ->  3 total, max 3 in pub,  mean 3.0, 2 before, 1.50x
//     reached the solver 3 of 8 across 2      ->  3 of 3 across 1
//     Complete Paths     8                    ->  3
//     Path Coverage      37.5%                ->  100%
//     Path Status        F 3, I 0, U 5        ->  F 3, I 0, U 0
//     U Reasons          unit-not-entered 5   ->  the line is GONE (no U at all)
//
// The disappearance of `U Reasons:` is pinned INDIRECTLY, by `Path Status: F 3,
// I 0, U 0`. That is not a weaker pin by accident: the regression runner has no
// negative-match support -- every line of test.desc after the third is a regex
// that MUST match -- so "this line is absent" cannot be written directly.
// bmc.cpp prints `U Reasons:` only when nU > 0, so `U 0` and the absence of the
// line are the same fact, and `U 0` is the one that is expressible.
//     the "1 unit(s) were not entered because --focus-function narrowed the
//     dispatcher to 'pub'" line               ->  replaced by the narrowing line
//
//   DID NOT MOVE, and is pinned here precisely so that stays true:
//     expanded 2 internal call(s) into their calling unit
//
// That last line is the load-bearing one. The internal-call EXPANSION loop is
// deliberately NOT narrowed: `expand_here` copies a callee's body as it is at
// that moment and the loop rewrites bodies in place, so narrowing it would
// change what lands inside the focused unit and every `enc` would silently mean
// something else. Leaving it alone keeps the focused unit's body BIT-IDENTICAL
// to a whole-contract run's -- which is also what makes reading a covered set
// across foci safe, since a stable path id is built only from the unit's own
// body. `Reached : 3` is unchanged for the same reason: the same three paths of
// `pub` are witnessed either way. What changed is the denominator they are
// divided by.
//
// The `U Reasons` line disappearing is not a loss of the `unit-not-entered`
// coverage this file used to provide -- it is that token's live producer being
// removed at the source. A focus-excluded unit now has no claims at all, so on a
// COMPLETE run `units_not_entered` can only name the FOCUSED unit, and that is
// audit_entry_liveness's hard failure rather than a token. The token remains
// reachable only on a PARTIAL run.
//
// The sibling `solidity_path_cov_internal_call_expands` still runs this contract
// under the full dispatcher and still pins 8 across 2 units. The two are now
// deliberately DIFFERENT, and that difference is the feature.
//
// See also `solidity_path_cov_focus_function_keeps_callee_decisions`, which
// focuses on `caller` -- the direction in which a callee's decisions must still
// be inside the focused unit's paths -- and
// `solidity_path_cov_focus_function_no_match_fails`.
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

    function helper(uint256 a) private {
        if (a > 3) {
            x = 3;
        }
    }

    function caller(uint256 a) public {
        pub(a);
        helper(a);
    }
}
