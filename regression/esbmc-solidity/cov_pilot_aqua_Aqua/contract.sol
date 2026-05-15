// SPDX-License-Identifier: MIT
//
// KNOWNBUG. The original aqua/Aqua pilot finding (Stage 0/1) was the
// `bare smt_sort (id=4)` SMT-backend abort.  Stage 2C (commit
// 3d6d424b73) removed that wall.  Post-2C blocker is independent and
// upstream of the SMT backend:
//   src/irep2/irep2_expr.cpp:366 assert_type_compat_for_with:
//   Assertion `is_array_type(b)' failed
// (goto-symex with2t chain on the 4-level mapping-of-struct
// storage-ref write `balance.tokensCount = 0xff`).  Reproduces with a
// non-struct value too (cov_pilot_aqua2A_4lvl_..._uint256) ⇒ NOT
// Stage 2C's struct-of-arrays path; pre-existing symex/IR limitation.
// KNOWNBUG regex `^Branch Coverage:` unchanged — still stable (no
// coverage emitted), flips when this symex/IR wall is fixed.
// See notes/Results/branch_cov/STAGE2C_FOLLOWUP_REPIN.md.
pragma solidity ^0.8.0;

struct Balance { uint248 amount; uint8 tokensCount; }

contract Aqua {
    mapping(address => mapping(address => mapping(bytes32 => mapping(address => Balance)))) private _balances;

    function dock(address app, bytes32 strategyHash, address[] calldata tokens) external {
        for (uint256 i = 0; i < tokens.length; i++) {
            Balance storage balance = _balances[msg.sender][app][strategyHash][tokens[i]];
            require(balance.tokensCount == tokens.length);
            balance.tokensCount = 0xff;
        }
    }
}
