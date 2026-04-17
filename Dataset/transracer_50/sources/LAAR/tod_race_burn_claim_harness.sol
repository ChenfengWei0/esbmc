// Auto-generated TOD (Transaction Order Dependence) harness
// Contract: LAAR
// Pair:     burn vs claim
// Mode:     race

// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// TOD classification helpers.  An assertion failure inside one
// of these functions tells the user which TOD category fired.
function __tod_race_check(bool cond) pure {
    assert(cond); // TOD-Race: non-commutative state update
}
function __tod_balance_check(bool cond) pure {
    assert(cond); // TOD-Balance: order-dependent ETH movement
}

// ===== Dependencies =====
library SafeMath 
{

  

  function mul(uint256 a, uint256 b) internal pure returns(uint256 c) 
  {
     if (a == 0) 
     {
     	return 0;
     }
     c = a * b;
     assert(c / a == b);
     return c;
  }

  

  function div(uint256 a, uint256 b) internal pure returns(uint256) 
  {
     return a / b;
  }

  

  function sub(uint256 a, uint256 b) internal pure returns(uint256) 
  {
     assert(b <= a);
     return a - b;
  }

  

  function add(uint256 a, uint256 b) internal pure returns(uint256 c) 
  {
     c = a + b;
     assert(c >= a);
     return c;
  }
}

abstract contract ERC20Interface
{
    function totalSupply() public view virtual returns (uint256);
    function balanceOf(address _who) public view virtual returns (uint256);
    function transfer(address _to, uint256 _value) public virtual returns (bool);
    function allowance(address _owner, address _spender) public view virtual returns (uint256);
    function transferFrom(address _from, address _to, uint256 _value) public virtual returns (bool);
    function approve(address _spender, uint256 _value) public virtual returns (bool);

    event Approval(address indexed _owner, address indexed _spender, uint256 _value);
    event Transfer(address indexed _from, address indexed _to, uint256 _value);
}

// ===== End dependencies =====

