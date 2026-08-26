// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Positive control for solidity_path_cov_certify_ignores_constructor_arithmetic:
// only CONSTRUCTOR-located checked-arithmetic claims are left out of a
// certification verdict.  Here the same uint32 add sits inside the focused
// unit, and a box that admits the overflow must still be reported UNSAFE.
contract PauseWindow {
    uint32 private immutable _end;
    constructor(uint32 duration) {
        _end = duration;
    }
    function end(uint32 extra) external view returns (uint32) {
        uint32 e = _end + extra; // uint32 add inside the unit: overflowable
        return (block.timestamp < e) ? e : 0;
    }
}
