pragma solidity ^0.8.0;

function __ESBMC_assume(bool) pure {}

contract CA {
  uint256 public x;

  function f(uint256 a) public payable returns (uint256) {
    __ESBMC_assume(false);
    if (a > 3) {
      x = 1;
    } else {
      x = 2;
    }
    return x;
  }
}
