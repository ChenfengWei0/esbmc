// SPDX-License-Identifier: MIT
//
// KNOWNBUG. The deep-nested-mapping WRITE symex/IR abort
// (irep2_expr.cpp:366) IS fixed (solidity_convert_expr.cpp:4203); this
// pilot no longer aborts.  Residual, pre-existing, ORTHOGONAL solver
// limits keep it from emitting coverage: scalar (uint256-valued) deep
// nested-mapping does not converge under k-induction coverage
// (k-induction budget-burn) and trips bitwuzla const-array-equality
// under assertion BMC.  Not the diagnosed bug, not introduced by the
// fix; stays KNOWNBUG (regex `^Branch Coverage:` unmatched).  See
// notes/Results/branch_cov/STAGE2C_FOLLOWUP_DIAG.md.
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
