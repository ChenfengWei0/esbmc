// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IReturnsWord {
    function read() external returns (uint256);
}

contract Probe {
    uint256 marker = 7;

    function __ESBMC_reverted() internal returns (bool) {}
    function __ESBMC_assert(bool, string memory) internal {}

    function check() public {
        bool caught;
        IReturnsWord target = IReturnsWord(address(0));

        try target.read() returns (uint256) {
        } catch {
            caught = true;
        }

        __ESBMC_assert(caught, "unbound zero target must be caught");
        __ESBMC_assert(marker == 7, "invalid external call cannot havoc state");
    }
}
