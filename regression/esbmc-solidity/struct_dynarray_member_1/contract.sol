// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0 <0.9.0;

// Struct with dynamic array member, accessed via storage ref.
// From Solidity docs "Arrays" section (ArrayContract.StructType).
// KNOWNBUG: ESBMC fails with "Null Element Pointer" assertion when
// assigning a memory array to a struct's dynamic array member via
// storage reference (g.contents = c).

contract ArrayContract {
    struct StructType {
        uint[] contents;
        uint moreInfo;
    }
    StructType s;

    function f(uint[] memory c) public {
        StructType storage g = s;
        g.moreInfo = 2;
        g.contents = c;
    }
}
