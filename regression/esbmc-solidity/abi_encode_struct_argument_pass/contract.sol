pragma solidity >=0.8.0;

contract C
{
  struct Params
  {
    address sender;
    uint256 amount;
    bool enabled;
    bytes data;
  }

  function check(
    address sender,
    uint256 amount,
    bool enabled,
    bytes memory data,
    bool[] memory flags) public pure
  {
    Params memory params = Params(sender, amount, enabled, data);
    abi.encode(params, flags);
    assert(true);
  }
}
