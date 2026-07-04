// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// Codex defect-1 known-answer: a bytes4 param whose value a branch depends on,
// plus a bytes4 constructor arg (defect-2). The generated literal MUST be
// bytes4(..), never bytes32(..).
contract KB {
    bytes4 public tag;
    bool public hit;
    constructor(bytes4 seed) { tag = seed; }
    function poke(bytes4 x) external { if (x == bytes4(0x12345678)) hit = true; }
}
