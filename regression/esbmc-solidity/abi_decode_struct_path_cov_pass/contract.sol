// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract AbiDecodeStructPathCov {
    struct Info {
        string name;
        uint64[] spots;
        uint64 deployerTradingFeeShare;
        address deployer;
        address evmContract;
        uint8 szDecimals;
        uint8 weiDecimals;
        int8 evmExtraWeiDecimals;
    }

    function scale(bytes memory data) public pure returns (uint256) {
        Info memory info = abi.decode(data, (Info));
        uint8 d = info.szDecimals;
        if (d > 8) {
            return 1;
        }
        return 10 ** (8 - d);
    }
}
