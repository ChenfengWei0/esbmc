// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// NARROWING D03. D03 (`struct { uint256[] raw; }`) aborts with SIGABRT on all
// three backends and trips the tool's own INTERNAL DEFECT ("NOT ONE of the 3
// instrumented path claim(s) reached the solver"), while D01 (string),
// D02 (struct with a mapping) and even D05 (a self-referential
// `struct Node { Node[] kids; }`) all finish 3/3 at 100%.
//
// So the array is implicated and the struct may not be. THIS contract has the
// dynamic array as a PLAIN STATE VARIABLE, no struct anywhere.
//
// EXPECTED, written before running: if this aborts too, "inside a struct" is
// irrelevant and the minimal repro is a dynamic array state variable that the
// constructor pushes to. If it finishes 3/3, the struct wrapper is load-bearing
// and D03 is already close to minimal.
contract D06_PlainDynArray {
    address public owner;
    address public feeReceiver;
    uint256[] internal raw;

    constructor() {
        owner = msg.sender;
        raw.push(7);
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
