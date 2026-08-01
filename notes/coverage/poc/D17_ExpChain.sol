// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// IS st1inch's `solver-unknown` THE CONSTRUCTOR'S EXPONENT TABLE?
//
// st1inch produces 128 path claims and EVERY ONE is U -- 59 `solver-unknown`,
// 69 `bounded-holds`, 0 `unit-not-entered`. So the units ARE entered and the
// paths ARE enumerated; 59 claims are simply not decided. Three obvious
// suspects are already RULED OUT by measurement, and none of them is this one:
//
//   * the ENCODER. The struct-tag fix let plain `--z3` finish st1inch where it
//     used to abort at encoding time, and the verdicts were IDENTICAL
//     (setFeeReceiver: 5 of 5 solver-unknown either way). What leaves these
//     claims undecided was never the encoder.
//   * the unit not being reached. `unit-not-entered` is 0.
//   * the outer timeout. The runs FINISH and write reports.
//
// The remaining named suspect is the shape `arith_exponent.py` measured: the
// constructor's `_EXP_TABLE_0..29` chain, thirty fixed-point
// multiply-then-divide steps, k = 29 -- by far the largest arithmetic depth in
// the corpus (aqua's worst unit is k = 1).
//
// THE FACTOR ISOLATED HERE IS CHAIN LENGTH, AND NOTHING ELSE. The two contracts
// below are byte-identical apart from how many steps the chain has. Same types,
// same operation, same unit, same trivial body. If the long one comes back
// `solver-unknown` and the short one decides, the chain length is the cause and
// this file is the reproduction. If BOTH decide, the suspect is refuted in
// seconds and the automatic reduction of the 4874-line original is justified
// rather than assumed -- which is the whole reason to write this first.
//
// WHY A HAND-WRITTEN PoC BEFORE THE REDUCER. Measured on this project: a
// ten-line contract refuted a three-hour configuration matrix, because the
// matrix had a hole exactly where the answer was. The reducer costs a solc
// compile plus an ESBMC run (up to 120s) per candidate over 4874 lines; this
// costs two runs.
//
// The unit is a trivial setter on purpose: it is the SAME unit st1inch's own
// `solver-unknown` was observed on (`setFeeReceiver`), and its body contributes
// no arithmetic of its own, so anything the solver cannot decide comes from the
// constructor and not from the function under test.
//
// EXPECTED, stated before running: the long contract reproduces
// `solver-unknown` and the short one does not. Whatever actually happens is the
// result; this line exists so the outcome cannot be read as confirming
// whichever way it went.

contract D17_ExpChainLong {
    address public feeReceiver;

    uint256 public immutable e0;
    uint256 public immutable e1;
    uint256 public immutable e2;
    uint256 public immutable e3;
    uint256 public immutable e4;
    uint256 public immutable e5;
    uint256 public immutable e6;
    uint256 public immutable e7;
    uint256 public immutable e8;
    uint256 public immutable e9;
    uint256 public immutable e10;
    uint256 public immutable e11;
    uint256 public immutable e12;
    uint256 public immutable e13;
    uint256 public immutable e14;
    uint256 public immutable e15;
    uint256 public immutable e16;
    uint256 public immutable e17;
    uint256 public immutable e18;
    uint256 public immutable e19;
    uint256 public immutable e20;
    uint256 public immutable e21;
    uint256 public immutable e22;
    uint256 public immutable e23;
    uint256 public immutable e24;
    uint256 public immutable e25;
    uint256 public immutable e26;
    uint256 public immutable e27;
    uint256 public immutable e28;
    uint256 public immutable e29;

    constructor(uint256 seed) {
        uint256 v = seed;
        e0 = v;
        v = v * v / 1e18;
        e1 = v;
        v = v * v / 1e18;
        e2 = v;
        v = v * v / 1e18;
        e3 = v;
        v = v * v / 1e18;
        e4 = v;
        v = v * v / 1e18;
        e5 = v;
        v = v * v / 1e18;
        e6 = v;
        v = v * v / 1e18;
        e7 = v;
        v = v * v / 1e18;
        e8 = v;
        v = v * v / 1e18;
        e9 = v;
        v = v * v / 1e18;
        e10 = v;
        v = v * v / 1e18;
        e11 = v;
        v = v * v / 1e18;
        e12 = v;
        v = v * v / 1e18;
        e13 = v;
        v = v * v / 1e18;
        e14 = v;
        v = v * v / 1e18;
        e15 = v;
        v = v * v / 1e18;
        e16 = v;
        v = v * v / 1e18;
        e17 = v;
        v = v * v / 1e18;
        e18 = v;
        v = v * v / 1e18;
        e19 = v;
        v = v * v / 1e18;
        e20 = v;
        v = v * v / 1e18;
        e21 = v;
        v = v * v / 1e18;
        e22 = v;
        v = v * v / 1e18;
        e23 = v;
        v = v * v / 1e18;
        e24 = v;
        v = v * v / 1e18;
        e25 = v;
        v = v * v / 1e18;
        e26 = v;
        v = v * v / 1e18;
        e27 = v;
        v = v * v / 1e18;
        e28 = v;
        v = v * v / 1e18;
        e29 = v;
    }

    function setFeeReceiver(address r) external {
        feeReceiver = r;
    }
}

// THE CONTROL. Three steps instead of thirty. Everything else -- the operation,
// the types, the unit, its body, the constructor parameter -- is the same.
contract D17_ExpChainShort {
    address public feeReceiver;

    uint256 public immutable e0;
    uint256 public immutable e1;
    uint256 public immutable e2;

    constructor(uint256 seed) {
        uint256 v = seed;
        e0 = v;
        v = v * v / 1e18;
        e1 = v;
        v = v * v / 1e18;
        e2 = v;
    }

    function setFeeReceiver(address r) external {
        feeReceiver = r;
    }
}
