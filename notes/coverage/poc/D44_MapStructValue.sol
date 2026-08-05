// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// ISOLATES ONE THING: does a PACKED STRUCT as a mapping's VALUE cost the
// mapping-entry rung its name in the assertion ladder?
//
// ---- WHY THIS PoC EXISTS -------------------------------------------------
//
// P28_MapMin.take path 15 is the WORKING reference: `mapping(uint256 =>
// uint256)`, and its emitted PUT carries four post-state assertions over the
// mapping entry, read through `vm.load` at `keccak256(abi.encode(k, p))` --
//     assertTrue(_post_bal_k != _pre_bal_k, "bal[k]: post != pre");
//     assertLe (_post_bal_k,  _pre_bal_k,   "bal[k]: post <= pre");
//     assertLt (_post_bal_k,  _pre_bal_k,   "bal[k]: post < pre");
//     assertLe (_post_bal_v,  _pre_bal_v,   "bal[v]: post <= pre");
// So naming, solving and rendering all work for ONE level with a scalar value.
//
// aqua's `_balances` differs from that by TWO changes at once:
//     mapping(address => mapping(address => mapping(bytes32 =>
//         mapping(address => Balance))))
//     struct Balance { uint248 amount; uint8 tokensCount; }
// -- FOUR levels of nesting, AND a packed struct value. Measured on aqua dock:
// the ladder returns six rows and all six name the immutable `_DOCKED`; no
// `_balances` row appears at all. Two changes, one symptom, and no way to tell
// from aqua alone which one costs the naming. Guessing wrong means building the
// second hash and finding the ladder still silent, or the reverse.
//
// This file moves ONE of them. The nesting stays at ONE level, exactly P28's.
// Only the VALUE becomes the packed struct, with aqua's own field widths.
//
// ---- THE TWO UNITS ARE A MATCHED PAIR ------------------------------------
//
// `takeScalar` and `takeStruct` are the same function over the same key type
// with the same guard; they differ only in the value type of the mapping they
// touch. Both run in ONE invocation, so solver options, transaction bound,
// unwind bound and entry state are shared and cannot explain a difference.
//
// `takeScalar` IS THE NEGATIVE CONTROL, and it is the reason the pair exists
// rather than a single unit: if a run comes back with no mapping rung for
// EITHER unit, this file has not measured the struct -- it has measured
// something about itself (the cell, the guard, the reachability) and the result
// must be thrown away rather than read as "the struct is the blocker". A PoC
// whose two outcomes look identical from outside decides nothing.
//
// ---- EXPECTED, WRITTEN BEFORE THE RUN ------------------------------------
//
// Run WHOLE CONTRACT at --solidity-max-tx 2 (the artefact cell), because
// `take*` is guarded by state only `put*` can establish -- the same cell P28's
// working PUT was produced in. Then read the assertion ladder's variable names.
//
//   EXPECT (control) : `balScalar[k]` rungs are named, as in P28.
//   THE QUESTION     : is there a `balStruct[k].amount` (or equivalent) rung?
//
//   IF YES -- the struct value is NOT what silences aqua, and the whole cost is
//   the four levels of NESTING. Work goes to naming a nested key.
//
//   IF NO, with the control present -- a packed struct value alone is enough to
//   lose the rung, and it must be fixed before nesting is worth touching.
//
//   ⛔ IF NEITHER unit gets a mapping rung, THIS FILE PROVES NOTHING about the
//   struct. Do not report it as evidence either way; find why the control is
//   silent first.
contract D44_MapStructValue {
    struct Bal {
        uint248 amount;
        uint8 tag;
    }

    mapping(uint256 => uint256) public balScalar;
    mapping(uint256 => Bal) public balStruct;

    function putScalar(uint256 k, uint256 v) external {
        require(v > 0);
        balScalar[k] = v;
    }

    function putStruct(uint256 k, uint248 v) external {
        require(v > 0);
        balStruct[k] = Bal(v, 1);
    }

    function takeScalar(uint256 k, uint256 v) external {
        require(v > 0);
        require(balScalar[k] >= v);
        balScalar[k] -= v;
    }

    function takeStruct(uint256 k, uint248 v) external {
        require(v > 0);
        require(balStruct[k].amount >= v);
        balStruct[k].amount -= v;
    }
}
