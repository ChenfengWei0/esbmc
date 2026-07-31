// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: `block.timestamp` as a coordinate, against a stored deadline.
///
/// THE FIRST VERSION OF THIS FILE DID NOT FORCE ITS OWN QUESTION. `unlockAt`
/// defaulted to zero, so `require(block.timestamp >= unlockAt)` was satisfiable
/// at entry with no preceding call at all, and the two-coordinate relation the
/// contract exists to probe never arose. The guard now refuses an unarmed
/// contract, so the deep path requires `arm(t)` first and the relation is
/// unavoidable.
///
/// Both sides of the comparison move: `unlockAt` is storage written by a call,
/// `block.timestamp` is environment. So the region is over a coordinate PAIR
/// with a relation between them, not over one free variable.
///
/// EXPECTED: `claim`'s taken path certifies with `block.timestamp` as an
/// interval or a pin, and the emitted test carries `vm.warp`.
///
/// THE SHARP QUESTION: the method's regions are products of per-coordinate sets
/// (Definition 6), and a product CANNOT express a relation between two
/// coordinates. So the honest outcome is a PINNED pair, and the interesting
/// number is how much of the feasible set that loses. This is the smallest
/// contract on which that loss is visible and countable by hand.
///
/// It is also, now, a two-hop setup: `arm` then `claim`. That makes it a second
/// witness for the transaction-bound result, on a contract whose blocking state
/// is environmental rather than a balance.
contract P09_TimeLock {
    uint256 public unlockAt;
    uint256 public claimed;

    function arm(uint256 t) external {
        require(t > 0);
        unlockAt = t;
    }

    function claim() external {
        require(unlockAt > 0);
        require(block.timestamp >= unlockAt);
        claimed += 1;
    }
}
