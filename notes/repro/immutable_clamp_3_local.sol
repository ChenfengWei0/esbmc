// Reproducer #3 -- the same as #2 with ONE change: the clamped value goes into
// a fresh LOCAL instead of being written back over the PARAMETER.
//
// #1 (all locals, one function)            -> model CORRECT, stayed symbolic
// #2 (parameter reassigned, base immutable) -> `t` folded to 0, contract dead
// #3 (this file: same as #2, local instead of parameter reassignment)
//
// If #3 is correct, the trigger is assigning to a function parameter and
// reading it back -- the read resolves to the assignment's guarded value rather
// than to the merge of both arms. If #3 also folds, the parameter is innocent
// and the trigger is elsewhere in the #1 -> #2 delta (the base-constructor
// immutable, or the internal call).
pragma solidity ^0.8.0;

contract Base3 {
    uint256 public immutable ORIGIN;

    constructor(uint256 origin_) {
        ORIGIN = origin_;
    }

    function _at(uint256 balance, uint256 timestamp)
        internal
        view
        returns (uint256 r)
    {
        uint256 ts = timestamp < ORIGIN ? ORIGIN : timestamp;
        unchecked {
            uint256 t = ts - ORIGIN;
            r = balance;
            if (t & 0x01 != 0) {
                r = r + 1;
            }
            if (t & 0x02 != 0) {
                r = r + 2;
            }
            if (t & 0x04 != 0) {
                r = r + 4;
            }
        }
        return r;
    }
}

contract D3 is Base3 {
    uint256 public v1;
    uint256 public v2;

    error Bad();

    constructor() Base3(block.timestamp) {
        v1 = _at(1000, block.timestamp + 63072000);
        v2 = _at(1000, block.timestamp + 63072001);
        if (v1 == v2) revert Bad();
    }

    function ping(uint256 x) public {
        if (x > 5) {
            v1 = x;
        } else {
            v2 = x;
        }
    }
}
