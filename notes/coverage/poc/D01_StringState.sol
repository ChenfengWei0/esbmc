// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 1 of 5 for the st1inch death: a `string` state variable.
//
// WHY THIS ONE. st1inch's `--focus-function setFeeReceiver` run -- a unit whose
// whole body is an owner check and one address assignment -- spends 0.095 s in
// symex, produces 875 assignments and 10 VCCs, and then NEVER RETURNS from a
// single solver call. Its BMC log is dominated by
//     Unwinding loop 35 ... function nondet_string
//     Unwinding loop 55/56 ... function _str_assign
// so the string machinery is inside that formula even though `setFeeReceiver`
// never mentions a string. It gets there through the ERC20 constructor.
//
// EXPECTED, written before running: 2 paths for `setFeeReceiver` (owner-ok and
// the revert), decided in well under a second. If this contract instead hangs
// on bitwuzla / OOMs on cvc5 / makes z3 say `datatype is not well-founded`,
// then a plain string state variable is sufficient and no other st1inch
// feature is needed.
contract D01_StringState {
    address public owner;
    address public feeReceiver;
    string public name;

    constructor() {
        owner = msg.sender;
        name = "Staking 1INCH v2";
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
