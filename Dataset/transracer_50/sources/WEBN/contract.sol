// ===== WEBN.sol =====
pragma solidity >=0.8.0;

library SafeMath {

  function mul(uint256 a, uint256 b) internal pure returns (uint256 c) {
    // Gas optimization: this is cheaper than asserting 'a' not being zero, but the
    // benefit is lost if 'b' is also tested.
    // See: https://github.com/OpenZeppelin/openzeppelin-solidity/pull/522
    if (a == 0) {
      return 0;
    }

    c = a * b;
    assert(c / a == b);
    return c;
  }

  function div(uint256 a, uint256 b) internal pure returns (uint256) {
    // assert(b > 0); // Solidity automatically throws when dividing by 0
    // uint256 c = a / b;
    // assert(a == b * c + a % b); // There is no case in which this doesn't hold
    return a / b;
  }


  function sub(uint256 a, uint256 b) internal pure returns (uint256) {
    assert(b <= a);
    return a - b;
  }

  function add(uint256 a, uint256 b) internal pure returns (uint256 c) {
    c = a + b;
    assert(c >= a);
    return c;
  }
}

abstract contract ERC20Basic {
  function totalSupply() public view virtual returns (uint256);
  function balanceOf(address who) public view virtual returns (uint256);
  function transfer(address to, uint256 value) public virtual returns (bool);
  event Transfer(address indexed from, address indexed to, uint256 value);
}

abstract contract ERC20 is ERC20Basic {
  function allowance(address owner, address spender)
    public view virtual returns (uint256);

  function transferFrom(address from, address to, uint256 value)
    public virtual returns (bool);

  function approve(address spender, uint256 value) public virtual returns (bool);
  event Approval(
    address indexed owner,
    address indexed spender,
    uint256 value
  );
}

contract BasicToken is ERC20Basic {
  
  // timestamp when token release is enabled
  uint256 public releaseTime = 1536917418;
  address owner;
  
  mapping(address => uint256) balances;

  uint256 totalSupply_;

  function totalSupply() public view virtual override returns (uint256) {
    return totalSupply_;
  }
    
    constructor() {
        owner  = msg.sender;
    }

    modifier notPaused {
        require(block.timestamp > releaseTime  || msg.sender == owner);
        _;
    }
    
  function transfer(address _to, uint256 _value) public virtual override notPaused  returns (bool) {
    require(_to != address(0));
    require(_value <= balances[msg.sender]);

    balances[msg.sender] = balances[msg.sender] - (_value);
    balances[_to] = balances[_to] + (_value);
    emit Transfer(msg.sender, _to, _value);
    return true;
  }

  function balanceOf(address _owner) public view virtual override returns (uint256) {
    return balances[_owner];
  }

}

contract StandardToken is ERC20, BasicToken {

  mapping (address => mapping (address => uint256)) internal allowed;

  function transferFrom(
    address _from,
    address _to,
    uint256 _value
  )
    public virtual override returns (bool) {
    require(_to != address(0));
    require(_value <= balances[_from]);
    require(_value <= allowed[_from][msg.sender]);
    
    balances[_from] = balances[_from] - (_value);
    balances[_to] = balances[_to] + (_value);
    allowed[_from][msg.sender] = allowed[_from][msg.sender] - (_value);
    emit Transfer(_from, _to, _value);
    
   
     return true;   
 
   
  }

  function approve(address _spender, uint256 _value) public virtual override returns (bool) {
    allowed[msg.sender][_spender] = _value;
    emit Approval(msg.sender, _spender, _value);
    return true;
  }

  function allowance(
    address _owner,
    address _spender
   )
    public
    view virtual override returns (uint256) {
    return allowed[_owner][_spender];
  }

  function increaseApproval(
    address _spender,
    uint256 _addedValue
  )
    public
    returns (bool)
  {
    allowed[msg.sender][_spender] = (
      allowed[msg.sender][_spender] + (_addedValue));
    emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
    return true;
  }

  function decreaseApproval(
    address _spender,
    uint256 _subtractedValue
  )
    public
    returns (bool)
  {
    uint256 oldValue = allowed[msg.sender][_spender];
    if (_subtractedValue > oldValue) {
      allowed[msg.sender][_spender] = 0;
    } else {
      allowed[msg.sender][_spender] = oldValue - (_subtractedValue);
    }
    emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
    return true;
  }

}

contract WEBN is StandardToken {

  string public constant name = "WEBN"; 
  string public constant symbol = "WEBN"; 
  uint8 public constant decimals = 8; 

  uint256 public constant INITIAL_SUPPLY = 6000000000 * (10 ** uint256(decimals));

  constructor() {
    totalSupply_ = INITIAL_SUPPLY;
    balances[msg.sender] = INITIAL_SUPPLY;
    emit Transfer(address(0), msg.sender, INITIAL_SUPPLY);
  }

}