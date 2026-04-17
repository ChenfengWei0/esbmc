// ===== Scale.sol =====
pragma solidity >=0.8.0;


abstract contract ERC20Basic {
  uint256 public totalSupply;
  function balanceOf(address who) public view virtual returns (uint256);
  function transfer(address to, uint256 value) public virtual returns (bool);
  event Transfer(address indexed from, address indexed to, uint256 value);
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


contract BasicToken is ERC20Basic {
  

  mapping(address => uint256) balances;

  
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


abstract contract ERC20 is ERC20Basic {
  function allowance(address owner, address spender) public view virtual returns (uint256);
  function transferFrom(address from, address to, uint256 value) public virtual returns (bool);
  function approve(address spender, uint256 value) public virtual returns (bool);
  event Approval(address indexed owner, address indexed spender, uint256 value);
}


contract StandardToken is ERC20, BasicToken {

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



contract MintableToken is StandardToken, Ownable {
  event Mint(address indexed to, uint256 amount);

  
  function mint(address _to, uint256 _amount) internal returns (bool) {
    totalSupply = totalSupply + (_amount);
    balances[_to] = balances[_to] + (_amount);
    emit Mint(_to, _amount);
    emit Transfer(address(0), _to, _amount);
    return true;
  }

}


contract HasNoEther is Ownable {

  
  constructor() public payable {
    require(msg.value == 0);
  }

  
  fallback() external {
  }

  
  function reclaimEther() external onlyOwner {
    assert(payable(owner).send(address(this).balance));
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
    totalSupply = totalSupply - (_value);
    emit Burn(_who, _value);
    emit Transfer(_who, address(0), _value);
  }
}




contract Scale is MintableToken, HasNoEther, BurnableToken {

    // Libraries

    // Token Information

    string public constant name = "SCALE";
    string public constant symbol = "SCALE";
    uint8 public constant  decimals = 18;

    // Variables For Staking and Pooling


    // -- Pool Minting Rates and Percentages -- //
    // Pool for Scale distribution to rewards pool
    // Set to 0 to prohibit issuing to the pool before it is assigned
    address public pool = address(0);

    // Pool and Owner minted tokens per second
    uint public poolMintRate;
    uint public ownerMintRate;

    // Amount of Scale to be staked to the pool, staking, and owner, as calculated through their percentages
    uint public poolMintAmount;
    uint public stakingMintAmount;
    uint public ownerMintAmount;

    // Scale distribution percentages
    uint public poolPercentage = 70;
    uint public ownerPercentage = 5;
    uint public stakingPercentage = 25;

    // Last time minted for owner and pool
    uint public ownerTimeLastMinted;
    uint public poolTimeLastMinted;

    // -- Staking -- //
    // Minted tokens per second
    uint public stakingMintRate;

    // Total Scale currently staked
    uint public totalScaleStaked;

    // Mapping of the timestamp => totalStaking that is created each time an address stakes or unstakes
    mapping (uint => uint) totalStakingHistory;

    // Variable for staking accuracy. Set to 86400 for seconds in a day so that staking gains are based on the day an account begins staking.
    uint timingVariable = 86400;

    // Address staking information
    struct AddressStakeData {
        uint stakeBalance;
        uint initialStakeTime;
        uint unstakeTime;
        mapping (uint => uint) stakePerDay;
    }

    // Track all tokens staked
    mapping (address => AddressStakeData) public stakeBalances;

    // -- Inflation -- //
    // Inflation rate begins at 100% per year and decreases by 30% per year until it reaches 10% where it decreases by 0.5% per year
    uint256 inflationRate = 1000;

    // Used to manage when to inflate. Allowed to inflate once per year until the rate reaches 1%.
    uint256 public lastInflationUpdate;

    // -- Events -- //
    // Fired when tokens are staked
    event Stake(address indexed staker, uint256 value);
    // Fired when tokens are unstaked
    event Unstake(address indexed unstaker, uint256 stakedAmount);
    // Fired when a user claims their stake
    event ClaimStake(address indexed claimer, uint256 stakedAmount, uint256 stakingGains);




    constructor() {
        // Assign owner
        owner = msg.sender;

        // Assign initial owner supply
        uint _initOwnerSupply = 10000000 ether;
        // Mint given to owner only one-time
        bool _success = mint(msg.sender, _initOwnerSupply);
        // Require minting success
        require(_success);

        // Set pool and owner last minted to ensure extra coins are not minted by either
        ownerTimeLastMinted = block.timestamp;
        poolTimeLastMinted = block.timestamp;

        // Set minting amount for pool, staking, and owner over the course of 1 year
        poolMintAmount = _initOwnerSupply * (poolPercentage) / (100);
        ownerMintAmount = _initOwnerSupply * (ownerPercentage) / (100);
        stakingMintAmount = _initOwnerSupply * (stakingPercentage) / (100);

        // One year in seconds
        uint _oneYearInSeconds = 31536000 ether;

        // Set the rate of coins minted per second for the pool, owner, and global staking
        poolMintRate = calculateFraction(poolMintAmount, _oneYearInSeconds, decimals);
        ownerMintRate = calculateFraction(ownerMintAmount, _oneYearInSeconds, decimals);
        stakingMintRate = calculateFraction(stakingMintAmount, _oneYearInSeconds, decimals);

        // Set the last time inflation was updated to block.timestamp so that the next time it can be updated is 1 year from block.timestamp
        lastInflationUpdate = block.timestamp;
    }

    // Inflation



    function adjustInflationRate() private {
      // Make sure adjustInflationRate cannot be called for at least another year
      lastInflationUpdate = block.timestamp;

      // Decrease inflation rate by 30% each year
      if (inflationRate > 100) {
        inflationRate = inflationRate - (300);
      }
      // Inflation rate reaches 10%. Decrease inflation rate by 0.5% from here on out until it reaches 1%.
      else if (inflationRate > 10) {
        inflationRate = inflationRate - (5);
      }

      adjustMintRates();
    }

    function adjustMintRates() internal {

      // Calculate new mint amount of Scale that should be created per year.
      poolMintAmount = totalSupply * (inflationRate) / (1000) * (poolPercentage) / (100);
      ownerMintAmount = totalSupply * (inflationRate) / (1000) * (ownerPercentage) / (100);
      stakingMintAmount = totalSupply * (inflationRate) / (1000) * (stakingPercentage) / (100);

      // Adjust Scale created per-second for each rate
      poolMintRate = calculateFraction(poolMintAmount, 31536000 ether, decimals);
      ownerMintRate = calculateFraction(ownerMintAmount, 31536000 ether, decimals);
      stakingMintRate = calculateFraction(stakingMintAmount, 31536000 ether, decimals);
    }

    function updateInflationRate() public {

      // Require 1 year to have passed for every inflation adjustment
      require(block.timestamp - (lastInflationUpdate) >= 31536000);

      adjustInflationRate();
    }

    // Staking


    function stake(uint _stakeAmount) external {
        // Require that tokens are staked successfully
        require(stakeScale(msg.sender, _stakeAmount));
    }

   function stakeFor(address _user, uint _amount) external {
        // Stake for the user
        require(stakeScale(_user, _amount));
   }


   function transferFromContract(uint _value) internal {

     // Sanity check to make sure we are not transferring more than the contract has
     require(_value <= balances[address(this)]);

     // Add to the msg.sender balance
     balances[msg.sender] = balances[msg.sender] + (_value);
     
     // Subtract from the contract's balance
     balances[address(this)] = balances[address(this)] - (_value);

     // Fire an event for transfer
     emit Transfer(address(this), msg.sender, _value);
   }


   function stakeScale(address _user, uint256 _value) private returns (bool success) {

       // You can only stake / stakeFor as many tokens as you have
       require(_value <= balances[msg.sender]);

       // Require the user is not in power down period
       require(stakeBalances[_user].unstakeTime == 0);

       // Transfer tokens to contract address
       transfer(address(this), _value);

       // Now as a day
       uint _nowAsDay = block.timestamp / (timingVariable);

       // Adjust the new staking balance
       uint _newStakeBalance = stakeBalances[_user].stakeBalance + (_value);

       // If this is the initial stake time, save
       if (stakeBalances[_user].stakeBalance == 0) {
         // Save the time that the stake started
         stakeBalances[_user].initialStakeTime = _nowAsDay;
       }

       // Add stake amount to staked balance
       stakeBalances[_user].stakeBalance = _newStakeBalance;

       // Assign the total amount staked at this day
       stakeBalances[_user].stakePerDay[_nowAsDay] = _newStakeBalance;

       // Increment the total staked tokens
       totalScaleStaked = totalScaleStaked + (_value);

       // Set the new staking history
       setTotalStakingHistory();

       // Fire an event for newly staked tokens
       emit Stake(_user, _value);

       return true;
   }

    function claimStake() external returns (bool) {

      // Require that at least 14 days have passed (days)
      require(block.timestamp / (timingVariable) - (stakeBalances[msg.sender].unstakeTime) >= 14);

      // Get the user's stake balance 
      uint _userStakeBalance = stakeBalances[msg.sender].stakeBalance;

      // Calculate tokens to mint using unstakeTime, rewards are not received during power-down period
      uint _tokensToMint = calculateStakeGains(stakeBalances[msg.sender].unstakeTime);

      // Clear out stored data from mapping
      stakeBalances[msg.sender].stakeBalance = 0;
      stakeBalances[msg.sender].initialStakeTime = 0;
      stakeBalances[msg.sender].unstakeTime = 0;

      // Return the stake balance to the staker
      transferFromContract(_userStakeBalance);

      // Mint the new tokens to the sender
      mint(msg.sender, _tokensToMint);

      // Scale unstaked event
      emit ClaimStake(msg.sender, _userStakeBalance, _tokensToMint);

      return true;
    }


    function initUnstake() external returns (bool) {

        // Require that the user has not already started the unstaked process
        require(stakeBalances[msg.sender].unstakeTime == 0);

        // Require that there was some amount staked
        require(stakeBalances[msg.sender].stakeBalance > 0);

        // Log time that user started unstaking
        stakeBalances[msg.sender].unstakeTime = block.timestamp / (timingVariable);

        // Subtract stake balance from totalScaleStaked
        totalScaleStaked = totalScaleStaked - (stakeBalances[msg.sender].stakeBalance);

        // Set this every time someone adjusts the totalScaleStaked amount
        setTotalStakingHistory();

        // Scale unstaked event
        emit Unstake(msg.sender, stakeBalances[msg.sender].stakeBalance);

        return true;
    }



    function timeUntilClaimAvaliable(address _user) view external returns (uint) {
      return stakeBalances[_user].unstakeTime + (14) * (86400);
    }



    function stakeBalanceOf(address _user) view external returns (uint) {
      return stakeBalances[_user].stakeBalance;
    }



    function getStakingGains(uint _now) view public returns (uint) {
        if (stakeBalances[msg.sender].stakeBalance == 0) {
          return 0;
        }
        return calculateStakeGains(_now / (timingVariable));
    }



    function calculateStakeGains(uint _unstakeTime) view private returns (uint mintTotal)  {

      uint _initialStakeTimeInVariable = stakeBalances[msg.sender].initialStakeTime; // When the user started staking as a unique day in unix time
      uint _timePassedSinceStakeInVariable = _unstakeTime - (_initialStakeTimeInVariable); // How much time has passed, in days, since the user started staking.
      uint _stakePercentages = 0; // Keeps an additive track of the user's staking percentages over time
      uint _tokensToMint = 0; // How many new Scale tokens to create
      uint _lastDayStakeWasUpdated;  // Last day the totalScaleStaked was updated
      uint _lastStakeDay; // Last day that the user staked

      // If user staked and init unstaked on the same day, gains are 0
      if (_timePassedSinceStakeInVariable == 0) {
        return 0;
      }
      // If user has been staking longer than 365 days, staked days after 365 days do not earn interest 
      else if (_timePassedSinceStakeInVariable >= 365) {
       _unstakeTime = _initialStakeTimeInVariable + (365);
       _timePassedSinceStakeInVariable = 365;
      }
      // Average this msg.sender's relative percentage ownership of totalScaleStaked throughout each day since they started staking
      for (uint i = _initialStakeTimeInVariable; i < _unstakeTime; i++) {

        // Total amount user has staked on i day
        uint _stakeForDay = stakeBalances[msg.sender].stakePerDay[i];

        // If this was a day that the user staked or added stake
        if (_stakeForDay != 0) {

            // If the day exists add it to the percentages
            if (totalStakingHistory[i] != 0) {

                // If the day does exist add it to the number to be later averaged as a total average percentage of total staking
                _stakePercentages = _stakePercentages + (calculateFraction(_stakeForDay, totalStakingHistory[i], decimals));

                // Set the last day someone staked
                _lastDayStakeWasUpdated = totalStakingHistory[i];
            }
            else {
                // Use the last day found in the totalStakingHistory mapping
                _stakePercentages = _stakePercentages + (calculateFraction(_stakeForDay, _lastDayStakeWasUpdated, decimals));
            }

            _lastStakeDay = _stakeForDay;
        }
        else {

            // If the day exists add it to the percentages
            if (totalStakingHistory[i] != 0) {

                // If the day does exist add it to the number to be later averaged as a total average percentage of total staking
                _stakePercentages = _stakePercentages + (calculateFraction(_lastStakeDay, totalStakingHistory[i], decimals));

                // Set the last day someone staked
                _lastDayStakeWasUpdated = totalStakingHistory[i];
            }
            else {
                // Use the last day found in the totalStakingHistory mapping
                _stakePercentages = _stakePercentages + (calculateFraction(_lastStakeDay, _lastDayStakeWasUpdated, decimals));
            }
        }
      }
        // Get the account's average percentage staked of the total stake over the course of all days they have been staking
        uint _stakePercentageAverage = calculateFraction(_stakePercentages, _timePassedSinceStakeInVariable, 0);

        // Calculate this account's mint rate per second while staking
        uint _finalMintRate = stakingMintRate * (_stakePercentageAverage);

        // Account for 18 decimals when calculating the amount of tokens to mint
        _finalMintRate = _finalMintRate / (1 ether);

        // Calculate total tokens to be minted. Multiply by timingVariable to convert back to seconds.
        _tokensToMint = calculateMintTotal(_timePassedSinceStakeInVariable * (timingVariable), _finalMintRate);

        return  _tokensToMint;
    }

    function setTotalStakingHistory() private {

      // Get block.timestamp in terms of the variable staking accuracy (days in Scale's case)
      uint _nowAsTimingVariable = block.timestamp / (timingVariable);

      // Set the totalStakingHistory as a timestamp of the totalScaleStaked today
      totalStakingHistory[_nowAsTimingVariable] = totalScaleStaked;
    }

    // Scale Owner Claiming


    function ownerClaim() external onlyOwner {

        require(block.timestamp > ownerTimeLastMinted);

        uint _timePassedSinceLastMint; // The amount of time passed since the owner claimed in seconds
        uint _tokenMintCount; // The amount of new tokens to mint
        bool _mintingSuccess; // The success of minting the new Scale tokens

        // Calculate the number of seconds that have passed since the owner last took a claim
        _timePassedSinceLastMint = block.timestamp - (ownerTimeLastMinted);

        assert(_timePassedSinceLastMint > 0);

        // Determine the token mint amount, determined from the number of seconds passed and the ownerMintRate
        _tokenMintCount = calculateMintTotal(_timePassedSinceLastMint, ownerMintRate);

        // Mint the owner's tokens; this also increases totalSupply
        _mintingSuccess = mint(msg.sender, _tokenMintCount);

        require(_mintingSuccess);

        // New minting was a success. Set last time minted to current block.timestamp (block.timestamp)
        ownerTimeLastMinted = block.timestamp;
    }

    // Scale Pool Distribution


    // @dev anyone can call this function that mints Scale to the pool dedicated to Scale distribution to rewards pool
    function poolIssue() public {

        // Do not allow tokens to be minted to the pool until the pool is set
        require(pool != address(0));

        // Make sure time has passed since last minted to pool
        require(block.timestamp > poolTimeLastMinted);
        require(pool != address(0));

        uint _timePassedSinceLastMint; // The amount of time passed since the pool claimed in seconds
        uint _tokenMintCount; // The amount of new tokens to mint
        bool _mintingSuccess; // The success of minting the new Scale tokens

        // Calculate the number of seconds that have passed since the owner last took a claim
        _timePassedSinceLastMint = block.timestamp - (poolTimeLastMinted);

        assert(_timePassedSinceLastMint > 0);

        // Determine the token mint amount, determined from the number of seconds passed and the ownerMintRate
        _tokenMintCount = calculateMintTotal(_timePassedSinceLastMint, poolMintRate);

        // Mint the owner's tokens; this also increases totalSupply
        _mintingSuccess = mint(pool, _tokenMintCount);

        require(_mintingSuccess);

        // New minting was a success! Set last time minted to current block.timestamp (block.timestamp)
        poolTimeLastMinted = block.timestamp;
    }


    function setPool(address _newAddress) public onlyOwner {
        pool = _newAddress;
    }

    // Helper Functions






    function calculateFraction(uint _numerator, uint _denominator, uint _precision) pure private returns(uint quotient) {
        // Take passed value and expand it to the required precision
        _numerator = _numerator * (10 ** (_precision + 1));
        // Handle last-digit rounding
        uint _quotient = ((_numerator / (_denominator)) + 5) / 10;
        return (_quotient);
    }



    function calculateMintTotal(uint _timeInSeconds, uint _mintRate) pure private returns(uint mintAmount) {
        // Calculates the amount of tokens to mint based upon the number of seconds passed
        return(_timeInSeconds * (_mintRate));
    }
}