// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: the method-attribution defect the tool warns about at
/// `--solidity-max-tx N >= 2`.
///
/// This is the contract the whole set now turns on. Hand-written experiments
/// showed that whole-contract enumeration at tx=2 reaches paths NOTHING else
/// reaches — `Tiny` goes from 75% to 100% — while ESBMC itself warns that at
/// tx>=2 it "reconstructs multi-transaction sequences unreliably (methods can
/// be mis-attributed across transactions)" for Foundry emission. So tx=2 is
/// simultaneously the only configuration that works and the one the tool says
/// not to trust. That contradiction has to be settled by looking at a generated
/// test, not by reading a warning.
///
/// The two units are made deliberately UNCONFUSABLE:
///   * `prepare` accepts exactly one value, 7, and takes no other input;
///   * `finish` accepts only values above 100;
///   * the ranges are disjoint, so an argument that ends up on the wrong call
///     cannot satisfy that call's guard;
///   * the state variables are separate, so an assertion attached to the wrong
///     method reads a variable that method never wrote.
///
/// EXPECTED, and a human can check it at a glance: the deep path's test reads
///     prepare(7);
///     finish(<bound amt, > 100>);
///     assertEq(mark, amt);
///
/// MIS-ATTRIBUTION WOULD LOOK LIKE any of: `prepare` missing entirely; `finish`
/// called with 7; `prepare` called with the bounded `amt`; the assertion on
/// `mark` placed after `prepare`; or the second call labelled `prepare`. Each
/// is visible without reading the report, which is the property this contract
/// was built for — the guards are disjoint precisely so a swapped argument
/// cannot silently still work.
contract P20_Tx2Attribution {
    uint256 public phase;
    uint256 public mark;

    function prepare(uint256 key) external {
        require(key == 7);
        phase = 1;
    }

    function finish(uint256 amt) external {
        require(phase == 1);
        require(amt > 100);
        mark = amt;
    }
}
