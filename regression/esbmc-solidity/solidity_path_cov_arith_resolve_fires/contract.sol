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
// test is red on the unmodified contract.
//
// ALL THREE RED TESTS in the PoC set are a chain-side arithmetic PANIC, and
// they are NOT all the same panic -- measured 2026-08-01 after the funnel's own
// RED attribution was fixed (it had been reporting four, one of them a phantom
// filed against a contract with no arithmetic):
//
//     D10_WrapNotPanic.test_cov_0   panic 0x11  overflow
//     Tiny2.test_cov_0              panic 0x11  overflow
//     P18_Unchecked.test_cov_5      panic 0x12  DIVISION BY ZERO
//
// The third one is why the fix below cannot be about the wrap alone. A
// counterexample that divides by zero is rejected by the chain for a different
// reason and through a different Panic code, and `--div-by-zero-check` is a
// separate flag from `--overflow-check`. Whatever decides "this counterexample
// is one the chain refuses" has to cover both, or a third of the measured
// failures survives the fix.
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
// ---- WHAT C1 COSTS, MEASURED 2026-08-01, BEFORE IMPLEMENTING IT ----
//
// Every checked operation that becomes a two-exit branch DOUBLES the paths
// through everything after it, and the path count is already the binding
// resource: the collector caps a unit at 10000 paths and on st1inch twelve
// units had call points WITHDRAWN from their path identity to fit that cap.
// So the cost is 2^k for k = checked ops reachable on a unit's path, counting
// the internal callees path coverage inlines. Measured by
// notes/coverage/scripts/arith_exponent.py:
//
//     aqua      worst unit `ship`          k = 1    -> x2
//     st1inch   `constructor`              k = 29   -> x536,870,912
//     st1inch   `earlyWithdraw(To)`        k = 10   -> x1024
//     st1inch   `deposit*` (four units)    k = 7    -> x128
//
// st1inch's 29 are the `_EXP_TABLE_0..29` chain -- thirty multiply-then-divide
// steps in the constructor, the same code the D13 reduction walked through.
//
// So C1 is x2 on a toy and 10^8 on a real contract, and this file is a toy. A
// decision taken on this contract alone would have been taken on the cheapest
// possible sample. The alternatives worth weighing against it, none of which
// multiply the path count:
//
//   (b) ASSUME no-overflow while solving a path claim. The counterexample can
//       then not wrap. Loses the overflow-revert path entirely -- an honest
//       coverage gap rather than a red test.
//   (c) RE-QUERY on demand: solve as now; if the counterexample wraps, re-solve
//       that ONE claim with a no-wrap constraint. At most one extra query per
//       affected claim, no path growth, and it reuses the certification
//       machinery that already assumes a constraint and re-solves. If no
//       non-wrapping witness exists, the path genuinely requires an overflow
//       and can be reported as exactly that.
//
// ---- DECIDED 2026-08-01: (c), WITH FOUR CONDITIONS ----
//
// (a) is not implementable at 2^29. (b) is UNSOUND in a way the number hides:
// `assume(no overflow)` deletes paths that genuinely exist on chain -- it is
// the `require -> assume` cell of the soundness table, "absent from the model",
// and it deletes them silently, with no count. (c) changes no model by default
// and pays only where a counterexample actually wraps.
//
// The four conditions are not polish; the first is a hole in (c) as first
// proposed here:
//
//   1. THE RE-SOLVE MUST CARRY THE PATH CONSTRAINT. Checked arithmetic is
//      itself a two-way decision -- wrapping and not wrapping are different
//      destinations -- so re-solving with only `no overflow` added lets the
//      solver return a witness of a DIFFERENT path, which is then not a witness
//      of this one at all. The query is
//
//          assume(tr == pi AND no overflow);  assert(false)
//
//      and its UNSAT is not "the re-solve failed": it is a PROOF that this path
//      is reachable only by overflowing. That proof is free, from the same
//      query.
//
//   2. "WRAPPED" IS THE VERIFIER'S OWN OVERFLOW CHECK, never a range test on
//      the rendered decimal. Deciding it by re-deriving whether a printed value
//      exceeds its type is the same shape as the geometric ladder's own wrap
//      defect -- a second implementation of an arithmetic the model already
//      performs, free to disagree with it.
//
//   3. "THIS PATH NECESSARILY OVERFLOWS" IS ITS OWN BUCKET, counted beside
//      F / I / U and not folded into U. It is not a failure to decide; it is a
//      decided property of the path, and it says the path is reachable on chain
//      only through a revert.
//
//   4. THE COST IS PRINTED, NOT INFERRED LATER: how many claims were re-solved
//      and what the re-solving cost in wall time. Nobody knows today whether
//      that is three claims or three thousand, and this project's recurring
//      failure is exactly the number nobody measured.
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