// ===== Target contract =====
contract LAAR is ERC20Interface
{
    using SafeMath for uint256;
   
    uint256 constant public TOKEN_DECIMALS = 10 ** 18;
    string public constant name            = "LaariCoin";
    string public constant symbol          = "LAAR";
    uint256 public totalTokenSupply        = 42000000 * TOKEN_DECIMALS;
    uint256 public totalSaleSupply         = 21000000 * TOKEN_DECIMALS;
    uint256 public totalReserveSupply      = 21000000 * TOKEN_DECIMALS;

    uint8 public constant decimals         = 18;
    address public owner;
    uint256 public totalBurned;
    bool public stopped = false;

    event Burn(address indexed _burner, uint256 _value);
    event OwnershipTransferred(address indexed _previousOwner, address indexed _newOwner);

    struct ClaimLimit 
    {
       uint256 time_limit_epoch;
       bool    limitSet;
    }

     
    mapping(address => ClaimLimit) public claimLimits;
    mapping(address => uint256) public  balances;
    mapping(address => mapping(address => uint256)) internal  allowed;
 
    

    modifier onlyOwner() 
    {
       require(msg.sender == owner);
       _;
    }
    
    

    constructor() {
       owner = msg.sender;
       balances[address(this)] = totalSaleSupply;

       emit Transfer(address(0x0), address(this), balances[address(this)]);
    }


    

    function pauseCrowdSale() external onlyOwner
    {
        stopped = true;
    }

    

    function resumeCrowdSale() external onlyOwner
    {
        stopped = false;
    }

    

    function initReserveWallet(address _reserveWallet) onlyOwner public 
    {
       require(!stopped);
       require( _reserveWallet != address(0x0)); 

       balances[_reserveWallet] = totalReserveSupply;

       emit Transfer(address(0x0), _reserveWallet, balances[_reserveWallet]);
    }

    

     function burn(uint256 _value) onlyOwner public returns (bool) 
     {
        require(!stopped);
        require(_value <= balances[msg.sender]);

        address burner = msg.sender;

        balances[burner] = balances[burner].sub(_value);
        totalTokenSupply = totalTokenSupply.sub(_value);
        totalBurned      = totalBurned.add(_value);

        emit Burn(burner, _value);
        emit Transfer(burner, address(0x0), _value);
        return true;
     }     

     

     function totalSupply() public view virtual override returns(uint256 _totalSupply) {
        _totalSupply = totalTokenSupply;
        return _totalSupply;
     }

    

    function balanceOf(address _owner) public view virtual override returns (uint256) {
       return balances[_owner];
    }

    

    function transferFrom(address _from, address _to, uint256 _value) public virtual override returns (bool) {
       require(!stopped);

       if (_value == 0) 
       {
           emit Transfer(_from, _to, _value);  // Follow the spec to launch the event when value is equal to 0
           return true;
       }

       require(!claimLimits[msg.sender].limitSet, "Limit is set and use claim");
       require(_to != address(0x0));
       require(balances[_from] >= _value && allowed[_from][msg.sender] >= _value && _value >= 0);

       balances[_from] = balances[_from].sub(_value);
       allowed[_from][msg.sender] = allowed[_from][msg.sender].sub(_value);
       balances[_to] = balances[_to].add(_value);

       emit Transfer(_from, _to, _value);
       return true;
    }

    

    function transferTo(address _address, uint256 _tokens) external onlyOwner returns(bool) 
    {
       require( _address != address(0x0)); 
       require( balances[address(this)] >= _tokens.mul(TOKEN_DECIMALS) && _tokens.mul(TOKEN_DECIMALS) > 0);

       balances[address(this)] = ( balances[address(this)]).sub(_tokens.mul(TOKEN_DECIMALS));
       balances[_address] = (balances[_address]).add(_tokens.mul(TOKEN_DECIMALS));

       emit Transfer(address(this), _address, _tokens.mul(TOKEN_DECIMALS));
       return true;
    }

    

    function approve(address _spender, uint256 _tokens) public virtual override returns(bool) {
       require(!stopped);
       require(_spender != address(0x0));

       allowed[msg.sender][_spender] = _tokens;

       emit Approval(msg.sender, _spender, _tokens);
       return true;
    }

    

    function allowance(address _owner, address _spender) public view virtual override returns(uint256) {
       require(!stopped);
       require(_owner != address(0x0) && _spender != address(0x0));

       return allowed[_owner][_spender];
    }

    

    function transfer(address _address, uint256 _tokens) public virtual override returns(bool) {
       require(!stopped);

       if (_tokens == 0) 
       {
           emit Transfer(msg.sender, _address, _tokens);  // Follow the spec to launch the event when tokens are equal to 0
           return true;
       }

       require(!claimLimits[msg.sender].limitSet, "Limit is set and use claim");
       require(_address != address(0x0));
       require(balances[msg.sender] >= _tokens);

       balances[msg.sender] = (balances[msg.sender]).sub(_tokens);
       balances[_address] = (balances[_address]).add(_tokens);

       emit Transfer(msg.sender, _address, _tokens);
       return true;
    }

    

    function transferOwnership(address _newOwner)public onlyOwner
    {
       require(!stopped);
       require( _newOwner != address(0x0));

       balances[_newOwner] = (balances[_newOwner]).add(balances[owner]);
       balances[owner] = 0;
       owner = _newOwner;

       emit Transfer(msg.sender, _newOwner, balances[_newOwner]);
   }

   

   function increaseApproval(address _spender, uint256 _addedValue) public returns (bool) 
   {
      require(!stopped);

      allowed[msg.sender][_spender] = allowed[msg.sender][_spender].add(_addedValue);

      emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
      return true;
   }

   

   function decreaseApproval(address _spender, uint256 _subtractedValue) public returns (bool) 
   {
      uint256 oldValue = allowed[msg.sender][_spender];

      require(!stopped);

      if (_subtractedValue > oldValue) 
      {
         allowed[msg.sender][_spender] = 0;
      }
      else 
      {
         allowed[msg.sender][_spender] = oldValue.sub(_subtractedValue);
      }

      emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
      return true;
   }

   

   function claim(address _recipient) public
   {
      require(_recipient != address(0x0), "Invalid recipient");
      require(msg.sender != _recipient, "Self transfer");
      require(claimLimits[msg.sender].limitSet, "Limit not set");

      require (block.timestamp > claimLimits[msg.sender].time_limit_epoch, "Time limit");
       
      uint256 tokens = balances[msg.sender];
       
      balances[msg.sender] = (balances[msg.sender]).sub(tokens);
      balances[_recipient] = (balances[_recipient]).add(tokens);
       
      emit Transfer(msg.sender, _recipient, tokens);
   }
 
   

   function setClaimLimit(address _address, uint256 _days) public onlyOwner
   {
      require(balances[_address] > 0, "No tokens");

      claimLimits[_address].time_limit_epoch = (block.timestamp + ((_days).mul(1 days)));
   		
      claimLimits[_address].limitSet = true;
   }

   

   function resetClaimLimit(address _address) public onlyOwner
   {
      claimLimits[_address].limitSet = false;
   }

}

// ===== TOD harness =====
// ----- burn vs claim -----
// Shared state variables (touched by both):
//   - totalTokenSupply
//   - totalBurned
//   - balances
contract TOD_burn_claim {
    function test(
        LAAR c1,
        LAAR c2,
        uint256 a__value,
        address b__recipient
    ) public {
        require(address(c1) != address(c2), "isolate c1/c2");
        // Order 1: c1 runs burn then claim
        try c1.burn(a__value) {} catch {}
        try c1.claim(b__recipient) {} catch {}

        // Order 2: c2 runs claim then burn
        try c2.claim(b__recipient) {} catch {}
        try c2.burn(a__value) {} catch {}

        // Race check: if any shared state differs the pair is order-dependent
        __tod_race_check(c1.totalTokenSupply() == c2.totalTokenSupply());
        __tod_race_check(c1.totalBurned() == c2.totalBurned());
        __tod_race_check(c1.balances(b__recipient) == c2.balances(b__recipient));
        __tod_race_check(c1.balances(address(this)) == c2.balances(address(this)));
    }
}

