// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// NARROWING D03, second axis: is it the ARRAY or the CONSTRUCTOR'S `push`?
//
// D03 declares `struct { uint256[] raw; }` AND pushes to it in the constructor.
// Two things changed at once, which is exactly the mistake this PoC set exists
// to stop. This contract keeps the declaration and DROPS THE PUSH; the
// constructor still writes a scalar so it is not empty.
//
// EXPECTED: if this finishes 3/3, the declaration is harmless and the write is
// the cause. If it aborts, merely declaring the shape is enough and the
// constructor is a red herring.
contract D07_StructDynArrayNoPush {
    struct Data {
        uint256[] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
