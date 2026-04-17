// ===== EthernetCash.sol =====
pragma solidity >=0.8.0;
/*-------------------------------------------------------------------------*/
 /*
  * Website	: https://ethernet.cash
  * Email	: contact(a)ethernet.cash
 */
/*-------------------------------------------------------------------------*/
interface tokenRecipient { function receiveApproval(address _from, uint256 _value, address _token, bytes calldata _extraData) external; }
/*-------------------------------------------------------------------------*/
contract owned {
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner {
        if (msg.sender != owner) revert();
        _;
    }

    function transferOwnership(address newOwner) public onlyOwner {
        if (newOwner == address(0)) revert();
        owner = newOwner;
    }
}
/*-------------------------------------------------------------------------*/
/**
 * Overflow aware uint math functions.
 */
contract SafeMath {
  //internals

  function safeMul(uint a, uint b) internal pure returns (uint) {
    uint c = a * b;
    _assert(a == 0 || c / a == b);
    return c;
  }

  function safeSub(uint a, uint b) internal pure returns (uint) {
    _assert(b <= a);
    return a - b;
  }

  function safeAdd(uint a, uint b) internal pure returns (uint) {
    uint c = a + b;
    _assert(c>=a && c>=b);
    return c;
  }

  function _assert(bool assertion) internal pure {
    if (!assertion) revert();
  }
}
/*-------------------------------------------------------------------------*/
contract EthernetCash is owned, SafeMath {
	
	string 	public EthernetCashWebsite	= "https://ethernet.cash";
	address public EthernetCashAddress 	= address(this);
	address payable public creator 				= payable(msg.sender);
    string 	public name 				= "Ethernet Cash";
    string 	public symbol 				= "ENC";
    uint8 	public decimals 			= 18;											    
    uint256 public totalSupply 			= 19999999986000000000000000000;
    uint256 public buyPrice 			= 1800000;
	uint256 public sellPrice 			= 1800000;
   	
    mapping (address => uint256) public balanceOf;
    mapping (address => mapping (address => uint256)) public allowance;
	mapping (address => bool) public frozenAccount;

    event Transfer(address indexed from, address indexed to, uint256 value);				
    event FundTransfer(address backer, uint amount, bool isContribution);
     // This notifies clients about the amount burnt
    event Burn(address indexed from, uint256 value);
	event FrozenFunds(address target, bool frozen);
    
    /**
     * Constrctor function
     *
     * Initializes contract with initial supply tokens to the creator of the contract
     */
    constructor() {
        balanceOf[msg.sender] = totalSupply;
		creator = payable(msg.sender);
    }
    /**
     * Internal transfer, only can be called by this contract
     */
    function _transfer(address _from, address _to, uint _value) internal {
        // Prevent transfer to 0x0 address. Use burn() instead
        require(_to != address(0));
        // Check if the sender has enough
        require(balanceOf[_from] >= _value);
        // Check for overflows
        require(balanceOf[_to] + _value >= balanceOf[_to]);
        // Subtract from the sender
        balanceOf[_from] -= _value;
        // Add the same to the recipient
        balanceOf[_to] += _value;
        emit Transfer(_from, _to, _value);
    }

    /**
     * Transfer tokens
     *
     * Send `_value` tokens to `_to` from your account
     *
     * @param _to The address of the recipient
     * @param _value the amount to send
     */
    function transfer(address _to, uint256 _value) public {
        _transfer(msg.sender, _to, _value);
    }
    
    /// @notice Buy tokens from contract by sending ether
    receive() external payable {
        uint amount = msg.value * buyPrice ; 
		uint amountRaised;
		uint bonus = 0;
		
		bonus = getBonus(amount);
		amount = amount +  bonus;
		
		//amount = block.timestamp ;
		
        require(balanceOf[creator] >= amount);               				
        require(msg.value > 0);
		amountRaised = safeAdd(amountRaised, msg.value);                    
		balanceOf[msg.sender] = safeAdd(balanceOf[msg.sender], amount);     
        balanceOf[creator] = safeSub(balanceOf[creator], amount);           
        emit Transfer(creator, msg.sender, amount);
        creator.transfer(amountRaised);
    }
	
	/// @notice Create `mintedAmount` tokens and send it to `target`
    /// @param target Address to receive the tokens
    /// @param mintedAmount the amount of tokens it will receive
    function mintToken(address target, uint256 mintedAmount) onlyOwner public {
        balanceOf[target] += mintedAmount;
        totalSupply += mintedAmount;
        emit Transfer(address(0), address(this), mintedAmount);
        emit Transfer(address(this), target, mintedAmount);
    }

	
	/**
     * Set allowance for other address
     *
     * Allows `_spender` to spend no more than `_value` tokens in your behalf
     *
     * @param _spender The address authorized to spend
     * @param _value the max amount they can spend
     */
    function approve(address _spender, uint256 _value) public
        returns (bool success) {
        allowance[msg.sender][_spender] = _value;
        return true;
    }

    /**
     * Set allowance for other address and notify
     *
     * Allows `_spender` to spend no more than `_value` tokens in your behalf, and then ping the contract about it
     *
     * @param _spender The address authorized to spend
     * @param _value the max amount they can spend
     * @param _extraData some extra information to send to the approved contract
     */
    function approveAndCall(address _spender, uint256 _value, bytes calldata _extraData)
        public
        returns (bool success) {
        tokenRecipient spender = tokenRecipient(_spender);
        if (approve(_spender, _value)) {
            spender.receiveApproval(msg.sender, _value, address(this), _extraData);
            return true;
        }
    }
	
    /// @notice `freeze? Prevent | Allow` `target` from sending & receiving tokens
    /// @param target Address to be frozen
    /// @param freeze either to freeze it or not
    function freezeAccount(address target, bool freeze) onlyOwner public {
        frozenAccount[target] = freeze;
        emit FrozenFunds(target, freeze);
    }

    /// @notice Allow users to buy tokens for `newBuyPrice` eth and sell tokens for `newSellPrice` eth
    /// @param newSellPrice Price the users can sell to the contract
    /// @param newBuyPrice Price users can buy from the contract
    function setPrices(uint256 newSellPrice, uint256 newBuyPrice) onlyOwner public {
        sellPrice = newSellPrice;
        buyPrice = newBuyPrice;
    }
	
	
	/**
     * Destroy tokens
     *
     * Remove `_value` tokens from the system irreversibly
     *
     * @param _value the amount of money to burn
     */
    function burn(uint256 _value) public returns (bool success) {
        require(balanceOf[msg.sender] >= _value);   // Check if the sender has enough
        balanceOf[msg.sender] -= _value;            // Subtract from the sender
        totalSupply -= _value;                      // Updates totalSupply
        emit Burn(msg.sender, _value);
        return true;
    }
	
	/**
     * Destroy tokens from other account
     *
     * Remove `_value` tokens from the system irreversibly on behalf of `_from`.
     *
     * @param _from the address of the sender
     * @param _value the amount of money to burn
     */
    function burnFrom(address _from, uint256 _value) public returns (bool success) {
        require(balanceOf[_from] >= _value);                // Check if the targeted balance is enough
        require(_value <= allowance[_from][msg.sender]);    // Check allowance
        balanceOf[_from] -= _value;                         // Subtract from the targeted balance
        allowance[_from][msg.sender] -= _value;             // Subtract from the sender's allowance
        totalSupply -= _value;                              // Update totalSupply
        emit Burn(_from, _value);
        return true;
    }
	
	function getBonus(uint _amount) view private returns (uint256) {
        
		if(block.timestamp >= 1524873600 && block.timestamp <= 1527551999) { 
            return _amount * 50 / 100;
        }
		
		if(block.timestamp >= 1527552000 && block.timestamp <= 1530316799) { 
            return _amount * 40 / 100;
        }
		
		if(block.timestamp >= 1530316800 && block.timestamp <= 1532995199) { 
            return _amount * 30 / 100;
        }
		
		if(block.timestamp >= 1532995200 && block.timestamp <= 1535759999) { 
            return _amount * 20 / 100;
        }
		
		if(block.timestamp >= 1535760000 && block.timestamp <= 1538438399) { 
            return _amount * 10 / 100;
        }
		
        return 0;
    }
	
	/// @notice Sell `amount` tokens to contract
    /// @param amount amount of tokens to be sold
    function sell(uint256 amount) public {
        require(address(this).balance >= amount * sellPrice);      // checks if the contract has enough ether to buy
        _transfer(msg.sender, address(this), amount);              // makes the transfers
        payable(msg.sender).transfer(amount * sellPrice);          // sends ether to the seller. It's important to do this last to avoid recursion attacks
    }
	
 }
/*-------------------------------------------------------------------------*/