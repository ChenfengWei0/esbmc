pragma solidity >=0.8.0;

contract C {
  function returnOr(bool a, bool b) public pure returns (bool) {
    return a || b;
  }
}
