// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IReturnsWord {
    function read() external returns (uint256);
}

contract Probe {
    function __ESBMC_reverted() internal returns (bool) {}
    function __ESBMC_assert(bool, string memory) internal {}

    function check() public {
        try IReturnsWord(address(0)).read() returns (uint256) {
        } catch {
            __ESBMC_assert(false, "zero-address call must reach catch");
        }
    }
}
