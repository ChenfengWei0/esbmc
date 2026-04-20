// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// Fixed-size array field — the clone must carry every element.
function __ESOL_shallow_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[3] public arr;
    function setAt(uint256 i, uint256 v) public { arr[i] = v; }
    function get(uint256 i) public view returns (uint256) { return arr[i]; }
}

contract H {
    function check(uint256 v0, uint256 v1, uint256 v2) public {
        C base = new C();
        base.setAt(0, v0);
        base.setAt(1, v1);
        base.setAt(2, v2);
        C clone = __ESOL_shallow_copy(base);
        assert(clone.get(0) == v0);
        assert(clone.get(1) == v1);
        assert(clone.get(2) == v2);
    }
}
