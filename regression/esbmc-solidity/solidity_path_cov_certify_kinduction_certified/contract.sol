pragma solidity >=0.8.0;

contract Flag
{
  bool public flag = true;
  uint256 public sink;

  function f() external payable returns (uint256)
  {
    if (flag)
    {
      sink = 1;
      return 1;
    }
    sink = 2;
    return 0;
  }
}
