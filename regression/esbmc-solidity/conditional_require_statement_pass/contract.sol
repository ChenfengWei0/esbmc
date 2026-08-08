// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0;

contract ConditionalRequireStatement
{
  function gate(uint256 x, bool takeLeft) public pure returns (uint256)
  {
    takeLeft ? require(x > 3, "small") : require(x < 9, "large");
    return x;
  }
}
