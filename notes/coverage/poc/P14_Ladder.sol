// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: the assertion ladder — the one contribution nothing else replaces.
///
/// `bump` moves `total` by an amount the caller chooses, but only within a
/// range the guard fixes. So on the taken path there is a TRUE, PROVABLE
/// statement of every strength the ladder climbs:
///
///   R1  post != pre
///   R2  post > pre                      (a direction)
///   R2  10 <= post - pre <= 20          (a bounded delta — the top rung)
///
/// and `touched` gives a variable that is written unconditionally, so the
/// `post == pre` rung has something to be false about.
///
/// EXPECTED: the ladder reaches the DELTA rung here. If it stops at `!=`, the
/// non-trivial-assertion rate is a property of the candidate ladder rather than
/// of the contracts, and this is the smallest place that distinguishes the two.
///
/// The delta rung also has a trap worth exercising deliberately: unsigned
/// subtraction wraps, so `post - pre` must be asserted with a direction
/// conjunct or the rung is true for the wrong reason.
contract P14_Ladder {
    uint256 public total;
    uint256 public touched;

    function bump(uint256 amt) external {
        require(amt >= 10 && amt <= 20);
        total += amt;
        touched = 1;
    }
}
