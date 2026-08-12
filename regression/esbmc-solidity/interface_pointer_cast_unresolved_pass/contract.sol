// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

interface IERC20 {}

interface IWETH {}

contract C {
  IWETH public weth;

  function check() public view returns (bool) {
    IERC20 token = IERC20(address(weth));
    return address(token) == address(weth);
  }
}
