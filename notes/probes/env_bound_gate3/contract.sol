// Discriminator for "an environment bound is counted but not applied".
//
// Here the PATH depends on msg.sender, which Gate2's did not. Two certification
// queries that differ ONLY in the msg.sender bound must therefore give DIFFERENT
// verdicts if the bound binds, and the SAME verdict if it does not.
pragma solidity ^0.8.0;

contract Gate3 {
    uint256 public sink;
    address constant BANNED = address(0x00000000000000000000000000000000000000ff);

    function send(uint256 x) external payable returns (uint256) {
        require(msg.sender != BANNED);
        sink = x;
        return 1;
    }
}
