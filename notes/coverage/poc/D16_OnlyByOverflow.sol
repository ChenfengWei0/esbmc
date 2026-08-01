// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// A PATH THAT IS REACHABLE ONLY BY OVERFLOWING, so the proof arm of
// --path-cov-arith-resolve has something to fire on.
//
// WHY IT HAD TO BE WRITTEN. The re-solve has two arms and only one of them was
// exercised by the contracts that motivated it. On D10_WrapNotPanic and Tiny2
// the re-solve comes back SAT -- a non-wrapping witness exists and is taken --
// so `arith_revert_only_paths` stays EMPTY on both, and the emitter refusal
// that reads it could not fire, could not be measured, and would have shipped
// as another mechanism whose only evidence is that it compiles. This project
// has already shipped a function that was never called and a guard that was
// always true; a third is not acceptable.
//
// THE SHAPE. `bal` starts at 500 and `add` raises it. `bal < 500` AFTER the
// addition is impossible in real arithmetic and possible in the model only by
// wrapping, so the path through the `if` body:
//
//   * IS witnessed by the unconstrained solve -- the model wraps, the guard
//     holds, the path is F with a counterexample;
//   * has NO witness once the enabled overflow check is assumed -- which is
//     exactly the UNSAT that PROVES "reachable only by overflowing".
//
// On chain the transaction that would reach it reverts with Panic(0x11), so the
// path is real and its rendering is `vm.expectRevert`, NOT a bare call. Until
// that rendering exists the case must be REFUSED, and the refusal counted.
//
// EXPECTED with `--overflow-check --path-cov-arith-resolve`:
//
//     Arithmetic Re-solve: ... 1 path(s) PROVEN reachable only through a
//     checked-arithmetic revert, 1 Foundry case(s) REFUSED for that reason
//
// and the same run WITHOUT the flag emits that case as a bare normal-exit call
// -- which is the defect, visible as a must-flip pair rather than argued.
//
// THE SIBLING PATH IS THE CONTROL. `bal >= 500` after the addition is the
// ordinary case; its witness must be unaffected, so a run that refused BOTH
// paths would be over-refusing and is distinguishable from a correct one by
// the counts alone.
contract D16_OnlyByOverflow {
    uint256 public bal;
    uint256 public flag;

    constructor() {
        bal = 500;
    }

    function add(uint256 amt) external {
        bal += amt;
        if (bal < 500) {
            flag = 1;
        }
    }
}
