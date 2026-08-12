pragma solidity >=0.8.0;

contract C
{
  function ptr(bytes memory value) internal pure returns (uint256 result)
  {
    assembly
    {
      result := add(value, 32)
    }
  }

  function check(bytes memory value) public pure
  {
    ptr(value);
    assert(true);
  }
}
