// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// TWO stores with DIFFERENT key kinds, written by ONE unit.
//
// `bal` is keyed by a uint256 the unit takes as a parameter -- a key this mode
// can express. `tagged` is keyed by a bytes32, which the frontend lowers to an
// aggregate, so `coord_expressible` refuses it.
//
// WHY THE PAIR IS THE POINT. `--path-cov-assert` used to REFUSE THE WHOLE
// LADDER on the first key it could not express, so a spec naming both answered
// NEITHER. That was defensible while `vars` was hand-written; it stopped being
// so when `propose_slot_vars` began emitting a cross product of guesses --
// MEASURED on aqua `dock`, where 16 proposed candidates produced rows=0
// because every one of them carried the same unexpressible bytes32 key, and
// the emitted PUT's empty oracle read as "this unit has nothing assertable".
//
// A key that does not RESOLVE is still a hard refusal, pinned by
// `solidity_path_cov_assert_mapping_slot_key_refused`, which must stay green:
// a typo is an INPUT error, an unsupported key TYPE is a CAPABILITY limit, and
// only the second is a per-candidate drop.
contract MixedKeys {
    mapping(bytes32 => uint256) public tagged;
    mapping(uint256 => uint256) public bal;

    function put(uint256 k, uint256 v) external {
        require(v > 0);
        bal[k] = v;
    }

    function take(bytes32 h, uint256 k, uint256 v) external {
        require(v > 0);
        require(bal[k] >= v);
        bal[k] -= v;
        tagged[h] = v;
    }
}
