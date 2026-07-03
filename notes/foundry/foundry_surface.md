# Foundry authoritative surface (from forge-std v1.16.2, extracted not remembered)

## Vm cheatcodes by category (name → count)

### Crypto (15)
  createEd25519Key, createWallet, deriveKey, publicKeyEd25519, publicKeyP256, rememberKey, rememberKeys, sign, signCompact, signEd25519, signKeychain, signKeychainAdmin, signP256, signWithNonceUnsafe, verifyEd25519

### Environment (12)
  envAddress, envBool, envBytes, envBytes32, envExists, envInt, envOr, envString, envUint, isContext, resolveEnv, setEnv

### EVM (39)
  accesses, addr, eth_getLogs, getBlobBaseFee, getBlockNumber, getBlockTimestamp, getChainId, getEvmVersion, getMappingKeyAndParentOf, getMappingLength, getMappingSlotAt, getNonce, getRawBlockHeader, getRecordedLogs, getRecordedLogsJson, getStateDiff, getStateDiffJson, getStorageAccesses, getStorageSlots, isImplicitlyApproved, isIsolateMode, lastCallGas, lastFrameGas, load, pauseGasMetering, record, recordLogs, resetGasMetering, resumeGasMetering, rpc, rpcJson, setEvmVersion, startDebugTraceRecording, startMappingRecording, startStateDiffRecording, stopAndReturnDebugTraceRecording, stopAndReturnStateDiff, stopMappingRecording, stopRecord

### Filesystem (36)
  closeFile, copyFile, createDir, currentFilePath, deployCode, exists, ffi, fsMetadata, getArtifactPathByCode, getArtifactPathByDeployedCode, getBroadcast, getBroadcasts, getCode, getDeployedCode, getDeployment, getDeployments, isDir, isFile, projectRoot, prompt, promptAddress, promptSecret, promptSecretUint, promptUint, readDir, readFile, readFileBinary, readLine, readLink, removeDir, removeFile, tryFfi, unixTime, writeFile, writeFileBinary, writeLine

### JSON (31)
  keyExists, keyExistsJson, parseJson, parseJsonAddress, parseJsonAddressArray, parseJsonBool, parseJsonBoolArray, parseJsonBytes, parseJsonBytes32, parseJsonBytes32Array, parseJsonBytesArray, parseJsonInt, parseJsonIntArray, parseJsonKeys, parseJsonString, parseJsonStringArray, parseJsonType, parseJsonTypeArray, parseJsonUint, parseJsonUintArray, serializeAddress, serializeBool, serializeBytes, serializeBytes32, serializeInt, serializeJson, serializeJsonType, serializeString, serializeUint, serializeUintToHex, writeJson

### Scripting (9)
  attachBlob, attachDelegation, broadcast, broadcastRawTransaction, getWallets, signAndAttachDelegation, signDelegation, startBroadcast, stopBroadcast

### String (14)
  contains, indexOf, parseAddress, parseBool, parseBytes, parseBytes32, parseInt, parseUint, replace, split, toLowercase, toString, toUppercase, trim

### Testing (30)
  assertApproxEqAbs, assertApproxEqAbsDecimal, assertApproxEqRel, assertApproxEqRelDecimal, assertEq, assertEqDecimal, assertFalse, assertGe, assertGeDecimal, assertGt, assertGtDecimal, assertLe, assertLeDecimal, assertLt, assertLtDecimal, assertNotEq, assertNotEqDecimal, assertTrue, assume, assumeImplicitApproval, assumeNoRevert, breakpoint, foundryVersionAtLeast, foundryVersionCmp, getChain, getFoundryVersion, rpcUrl, rpcUrlStructs, rpcUrls, sleep

### Toml (20)
  keyExistsToml, parseToml, parseTomlAddress, parseTomlAddressArray, parseTomlBool, parseTomlBoolArray, parseTomlBytes, parseTomlBytes32, parseTomlBytes32Array, parseTomlBytesArray, parseTomlInt, parseTomlIntArray, parseTomlKeys, parseTomlString, parseTomlStringArray, parseTomlType, parseTomlTypeArray, parseTomlUint, parseTomlUintArray, writeToml

