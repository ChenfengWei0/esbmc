// SPDX-License-Identifier: MIT
//
// KNOWNBUG. Stage 2C (commit 3d6d424b73) removed the `bare smt_sort
// (id=4)` SMT-backend abort this was pinned for.  Post-2C blocker is
// independent and upstream of the SMT backend:
//   src/pointer-analysis/value_set.cpp:1258 value_sett::assign:
//   Assertion `base_type_eq(rhs->type, lhs_type, ns)' failed
// (goto-symex value-set on the 3-level mapping-of-struct storage-ref
// write).  KNOWNBUG regex `^Branch Coverage:` unchanged — still stable
// (no coverage emitted), flips when this symex/IR wall is fixed.
// See notes/Results/branch_cov/STAGE2C_FOLLOWUP_REPIN.md.
pragma solidity ^0.8.0;
struct Balance { uint248 amount; uint8 tokensCount; }
contract C {
    mapping(address => mapping(bytes32 => mapping(address => Balance))) private _b;
    function dock(bytes32 h, address[] calldata toks) external {
        for (uint256 i = 0; i < toks.length; i++) {
            Balance storage b = _b[msg.sender][h][toks[i]];
            require(b.tokensCount == toks.length);
            b.tokensCount = 0xff;
        }
    }
}
