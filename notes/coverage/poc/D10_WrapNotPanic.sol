// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// THE MODEL WRAPS WHERE SOLIDITY 0.8 PANICS, AND THAT IS WHAT MAKES A RED TEST.
//
// One statement, one unit, no branch. `add` is the whole contract, so nothing
// else can be blamed for the counterexample the solver picks.
//
// MEASURED on Tiny2, whose `deposit` has this shape behind a `require`:
//
//     deposit:path:7   inputs {amt: 0xFFFF...FFFF}
//                      entry  {bal: 500}
//                      final  {bal: 499}          <- 500 + (2^256-1) mod 2^256
//                      exit_kind normal
//
// and the emitted case carries `// [asserted] path exits normally; a revert
// fails the test`, so `forge test` says:
//
//     [FAIL: panic: arithmetic underflow or overflow (0x11)] test_cov_0()
//
// The path is real, the exit classification is right ABOUT THE MODEL, and the
// test is red on the unmodified contract. Two of the two RED tests across the
// whole PoC set are this one cause (Tiny2 and P18_Unchecked).
//
// ---- WHAT DOES NOT FIX IT, MEASURED RATHER THAN ASSUMED ----
//
//   --overflow-check                      counterexample BYTE-IDENTICAL
//   --overflow-check --conv-assert-to-assume   also unchanged
//
// `goto_check` lowers a checked `+` to a SINGLE-SUCCESSOR ASSERT, and path
// coverage neutralises pre-existing asserts, so the flag adds a claim and
// constrains no model. `--conv-assert-to-assume` covers bounds and pointer
// checks, not overflow. There is no flag combination that makes the solver
// avoid a wrapping value.
//
// ---- WHAT WOULD ----
//
// The implementation plan's decision C1: lower checked arithmetic to a real
// `if (overflow) revert` TWO-EXIT branch in the frontend. Then the overflow is
// its own enumerated path with `exit_kind: revert` and a
// `vm.expectRevert(Panic 0x11)` rendering, and the non-overflow path's
// counterexample cannot wrap because wrapping now leaves the path. C1 was
// DECIDED and never implemented; until it is, this contract's `add` has one
// enumerated normal exit whose counterexample the chain rejects.
//
// EXPECTED once C1 lands: `add` enumerates a revert exit for the overflow, and
// the normal-exit counterexample satisfies `a + b < 2^256`.
//
// ---- THE `require` IS LOAD-BEARING, AND THE FIRST VERSION LACKED IT ----
//
// Without `require(amt > 0)` this contract does NOT reproduce: measured, the
// solver picks `amt = 0`, `bal` stays 500, and the emitted test is green. The
// counterexample is any member of the path's domain and 0 is the cheapest one.
//
// The wrap needs the trivial value EXCLUDED, which is exactly what Tiny2's
// `require(amt > 0)` does -- and then the solver's next choice is the extreme,
// `2^256-1`. So the defect is not "the solver likes overflowing values"; it is
// that NOTHING in the formula distinguishes a wrapping member of the domain
// from a non-wrapping one, so as soon as the convenient value is ruled out the
// choice is unconstrained and may land outside what the chain accepts. Keeping
// the failed first version recorded here matters: a PoC that reproduces only
// with a guard says something sharper than one that reproduces always.
contract D10_WrapNotPanic {
    uint256 public bal;

    constructor() {
        bal = 500;
    }

    function add(uint256 amt) external {
        require(amt > 0);
        bal += amt;
    }
}
