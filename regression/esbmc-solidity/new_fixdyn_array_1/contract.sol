// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0 <0.9.0;

// new T[N][](size) — allocating a dynamic array of fixed-size sub-arrays.
// From Solidity docs "Arrays" section (ArrayContract.createMemoryArray/clear).
// KNOWNBUG: ESBMC crashes with json type_error when processing `new T[N][](size)`
// expressions — the array size extraction for nested fixed/dynamic array types
// encounters a null field.

contract ArrayContract {
    bool[2][] pairsOfFlags;

    function clear() public {
        pairsOfFlags = new bool[2][](0);
    }

    function createMemoryArray(uint size) public pure returns (bytes memory) {
        uint[2][] memory arrayOfPairs = new uint[2][](size);
        arrayOfPairs[0] = [uint(1), 2];
        bytes memory b = new bytes(200);
        for (uint i = 0; i < b.length; i++)
            b[i] = bytes1(uint8(i));
        return b;
    }
}
