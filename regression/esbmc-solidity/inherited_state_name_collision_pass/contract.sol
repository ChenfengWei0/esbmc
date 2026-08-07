// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0;

contract A {
  uint256 internal x;

  constructor(uint256 a) {
    x = a;
  }

  function ax() internal view returns (uint256) {
    return x;
  }
}

contract B {
  uint256 private x;

  constructor(uint256 b) {
    x = b;
  }

  function bx() internal view returns (uint256) {
    return x;
  }
}

contract C is A, B {
  constructor() A(1) B(2) {}

  function check() public view {
    assert(ax() == 1);
    assert(bx() == 2);
  }
}
