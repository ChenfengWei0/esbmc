// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// T1.1 Stage S0 — clone preserves base's pre-clone data.
// Real Solidity: __ESOL_deep_copy(base) yields a clone whose elements
// match base at clone time.  clone.arr[0] should equal base's arr[0].
// Today: clone walker explicitly skips dyn-array fields, so clone's
// element backing is uninitialised (or aliased to base's, depending
// on path).  Will flip to CORE at Stage S3 (T1.1).
function __ESOL_deep_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[] public arr;
    function push(uint256 v) public { arr.push(v); }
    function get(uint256 i) public view returns (uint256) { return arr[i]; }
}

contract H {
    function check(uint256 a) public {
        C base = new C();
        base.push(a);
        C clone = __ESOL_deep_copy(base);
        assert(clone.get(0) == a);
    }
}
