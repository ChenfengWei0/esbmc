// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// ISOLATES ONE THING: two levels of nesting AND a packed struct value
// together -- aqua's shape, reduced to the smallest thing that still has both.
//
// ---- WHY A SECOND FILE RATHER THAN ONE MORE UNIT IN N01 -------------------
//
// N01 moves ONE variable (nesting) with the value held scalar. This file moves
// the second (a packed struct value) ON TOP of the first. Keeping them in
// separate files is what makes the outcome readable: if N01 succeeds and this
// one fails, the combination is the blocker even though neither ingredient is;
// if both succeed, aqua's remaining distance is only DEPTH, which is a number
// rather than a mechanism. Putting all of it in one file would leave those
// three outcomes indistinguishable.
//
// The field widths are aqua's own -- `Balance{uint248 amount; uint8
// tokensCount}` -- because that is a packed pair sharing ONE word, offsets 0
// and 31. A struct whose fields happened to land in separate words would
// exercise a different (and, per `storage_layout`, currently unimplemented)
// address computation and would not be aqua's case.
//
// ---- THE MATCHED PAIR, AGAIN ---------------------------------------------
//
// `spendScalar` touches the two-level SCALAR mapping and is the CONTROL; it is
// the unit N01 expects to work. `spendStruct` touches the two-level STRUCT
// mapping. One contract, one invocation, so nothing but the value type differs
// between them.
//
// ⛔ IF THE CONTROL IS SILENT HERE, this file measured itself and not the
// struct-on-top-of-nesting question. Discard it and fix the control first.
//
// ---- EXPECTED, WRITTEN BEFORE THE RUN ------------------------------------
//
//   CONTROL     : `two[o][s]` rungs named and read at keccak(s.keccak(o.p)).
//   THE QUESTION: are there `pack[o][s].amount` and `pack[o][s].tokensCount`
//                 rungs, read at the SAME iterated hash with the mask
//                 `& (2**248 - 1)` and the shift `>> 248` respectively?
//
//   IF YES -- every mechanism aqua needs exists; only depth 4 is untried, and
//             depth is a loop count in the peel, not a new mechanism.
//   IF NO, with the control present -- the struct-value row and the nesting
//             peel do not compose, and `storage_layout` is where to look: it
//             emits one row per scalar field, and that row has to carry the
//             key-type TUPLE rather than a single key type.
contract N02_TwoLevelStruct {
    struct Balance {
        uint248 amount;
        uint8 tokensCount;
    }

    mapping(address => mapping(address => uint256)) public two;
    mapping(address => mapping(address => Balance)) public pack;

    function putTwo(address o, address s, uint256 v) external {
        require(v > 0);
        two[o][s] = v;
    }

    function putPack(address o, address s, uint248 v) external {
        require(v > 0);
        pack[o][s] = Balance(v, 1);
    }

    // THE CONTROL: two levels, scalar value.
    function spendScalar(address o, address s, uint256 v) external {
        require(v > 0);
        require(two[o][s] >= v);
        two[o][s] -= v;
    }

    // THE QUESTION: two levels, packed struct value.
    function spendStruct(address o, address s, uint248 v) external {
        require(v > 0);
        require(pack[o][s].amount >= v);
        pack[o][s].amount -= v;
    }
}
