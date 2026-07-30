// Reproducer #4 -- is the defect specific to immutables/ternaries, or does it
// hit ANY local declaration-with-initialiser inside an INHERITED function body?
//
// Nothing here involves an immutable, a base-constructor argument, a ternary or
// block.timestamp. Just `uint256 y = x + 1;` in a base function, called through
// the derived contract.
pragma solidity ^0.8.0;

contract B4 {
    function f(uint256 x) internal pure returns (uint256) {
        uint256 y = x + 1;
        return y;
    }
}

contract D4 is B4 {
    uint256 public out;

    function g(uint256 x) public {
        out = f(x);
    }
}
