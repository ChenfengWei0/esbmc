// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// NARROWING D03, third axis: DYNAMIC vs FIXED length.
//
// If a struct field of type `uint256[3]` is fine and `uint256[]` is not, then
// the cause is the unbounded length -- the thing an SMT encoding has to model
// with an array of unknown size -- and not "an array inside a struct".
//
// EXPECTED: 3 paths, sub-second, on all three backends.
contract D08_StructFixedArray {
    struct Data {
        uint256[3] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
        items.raw[0] = 7;
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
