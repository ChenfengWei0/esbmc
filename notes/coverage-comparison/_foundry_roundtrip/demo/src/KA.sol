// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

/// Known-answer for round-trip probe:
///  - setBig(uint256): scalar branch (mechanism check)
///  - setHash(bytes32): bytes32 branch (feasibility probe for Phase-1 rendering)
contract KA {
    uint256 public hi;
    bool public flag;
    function setBig(uint256 v) external { if (v > 100) hi = v; else hi = 1; }
    function setHash(bytes32 h) external { if (h == bytes32(uint256(7))) flag = true; }
}
