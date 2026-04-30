// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// Audit finding F1: _ESBMC_abi_fold (T2.1, solidity_abi.c:101-105) uses
// `acc * 0x100000001b3 + next` over uint256. The "FNV-injective" claim in
// the comment holds only in infinite precision; mod 2^256, the SMT solver
// can find (a, b) != (c, d) such that fold(a, b) == fold(c, d). With
// keccak256 modelled as `~x` (bijective), distinct argument tuples can
// produce identical keccak hashes, contradicting both the comment's
// promise and real EVM's hash semantics.
//
// Closure requires an SMT-sound tuple encoding (e.g. position-tagged bit-
// vector concatenation). Hard fix; KNOWNBUG-locked under ledger entry #3.
contract H {
    function check(uint256 a, uint256 b, uint256 c, uint256 d) public pure {
        require(!(a == c && b == d));
        assert(keccak256(abi.encode(a, b)) != keccak256(abi.encode(c, d)));
    }
}
