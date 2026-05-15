// SPDX-License-Identifier: MIT
//
// KNOWNBUG. Stage 2C (commit 3d6d424b73) removed the `bare smt_sort
// (id=4)` SMT-backend abort this was pinned for.  Post-2C blocker is
// independent and upstream of the SMT backend:
//   src/irep2/irep2_expr.cpp:366 assert_type_compat_for_with:
//   Assertion `is_array_type(b)' failed
// (goto-symex with2t chain on a 4-level mapping-of-X storage-ref
// write; reproduces even with a non-struct value — see the
// _uint256 sibling — so it is NOT Stage 2C's struct-of-arrays path).
// KNOWNBUG regex `^Branch Coverage:` unchanged — still stable (no
// coverage emitted), flips when this symex/IR wall is fixed.
// See notes/Results/branch_cov/STAGE2C_FOLLOWUP_REPIN.md.
pragma solidity ^0.8.0;
struct Single { uint256 v; }
contract C {
    mapping(address => mapping(address => mapping(bytes32 => mapping(address => Single)))) private _b;
    function dock(address app, bytes32 h, address[] calldata toks) external {
        for (uint256 i = 0; i < toks.length; i++) {
            Single storage b = _b[msg.sender][app][h][toks[i]];
            require(b.v == toks.length);
            b.v = 1;
        }
    }
}
