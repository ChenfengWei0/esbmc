// THE SAME 20 LINES AS ITS SIBLING, AT THE DEFAULT BOUND: the run establishes
// nothing and says so only by aborting.
//
// KNOWNBUG, so these expectations describe what SHOULD happen and do not today.
// Measured now, `--solidity-path-coverage --contract C --solidity-max-tx 1`
// with no `--unwind`:
//
//     --solidity-path-coverage: instrumented 3 complete path(s) across 1 unit(s)
//     Symex completed in: 0.003s (227 assignments)
//     Generated 0 VCC(s), 0 remaining after simplification (59 assignments)
//     WARNING: Coverage may be UNDER-REPORTED: 1 loop(s) hit the unwind bound
//              while --no-unwinding-assertions was active
//     WARNING:   loop 63 at .../library/string.c line 278 function __memcpy_impl
//     --solidity-path-coverage: 0 of 3 instrumented path claim(s) reached the solver
//     ERROR: --solidity-path-coverage: INTERNAL DEFECT — NOT ONE of the 3
//            instrumented path claim(s) reached the solver...
//     (SIGABRT)
//
// TWO SEPARATE THINGS ARE WRONG AND ONLY ONE OF THEM IS THE BOUND.
//
// 1. THE BOUND. The pass installs `--unwind 4` for itself and forces
//    `--no-unwinding-assertions`, so a truncated library loop becomes
//    `ASSUME(!loopcond)` and deletes the executions that would have entered the
//    harness. `--unwind 64` fixes it entirely (the sibling directory pins that).
//    A `push` onto a struct-member dynamic array is enough to need it; the
//    plain array, the same struct without the push, and a fixed-length array
//    are all fine -- see D06/D07/D08 in notes/coverage/poc/.
//
// 2. THE ATTRIBUTION. The abort blames INTERNAL DEFECT -- "a tool failure, not
//    a result" -- when the cause is stated two lines above it in the run's own
//    output: a loop was truncated and the truncation was assumed away. The
//    invariant is right to fire; what it says sends the reader to look for a
//    bug in the instrumentation instead of at `--unwind`. When a run reaches no
//    claim AND `truncated_loops` is non-empty, it should say which loop and
//    what to raise. Fixing (1) without (2) leaves the next contract that needs
//    more than 64 iterations exactly as unreadable.
//
// This is the minimal reproduction produced by DEBUGGING 1inch st1inch rather
// than by adding resources to it, which is the standing rule for a benchmark
// that will not run.
pragma solidity ^0.8.0;

contract C {
    struct Data {
        uint256[] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
        items.raw.push(7);
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
