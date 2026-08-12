// A verifier-generated checked-arithmetic ASSERT is a safety obligation, not a
// source-level Solidity path decision. With `--overflow-check` this remains:
//
//     instrumented 3 complete path(s) across 1 unit(s)
//     Complete Paths : 3        Path Status: F 3, I 0, U 0
//     (and 9 solver claims, not 3 -- the check ADDS CLAIMS and no PATHS)
//
// `goto_check` lowers a checked `+` to a SINGLE-SUCCESSOR ASSERT: `targets` is
// never assigned, so the enumerating DFS -- which fans out only at a conditional
// GOTO and at a folded short-circuit -- walks straight past it. The flag cannot
// change the path set, which is exactly what INVOCATION_DECISIONS.md row 6 says.
//
// ---- WHY IT IS NOT AN ACADEMIC GAP: IT COSTS GREEN TESTS ----
//
// With no overflow exit, the addition's ONLY enumerated exit is the normal one,
// and the counterexample for it is any member of the path's domain. Measured on
// this contract:
//
//     add:path:7   inputs {amt: 0xFFFF...FFFF}
//                  entry  {bal: 500}
//                  final  {bal: 499}        <- 500 + (2^256-1) mod 2^256
//                  exit_kind normal
//
// and the emitted case says `// [asserted] path exits normally; a revert fails
// the test`, so forge reports
//
//     [FAIL: panic: arithmetic underflow or overflow (0x11)] test_cov_0()
//
// Both RED tests in the entire hand-written PoC set are this cause (Tiny2 and
// P18_Unchecked). The model wrapped where Solidity 0.8 panics; the exit
// classification is correct ABOUT THE MODEL and wrong about the chain.
//
// ---- THE `require` IS LOAD-BEARING ----
//
// Without `require(amt > 0)` the solver picks `amt = 0`, nothing wraps, and the
// test is green -- measured. The defect is not that the solver prefers
// overflowing values; it is that NOTHING in the formula separates a wrapping
// member of the domain from a non-wrapping one, so once the convenient value is
// excluded the choice is unconstrained. A guard that merely excludes 0 is enough
// to make it land outside what the chain accepts.
//
// ---- WHAT DOES NOT FIX IT, MEASURED ----
//
//     --overflow-check                            counterexample BYTE-IDENTICAL
//     --overflow-check --conv-assert-to-assume    also unchanged
//
// The first adds a claim and constrains no model (path coverage neutralises
// pre-existing asserts); the second covers bounds and pointer checks, not
// overflow. There is no flag combination that makes the solver avoid a wrapping
// value, which is why the fix is C1 -- a frontend change lowering checked
// arithmetic to a real `if (overflow) revert` two-exit branch -- and not a knob.
//
// The exit census must apply the same distinction. Treating every ASSERT as a
// physical path exit makes it report the overflow obligation as reachable but
// unenumerated and abort before writing cov-report.json. This regression keeps
// the safety claim enabled while checking that the declared path universe stays
// at three and instrumentation completes.
pragma solidity ^0.8.0;

contract C {
    uint256 public bal;

    constructor() {
        bal = 500;
    }

    function add(uint256 amt) external {
        require(amt > 0);
        bal += amt;
    }
}
