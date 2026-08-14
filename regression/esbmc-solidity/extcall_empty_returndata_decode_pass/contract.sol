// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IReturnsWord {
    function read() external returns (uint256);
}

contract WithGetter {
    uint256 public value;
}

contract Probe {
    uint256 calls;

    function zeroAddress() internal returns (address) {
        assert(calls == 0);
        calls++;
        return address(0);
    }

    function checkLiteral() public {
        IReturnsWord(address(0)).read();
        assert(false);
    }

    function checkComputed() public {
        IReturnsWord(zeroAddress()).read();
        assert(false);
    }

    function checkTypedVariable() public {
        IReturnsWord target = IReturnsWord(address(0));
        target.read();
        assert(false);
    }

    function checkGetter() public {
        WithGetter getter = WithGetter(address(0));
        getter.value();
        assert(false);
    }
}
