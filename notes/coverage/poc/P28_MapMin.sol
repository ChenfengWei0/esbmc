// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// P16_Mapping.take returns `solver-unknown` for every path in 7ms per claim,
// and the run log names the reason once per claim:
//     [bzla] warning: Equality over constant arrays not fully supported yet
// P16's mapping is TWO levels (address => uint256 => uint256). This contract is
// the same shape with ONE level and no msg.sender key, so a difference between
// the two isolates the nesting and nothing else.
//
// `take` is guarded by state that only `put` can establish, exactly as in P16,
// so the reachability question is unchanged; only the map's arity moved.
contract P28_MapMin {
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
