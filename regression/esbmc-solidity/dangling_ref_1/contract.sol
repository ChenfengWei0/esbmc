// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0 <0.9.0;

// Dangling storage reference: local storage ref to dynamic array element.
// From Solidity docs "Dangling References to Storage Array Elements".
// KNOWNBUG: ESBMC does not support local storage references to dynamic arrays
// (`uint[] storage ptr = s[0]`). Only struct storage refs are supported.
// Error: "Unexpect initialization for dynamic array"

contract DanglingRef {
    uint[][] s;

    function f() public {
        s.push();
        assert(s.length == 1);

        uint[] storage ptr = s[s.length - 1];
        s.pop();
        ptr.push(0x42);
        s.push();
        assert(s[s.length - 1][0] == 0x42);
    }
}
