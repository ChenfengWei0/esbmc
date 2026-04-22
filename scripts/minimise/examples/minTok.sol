pragma solidity >=0.8.0;

contract T {
    address public owner;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    modifier onlyOwner {
        if (msg.sender != owner) {
            revert();
        } else {
            _;
        }
    }

    constructor() {
        owner = msg.sender;
        totalSupply = 10000;
    }

    function mintToken(address target, uint256 mintedAmount) public onlyOwner {
        balanceOf[target] += mintedAmount;
        totalSupply += mintedAmount;
    }
}
