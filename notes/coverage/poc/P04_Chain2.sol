// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: how many preceding calls the transaction bound can buy.
///
/// `use()`'s guarded path needs `open()` AND THEN `fund()`. Tiny showed one hop
/// is bought by `--solidity-max-tx 2`. This asks whether two hops need 3.
///
/// EXPECTED, and each answer means something different:
///   tx=2 leaves the deep path bounded-holds, tx=3 witnesses it
///        -> the bound buys exactly N-1 preceding calls, and a contract needing
///           a k-step setup needs tx=k+1. That is a cost curve, and a real
///           contract's setup depth becomes a predictor.
///   tx=2 already witnesses it
///        -> one transaction body can call several functions in declaration
///           order, so depth is bought by ORDER, not by transaction count, and
///           the bound only has to exceed 1.
///   tx=3 still leaves it
///        -> something other than the bound blocks chains, and the Tiny result
///           was a one-hop special case.
contract P04_Chain2 {
    bool public opened;
    uint256 public funds;

    function open() external {
        opened = true;
    }

    function fund(uint256 amt) external {
        require(opened);
        funds = amt;
    }

    function use(uint256 amt) external {
        require(amt > 0);
        require(funds >= amt);
        funds -= amt;
    }
}
