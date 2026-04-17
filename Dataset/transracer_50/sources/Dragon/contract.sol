// ===== Dragon.sol =====
pragma solidity >=0.8.0;





abstract contract tokenRecipient {
    function receiveApproval(address _from, uint256 _value, address _token, bytes memory _extraData) public virtual;
}


abstract contract ERC20 {

    function totalSupply() external view virtual returns(uint _totalSupply);

    function balanceOf(address who) external view virtual returns(uint256);

    function transfer(address to, uint256 value) external virtual returns(bool ok);

    function transferFrom(address from, address to, uint256 value) external virtual returns(bool ok);

    function approve(address spender, uint256 value) external virtual returns(bool ok);

    function allowance(address owner, address spender) external view virtual returns(uint256);
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

}

contract Burner { function dragonHandler( uint256 _amount) public {} }
 
contract Dragon is ERC20 {


    string public standard = 'DRAGON 1.2';
    string public constant name = "DRAGON";
    string public constant symbol = "DRG";
    uint8 public decimals;
    uint256 public override totalSupply;
  
    
    address public owner;
    mapping( address => uint256) public override balanceOf;
    mapping( uint => address) public accountIndex;
    uint public accountCount;
    
    mapping(address => mapping(address => uint256)) public override allowance;
    address public burner;
    bool public burnerSet;
    
    event Burn(address indexed from, uint256 value);

    
    constructor() {
         
        uint supply = 50000000000000000; 
        appendTokenHolders( msg.sender );
        balanceOf[msg.sender] =  supply;
        totalSupply = supply; // 
        burnerSet = false;
        
        decimals = 8; // Amount of decimals for display purposes
        owner = msg.sender;
        
  
    }
    
    
    modifier onlyOwner() {
        if (msg.sender != owner) {
            revert();
        }
        _;
    }

     modifier onlyBurner() {
        if (msg.sender != burner) {
            revert();
        }
        _;
    }
    
    function changeOwnership( address _owner ) public onlyOwner {
        
        owner = _owner;
        
    }
    
    function setBurner( address _burner ) public onlyOwner {
        require ( !burnerSet );
        burner = _burner;
        burnerSet = true;
        
    }

    function burnCheck( address _tocheck , uint256 amount ) internal {
        
        if ( _tocheck != burner )return;
        
        Burner burn = Burner ( burner );
        burn.dragonHandler( amount );
        
        
    }
    
    function burnDragons ( uint256 _amount ) public onlyBurner{
        
        
        burn( _amount );
        
        
    }
    
    function getAccountCount() public view returns(uint256) {

        return accountCount;
    }

    function getAddress(uint256 slot) public view returns(address) {

        return accountIndex[slot];

    }

    
    function appendTokenHolders(address tokenHolder) private {

        if (balanceOf[tokenHolder] == 0) {
            if ( tokenHolder == burner ) return;
            accountIndex[accountCount] = tokenHolder;
            accountCount++;
        }

    }

    
    function transfer(address _to, uint256 _value) public virtual override returns(bool ok) {   
        if (balanceOf[msg.sender] < _value) revert(); 
        if (balanceOf[_to] + _value < balanceOf[_to]) revert();
        
        appendTokenHolders(_to);
        balanceOf[msg.sender] -= _value; 
        balanceOf[_to] += _value; 
        emit Transfer(msg.sender, _to, _value);
        burnCheck( _to, _value );
        
        return true;
    }
    
    function approve(address _spender, uint256 _value) public virtual override returns(bool success) {        allowance[msg.sender][_spender] = _value;
        emit Approval( msg.sender ,_spender, _value);
        return true;
    }

 
    function approveAndCall(address _spender, uint256 _value, bytes memory _extraData) public returns(bool success) {
        tokenRecipient spender = tokenRecipient(_spender);
        if (approve(_spender, _value)) {
            spender.receiveApproval(msg.sender, _value, address(this), _extraData);
            return true;
        }
    }

    function transferFrom(address _from, address _to, uint256 _value) public virtual override returns(bool success) {
     
        if (balanceOf[_from] < _value) revert();  
        if (balanceOf[_to] + _value < balanceOf[_to]) revert();  
        if (_value > allowance[_from][msg.sender]) revert(); 
        appendTokenHolders(_to);
        balanceOf[_from] -= _value; 
        balanceOf[_to] += _value; 
        allowance[_from][msg.sender] -= _value;
        emit Transfer(_from, _to, _value);
       
        return true;
    }
  
    function burn(uint256 _value) public returns(bool success) {
        if (balanceOf[msg.sender] < _value) revert(); 
        if( totalSupply -  _value < 2100000000000000) revert();
        balanceOf[msg.sender] -= _value; 
        totalSupply -= _value; 
        emit Burn(msg.sender, _value);
        return true;
    }

    function burnFrom(address _from, uint256 _value) public returns(bool success) {
        
        if( totalSupply -  _value < 2100000000000000) revert();
        if (balanceOf[_from] < _value) revert(); 
        if (_value > allowance[_from][msg.sender]) revert(); 
        balanceOf[_from] -= _value; 
        allowance[_from][msg.sender] -= _value; 
        totalSupply -= _value; 
        emit Burn(_from, _value);
        return true;
    }
 
    
}