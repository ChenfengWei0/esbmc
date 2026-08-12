pragma solidity >=0.8.0;

contract C {
  function assignTernary(bool a) public pure returns (bool) {
    bool z = a ? true : false;
    return z;
  }

  function returnOr(bool a, bool b) public pure returns (bool) {
    return a || b;
  }
}
