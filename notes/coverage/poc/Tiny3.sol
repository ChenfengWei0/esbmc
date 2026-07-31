// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// SECOND DISCRIMINATING EXPERIMENT — what exactly does a preceding call break?
///
/// Established so far, each in about a second:
///   Tiny  (bal starts 0)      withdraw focus : 5 paths, 3 F, 2 bounded-holds
///   Tiny  whole contract      (dispatcher may call deposit() then withdraw()
///                              inside one transaction)  : 8 paths, 6 F,
///                              THE SAME 2 bounded-holds
///   Tiny2 (constructor sets bal = 500)  withdraw focus : 5 paths, 5 F, 100%
///
/// So the state is not the obstacle; a preceding call is. The leading
/// hypothesis is that the path identity is the obstacle: the claim is
/// `tr != enc || cnt != depth`, and if `tr` accumulates across the WHOLE
/// transaction then the execution "deposit() then withdraw()" carries deposit's
/// bits too, so it cannot match withdraw's own `enc`. The only other execution,
/// "withdraw() alone", fails the balance guard. Both are excluded, which is
/// exactly what was measured.
///
/// This contract narrows it. `seed()` writes the balance with NO user-level
/// decision in its body — no `require`, no branch. Its only decision is the
/// synthesised ABI non-payable value gate, which every unit has.
///
///   * if withdraw's two guarded paths become F in the whole-contract run, the
///     damage comes from the PREDECESSOR'S DECISIONS, i.e. accumulator
///     pollution, and the fix is to scope the accumulator to the unit call;
///   * if they stay bounded-holds, then even a decision-free predecessor breaks
///     it, and the mechanism is something stronger — the unit must be the FIRST
///     call of the transaction, or the state is snapshotted at transaction
///     entry rather than read live.
///
/// The two answers call for different fixes, which is why this is worth one
/// more second.
contract Tiny3 {
    uint256 public bal;

    function seed() external {
        bal = 500;
    }

    function withdraw(uint256 amt) external {
        require(amt > 0);
        require(bal >= amt);
        if (amt > 100) {
            bal -= amt;
        } else {
            bal -= 1;
        }
    }
}
