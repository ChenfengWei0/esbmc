// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// A certification query re-runs deployment before the focused unit, and the
// deployment-time environment (block.timestamp here) is NOT bound by the
// box: it is a fresh nondet.  A checked-arithmetic claim inside the
// constructor can therefore be refuted by a deployment the box says nothing
// about, and that refutation used to be reported as `RESULT: UNSAFE` for the
// unit's region.  Deployment is not part of the certified path -- the emitted
// test deploys concretely and would fail on its own if deployment reverted --
// so certify mode must leave constructor-located safety claims out of the
// verdict.  With them out, `end`'s region certifies below.
contract PauseWindow {
    uint32 private immutable _end;
    constructor(uint32 duration) {
        _end = uint32(block.timestamp) + duration; // uint32 add: overflowable at deployment
    }
    function end() external view returns (uint32) {
        return (block.timestamp < _end) ? _end : 0;
    }
}
