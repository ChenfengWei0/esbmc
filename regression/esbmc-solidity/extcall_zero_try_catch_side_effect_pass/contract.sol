// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IReturnsWord {
    function read() external payable returns (uint256);
}

contract Probe {
    uint256 targetCalls;

    function __ESBMC_reverted() internal returns (bool) {}
    function __ESBMC_assert(bool, string memory) internal {}

    function zeroAddress() internal returns (address) {
        targetCalls++;
        return address(0);
    }

    function check() public {
        bool caught;
        try IReturnsWord(zeroAddress()).read() returns (uint256) {
        } catch {
            caught = true;
        }

        __ESBMC_assert(caught, "zero-address call must be caught");
        __ESBMC_assert(targetCalls == 1, "target evaluated exactly once");
    }

    function checkWrapper() public payable {
        address senderBefore = msg.sender;
        uint256 valueBefore = msg.value;
        uint256 balanceBefore = address(this).balance;
        require(balanceBefore >= 1);
        bool caught;
        IReturnsWord target = IReturnsWord(address(0));

        try target.read{value: 1}() returns (uint256) {
        } catch {
            caught = true;
        }

        __ESBMC_assert(caught, "zero-address value call must be caught");
        __ESBMC_assert(msg.sender == senderBefore, "msg.sender restored");
        __ESBMC_assert(msg.value == valueBefore, "msg.value restored");
        __ESBMC_assert(
            address(this).balance == balanceBefore,
            "failed value call does not transfer balance"
        );
    }

    function sameContract() external returns (uint256) {
        return 1;
    }

    function checkSameContract() public {
        address senderBefore = msg.sender;
        bool caught;
        Probe target = Probe(address(0));

        try target.sameContract() returns (uint256) {
        } catch {
            caught = true;
        }

        __ESBMC_assert(caught, "same-contract zero target must be caught");
        __ESBMC_assert(
            msg.sender == senderBefore,
            "same-contract wrapper preserves msg.sender"
        );
    }
}
