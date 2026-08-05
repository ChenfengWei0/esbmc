// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// ISOLATES ONE THING: does a TWO-LEVEL mapping with a SCALAR value get a
// rung in the assertion ladder, and does the emitted PUT read it at the
// ITERATED hash?
//
// ---- WHY THIS FILE EXISTS ------------------------------------------------
//
// Measured on aqua: every unit's PUT carries NO oracle. The only rungs the
// ladder builds name the immutable `_DOCKED`, which the emitter then drops as
// a compile-time tautology. aqua's one real state variable is
//
//     mapping(address => mapping(address => mapping(bytes32 =>
//         mapping(address => Balance))))    struct Balance{uint248;uint8}
//
// which differs from the WORKING reference (P28_MapMin, one level, scalar
// value) by TWO things at once: four levels of NESTING, and a packed STRUCT
// value. D44_MapStructValue already moved the second one and settled it: with
// ONE level and a packed struct value the rungs ARE built and rendered, with
// the right mask and shift. So the remaining suspect is the NESTING, and this
// file moves exactly that: two levels, SCALAR value, nothing else new.
//
// ---- THE TWO UNITS ARE A MATCHED PAIR ------------------------------------
//
// `spend1` and `spend2` are the same function with the same guard over the
// same key type; they differ ONLY in how many levels the mapping they touch
// has. Both live in one contract and run in one invocation, so solver options,
// transaction bound, unwind bound and entry state are shared and cannot
// explain a difference between them.
//
// `spend1` IS THE NEGATIVE CONTROL. If NEITHER unit gets a mapping rung, this
// file has measured something about itself -- the cell, the guard, the
// reachability -- and NOT about nesting. The result must then be thrown away
// rather than read as "nesting is the blocker".
//
// ---- BOTH KEYS ARE PARAMETERS, DELIBERATELY -------------------------------
//
// Not `msg.sender`. The emitter REFUSES a slot keyed by an environment
// quantity, and correctly: inside a Foundry test `msg.sender` is whoever
// called the test while the unit sees the test contract, so the slot written
// and the slot read would be different words and a `post == pre` rung over an
// untouched slot would stay GREEN while establishing nothing. A PoC that hit
// that refusal would measure the refusal, not the nesting.
//
// ---- EXPECTED, WRITTEN BEFORE THE RUN ------------------------------------
//
//   CONTROL   : `bal1[o]` rungs are named and rendered, as in P28.
//   THE QUESTION: is there a `bal2[o][s]` rung, and is its PUT's read
//                 hashed TWICE -- keccak(s . keccak(o . p)) -- rather than
//                 once?
//
//   IF YES  -- nesting is handled; aqua's remaining distance is depth 4 and
//              the struct, both already implemented, so aqua should be re-run.
//   IF NO, with the control present -- naming stops at one level, and the
//              spelling fix is not sufficient: the ladder never proposes it.
//   IF the rung appears but the verdict is `solver-unknown` -- that is the
//              OTHER, independent defect (141 claims across st1inch, all on
//              two-level mappings). It is NOT a failure of the nesting work
//              and must be filed in bucket (1) on its own.
//   ⛔ IF NEITHER unit gets a mapping rung -- this file proves nothing about
//              nesting. Find why the control is silent first.
//
// ---- SIZED FOR THE 60-SECOND BOX -----------------------------------------
//
// No loops, no external calls, no library, no constructor arguments. Two
// require()s per unit, so each unit has a handful of paths, not thousands.
contract N01_TwoLevelScalar {
    mapping(address => uint256) public bal1;
    mapping(address => mapping(address => uint256)) public bal2;

    function put1(address o, uint256 v) external {
        require(v > 0);
        bal1[o] = v;
    }

    function put2(address o, address s, uint256 v) external {
        require(v > 0);
        bal2[o][s] = v;
    }

    // THE CONTROL: one level.
    function spend1(address o, uint256 v) external {
        require(v > 0);
        require(bal1[o] >= v);
        bal1[o] -= v;
    }

    // THE QUESTION: two levels, same guard, same key type, scalar value.
    function spend2(address o, address s, uint256 v) external {
        require(v > 0);
        require(bal2[o][s] >= v);
        bal2[o][s] -= v;
    }
}
