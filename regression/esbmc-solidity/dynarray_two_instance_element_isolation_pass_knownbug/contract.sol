// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// T1.1 Stage S0 — two-instance element isolation, NO clone.
// Real Solidity: c1's push doesn't reach c2's element [0]; if c1 pushed v
// and c2 also pushed (with a default 0), c2.arr[0] must be 0, not v.
// Today: shared global `arr` makes c2 see c1's push.
// Will flip to CORE at Stage S2 (T1.1).
contract C {
    uint256[] public arr;
    function push(uint256 v) public { arr.push(v); }
    function get(uint256 i) public view returns (uint256) { return arr[i]; }
}

contract H {
    function check(uint256 v) public {
        if (v == 0) return;  // make the FAIL distinguishable from default 0
        C c1 = new C();
        C c2 = new C();
        c1.push(v);
        c2.push(0);  // c2 has its own length-1 array with element 0
        assert(c2.get(0) == 0);
    }
}
