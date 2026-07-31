// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 5 of 5, and the CONTROL: a genuinely self-referential struct.
//
// WHY THIS ONE. `S { S[] kids; }` is the textbook non-well-founded datatype --
// a datatype whose only constructor mentions itself and has no base case. If z3
// says `datatype is not well-founded` HERE and nowhere else, then the message on
// st1inch means ESBMC built a recursive datatype from a NON-recursive Solidity
// declaration, which is an encoding defect rather than a hard formula.
//
// If z3 accepts this one too, the message on st1inch is not about recursion in
// the source at all and this suspect list is looking in the wrong place --
// which is why the control is in the set rather than assumed.
//
// EXPECTED: 2 paths. This is the one contract in the set where a refusal would
// be the ORDINARY outcome, so it is the calibration point for the other four.
contract D05_RecursiveStruct {
    struct Node {
        uint256 value;
        Node[] kids;
    }

    address public owner;
    address public feeReceiver;
    Node internal root;

    constructor() {
        owner = msg.sender;
        root.value = 1;
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
