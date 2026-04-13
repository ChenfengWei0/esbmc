// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.0;

contract C {
    function f1(bytes[1] calldata a)
        external
        pure
        returns (uint256, uint256, uint256, uint256)
    {
        return (a[0].length, uint8(a[0][0]), uint8(a[0][1]), uint8(a[0][2]));
    }

    function g2(bytes[] calldata a) external pure returns (uint256[8] memory) {
        return [
            a.length,
            a[0].length,
            uint8(a[0][0]),
            uint8(a[0][1]),
            a[1].length,
            uint8(a[1][0]),
            uint8(a[1][1]),
            uint8(a[1][2])
        ];
    }
}
