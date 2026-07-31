// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 3 of 5: a struct whose field is a dynamic array.
//
// WHY THIS ONE. It is the other half of 1inch's `AddressSet`, which st1inch
// reaches through the plugin machinery:
//     library AddressArray { struct Data { uint256[] _raw; } }
// A dynamic array inside a datatype is the second shape that can leave an SMT
// datatype without a finite base case, and unlike the mapping it also carries a
// length the solver must reason about.
//
// EXPECTED: 2 paths, sub-second.
contract D03_StructWithDynArray {
    struct Data {
        uint256[] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
        items.raw.push(7);
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
