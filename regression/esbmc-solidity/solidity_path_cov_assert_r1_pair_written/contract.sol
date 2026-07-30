// STAGE 3 -- the MUST-FLIP pair, and the only self-testing check this mode has.
//
// One scalar state variable, two paths, differing in exactly one thing: whether
// the path WRITES it. The two specs differ ONLY in `enc` and in the region on
// `a`. Expected:
//   _written    eq REFUTED, ne HOLDS
//   _unchanged  eq HOLDS,   ne REFUTED
//
// PASSING EITHER HALF ALONE IS NOT EVIDENCE OF ANYTHING. The property under
// test is that the two verdicts are consistently OPPOSITE, and that is what
// makes this pair a detector rather than a smoke test:
//
//   * if no assert is emitted at all, N1's zero-candidate gate fires and BOTH
//     halves exit non-zero;
//   * if the antecedent is broken open (a wrong enc, or the N3 depth gate
//     removed), `tr != enc || cnt != depth` is true on every execution, every
//     candidate holds vacuously, and the pair comes out ALL GREEN;
//   * if the exit read is bound to the WRONG contract object -- the substring
//     hazard path_cov_contract_object exists to close -- the unit's writes land
//     somewhere this mode never looks, `post == pre` holds on the writing path
//     too, and the pair again comes out ALL GREEN.
//
// The last one is why this pair is the first fixture: it is the only place in
// the whole mode where reading the wrong object produces a visible symptom
// rather than a plausible report.
//
// `payable`, so no ABI value gate is synthesised and msg.value needs no bound.
// `bal` is not public, so no getter unit is generated to confuse the unit name.
// The region pins state.bal into [0, 100] so `bal + 1` cannot wrap: the sign
// rungs are then decided by the arithmetic and not by the entry state.
pragma solidity ^0.8.0;

contract St {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
