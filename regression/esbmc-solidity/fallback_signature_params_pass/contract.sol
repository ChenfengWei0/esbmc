pragma solidity >=0.8.0;

contract FallbackSignatureParams {
  uint256 public created;
  uint256 public received;

  constructor(uint256 seed) {
    created = seed;
  }

  fallback(bytes calldata data) external returns (bytes memory) {
    return data;
  }

  receive() external payable {
    received += msg.value;
  }

  function ping() public pure returns (uint256) {
    return 1;
  }
}
