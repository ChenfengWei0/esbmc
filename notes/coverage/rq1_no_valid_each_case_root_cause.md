# RQ1 VeriPUT No-Valid 逐 Case 根因表

- no-valid case 数：205
- 这份表的“根因”指产物链断点和代码层原因，不是 stderr/bucket 复述。
- 每个编号是一条当前 canonical no-valid subject；没有把 case 合并掉。

## 断点分布（仅作索引，不替代逐 case）
- 混合失败，需要按 unit 拆修: 60
- ESBMC Solidity symbol/type table: 26
- VeriPUT region too weak/too wide: 25
- Timeout/resource scheduling: 16
- ESBMC frontend/migrate: 13
- 结果记录不足: 11
- ESBMC coverage goal explosion / VeriPUT no split: 11
- VeriPUT region coordinate selection/materialization: 8
- ESBMC Solidity tuple lowering: 7
- VeriPUT unit scheduler/focus mismatch: 7
- Coverage obstacle model / certifier refusal: 5
- VeriPUT Stage4 materialization: 4
- ESBMC coverage instrumentation/symex reachability: 4
- ESBMC frontend/backend crash: 2
- ESBMC Solidity address/member lowering: 2
- VeriPUT certifier salvage gap: 2
- ESBMC modifier/selector instrumentation: 1
- VeriPUT reachability setup: 1
- ESBMC Solidity bytes operation support: 1
- ESBMC Solidity state/member flattening: 1
- ESBMC Solidity function type-name support: 1

## 逐 Case 根因

### 1. bugfix124/acfix_032_CVE_2021_39167
- 断点阶段：Coverage obstacle model / certifier refusal
- 根因：所有 witnessed paths 都被标成 named-obstacle，certifier 认为这些路径由结构性障碍支配，拒绝认证。
- 为什么导致 no-valid：这不是 solver 没找到路径；是可观测路径被 obstacle 规则排除，导致没有可用于测试的 region。
- 应修位置：修 scripts/solidity_path_generalise.py 的 obstacle 分类，必要时修 src/goto-programs/goto_coverage.cpp 或 Solidity model 让目标状态可观测。
- 涉及 unit：cancel, updateDelay, grantRole, revokeRole, renounceRole, schedule, scheduleBatch, execute
- 辅助 bucket：{"KILLED": 1, "NO-PATH": 1, "NO-WITNESS-UNDECIDED": 6}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_032_CVE_2021_39167/result.json`

### 2. bugfix124/acfix_033_CVE_2021_39168
- 断点阶段：Coverage obstacle model / certifier refusal
- 根因：所有 witnessed paths 都被标成 named-obstacle，certifier 认为这些路径由结构性障碍支配，拒绝认证。
- 为什么导致 no-valid：这不是 solver 没找到路径；是可观测路径被 obstacle 规则排除，导致没有可用于测试的 region。
- 应修位置：修 scripts/solidity_path_generalise.py 的 obstacle 分类，必要时修 src/goto-programs/goto_coverage.cpp 或 Solidity model 让目标状态可观测。
- 涉及 unit：schedule, scheduleBatch
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_033_CVE_2021_39168/result.json`

### 3. bugfix124/acfix_077_L1Block
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-COORDINATE=1, NOT-CERTIFIED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setL1BlockValues, version
- 辅助 bucket：{"NO-COORDINATE": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_077_L1Block/result.json`

### 4. bugfix124/acfix_3_5_077_L1Block
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-COORDINATE=1, NOT-CERTIFIED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setL1BlockValues, version
- 辅助 bucket：{"NO-COORDINATE": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_3_5_077_L1Block/result.json`

### 5. bugfix124/acfix_fixlink_DnGmxBatchingManager
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：executeBatchDeposit
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_DnGmxBatchingManager/result.json`

### 6. bugfix124/acfix_fixlink_MStableYieldSource
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：supplyTokenTo, redeemToken
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_MStableYieldSource/result.json`

### 7. bugfix124/acfix_fixlink_Product
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：initialize, settleAccount, openTake, openTakeFor, closeTake, closeTakeFor, openMake, openMakeFor, closeMake, closeMakeFor, closeAll, updateClosed, updateOracle, updateMaintenance, updateFundingFee, updateMakerFee, updateTakerFee, updatePositionFee, updateMakerLimit, updateUtilizationCurve, utilizationBuffer, closed, settle, maintenance, maintenanceNext, isClosed, isLiquidating, position, pre, latestVersion, positionAtVersion, valueAtVersion, shareAtVersion, rate, oracle, payoffDefinition, currentVersion, atVersion, fundingFee, makerFee, takerFee, positionFee, makerLimit, utilizationCurve, pendingFeeUpdates, controller, name, symbol
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 48}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_Product/result.json`

### 8. bugfix124/acfix_fixlink_Product2
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：initialize, settleAccount, openTake, openTakeFor, closeTake, closeTakeFor, openMake, openMakeFor, closeMake, closeMakeFor, closeAll, updateClosed, updateOracle, updateMaintenance, updateFundingFee, updateMakerFee, updateTakerFee, updatePositionFee, updateMakerLimit, updateUtilizationCurve, utilizationBuffer, closed, settle, maintenance, maintenanceNext, isClosed, isLiquidating, position, pre, latestVersion, positionAtVersion, valueAtVersion, shareAtVersion, rate, oracle, payoffDefinition, currentVersion, atVersion, fundingFee, makerFee, takerFee, positionFee, makerLimit, utilizationCurve, pendingFeeUpdates, controller, name, symbol
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 48}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_Product2/result.json`

### 9. bugfix124/acfix_real_FlashGovernanceArbiter
- 断点阶段：ESBMC frontend/backend crash
- 根因：ESBMC 发生 SIGSEGV，属于内部崩溃而非验证结论。
- 为什么导致 no-valid：进程崩溃导致 no cov-report、no CE、no valid。
- 应修位置：用对应 driver.log 的最小 flat.sol/solast 复现并修 ESBMC 崩溃点。
- 涉及 unit：setGoverned, assertGovernanceApproved
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_real_FlashGovernanceArbiter/result.json`

### 10. bugfix124/ct_5_Proposals_can_be_cancelled
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：cancelProposal
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/ct_5_Proposals_can_be_cancelled/result.json`

### 11. bugfix124/pop_001_Multicall
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_001_Multicall/result.json`

### 12. bugfix124/pop_009_PrivatePool
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：buy, sell, execute
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2, "NOT-CERTIFIED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_009_PrivatePool/result.json`

### 13. bugfix124/pop_018_PrivatePool
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：execute
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_018_PrivatePool/result.json`

### 14. bugfix124/pop_032_PuttyV2
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：fillOrder, setBaseURI, setFee
- 辅助 bucket：{"DRIVER-REFUSED": 1, "KILLED": 1, "NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_032_PuttyV2/result.json`

