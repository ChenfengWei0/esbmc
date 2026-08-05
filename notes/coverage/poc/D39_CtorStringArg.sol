// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// SUSPECT 4 for the st1inch death, after three were REFUTED by measurement:
//
//   D17  constructor's 30-step exponent chain vs 3   0.005s/claim, F 2 U 0 BOTH
//   D01  a `string` state variable + owner require   0.005s/claim, F 3 U 0
//   A/B  the report's slicing exemption              120.58s WITHOUT it,
//                                                    120.90s WITH it
//
// So it is not the arithmetic, not having a string, not the guard, and not the
// slicing exemption. What is left in the evidence is the ONE thing still
// unwinding in the real contract's BMC log and absent from every fast PoC:
//
//   Unwinding loop 35 ... function nondet_string     (twice)
//   Unwinding loop 55/56 ... function _str_assign    (repeatedly)
//
// AND THE DIFFERENCE FROM D01 IS EXACT. D01 has a string state variable and is
// fast -- but its constructor takes NO ARGUMENTS and assigns a LITERAL:
//
//   constructor() { name = "Staking 1INCH v2"; }
//
// st1inch reaches its string through the ERC20 constructor's PARAMETERS, so the
// harness has to invent them, and `nondet_string` is the harness inventing a
// SYMBOLIC string. A symbolic string is a symbolic length plus a symbolic body,
// and the solver then reasons about every byte of it -- on a formula that is
// otherwise tiny (5 VCCs, 633 assignments after slicing) and still does not
// finish in 120s.
//
// THE FACTOR ISOLATED HERE IS WHERE THE STRING COMES FROM, AND NOTHING ELSE.
// The two contracts below are byte-identical apart from whether the constructor
// takes the string as a PARAMETER or assigns the same literal. Same state
// variables, same unit, same body, same guard.
//
// EXPECTED, written before running (推断): the parameter version reproduces the
// hang or the 120s budget exhaustion, and the literal version decides in
// milliseconds like D01 did.
// WHAT WOULD REFUTE IT: both decide fast. Then a nondet string constructor
// argument is NOT sufficient either, and the next thing to isolate is the
// nested mapping / plugin set that this PoC deliberately does not have.
// ⛔ Either way the per-claim budget stays at 120s. This file exists to name
// the cause, not to buy more time for it.

contract D39_CtorStringLiteral {
    address public owner;
    address public feeReceiver;
    uint256 public maxLossRatio;
    string public name;
    string public symbol;

    constructor() {
        owner = msg.sender;
        name = "Staking 1INCH v2";
        symbol = "st1INCH";
    }

    function setMaxLossRatio(uint256 r) external {
        require(msg.sender == owner, "not owner");
        maxLossRatio = r;
    }
}

// THE ONLY CHANGE: the two strings arrive as constructor PARAMETERS, which is
// what forces the harness to build them with `nondet_string`.
contract D39_CtorStringParam {
    address public owner;
    address public feeReceiver;
    uint256 public maxLossRatio;
    string public name;
    string public symbol;

    constructor(string memory name_, string memory symbol_) {
        owner = msg.sender;
        name = name_;
        symbol = symbol_;
    }

    function setMaxLossRatio(uint256 r) external {
        require(msg.sender == owner, "not owner");
        maxLossRatio = r;
    }
}
