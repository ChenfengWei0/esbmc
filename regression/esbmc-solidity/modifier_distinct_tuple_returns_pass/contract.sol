pragma solidity >=0.8.0;

contract C
{
  modifier allowed()
  {
    _;
  }

  function narrow() public pure allowed returns (bool, uint32, uint32)
  {
    return (true, 2, 3);
  }

  function wide() public pure allowed returns (uint256, uint256)
  {
    return (type(uint256).max, type(uint256).max - 1);
  }

  function check() public pure
  {
    (bool a, uint32 b, uint32 c) = narrow();
    (uint256 x, uint256 y) = wide();
    assert(a && b == 2 && c == 3);
    assert(x == type(uint256).max && y == type(uint256).max - 1);
  }
}