### 15. bugfix124/pop_033_PrivatePool
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-COORDINATE=1, NOT-CERTIFIED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：flashFee, initialize
- 辅助 bucket：{"NO-COORDINATE": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_033_PrivatePool/result.json`

### 16. bugfix124/pop_042_VaultAdapter
- 断点阶段：ESBMC modifier/selector instrumentation
- 根因：modifier/access-control 插桩生成的 `_selector` 参数类型与被调用 helper 期望类型不一致。
- 为什么导致 no-valid：目标函数在 modifier harness 调用处类型检查失败，coverage 无法产出路径。
- 应修位置：修 src/solidity-frontend/solidity_convert_modifier.cpp 或相关 selector helper 的参数类型建模。
- 涉及 unit：initialize, rate, setSlopes, setLimits, upgradeToAndCall, proxiableUUID
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 6}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_042_VaultAdapter/result.json`

### 17. bugfix124/pop_046_CVXStaker
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：getReward
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_046_CVXStaker/result.json`

### 18. bugfix124/pop_048_PrivatePool
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：buy, sell, execute
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2, "NOT-CERTIFIED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_048_PrivatePool/result.json`

### 19. bugfix124/pop_049_Cooler
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：rollLoan, requestLoan, rescindRequest, repayLoan, delegateVoting, clearRequest, provideNewTermsForRoll, claimRepaid, claimDefaulted, approveTransfer, transferOwnership, setDirectRepay, owner, collateral, debt, factory, collateralFor, newCollateralFor, interestFor, isDefaulted, isActive, getRequest, getLoan
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 23}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_049_Cooler/result.json`

### 20. bugfix124/pop_058_PuttyV2
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=24。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：fillOrder, exercise, setBaseURI, setFee, withdraw, cancel, batchFillOrder, acceptCounterOffer, transferOwnership, onERC721Received, transferFrom, approve, setApprovalForAll, safeTransferFrom, isWhitelisted, hashOppositeOrder, hashOrder, encodeERC20Assets, encodeERC721Assets, domainSeparatorV4, tokenURI, owner, renounceOwnership, balanceOf
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 24}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_058_PuttyV2/result.json`

### 21. bugfix124/pop_066_LRTDepositPool
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：depositAsset
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_066_LRTDepositPool/result.json`

### 22. bugfix124/pop_070_PhiNFT1155
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：transferOwnership, setApprovalForAll
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_070_PhiNFT1155/result.json`

