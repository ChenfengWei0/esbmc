pragma solidity >=0.8.0;

contract C {
  function check() public pure {
    bytes4 data = 0x01020304;
    uint256 highByte;
    assembly {
      highByte := shr(248, data)
    }
    assert(highByte == 0);
  }
}
