pragma solidity ^0.8.0;

contract ExactR2
{
  address owner;

  constructor()
  {
    owner = address(uint160(7));
  }

  function get() external payable returns (address)
  {
    return owner;
  }
}