### 23. bugfix124/pop_077_MergingPool
- 断点阶段：VeriPUT Stage4 materialization
- 根因：Stage2 已经有 CERTIFIED 路径，但 Stage4 没有把 certified region 写成 raw/valid Foundry 测试。
- 为什么导致 no-valid：验证器其实给了可用证明，no-valid 是 certifier 到 materializer/adoption 的断链。
- 应修位置：修 notes/coverage/scripts/put_all.py 和 rq1_veriput_run.py 的 certified row materialization/result adoption。
- 涉及 unit：transferOwnership, updateWinnersPerPeriod, pickWinner, adjustAdminAccess, addPoints
- 辅助 bucket：{"CERTIFIED": 2, "KILLED": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_077_MergingPool/result.json`

### 24. bugfix124/rc_access_control__proxy__SolGPT__proxy_4round
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：forward
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_access_control__proxy__SolGPT__proxy_4round/result.json`

### 25. bugfix124/rc_reentrancy__modifier_reentrancy__SmartFix__modifier_reentrancy
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：airDrop
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_reentrancy__modifier_reentrancy__SmartFix__modifier_reentrancy/result.json`

### 26. bugfix124/rc_reentrancy__reentrancy_bonus__SmartFix__reentrancy_bonus
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：withdrawReward, getFirstWithdrawalBonus
- 辅助 bucket：{"NOT-CERTIFIED": 2}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_reentrancy__reentrancy_bonus__SmartFix__reentrancy_bonus/result.json`

### 27. bugfix124/rc_time_manipulation__roulette__SolGPT__roulette_1round
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_time_manipulation__roulette__SolGPT__roulette_1round/result.json`

### 28. bugfix124/rc_time_manipulation__roulette__SolGPT__roulette_2round
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_time_manipulation__roulette__SolGPT__roulette_2round/result.json`

### 29. bugfix124/rc_time_manipulation__roulette__SolGPT__roulette_3round
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_time_manipulation__roulette__SolGPT__roulette_3round/result.json`

### 30. bugfix124/rc_time_manipulation__roulette__SolGPT__roulette_4round
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_time_manipulation__roulette__SolGPT__roulette_4round/result.json`

### 31. bugfix124/rc_unchecked_low_level_calls__0x2972d548497286d18e92b5fa1f8f9139e5653fd2__SolGPT__0x2972d548497286d18e92b5fa1f8f9139e5653fd2_3round
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x2972d548497286d18e92b5fa1f8f9139e5653fd2__SolGPT__0x2972d548497286d18e92b5fa1f8f9139e5653fd2_3round/result.json`

### 32. bugfix124/rc_unchecked_low_level_calls__0x4051334adc52057aca763453820cb0e045076ef3__SolGPT__0x4051334adc52057aca763453820cb0e045076ef3_2round
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x4051334adc52057aca763453820cb0e045076ef3__SolGPT__0x4051334adc52057aca763453820cb0e045076ef3_2round/result.json`

### 33. bugfix124/rc_unchecked_low_level_calls__0x4051334adc52057aca763453820cb0e045076ef3__SolGPT__0x4051334adc52057aca763453820cb0e045076ef3_3round
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x4051334adc52057aca763453820cb0e045076ef3__SolGPT__0x4051334adc52057aca763453820cb0e045076ef3_3round/result.json`

### 34. bugfix124/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_1round
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_1round/result.json`

### 35. bugfix124/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_2round
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_2round/result.json`

### 36. bugfix124/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_3round
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152__SolGPT__0x4b71ad9c1a84b9b643aa54fdd66e2dec96e8b152_3round/result.json`

### 37. bugfix124/rc_unchecked_low_level_calls__0xa1fceeff3acc57d257b917e30c4df661401d6431__SolGPT__0xa1fceeff3acc57d257b917e30c4df661401d6431_1round
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0xa1fceeff3acc57d257b917e30c4df661401d6431__SolGPT__0xa1fceeff3acc57d257b917e30c4df661401d6431_1round/result.json`

### 38. bugfix124/rc_unchecked_low_level_calls__0xb7c5c5aa4d42967efe906e1b66cb8df9cebf04f7__sGuardPlus__0xb7c5c5aa4d42967efe906e1b66cb8df9cebf04f7
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：withdraw
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0xb7c5c5aa4d42967efe906e1b66cb8df9cebf04f7__sGuardPlus__0xb7c5c5aa4d42967efe906e1b66cb8df9cebf04f7/result.json`

### 39. bugfix124/rc_unchecked_low_level_calls__0xe894d54dca59cb53fe9cbc5155093605c7068220__TIPS__0xe894d54dca59cb53fe9cbc5155093605c7068220U1
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rc_unchecked_low_level_calls__0xe894d54dca59cb53fe9cbc5155093605c7068220__TIPS__0xe894d54dca59cb53fe9cbc5155093605c7068220U1/result.json`

### 40. bugfix124/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__SmartFix
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__SmartFix/result.json`

### 41. bugfix124/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__TIPS
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__TIPS/result.json`

### 42. bugfix124/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__sGuard
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0x4a66ad0bca2d700f11e1f2fc2c106f7d3264504c__sGuard/result.json`

### 43. bugfix124/rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__TIPS
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__TIPS/result.json`

### 44. bugfix124/rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__sGuard
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__sGuard/result.json`

### 45. bugfix124/rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__TIPS
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__TIPS/result.json`

### 46. bugfix124/rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__sGuard
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__sGuard/result.json`

### 47. bugfix124/rcx_unchecked_low_level_calls__0xf29ebe930a539a60279ace72c707cba851a57707__TIPS
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：go
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xf29ebe930a539a60279ace72c707cba851a57707__TIPS/result.json`

### 48. bugfix124/rcx_unchecked_low_level_calls__0xf29ebe930a539a60279ace72c707cba851a57707__sGuardPlus
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：go
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/rcx_unchecked_low_level_calls__0xf29ebe930a539a60279ace72c707cba851a57707__sGuardPlus/result.json`

### 49. peer182/peer_ccsolbmc__Animalia
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__Animalia/result.json`

### 50. peer182/peer_ccsolbmc__COINNetwork
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：mint
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__COINNetwork/result.json`

### 51. peer182/peer_ccsolbmc__ClockBoxContract
- 断点阶段：Coverage obstacle model / certifier refusal
- 根因：所有 witnessed paths 都被标成 named-obstacle，certifier 认为这些路径由结构性障碍支配，拒绝认证。
- 为什么导致 no-valid：这不是 solver 没找到路径；是可观测路径被 obstacle 规则排除，导致没有可用于测试的 region。
- 应修位置：修 scripts/solidity_path_generalise.py 的 obstacle 分类，必要时修 src/goto-programs/goto_coverage.cpp 或 Solidity model 让目标状态可观测。
- 涉及 unit：transfer, increaseAllowance, decreaseAllowance, transferFrom, vault, approve, mint, burn, withdraw, name, symbol, decimals, totalSupply, balanceOfInValut, balanceOfvault, balanceOfFeeAddress, balanceOf, calculateFee, deductFeeFromValue, getKeeper, allowance
- 辅助 bucket：{"NO-COORDINATE": 1, "NO-PATH": 17, "NO-WITNESS-UNDECIDED": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__ClockBoxContract/result.json`

### 52. peer182/peer_ccsolbmc__StarNFTProxy
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：admin, implementation, changeAdmin
- 辅助 bucket：{"NOT-CERTIFIED": 3}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__StarNFTProxy/result.json`

### 53. peer182/peer_ccsolbmc__WrappedToken
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：abca4
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__WrappedToken/result.json`

### 54. peer182/peer_ccsolbmc__kia_quiz
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：Try, Start, New, asdf, Stop, fallback
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 1, "NOT-CERTIFIED": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_ccsolbmc__kia_quiz/result.json`

### 55. peer182/peer_solar__EzToken
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：transfer, transferFrom
- 辅助 bucket：{"NOT-CERTIFIED": 2}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__EzToken/result.json`

### 56. peer182/peer_solar__Gift_1_ETH
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：SetPass, GetGift, PassHasBeenSet, GetHash
- 辅助 bucket：{"NOT-CERTIFIED": 4}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__Gift_1_ETH/result.json`

### 57. peer182/peer_solar__LotteryFor10
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__LotteryFor10/result.json`

### 58. peer182/peer_solar__OpenAddressLottery
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：participate
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__OpenAddressLottery/result.json`

### 59. peer182/peer_solar__Prover
- 断点阶段：Coverage obstacle model / certifier refusal
- 根因：所有 witnessed paths 都被标成 named-obstacle，certifier 认为这些路径由结构性障碍支配，拒绝认证。
- 为什么导致 no-valid：这不是 solver 没找到路径；是可观测路径被 obstacle 规则排除，导致没有可用于测试的 region。
- 应修位置：修 scripts/solidity_path_generalise.py 的 obstacle 分类，必要时修 src/goto-programs/goto_coverage.cpp 或 Solidity model 让目标状态可观测。
- 涉及 unit：addEntry, deleteEntry
- 辅助 bucket：{"NO-COORDINATE": 1, "NO-WITNESS-UNDECIDED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__Prover/result.json`

### 60. peer182/peer_solar__TestDateTime
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：test
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_solar__TestDateTime/result.json`

### 61. peer182/peer_soltg__branches_inside_modifiers_2
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：g
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__branches_inside_modifiers_2/result.json`

### 62. peer182/peer_soltg__constructor_state_variable_init
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__constructor_state_variable_init/result.json`

### 63. peer182/peer_soltg__constructor_state_variable_init_chain_alternate
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__constructor_state_variable_init_chain_alternate/result.json`

### 64. peer182/peer_soltg__constructor_state_variable_init_diamond
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__constructor_state_variable_init_diamond/result.json`

### 65. peer182/peer_soltg__constructors
- 断点阶段：结果记录不足
- 根因：result.json 显示 no-valid，但没有保留足够 cert/unit 失败信息。
- 为什么导致 no-valid：当前无法从该结果本身判断 ESBMC 还是 VeriPUT 断点；这说明 runner 证据保留失败。
- 应修位置：修 certify_all.py/rq1_veriput_run.py failure_evidence 写入，再对该 case 复现。
- 涉及 unit：<无>
- 辅助 bucket：{}
- 置信度：低
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__constructors/result.json`

### 66. peer182/peer_soltg__few_calls
- 断点阶段：VeriPUT Stage4 materialization
- 根因：Stage2 已经有 CERTIFIED 路径，但 Stage4 没有把 certified region 写成 raw/valid Foundry 测试。
- 为什么导致 no-valid：验证器其实给了可用证明，no-valid 是 certifier 到 materializer/adoption 的断链。
- 应修位置：修 notes/coverage/scripts/put_all.py 和 rq1_veriput_run.py 的 certified row materialization/result adoption。
- 涉及 unit：f
- 辅助 bucket：{"CERTIFIED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__few_calls/result.json`

### 67. peer182/peer_soltg__triple_nested_if
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：f
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_soltg__triple_nested_if/result.json`

### 68. peer182/peer_syntest__AavePoolReward
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：stake
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__AavePoolReward/result.json`

### 69. peer182/peer_syntest__CryptoGhost
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：transferFrom, transferOwnership, approve, setApprovalForAll
- 辅助 bucket：{"KILLED": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__CryptoGhost/result.json`

### 70. peer182/peer_syntest__DJCoin
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：burn
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__DJCoin/result.json`

### 71. peer182/peer_syntest__FreakCoin
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1, NOT-CERTIFIED=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setUniswapAddress, approve, transfer
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__FreakCoin/result.json`

### 72. peer182/peer_syntest__GAZ_ERC20
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNDECIDED=2, NOT-CERTIFIED=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer, transferFrom, approve, approve
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2, "NOT-CERTIFIED": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__GAZ_ERC20/result.json`

### 73. peer182/peer_syntest__INS
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-PATH=5, NO-WITNESS-UNDECIDED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：transfer, transferFrom, approve, approveAndCall, burn, burnFrom
- 辅助 bucket：{"NO-PATH": 5, "NO-WITNESS-UNDECIDED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__INS/result.json`

### 74. peer182/peer_syntest__Revive
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：recoverERC20, lock, transferWithLock, extendLock, increaseLockAmount, unlock, transferOwnership, transfer, approve, transferFrom, increaseAllowance, decreaseAllowance, tokensLocked, tokensLockedAtTime, totalBalanceOf, tokensUnlockable, getUnlockableTokens, owner, isOwner, renounceOwnership, totalSupply, balanceOf, allowance
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 20, "NO-WITNESS-UNKNOWN": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__Revive/result.json`

### 75. peer182/peer_syntest__TimeMiner
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：preSale
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__TimeMiner/result.json`

### 76. peer182/peer_syntest__WOLF
- 断点阶段：VeriPUT reachability setup
- 根因：在当前 constructor/prestate、scope、max-tx 和 path_function 下，目标 unit 没有可达 path。
- 为什么导致 no-valid：no-valid 的原因是 harness 没建立到目标状态/路径，而不是 PUT materialization。
- 应修位置：修 unit_schedule.py 的 path_function/focus/max-tx 选择，或 generalise.py 的 prestate/constructor 状态构造。
- 涉及 unit：transfer
- 辅助 bucket：{"NO-PATH": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/subjects/peer_syntest__WOLF/result.json`

### 77. real203/ERC-3643__ERC-3643__ClaimTopicsRegistryProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__ClaimTopicsRegistryProxy/result.json`

### 78. real203/ERC-3643__ERC-3643__IdentityRegistryProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__IdentityRegistryProxy/result.json`

### 79. real203/ERC-3643__ERC-3643__IdentityRegistryStorageProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__IdentityRegistryStorageProxy/result.json`

### 80. real203/ERC-3643__ERC-3643__ModularComplianceProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__ModularComplianceProxy/result.json`

### 81. real203/ERC-3643__ERC-3643__TREXFactory
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：setImplementationAuthority, setIdFactory
- 辅助 bucket：{"CERTIFIED": 1, "DRIVER-REFUSED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__TREXFactory/result.json`

### 82. real203/ERC-3643__ERC-3643__TREXGateway
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：setFactory, setPublicDeploymentStatus
- 辅助 bucket：{"NOT-CERTIFIED": 2}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__TREXGateway/result.json`

### 83. real203/ERC-3643__ERC-3643__TREXImplementationAuthority
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：setTREXFactory
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__TREXImplementationAuthority/result.json`

### 84. real203/ERC-3643__ERC-3643__TokenProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__TokenProxy/result.json`

### 85. real203/ERC-3643__ERC-3643__TrustedIssuersRegistryProxy
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=2。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setImplementationAuthority, getImplementationAuthority
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ERC-3643__ERC-3643__TrustedIssuersRegistryProxy/result.json`

### 86. real203/ProjectOpenSea__seaport__LocalConduitController
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=14。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：cancelOwnershipTransfer, acceptOwnership, updateChannel, transferOwnership, createConduit, getConduitCodeHashes, ownerOf, getKey, getPotentialOwner, getTotalChannels, getChannels, getConduit, getChannelStatus, getChannel
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 14}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__LocalConduitController/result.json`

### 87. real203/ProjectOpenSea__seaport__PausableZone
- 断点阶段：VeriPUT Stage4 materialization
- 根因：Stage2 已经有 CERTIFIED 路径，但 Stage4 没有把 certified region 写成 raw/valid Foundry 测试。
- 为什么导致 no-valid：验证器其实给了可用证明，no-valid 是 certifier 到 materializer/adoption 的断链。
- 应修位置：修 notes/coverage/scripts/put_all.py 和 rq1_veriput_run.py 的 certified row materialization/result adoption。
- 涉及 unit：cancelOrders, assignOperator, executeMatchOrders
- 辅助 bucket：{"CERTIFIED": 1, "KILLED": 1, "NOT-CERTIFIED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__PausableZone/result.json`

### 88. real203/ProjectOpenSea__seaport__ReferenceConduit
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：execute
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__ReferenceConduit/result.json`

### 89. real203/ProjectOpenSea__seaport__ReferenceConduitController
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=14。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：cancelOwnershipTransfer, acceptOwnership, updateChannel, transferOwnership, createConduit, getConduitCodeHashes, ownerOf, getKey, getPotentialOwner, getTotalChannels, getChannels, getConduit, getChannelStatus, getChannel
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 14}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__ReferenceConduitController/result.json`

### 90. real203/ProjectOpenSea__seaport__ReferenceConsideration
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：fulfillBasicOrder
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__ReferenceConsideration/result.json`

### 91. real203/ProjectOpenSea__seaport__SeaportNavigator
- 断点阶段：ESBMC Solidity bytes operation support
- 根因：目标代码对非 static bytesN 执行 bitwise 操作，当前 Solidity frontend 只支持 bytesN 的位运算。
- 为什么导致 no-valid：该表达式无法 lowering，coverage 阶段没有 cov-report，因此 no-valid。
- 应修位置：扩展 src/solidity-frontend/solidity_convert_expr.cpp 的 bytes/bytesN bitwise lowering，或在 VeriPUT 中避开不可支持 unit。
- 涉及 unit：prepare, criteriaRoot, criteriaProof
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__SeaportNavigator/result.json`

### 92. real203/ProjectOpenSea__seaport__SeaportValidator
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：isValidZone, validateContractOfferer
- 辅助 bucket：{"KILLED": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__SeaportValidator/result.json`

### 93. real203/ProjectOpenSea__seaport__TransferHelper
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：bulkTransfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ProjectOpenSea__seaport__TransferHelper/result.json`

### 94. real203/balancer__balancer-v3-monorepo__BalancerContractRegistryInitializer
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：initializeBalancerContractRegistry
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__BalancerContractRegistryInitializer/result.json`

### 95. real203/balancer__balancer-v3-monorepo__BalancerPoolToken
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：transfer
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__BalancerPoolToken/result.json`

### 96. real203/balancer__balancer-v3-monorepo__BaseSplitCodeFactory
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：getCreationCode, getCreationCodeContracts
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__BaseSplitCodeFactory/result.json`

### 97. real203/balancer__balancer-v3-monorepo__BatchRouter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：getWeth, getPermit2, getVault, version, getSender, swapExactInHook, swapExactOutHook, querySwapExactInHook, querySwapExactOutHook, querySwapExactIn, querySwapExactOut, multicall, swapExactIn, swapExactOut, permitBatchAndCall
- 辅助 bucket：{"KILLED": 1, "NO-WITNESS-UNKNOWN": 14}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__BatchRouter/result.json`

### 98. real203/balancer__balancer-v3-monorepo__BufferRouter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：initializeBuffer, initializeBufferHook, addLiquidityToBuffer, addLiquidityToBufferHook
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__BufferRouter/result.json`

### 99. real203/balancer__balancer-v3-monorepo__CallAndRevert
- 断点阶段：ESBMC Solidity address/member lowering
- 根因：address 成员访问 lowering 期望 base 是 address，但当前 call/tuple 返回值没有先解包就进入 address member 逻辑。
- 为什么导致 no-valid：frontend 报错后没有 coverage result，Stage2/Stage4 都没有输入。
- 应修位置：修 src/solidity-frontend/solidity_convert_ref.cpp 或 call return tuple 解包到 address 的路径。
- 涉及 unit：callAndRevertHook
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__CallAndRevert/result.json`

### 100. real203/balancer__balancer-v3-monorepo__ClaimSignatureRegistry
- 断点阶段：Coverage obstacle model / certifier refusal
- 根因：所有 witnessed paths 都被标成 named-obstacle，certifier 认为这些路径由结构性障碍支配，拒绝认证。
- 为什么导致 no-valid：这不是 solver 没找到路径；是可观测路径被 obstacle 规则排除，导致没有可用于测试的 region。
- 应修位置：修 scripts/solidity_path_generalise.py 的 obstacle 分类，必要时修 src/goto-programs/goto_coverage.cpp 或 Solidity model 让目标状态可观测。
- 涉及 unit：recordSignature, recordSignatureFor
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ClaimSignatureRegistry/result.json`

### 101. real203/balancer__balancer-v3-monorepo__CompositeLiquidityRouter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：addLiquidityUnbalancedToERC4626Pool, queryAddLiquidityUnbalancedToERC4626Pool, addLiquidityProportionalToERC4626Pool, queryAddLiquidityProportionalToERC4626Pool
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__CompositeLiquidityRouter/result.json`

### 102. real203/balancer__balancer-v3-monorepo__CowRouter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：setProtocolFeePercentage, setFeeSweeper, swapExactInAndDonateSurplus, swapExactOutAndDonateSurplus
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__CowRouter/result.json`

### 103. real203/balancer__balancer-v3-monorepo__CowSwapFeeBurner
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：retryOrder, cancelOrder, emergencyCancelOrder, burn
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__CowSwapFeeBurner/result.json`

### 104. real203/balancer__balancer-v3-monorepo__DynamicWeightedLPOracle
- 断点阶段：VeriPUT certifier salvage gap
- 根因：coverage 只留下 refutation/witness journal，没有形成完整 certified region；runner 没能把 partial journal 转成 concrete fallback。
- 为什么导致 no-valid：已有低层信息没有被转成 Foundry valid，断在 certifier-to-materializer 接口。
- 应修位置：修 notes/coverage/scripts/certify_all.py 的 partial journal salvage 和 notes/coverage/scripts/put_all.py concrete fallback。
- 涉及 unit：getWeights, version, decimals
- 辅助 bucket：{"CERTIFIED": 2, "KILLED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__DynamicWeightedLPOracle/result.json`

### 105. real203/balancer__balancer-v3-monorepo__DynamicWeightedLPOracleFactory
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：create, getOracleVersion, getOracle, isOracleFromFactory
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__DynamicWeightedLPOracleFactory/result.json`

### 106. real203/balancer__balancer-v3-monorepo__ECLPSurgeHook
- 断点阶段：ESBMC coverage instrumentation/symex reachability
- 根因：coverage claim 已插入，但 symex/solver 没有到达任何 claim；说明插桩位置、focus path 或前置 assume 把目标 claim 全部挡掉。
- 为什么导致 no-valid：VeriPUT 看到的是 no witness，而不是不可达证明；需要修 coverage 插桩和 path_function 选择。
- 应修位置：修 src/goto-programs/goto_coverage.cpp 的 claim placement/reachability，配合 unit_schedule.py 的 path_function/focus。
- 涉及 unit：onRegister, setImbalanceSlopeBelowPeak, setImbalanceSlopeAbovePeak
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2, "NO-WITNESS-UNKNOWN": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ECLPSurgeHook/result.json`

### 107. real203/balancer__balancer-v3-monorepo__ERC4626CowSwapFeeBurner
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：burn, retryOrder, cancelOrder, emergencyCancelOrder
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ERC4626CowSwapFeeBurner/result.json`

### 108. real203/balancer__balancer-v3-monorepo__FactoryWidePauseWindow
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-COORDINATE=2, NOT-CERTIFIED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：getPauseWindowDuration, getOriginalPauseWindowEndTime, getNewPoolPauseWindowEndTime
- 辅助 bucket：{"NO-COORDINATE": 2, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__FactoryWidePauseWindow/result.json`

### 109. real203/balancer__balancer-v3-monorepo__HyperEVMRateProvider
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：getSpotPriceMultiplier, getTokenIndex, getPairIndex, getRate
- 辅助 bucket：{"NO-COORDINATE": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__HyperEVMRateProvider/result.json`

### 110. real203/balancer__balancer-v3-monorepo__HyperEVMRateProviderFactory
- 断点阶段：ESBMC coverage instrumentation/symex reachability
- 根因：coverage claim 已插入，但 symex/solver 没有到达任何 claim；说明插桩位置、focus path 或前置 assume 把目标 claim 全部挡掉。
- 为什么导致 no-valid：VeriPUT 看到的是 no witness，而不是不可达证明；需要修 coverage 插桩和 path_function 选择。
- 应修位置：修 src/goto-programs/goto_coverage.cpp 的 claim placement/reachability，配合 unit_schedule.py 的 path_function/focus。
- 涉及 unit：create, getRateProviderVersion, getRateProvider, isRateProviderFromFactory, disable, version, getVault, getAuthorizer, getActionId
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2, "NO-WITNESS-UNKNOWN": 7}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__HyperEVMRateProviderFactory/result.json`

### 111. real203/balancer__balancer-v3-monorepo__MevCaptureHook
- 断点阶段：ESBMC Solidity state/member flattening
- 根因：继承/合成 struct flatten 后出现同名 state member，member lookup 变成歧义。
- 为什么导致 no-valid：GOTO 构造无法确定读写哪个 field，coverage 在目标函数前失败。
- 应修位置：修 src/solidity-frontend 的继承 state 合并命名策略，避免 duplicate member name 或使用 disambiguated id。
- 涉及 unit：onRegister, setMaxMevSwapFeePercentage, setDefaultMevTaxMultiplier, setPoolMevTaxMultiplier
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__MevCaptureHook/result.json`

### 112. real203/balancer__balancer-v3-monorepo__OwnableAuthentication
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：forceTransferOwnership
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__OwnableAuthentication/result.json`

### 113. real203/balancer__balancer-v3-monorepo__PoolPauseHelper
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：pausePools, createPoolSet, destroyPoolSet, transferPoolSetOwnership
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__PoolPauseHelper/result.json`

### 114. real203/balancer__balancer-v3-monorepo__PoolSwapFeeHelper
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：setStaticSwapFeePercentage, createPoolSet, destroyPoolSet, transferPoolSetOwnership
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__PoolSwapFeeHelper/result.json`

### 115. real203/balancer__balancer-v3-monorepo__PriceImpactHelper
- 断点阶段：ESBMC Solidity address/member lowering
- 根因：address 成员访问 lowering 期望 base 是 address，但当前 call/tuple 返回值没有先解包就进入 address member 逻辑。
- 为什么导致 no-valid：frontend 报错后没有 coverage result，Stage2/Stage4 都没有输入。
- 应修位置：修 src/solidity-frontend/solidity_convert_ref.cpp 或 call return tuple 解包到 address 的路径。
- 涉及 unit：calculateAddLiquidityUnbalancedPriceImpact, callAndRevertHook
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__PriceImpactHelper/result.json`

### 116. real203/balancer__balancer-v3-monorepo__ProtocolFeeController
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：collectAggregateFees, collectAggregateFeesHook, updateProtocolSwapFeePercentage, updateProtocolYieldFeePercentage
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ProtocolFeeController/result.json`

### 117. real203/balancer__balancer-v3-monorepo__ProtocolFeeControllerMigration
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：migratePools, isMigrationComplete, finalizeMigration, getVault
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ProtocolFeeControllerMigration/result.json`

### 118. real203/balancer__balancer-v3-monorepo__ProtocolFeeHelper
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：setProtocolSwapFeePercentage, setProtocolYieldFeePercentage, createPoolSet, destroyPoolSet
- 辅助 bucket：{"NO-WITNESS-UNDECIDED": 3, "NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ProtocolFeeHelper/result.json`

### 119. real203/balancer__balancer-v3-monorepo__ProtocolFeePercentagesProvider
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：setFactorySpecificProtocolFeePercentages, setProtocolFeePercentagesForPools, getProtocolFeeController, getBalancerContractRegistry
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ProtocolFeePercentagesProvider/result.json`

### 120. real203/balancer__balancer-v3-monorepo__ProtocolFeeSweeper
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：sweepProtocolFeesForToken, sweepProtocolFeesForWrappedToken, setFeeRecipient, setTargetToken
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__ProtocolFeeSweeper/result.json`

### 121. real203/balancer__balancer-v3-monorepo__Router
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：initialize, addLiquidityProportional, queryAddLiquidityProportional, addLiquidityUnbalanced
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__Router/result.json`

### 122. real203/balancer__balancer-v3-monorepo__StableLPOracle
- 断点阶段：ESBMC coverage instrumentation/symex reachability
- 根因：coverage claim 已插入，但 symex/solver 没有到达任何 claim；说明插桩位置、focus path 或前置 assume 把目标 claim 全部挡掉。
- 为什么导致 no-valid：VeriPUT 看到的是 no witness，而不是不可达证明；需要修 coverage 插桩和 path_function 选择。
- 应修位置：修 src/goto-programs/goto_coverage.cpp 的 claim placement/reachability，配合 unit_schedule.py 的 path_function/focus。
- 涉及 unit：version, decimals, description, getRoundData
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__StableLPOracle/result.json`

### 123. real203/balancer__balancer-v3-monorepo__StableLPOracleFactory
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：create, getOracleVersion, getOracle, isOracleFromFactory
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__StableLPOracleFactory/result.json`

### 124. real203/balancer__balancer-v3-monorepo__TimelockAuthorizer
- 断点阶段：ESBMC Solidity function type-name support
- 根因：Solidity `type(SomeInterface.someFunction)` / function type-name 形式没有被 frontend 支持。
- 为什么导致 no-valid：函数选择器/接口元信息相关表达式无法转换，导致 path coverage 不产生报告。
- 应修位置：修 src/solidity-frontend 对 function type-name/type selector 的转换。
- 涉及 unit：setDelay
- 辅助 bucket：{"KILLED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__TimelockAuthorizer/result.json`

### 125. real203/balancer__balancer-v3-monorepo__TimelockAuthorizerMigrator
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：executeDelays, finalizeMigration
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__TimelockAuthorizerMigrator/result.json`

### 126. real203/balancer__balancer-v3-monorepo__TimelockExecutionHelper
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：execute, getAuthorizer
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__TimelockExecutionHelper/result.json`

### 127. real203/balancer__balancer-v3-monorepo__UnbalancedAddViaSwapRouter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：addLiquidityUnbalanced, queryAddLiquidityUnbalanced, addLiquidityUnbalancedHook, queryAddLiquidityUnbalancedHook
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__UnbalancedAddViaSwapRouter/result.json`

### 128. real203/balancer__balancer-v3-monorepo__VaultAdmin
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：pausePool, unpausePool, setStaticSwapFeePercentage
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__VaultAdmin/result.json`

### 129. real203/balancer__balancer-v3-monorepo__VaultExtension
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：registerPool
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__VaultExtension/result.json`

### 130. real203/balancer__balancer-v3-monorepo__Version
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：version
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__Version/result.json`

### 131. real203/balancer__balancer-v3-monorepo__WeightedLPOracle
- 断点阶段：ESBMC coverage instrumentation/symex reachability
- 根因：coverage claim 已插入，但 symex/solver 没有到达任何 claim；说明插桩位置、focus path 或前置 assume 把目标 claim 全部挡掉。
- 为什么导致 no-valid：VeriPUT 看到的是 no witness，而不是不可达证明；需要修 coverage 插桩和 path_function 选择。
- 应修位置：修 src/goto-programs/goto_coverage.cpp 的 claim placement/reachability，配合 unit_schedule.py 的 path_function/focus。
- 涉及 unit：getWeights, version, decimals, description
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__WeightedLPOracle/result.json`

### 132. real203/balancer__balancer-v3-monorepo__WeightedLPOracleFactory
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：create, getOracleVersion, getOracle, isOracleFromFactory
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__WeightedLPOracleFactory/result.json`

### 133. real203/balancer__balancer-v3-monorepo__WrappedBalancerPoolToken
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：mint, burn, burnFrom, permit
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__WrappedBalancerPoolToken/result.json`

### 134. real203/balancer__balancer-v3-monorepo__WrappedBalancerPoolTokenFactory
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：createWrappedToken, getVault, getWrappedToken
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 3}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/balancer__balancer-v3-monorepo__WrappedBalancerPoolTokenFactory/result.json`

### 135. real203/compound-finance__comet__AssetList
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：getAssetInfo
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__AssetList/result.json`

### 136. real203/compound-finance__comet__AssetListFactory
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：createAssetList
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__AssetListFactory/result.json`

### 137. real203/compound-finance__comet__BaseBridgeReceiver
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：initialize, executeProposal
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__BaseBridgeReceiver/result.json`

### 138. real203/compound-finance__comet__CometExt
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：approve
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometExt/result.json`

### 139. real203/compound-finance__comet__CometExtAssetList
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：approve
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometExtAssetList/result.json`

### 140. real203/compound-finance__comet__CometFactoryWithExtendedAssetList
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：clone
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometFactoryWithExtendedAssetList/result.json`

### 141. real203/compound-finance__comet__CometProxyAdmin
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：deployAndUpgradeTo
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometProxyAdmin/result.json`

### 142. real203/compound-finance__comet__CometProxyAdminOld
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：deployAndUpgradeTo
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometProxyAdminOld/result.json`

### 143. real203/compound-finance__comet__CometRewards
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setRewardConfigWithMultiplier
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometRewards/result.json`

### 144. real203/compound-finance__comet__CometWithExtendedAssetList
- 断点阶段：ESBMC frontend/backend crash
- 根因：ESBMC 发生 SIGSEGV，属于内部崩溃而非验证结论。
- 为什么导致 no-valid：进程崩溃导致 no cov-report、no CE、no valid。
- 应修位置：用对应 driver.log 的最小 flat.sol/solast 复现并修 ESBMC 崩溃点。
- 涉及 unit：accrueAccount, pause
- 辅助 bucket：{"KILLED": 1, "NO-WITNESS-UNKNOWN": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__CometWithExtendedAssetList/result.json`

### 145. real203/compound-finance__comet__Configurator
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：initialize
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__Configurator/result.json`

### 146. real203/compound-finance__comet__ConfiguratorOld
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：initialize
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__ConfiguratorOld/result.json`

### 147. real203/compound-finance__comet__ConfiguratorProxy
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：admin, implementation
- 辅助 bucket：{"NOT-CERTIFIED": 2}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__ConfiguratorProxy/result.json`

### 148. real203/compound-finance__comet__EzETHExchangeRatePriceFeed
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__EzETHExchangeRatePriceFeed/result.json`

### 149. real203/compound-finance__comet__MarketUpdateProposer
- 断点阶段：VeriPUT Stage4 materialization
- 根因：Stage2 已经有 CERTIFIED 路径，但 Stage4 没有把 certified region 写成 raw/valid Foundry 测试。
- 为什么导致 no-valid：验证器其实给了可用证明，no-valid 是 certifier 到 materializer/adoption 的断链。
- 应修位置：修 notes/coverage/scripts/put_all.py 和 rq1_veriput_run.py 的 certified row materialization/result adoption。
- 涉及 unit：setGovernor, setProposalGuardian, setMarketAdmin, cancel
- 辅助 bucket：{"CERTIFIED": 3, "KILLED": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__MarketUpdateProposer/result.json`

### 150. real203/compound-finance__comet__MultiplicativePriceFeed
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__MultiplicativePriceFeed/result.json`

### 151. real203/compound-finance__comet__OnChainLiquidator
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：absorbAndArbitrage, uniswapV3FlashCallback, unwrapWETH9
- 辅助 bucket：{"KILLED": 1, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__OnChainLiquidator/result.json`

### 152. real203/compound-finance__comet__PriceFeedWith4626Support
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__PriceFeedWith4626Support/result.json`

### 153. real203/compound-finance__comet__RateBasedScalingPriceFeed
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__RateBasedScalingPriceFeed/result.json`

### 154. real203/compound-finance__comet__ReverseMultiplicativePriceFeed
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__ReverseMultiplicativePriceFeed/result.json`

### 155. real203/compound-finance__comet__RsETHScalingPriceFeed
- 断点阶段：VeriPUT unit scheduler/focus mismatch
- 根因：VeriPUT 选择了一个源码/ABI 看似存在但 ESBMC path coverage 实际不会枚举的 unit。
- 为什么导致 no-valid：ESBMC 没有枚举任何目标路径，所以 valid=0；这是调度选择错误，不是验证失败。
- 应修位置：修 notes/coverage/scripts/unit_schedule.py 和 veriput_subjects.py：只调度 ESBMC coverage universe 中真实可枚举的 target function/getter。
- 涉及 unit：latestRoundData, version, decimals, description
- 辅助 bucket：{"NO-COORDINATE": 2, "NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/compound-finance__comet__RsETHScalingPriceFeed/result.json`

### 156. real203/ensdomains__ens-contracts__BaseRegistrarImplementation
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=24。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：addController, removeController, setResolver, register, registerOnly, renew, reclaim, transferOwnership, approve, setApprovalForAll, transferFrom, safeTransferFrom, ownerOf, nameExpires, available, supportsInterface, owner, renounceOwnership, balanceOf, name, symbol, tokenURI, getApproved, isApprovedForAll
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 24}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__BaseRegistrarImplementation/result.json`

### 157. real203/ensdomains__ens-contracts__BulkRenewal
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=3。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：renewAll, rentPrice, supportsInterface
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 3}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__BulkRenewal/result.json`

### 158. real203/ensdomains__ens-contracts__DNSRegistrar
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=4。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setPublicSuffixList, proveAndClaim, proveAndClaimWithResolver, enableNode
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__DNSRegistrar/result.json`

### 159. real203/ensdomains__ens-contracts__DNSSECImpl
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：setAlgorithm, setDigest, setOwner, verifyRRSet
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__DNSSECImpl/result.json`

### 160. real203/ensdomains__ens-contracts__ENSRegistry
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：setOwner, setResolver, setTTL
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__ENSRegistry/result.json`

### 161. real203/ensdomains__ens-contracts__ENSRegistryWithFallback
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：setOwner, setResolver, setTTL
- 辅助 bucket：{"KILLED": 1, "NOT-CERTIFIED": 2}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__ENSRegistryWithFallback/result.json`

### 162. real203/ensdomains__ens-contracts__ETHRegistrarController
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=13。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：commit, register, renew, recoverFunds, transferOwnership, rentPrice, valid, available, makeCommitment, withdraw, supportsInterface, owner, renounceOwnership
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 13}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__ETHRegistrarController/result.json`

### 163. real203/ensdomains__ens-contracts__ExponentialPremiumPriceOracle
- 断点阶段：ESBMC coverage goal explosion / VeriPUT no split
- 根因：目标 unit 的 branch arms × physical exits 生成的 coverage probe 数超过 goal cap，当前策略直接拒绝而不是分片/降级。
- 为什么导致 no-valid：没有 cov-report 就没有 CE；这类 case 需要 coverage 分片或 cheap-first，而不是增加 PUT 逻辑。
- 应修位置：修 scripts/solidity_path_generalise.py 的 probe goal cap fallback，或 src/goto-programs/goto_coverage.cpp 支持分片/采样。
- 涉及 unit：supportsInterface, decayedPremium, price, premium
- 辅助 bucket：{"KILLED": 2, "NO-COORDINATE": 1, "NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__ExponentialPremiumPriceOracle/result.json`

### 164. real203/ensdomains__ens-contracts__FIFSRegistrar
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：register
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__FIFSRegistrar/result.json`

### 165. real203/ensdomains__ens-contracts__L2ReverseRegistrar
- 断点阶段：VeriPUT region coordinate selection/materialization
- 根因：路径有 witness，但 region 中没有可由测试参数控制的 coordinate，或只剩 immutable/constructor 固定值。
- 为什么导致 no-valid：这类 case 应该至少落 concrete fallback；当前 valid=0 说明 fallback materialization/adoption 也断了。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 分类，以及 put_all.py 对 no-coordinate concrete fallback 的落盘。
- 涉及 unit：setName
- 辅助 bucket：{"NO-COORDINATE": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__L2ReverseRegistrar/result.json`

### 166. real203/ensdomains__ens-contracts__L2ReverseRegistrarWithMigration
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：batchSetName
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__L2ReverseRegistrarWithMigration/result.json`

### 167. real203/ensdomains__ens-contracts__LinearPremiumPriceOracle
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：supportsInterface, timeUntilPremium, price, premium
- 辅助 bucket：{"KILLED": 1, "NO-COORDINATE": 1, "NO-WITNESS-UNKNOWN": 1, "NOT-CERTIFIED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__LinearPremiumPriceOracle/result.json`

### 168. real203/ensdomains__ens-contracts__NameWrapper
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：setMetadataService, setUpgradeContract, supportsInterface, ownerOf, getApproved, getData, uri, canModifyName, canExtendSubnames, allFusesBurned, isWrapped, names, setFuses, setChildFuses
- 辅助 bucket：{"DRIVER-REFUSED": 1, "KILLED": 13}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__NameWrapper/result.json`

### 169. real203/ensdomains__ens-contracts__OwnedResolver
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：setText, setPubkey, setName, setInterface
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__OwnedResolver/result.json`

### 170. real203/ensdomains__ens-contracts__P256SHA256Algorithm
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：verify
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__P256SHA256Algorithm/result.json`

### 171. real203/ensdomains__ens-contracts__PublicResolver
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：setApprovalForAll, approve, setText, setPubkey
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__PublicResolver/result.json`

### 172. real203/ensdomains__ens-contracts__RSASHA1Algorithm
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：verify
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__RSASHA1Algorithm/result.json`

### 173. real203/ensdomains__ens-contracts__RSASHA256Algorithm
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：verify
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__RSASHA256Algorithm/result.json`

### 174. real203/ensdomains__ens-contracts__RegistrarSecurityController
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=10。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：addRegistrarController, removeRegistrarController, setRegistrarResolver, transferRegistrarOwnership, disableRegistrarController, setController, transferOwnership, supportsInterface, owner, renounceOwnership
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 10}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__RegistrarSecurityController/result.json`

### 175. real203/ensdomains__ens-contracts__Root
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=8。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setSubnodeOwner, setResolver, lock, setController, transferOwnership, supportsInterface, owner, renounceOwnership
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 8}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__Root/result.json`

### 176. real203/ensdomains__ens-contracts__RootSecurityController
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=5。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：disableTLD, transferOwnership, supportsInterface, owner, renounceOwnership
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 5}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__RootSecurityController/result.json`

### 177. real203/ensdomains__ens-contracts__SHA1Digest
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：verify
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__SHA1Digest/result.json`

### 178. real203/ensdomains__ens-contracts__SHA256Digest
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：verify
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__SHA256Digest/result.json`

### 179. real203/ensdomains__ens-contracts__StaticBulkRenewal
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=3。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：renewAll, rentPrice, supportsInterface
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 3}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__StaticBulkRenewal/result.json`

### 180. real203/ensdomains__ens-contracts__TLDPublicSuffixList
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：isPublicSuffix
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__TLDPublicSuffixList/result.json`

### 181. real203/ensdomains__ens-contracts__UniversalResolver
- 断点阶段：VeriPUT certifier salvage gap
- 根因：coverage 只留下 refutation/witness journal，没有形成完整 certified region；runner 没能把 partial journal 转成 concrete fallback。
- 为什么导致 no-valid：已有低层信息没有被转成 Foundry valid，断在 certifier-to-materializer 接口。
- 应修位置：修 notes/coverage/scripts/certify_all.py 的 partial journal salvage 和 notes/coverage/scripts/put_all.py concrete fallback。
- 涉及 unit：findResolver
- 辅助 bucket：{"KILLED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/ensdomains__ens-contracts__UniversalResolver/result.json`

### 182. real203/euler-xyz__euler-vault-kit__BalanceForwarder
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：enableBalanceForwarder
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__BalanceForwarder/result.json`

### 183. real203/euler-xyz__euler-vault-kit__Borrowing
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：touch
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Borrowing/result.json`

### 184. real203/euler-xyz__euler-vault-kit__ESynth
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：setCapacity
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__ESynth/result.json`

### 185. real203/euler-xyz__euler-vault-kit__EVault
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：initialize
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__EVault/result.json`

### 186. real203/euler-xyz__euler-vault-kit__EulerSavingsRate
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：gulp, updateInterestAndReturnESRSlotCache, transfer, redeem, transferFrom, approve, mint, deposit, withdraw, totalAssets, interestAccrued, getESRSlot, decimals, asset, name, symbol, totalSupply, EVC, convertToShares, convertToAssets, maxDeposit, maxMint, maxWithdraw, maxRedeem, previewDeposit, previewMint, previewWithdraw, previewRedeem, balanceOf, allowance
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 30}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__EulerSavingsRate/result.json`

### 187. real203/euler-xyz__euler-vault-kit__Governance
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：setGovernorAdmin
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Governance/result.json`

### 188. real203/euler-xyz__euler-vault-kit__Initialize
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：initialize
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 1}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Initialize/result.json`

### 189. real203/euler-xyz__euler-vault-kit__Liquidation
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：liquidate, checkLiquidation
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 2}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Liquidation/result.json`

### 190. real203/euler-xyz__euler-vault-kit__PegStabilityModule
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：swapToUnderlyingGivenIn, swapToUnderlyingGivenOut, swapToSynthGivenIn, swapToSynthGivenOut, EVC, quoteToUnderlyingGivenIn, quoteToUnderlyingGivenOut, quoteToSynthGivenIn, quoteToSynthGivenOut
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 9}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__PegStabilityModule/result.json`

### 191. real203/euler-xyz__euler-vault-kit__RiskManager
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：disableController
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__RiskManager/result.json`

### 192. real203/euler-xyz__euler-vault-kit__SequenceRegistry
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：reserveSeqId
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__SequenceRegistry/result.json`

### 193. real203/euler-xyz__euler-vault-kit__Token
- 断点阶段：Timeout/resource scheduling
- 根因：目标 unit 在当前预算内没有完成，worker 把它记为 killed；没有证据表明已经到达可 materialize 的 witness。
- 为什么导致 no-valid：生成链断在 Stage2 枚举/认证时间预算，无法靠 PUT 端补救。
- 应修位置：修 unit_schedule.py cheap-first/path slicing，generalise.py timeout fallback；必要时对该类函数降低 coverage universe。
- 涉及 unit：transfer
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Token/result.json`

### 194. real203/euler-xyz__euler-vault-kit__Vault
- 断点阶段：ESBMC Solidity tuple lowering
- 根因：tuple assignment / tuple RHS 的 frontend fallback 不覆盖当前 RHS 形状，导致 tuple 返回值无法展开成可赋值字段。
- 为什么导致 no-valid：目标函数还没进入 coverage solver，VeriPUT 拿不到 witness；如果只调 region/PUT 层不会改变结果。
- 应修位置：修 src/solidity-frontend/solidity_convert_tuple.cpp 对 symbol/function/struct tuple RHS 的统一展开；必要时补 migrate conditional 类型归一。
- 涉及 unit：deposit, mint, withdraw, redeem
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/euler-xyz__euler-vault-kit__Vault/result.json`

### 195. real203/morpho-org__morpho-blue__Morpho
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=28。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setOwner, enableIrm, enableLltv, setFee, setFeeRecipient, createMarket, supply, withdraw, borrow, repay, supplyCollateral, withdrawCollateral, liquidate, flashLoan, setAuthorization, setAuthorizationWithSig, accrueInterest, extSloads, position, market, idToMarketParams, DOMAIN_SEPARATOR, owner, feeRecipient, isIrmEnabled, isLltvEnabled, isAuthorized, nonce
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 28}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/morpho-org__morpho-blue__Morpho/result.json`

### 196. real203/sablier-labs__evm-monorepo__BobVaultShare
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：KILLED=1。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：mint
- 辅助 bucket：{"KILLED": 1}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/sablier-labs__evm-monorepo__BobVaultShare/result.json`

### 197. real203/sablier-labs__evm-monorepo__SablierBob
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：createVault, enter, enterWithNativeToken, redeem
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/sablier-labs__evm-monorepo__SablierBob/result.json`

### 198. real203/sablier-labs__evm-monorepo__SablierComptroller
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：initialize, disableCustomFeeUSDFor, execute, lowerMinFeeUSDForCampaign
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/sablier-labs__evm-monorepo__SablierComptroller/result.json`

### 199. real203/sablier-labs__evm-monorepo__SablierEscrow
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：cancelOrder, createOrder, fillOrder, setNativeToken
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/sablier-labs__evm-monorepo__SablierEscrow/result.json`

### 200. real203/sablier-labs__evm-monorepo__SablierLidoAdapter
- 断点阶段：ESBMC Solidity symbol/type table
- 根因：frontend 生成了引用某个 Solidity 合成类型/符号的表达式，但 symbol table 里没有对应完整 type，namespace::follow 断言。
- 为什么导致 no-valid：coverage 构造阶段崩溃，所有 unit 都无法转成 certifiable path。
- 应修位置：修 src/solidity-frontend 的合成符号声明/类型注册，重点查 library/interface/继承生成的 type name。
- 涉及 unit：processRedemption, registerVault, requestLidoWithdrawal, setSlippageTolerance
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 4}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/sablier-labs__evm-monorepo__SablierLidoAdapter/result.json`

### 201. real203/safe-fndn__safe-smart-account__ExtensibleFallbackHandler
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=12。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：setSupportedInterface, addSupportedInterfaceBatch, removeSupportedInterfaceBatch, setDomainVerifier, setSafeMethod, supportsInterface, safeInterfaces, onERC1155Received, onERC1155BatchReceived, onERC721Received, isValidSignature, domainVerifiers
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 12}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/safe-fndn__safe-smart-account__ExtensibleFallbackHandler/result.json`

### 202. real203/safe-fndn__safe-smart-account__Safe
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：setup, execTransaction, approveHash, simulateAndRevert, setFallbackHandler, addOwnerWithThreshold, removeOwner, swapOwner, changeThreshold, setGuard, enableModule, disableModule, execTransactionFromModule, execTransactionFromModuleReturnData, setModuleGuard, checkSignatures, checkNSignatures, domainSeparator, getTransactionHash, VERSION, nonce
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 21}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/safe-fndn__safe-smart-account__Safe/result.json`

### 203. real203/safe-fndn__safe-smart-account__SafeL2
- 断点阶段：ESBMC frontend/migrate
- 根因：Solidity lowering 产生了算术表达式的类型不一致 IR：表达式结果是 bit-vector，但参与运算的 operand 没有被提升/截断到同一 BV 类型。
- 为什么导致 no-valid：coverage 在生成 GOTO/irep2 阶段直接 abort，cov-report.json 不会产生，所以 VeriPUT 没有 CE、没有 region、也没有 Stage4 输入。
- 应修位置：修 src/util/migrate.cpp 的算术 operand 归一化，并检查 src/solidity-frontend 对 bytes/index/Yul 近似后产生的 uintN/uint256 混合表达式。
- 涉及 unit：setup, execTransaction, approveHash, simulateAndRevert, setFallbackHandler, addOwnerWithThreshold, removeOwner, swapOwner, changeThreshold, setGuard
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 10}
- 置信度：高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/safe-fndn__safe-smart-account__SafeL2/result.json`

### 204. real203/safe-fndn__safe-smart-account__SafeProxyFactory
- 断点阶段：混合失败，需要按 unit 拆修
- 根因：该 subject 的多个 unit 断在不同阶段：NO-WITNESS-UNKNOWN=7。
- 为什么导致 no-valid：只修一个泛化策略不足以保证 subject valid；必须优先处理其中最高优先级断点。
- 应修位置：处理顺序：ESBMC crash/focus mismatch > CERTIFIED未落盘 > NOT-CERTIFIED > NO-COORDINATE > NO-PATH > timeout。
- 涉及 unit：createProxyWithNonce, createProxyWithNonceL2, createChainSpecificProxyWithNonce, createChainSpecificProxyWithNonceL2, proxyCreationCode, proxyCreationCodehash, getChainId
- 辅助 bucket：{"NO-WITNESS-UNKNOWN": 7}
- 置信度：中
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/safe-fndn__safe-smart-account__SafeProxyFactory/result.json`

### 205. real203/safe-fndn__safe-smart-account__SimulateTxAccessor
- 断点阶段：VeriPUT region too weak/too wide
- 根因：候选 CE/region 被生成了，但 ESBMC 不能证明该参数化区域总是满足目标断言；说明 pin/shrink/refine 后的 R0/R1/R2 仍不稳定。
- 为什么导致 no-valid：certifier 不通过，所以 Stage4 没有 valid PUT；需要收窄 region 或退回 concrete fallback。
- 应修位置：修 solidity_path_generalise.py 的 coordinate 选择、pin、shrink/refine；修 certify_all.py 的 not-certified CE fallback。
- 涉及 unit：simulate
- 辅助 bucket：{"NOT-CERTIFIED": 1}
- 置信度：中高
- 结果文件：`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/real203/subjects/safe-fndn__safe-smart-account__SimulateTxAccessor/result.json`
