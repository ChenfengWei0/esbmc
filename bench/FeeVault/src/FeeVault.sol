pragma solidity ^0.8.0;

/// The motivation example of the paper (Listing 1), with the two transfers and
/// the events removed: the first vertical slice does not touch external calls.
contract FeeVault {
    address public owner = msg.sender;
    address public feeReceiver = msg.sender;
    uint16 public constant feeBps = 250;
    uint256 public constant maxFee = 1e17;
    mapping(address => uint256) public deposits;
    mapping(address => uint16) public discountBps;

    function deposit() external payable {
        deposits[msg.sender] += msg.value;
    }

    function setDiscount(address u, uint16 bps) external {
        require(msg.sender == owner && bps <= feeBps);
        discountBps[u] = bps;
    }

    function withdraw(uint256 amount) external returns (uint256 net) {
        uint256 d = deposits[msg.sender];
        require(amount > 0 && amount <= d);
        uint256 rate = feeBps;
        if (discountBps[msg.sender] > 0) rate = feeBps - discountBps[msg.sender];
        uint256 fee = amount * rate / 10000;
        if (fee > maxFee) fee = maxFee;
        net = amount - fee;
        deposits[msg.sender] = d - amount;
    }
}
