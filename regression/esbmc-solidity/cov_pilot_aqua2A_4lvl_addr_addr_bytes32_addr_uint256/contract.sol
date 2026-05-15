// SPDX-License-Identifier: MIT
//
// KNOWNBUG. Stage 2C (commit 3d6d424b73) removed the `bare smt_sort
// (id=4)` SMT-backend abort this was pinned for.  Post-2C blocker is
// independent and upstream of the SMT backend:
//   src/irep2/irep2_expr.cpp:366 assert_type_compat_for_with:
//   Assertion `is_array_type(b)' failed
// This pilot's mapping value is a plain `uint256` (NOT a struct), so
// the wall is the 4-level nested-mapping storage-ref WRITE itself in
// goto-symex/IR — categorically not Stage 2C's struct-of-arrays path.
// KNOWNBUG regex `^Branch Coverage:` unchanged — still stable (no
// coverage emitted), flips when this symex/IR wall is fixed.
// See notes/Results/branch_cov/STAGE2C_FOLLOWUP_REPIN.md.
pragma solidity ^0.8.0;
contract C {
    mapping(address => mapping(address => mapping(bytes32 => mapping(address => uint256)))) private _b;
    function dock(address app, bytes32 h, address[] calldata toks) external {
        for (uint256 i = 0; i < toks.length; i++) {
            uint256 v = _b[msg.sender][app][h][toks[i]];
            require(v == toks.length);
            _b[msg.sender][app][h][toks[i]] = 0xff;
        }
    }
}
