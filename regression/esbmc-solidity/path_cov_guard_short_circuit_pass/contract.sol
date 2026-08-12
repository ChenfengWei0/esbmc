pragma solidity >=0.8.0;

contract C {
  function gate(bool a, bool b) public pure returns (uint256) {
    if (a && b)
      return 1;
    return 0;
  }
}
