// A folded short-circuit chain wider than the decision cap.
//
// The runtime accumulator and the offline enumeration must agree on which
// operands count as decisions. When they disagreed (Phase 1 snapshotting K
// operands the DFS did not enumerate), every emitted path carried a decision
// depth short by K, so the identity assertion `cnt != depth` held on EVERY real
// execution: the path became permanently uncoverable AND was then reported
// PASSED — a false claim of unreachability, which under `--solidity-max-tx 0`
// would be promoted to "I: proven unreachable".
//
// Measured before the fix on this exact contract: `Reached : 0`,
// `Path Coverage: 0%`, claim reported PASSED.
//
// Now both phases share one cap, so a site above it is left OUT of the decision
// set entirely (its paths merge rather than split) and stays coverable. The
// incompleteness is reported rather than silent.
pragma solidity ^0.8.0;

contract K26 {
    bool public x;

    function f(
        bool a0, bool a1, bool a2, bool a3, bool a4, bool a5, bool a6,
        bool a7, bool a8, bool a9, bool a10, bool a11, bool a12, bool a13,
        bool a14, bool a15, bool a16, bool a17, bool a18, bool a19, bool a20,
        bool a21, bool a22, bool a23, bool a24, bool a25
    ) public {
        x = a0 && a1 && a2 && a3 && a4 && a5 && a6 && a7 && a8 && a9 &&
            a10 && a11 && a12 && a13 && a14 && a15 && a16 && a17 && a18 &&
            a19 && a20 && a21 && a22 && a23 && a24 && a25;
    }
}
