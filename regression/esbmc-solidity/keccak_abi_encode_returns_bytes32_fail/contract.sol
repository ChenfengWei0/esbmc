// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// FAIL dual of keccak_abi_encode_returns_bytes32_pass.  Different
// inputs must produce different hashes (injectivity holds in the
// identity-hash abstraction).  The user-function returning bytes32
// with keccak256(abi.encodePacked(...)) no longer crashes symex (root
// cause fixed by always packing the keccak result into BytesStatic),
// so the assertion `h1 == h2 when x1 != x2` now reaches the solver
// and is refutable.
contract C {
    function getHash(uint256 x) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(x));
    }

    function test(uint256 x, uint256 y) public pure {
        require(x != y);
        bytes32 a = getHash(x);
        bytes32 b = getHash(y);
        // Identity-hash abstraction: distinct inputs -> distinct
        // outputs, so `a == b` must FAIL.
        assert(a == b);
    }
}
