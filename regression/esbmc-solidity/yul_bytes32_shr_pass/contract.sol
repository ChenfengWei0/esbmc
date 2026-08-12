pragma solidity >=0.8.0;

contract C {
  function check() public pure {
    bytes32 data = hex"0100000000000000000000000000000000000000000000000000000000000000";
    uint256 highByte;
    bool highByteIsZero;
    assembly {
      highByte := shr(248, data)
      highByteIsZero := iszero(shr(248, data))
    }
    assert(highByte == 1);
    assert(!highByteIsZero);
  }
}
