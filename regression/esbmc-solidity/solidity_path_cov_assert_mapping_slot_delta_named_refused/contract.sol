// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// A contract whose ENTIRE state is a mapping. Before the slot ladder existed
// this was the shape on which --path-cov-assert exited with
// `NOT ONE candidate assertion could be formed`: the ladder builds every
// candidate from the contract object's struct COMPONENTS, and a mapping is not
// one of them -- the frontend lowers it to a contract-scope global.
//
// `take` is chosen because its post-state is FULLY DETERMINED relative to its
// pre-state on the normal path (`require(v>0)` then `bal[k] -= v`), so every
// one of the six sign rungs has exactly one right answer and the pinned table
// below is a verdict VECTOR rather than "some rows appeared".
//
// `put` is here so the --focus-function alphabet can establish `bal[k] >= v`;
// only `take` is instrumented, so the denominator is take's own.
contract SlotMin {
    mapping(uint256 => uint256) public bal;

    function put(uint256 k, uint256 v) external {
        require(v > 0);
        bal[k] = v;
    }

    function take(uint256 k, uint256 v) external {
        require(v > 0);
        require(bal[k] >= v);
        bal[k] -= v;
    }
}
