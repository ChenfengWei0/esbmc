pragma solidity >=0.8.0;

contract C {
  function check() public pure {
    bytes4 data = 0x01020304;
    uint256 highByte;
    uint256 word;
    assembly {
      word := data
      highByte := shr(248, data)
    }
    assert(word == uint256(0x01020304) << 224);
    assert(highByte == 1);
  }
}
