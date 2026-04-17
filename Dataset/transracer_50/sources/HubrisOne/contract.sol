// ===== HUBRIS.sol =====
pragma solidity >=0.8.0;



library SafeMath {

  
  function mul(uint256 a, uint256 b) internal pure returns (uint256) {
    if (a == 0) {
      return 0;
    }
    uint256 c = a * b;
    assert(c / a == b);
    return c;
  }

  
  function div(uint256 a, uint256 b) internal pure returns (uint256) {
    // assert(b > 0); // Solidity automatically throws when dividing by 0
    uint256 c = a / b;
    // assert(a == b * c + a % b); // There is no case in which this doesn't hold
    return c;
  }

  
  function sub(uint256 a, uint256 b) internal pure returns (uint256) {
    assert(b <= a);
    return a - b;
  }

  
  function add(uint256 a, uint256 b) internal pure returns (uint256) {
    uint256 c = a + b;
    assert(c >= a);
    return c;
  }
}




contract Ownable {
  address public owner;


  event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

  
  constructor() {
    owner = msg.sender;
  }

  
  modifier onlyOwner() {
    require(msg.sender == owner);
    _;
  }

  
  function transferOwnership(address newOwner) public onlyOwner {
    require(newOwner != address(0));
    emit OwnershipTransferred(owner, newOwner);
    owner = newOwner;
  }
}

contract Pausable is Ownable {
  event Pause();
  event Unpause();

  bool public paused = false;


  
  modifier whenNotPaused() {
    require(!paused);
    _;
  }

  
  modifier whenPaused() {
    require(paused);
    _;
  }

  
  function pause() onlyOwner whenNotPaused public {
    paused = true;
    emit Pause();
  }

  
  function unpause() onlyOwner whenPaused public {
    paused = false;
    emit Unpause();
  }
}


abstract contract ERC20Basic {
  function totalSupply() public view virtual returns (uint256);
  function balanceOf(address who) public view virtual returns (uint256);
  function transfer(address to, uint256 value) public virtual returns (bool);
  event Transfer(address indexed from, address indexed to, uint256 value);
}

abstract contract ERC20 is ERC20Basic {
  function allowance(address owner, address spender) public view virtual returns (uint256);
  function transferFrom(address from, address to, uint256 value) public virtual returns (bool);
  function approve(address spender, uint256 value) public virtual returns (bool);
  event Approval(address indexed owner, address indexed spender, uint256 value);
}

abstract contract HUBRISTOKEN is ERC20 {
  string public name;
  string public symbol;
  uint8 public decimals;

 constructor(string memory _name, string memory _symbol, uint8 _decimals) {
    name = _name;
    symbol = _symbol;
    decimals = _decimals;
  }
}

contract BasicToken is ERC20Basic {
  

  mapping(address => uint256) balances;

  uint256 totalSupply_;

  
  function totalSupply() public view virtual override returns (uint256) {
    return totalSupply_;
  }

  
  function transfer(address _to, uint256 _value) public virtual override returns (bool) {
    require(_to != address(0));
    require(_value <= balances[msg.sender]);

    // SafeMath.sub will throw if there is not enough balance.
    balances[msg.sender] = balances[msg.sender] - (_value);
    balances[_to] = balances[_to] + (_value);
    emit Transfer(msg.sender, _to, _value);
    return true;
  }

  
  function balanceOf(address _owner) public view virtual override returns (uint256 balance) {
    return balances[_owner];
  }

}

contract Standard is ERC20, BasicToken {

  mapping (address => mapping (address => uint256)) internal allowed;


  function transferFrom(address _from, address _to, uint256 _value) public virtual override returns (bool) {
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


  function allowance(address _owner, address _spender) public view virtual override returns (uint256) {
    return allowed[_owner][_spender];
  }


  function increaseApproval(address _spender, uint _addedValue) public returns (bool) {
    allowed[msg.sender][_spender] = allowed[msg.sender][_spender] + (_addedValue);
    emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
    return true;
  }


  function decreaseApproval(address _spender, uint _subtractedValue) public returns (bool) {
    uint oldValue = allowed[msg.sender][_spender];
    if (_subtractedValue > oldValue) {
      allowed[msg.sender][_spender] = 0;
    } else {
      allowed[msg.sender][_spender] = oldValue - (_subtractedValue);
    }
    emit Approval(msg.sender, _spender, allowed[msg.sender][_spender]);
    return true;
  }

}

contract BurnableToken is BasicToken {

  event Burn(address indexed burner, uint256 value);

  
  function burn(uint256 _value) public {
    _burn(msg.sender, _value);
  }

  function _burn(address _who, uint256 _value) internal virtual {
    require(_value <= balances[_who]);
    // no need to require value <= totalSupply, since that would imply the
    // sender's balance is greater than the totalSupply, which *should* be an assertion failure

    balances[_who] = balances[_who] - (_value);
    totalSupply_ = totalSupply_ - (_value);
    emit Burn(_who, _value);
    emit Transfer(_who, address(0), _value);
  }
}



contract HUBRIS is Ownable, Pausable, Standard, BurnableToken, HUBRISTOKEN {
    

    // duplicates removed (inherited from HUBRISTOKEN); set via constructor

    //token allocation addresses
    address TOKEN_SALE = 0xdff99ef7ed50f9EB06183d0DfeD9CD5DB051878B;
    address EQUITY_SHARE = 0xb2aA0f5c0e2e7f94A26022C076240509C85eDab1;
    address TEAM = 0x922E97d03bEeA115Ab95CC638765d2BebEb04f20;
    address ADVISORS = 0x6FB54a06f94591EAF330c4BdD644c4Ab753eb105;
    address CUSTOMERS = 0x382C33946B73A3B8B7F3E70A553b6965d6F28a48;
    address BOUNTY = 0x1d1390c9d5e08aCEC31991EA7Be7443ad2EEA6e6;
    address RESERVE = 0x79641ae5D204C45038a9cF07c32E39d2EeC23C5c;
    address LEGAL = 0xe49941b4B66D61d98d4766c8EEB3004c0961075B;
    
    bool tokensAllocated = false;

    constructor() HUBRISTOKEN("HUBRIS", "HBRS", 18) {
        totalSupply_ = 1000000000E18;
        balances[address(this)] = totalSupply_;
    }

    function envokeTokenAllocation() public onlyOwner {
        require(!tokensAllocated);
        tokensAllocated = true;
        this.transfer(TOKEN_SALE, 300000000E18); //30% of totalSupply_
        this.transfer(EQUITY_SHARE, 300000000E18); //30% of totalSupply_
        this.transfer(TEAM, 150000000E18); //15% of totalSupply_
        this.transfer(ADVISORS, 30000000E18); //3% of totalSupply_
        this.transfer(CUSTOMERS, 100000000E18); //10% of totalSupply_
        this.transfer(msg.sender, 50000000E18); //5% of totalSupply_
        this.transfer(BOUNTY, 40000000E18); //4% of totalSupply_
        this.transfer(RESERVE, 20000000E18); //2% of totalSupply_
        this.transfer(LEGAL, 10000000E18); //1% of totalSupply_
    }

}