### Utilities (25)
  bound, computeCreate2Address, computeCreateAddress, eip712HashStruct, eip712HashType, eip712HashTypedData, ensNamehash, fromRlp, getLabel, label, pauseTracing, randomAddress, randomBool, randomBytes, randomBytes4, randomBytes8, randomInt, randomUint, resumeTracing, setSeed, shuffle, sort, toBase64, toBase64URL, toRlp

### EVM (63)
  accessList, activeFork, allowCheatcodes, blobBaseFee, blobhashes, chainId, clearMockedCalls, cloneAccount, coinbase, cool, coolSlot, createFork, createSelectFork, deal, deleteSnapshot, deleteSnapshots, deleteStateSnapshot, deleteStateSnapshots, difficulty, dumpState, etch, executeTransaction, fee, getBlobhashes, isPersistent, loadAllocs, makePersistent, mockCall, mockCallRevert, mockCalls, mockFunction, noAccessList, prank, prevrandao, readCallers, resetNonce, revertTo, revertToAndDelete, revertToState, revertToStateAndDelete, revokePersistent, roll, rollFork, selectFork, setBlockhash, setLogoURI, setNonce, setNonceUnsafe, setTip20LogoURI, snapshot, snapshotGasLastCall, snapshotGasLastFrame, snapshotState, snapshotValue, startPrank, startSnapshotGas, stopPrank, stopSnapshotGas, store, transact, txGasPrice, warmSlot, warp

### Testing (16)
  expectCall, expectCallMinGas, expectCreate, expectCreate2, expectEmit, expectEmitAnonymous, expectKeychainAdminVerified, expectKeychainVerified, expectLogoURIUpdated, expectPartialRevert, expectRevert, expectSafeMemory, expectSafeMemoryCall, expectTip20LogoURIUpdated, skip, stopExpectSafeMemory

### Utilities (3)
  copyStorage, interceptInitcode, setArbitraryStorage

**Vm total (categorized): 313**

## StdAssertions (24 distinct names, many overloads)
  assertApproxEqAbs, assertApproxEqAbsDecimal, assertApproxEqRel, assertApproxEqRelDecimal, assertEq, assertEq0, assertEq32, assertEqCall, assertEqDecimal, assertEqUint, assertFalse, assertGe, assertGeDecimal, assertGt, assertGtDecimal, assertLe, assertLeDecimal, assertLt, assertLtDecimal, assertNotEq, assertNotEq0, assertNotEq32, assertNotEqDecimal, assertTrue

## StdCheats.sol helpers (42) — plain calls, NOT vm.*/assert* (gate hole #2)
  _bytesToUint, _console2_log_StdCheats, _isPayable, _pureChainId, _viewChainId, assumeAddressIsNot, assumeNoBlacklisted, assumeNotBlacklisted, assumeNotForgeAddress, assumeNotPayable, assumeNotPrecompile, assumeNotZeroAddress, assumePayable, assumeUnusedAddress, changePrank, deal, dealERC1155, dealERC721, deployCode, deployCodeTo, deriveRememberKey, destroyAccount, expectAndMockCall, hoax, isFork, makeAccount, makeAddr, makeAddrAndKey, rawToConvertedEIP1559Detail, rawToConvertedEIPTx1559, rawToConvertedEIPTx1559s, rawToConvertedReceipt, rawToConvertedReceiptLogs, rawToConvertedReceipts, readEIP1559ScriptArtifact, readReceipt, readReceipts, readTx1559, readTx1559s, rewind, skip, startHoax

## StdUtils.sol helpers (13) — plain calls, NOT vm.*/assert* (gate hole #2)
  _addressFromLast20Bytes, _bound, _castLogPayloadViewToPure, _console2_log_StdUtils, _sendLogPayload, _sendLogPayloadView, bound, boundPrivateKey, bytesToUint, computeCreate2Address, computeCreateAddress, getTokenBalances, hashInitCode

## StdInvariant.sol helpers (20) — plain calls, NOT vm.*/assert* (gate hole #2)
  excludeArtifact, excludeArtifacts, excludeContract, excludeContracts, excludeSelector, excludeSelectors, excludeSender, excludeSenders, targetArtifact, targetArtifactSelector, targetArtifactSelectors, targetArtifacts, targetContract, targetContracts, targetInterface, targetInterfaces, targetSelector, targetSelectors, targetSender, targetSenders

