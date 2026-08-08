// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// TWO mapping stores with DIFFERENT key kinds, both named in ONE assertion
// spec.
//
// `bal` is keyed by a uint256 the unit takes as a parameter and writes -- a key
// this mode can express. `tagged[state.box]` is only named in the spec:
// `state.box` resolves to an entry-state struct, so `coord_expressible` refuses
// that single candidate.
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
    struct Box {
        uint256 x;
    }

    Box box;
    mapping(uint256 => uint256) tagged;
    mapping(uint256 => uint256) public bal;

    constructor() {
        bal[0] = 1000;
    }

    function put(uint256 k, uint256 v) external {
        require(v > 0);
        bal[k] = v;
    }

    function take(uint256 k, uint256 v) external {
        require(v > 0);
        require(bal[k] >= v);
        bal[k] -= v;
    }
}
