// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 4 of 5: the exact 1inch `AddressSet` shape -- a struct whose fields
// are ANOTHER STRUCT holding a dynamic array, plus a mapping.
//
// WHY THIS ONE. This is not a guess at a shape, it is the shape st1inch
// actually has (`@1inch/solidity-utils` AddressSet/AddressArray, reached from
// the plugin bookkeeping that every St1inch unit's constructor sets up). If
// suspects 2 and 3 are individually harmless, the composition is the next
// candidate, because the datatype nesting is what deepens here rather than any
// single field.
//
// EXPECTED: 2 paths, sub-second.
contract D04_AddressSetShape {
    struct ArrayData {
        address[] items;
    }

    struct SetData {
        ArrayData items;
        mapping(address => uint256) lookup;
    }

    address public owner;
    address public feeReceiver;
    SetData internal plugins;

    constructor() {
        owner = msg.sender;
        plugins.items.items.push(msg.sender);
        plugins.lookup[msg.sender] = 1;
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
