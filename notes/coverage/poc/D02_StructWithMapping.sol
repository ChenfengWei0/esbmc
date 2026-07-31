// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 2 of 5: a struct whose field is a mapping.
//
// WHY THIS ONE. z3 does not merely run out of time on the st1inch claim, it
// refuses the query outright:
//     ERROR: Z3 error datatype is not well-founded
// That message is about an ALGEBRAIC DATATYPE with no base case. ESBMC encodes
// Solidity structs as SMT datatypes, and a struct field that is itself an
// unbounded map is the first shape that could produce one.
//
// EXPECTED: 2 paths, sub-second. A hang / OOM / the z3 message here would mean
// a struct-with-mapping is enough on its own.
contract D02_StructWithMapping {
    struct Bucket {
        uint256 total;
        mapping(address => uint256) shares;
    }

    address public owner;
    address public feeReceiver;
    Bucket internal bucket;

    constructor() {
        owner = msg.sender;
        bucket.total = 1;
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
