# RQ1 no-valid manual root causes

本文件只记录逐个 case 手读后的归因。禁止把批量脚本输出直接改写成“原因”。

## 001. bugfix124/acfix_032_CVE_2021_39167 - TimelockController

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_032_CVE_2021_39167/flat.sol`
- `AccessControl.onlyRole/_checkRole/grantRole/revokeRole/renounceRole`: lines 104-145
- `TimelockController.schedule/scheduleBatch/cancel/execute/executeBatch/_beforeCall/_afterCall/updateDelay`: lines 278-379
- `diff.patch`: mutation target is `_afterCall`, adding `require(hasRole(EXECUTOR_ROLE, _msgSender()))` before the original readiness check.

读过的失败证据：

- `result.json`: `valid=0`, `put_valid=0`, `r1r2=0`, `raw=0`; attempted units are `cancel`, `updateDelay`, `grantRole`, `revokeRole`, `renounceRole`, `schedule`, `scheduleBatch`, `execute`; status is budget exhausted before the remaining units.
- `certify-results.jsonl`: `cancel/schedule/scheduleBatch/grantRole/revokeRole/renounceRole` are `NO-WITNESS-UNDECIDED`; `updateDelay` is `NO-PATH`; `execute` is `KILLED`.
- `cancel/driver.log`: all 5 claims are `named-obstacle`, so no claim was put to the solver.
- `schedule/driver.log`: all 13 claims are `named-obstacle`.
- `scheduleBatch/driver.log`: all 65 claims are `named-obstacle`.
- `updateDelay/driver.log`: all 3 claims are `bounded-holds` under `--scope focus --max-tx 1`.
- `execute/driver.log`: timeout after 403s before useful certification output.

归因：

这个 case 不是“找不到 unit”。目标修改在私有函数 `_afterCall`，只有 `execute` 和 `executeBatch` 能到达。当前调度却先花预算跑了大量 role-admin 或 schedule/cancel 入口，并且每个入口都在 `onlyRole`、`onlyRoleOrOpenRole`、`require(isOperationPending)`、`require(delay >= getMinDelay())` 等前置条件附近被 coverage 标成 `named-obstacle`。这些 claim 没有进入 solver，因此不会有 witness，也就没有 R0/R1/R2 可以生长。

`updateDelay` 单独跑也不该作为优先入口，因为源码要求 `msg.sender == address(this)`；在当前 `--scope focus --max-tx 1` 下，直接从外部调用 `updateDelay` 无法满足这个自调用条件，所以出现 `bounded-holds`。它需要通过 timelock 自身 schedule/execute 调用，不能当作普通单交易 unit。

`execute` 是正确 wrapper，但当前 harness 没有先建立三类必要状态：executor role、scheduled operation 的 `_timestamps[id]`、以及 `id` 与 calldata 的一致性。于是 `execute` 直接进入昂贵路径，最终 403s 被杀，没有产物。

应该修哪里：

1. VeriPUT unit scheduler：当 target metadata 的 `units_hint` 是私有/内部函数 `_afterCall` 时，优先选择能到达它的 public wrappers `execute`/`executeBatch`，不要先消耗预算在 `grantRole/revokeRole/renounceRole/cancel/scheduleBatch` 这种不会直接覆盖 mutation 的入口上。
2. VeriPUT harness/prestate synthesis：为 timelock 类 wrapper 生成 prestate，至少需要 pin/构造 `hasRole(EXECUTOR_ROLE, msg.sender)`、`_timestamps[id] > 1 && _timestamps[id] <= block.timestamp`，并保持 `id == hashOperation(...)`。否则 `_afterCall` 的新增 oracle 永远不能形成有效测试。
3. ESBMC/path coverage obstacle handling：当前 `require`/modifier 相关 source-level decisions 被降成 control-flow-free assume 后，coverage 把 sibling 标成 `named-obstacle`，导致“成功路径也没有 witness”。对这类 obstacle，不能让它把整个 unit 变成无产物；应允许把这些前置条件作为 harness assumptions/pins 继续枚举后继 target path。
4. VeriPUT scheduler budget：遇到 role-only/admin-only unit 连续返回 all-named-obstacle 时，应立即停止同类入口，转向 target-reaching wrapper，而不是等 subject 600s budget 被耗尽。

2026-08-11 rerun 后的新证据：

- 代码提交 `66785318ec` 后，`unit-schedule.json` 已确认 `execute` 和 `executeBatch` 都是 `priority_reason=internal-target-wrapper`，`sequence_strategy={"scope":"whole","max_tx":2}`；实际 ESBMC 命令也带 `--solidity-max-tx 2`。
- 当前 RQ1 结果已经从 no-valid 提升为 `valid-no-PUT`，但唯一 valid artifact 是 `final_deploy_concrete_fallback`，`put_valid=0`、`valid_put_with_R1_or_R2=0`。
- 最新 `cert/shards/001-execute.jsonl`: `scope=whole`, `max_tx=2`, `bucket=KILLED`, `exit=124`, `unit_timeout_s=59`, `wall_s=66.0`, `certified={}`, `not_certified={}`, `witnessed=null`。
- 最新 `cert/shards/013-executeBatch.jsonl`: `scope=whole`, `max_tx=2`, `bucket=KILLED`, `exit=124`, `unit_timeout_s=37`, `wall_s=40.7`, `certified={}`, `not_certified={}`, `witnessed=null`。
- `auto_cheap_stage2_retry.reason=esbmc-no-cov-report`，active workdir 下没有 `cov-report.json`，也没有 `cov-ce-journal.json`；因此 Stage4 没有可消费的 certified/not-certified row。
- `put/final_deploy_concrete_fallback/put-summary.json` 显示 `puts_emitted=0`、`concrete_replays_emitted=1`，说明当前 valid 是最后的 deploy-only safety net，不是目标函数测试。

更新后的归因：

`max_tx=2` 的调度缺口已修，但 Timelock 仍没有 PUT 的直接原因变成 runner 预算：`_effective_stage2_unit_timeout_cap_s()` 对 `internal-target-wrapper` 仍应用 adaptive/fair-share cap。这个 subject 有 13 个 scheduled jobs，`execute` 虽然排第一，但被截成约 59 秒；`executeBatch` 排到最后时只剩约 37 秒。两次 wrapper 都在 path coverage 产出 `cov-report.json` 前被 wrapper timeout kill，所以没有 Stage2 evidence，Stage4 只能落到 deploy-only concrete fallback。

下一处代码修复点：

1. `notes/coverage/scripts/rq1_veriput_run.py::_effective_stage2_unit_timeout_cap_s`：`priority_reason=="internal-target-wrapper"` 的 job 不应走 adaptive/fair-share cap，至少应给一个 target-wrapper floor，例如 180-300s，或者在 subject budget 内优先独占 Stage2 窗口。
2. `notes/coverage/scripts/rq1_veriput_run.py` 的 scheduler loop：一旦 target wrapper `KILLED` 且 `cov-report.json` 缺失，应停止跑辅助 getter/schedule units，保留剩余 budget 给另一个 target wrapper 或 target-wrapper retry，而不是继续把预算分给 NO-PATH 单元。
3. `notes/coverage/scripts/unit_schedule.py`：`internal-target-wrapper` 已能正确排序，但 `executeBatch` 仍会排在很多辅助 unit 后面；对 `_afterCall` 这种 only-wrapper target，应把 `execute` 和 `executeBatch` 作为同一 target-wrapper group 连续运行。

## 002. bugfix124/acfix_033_CVE_2021_39168 - TimelockController

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_033_CVE_2021_39168/flat.sol`
- `TimelockController.schedule/scheduleBatch/execute/executeBatch/_afterCall`: lines 278-360
- `diff.patch`: mutation target is `_afterCall`, adding `require(hasRole(TIMELOCK_ADMIN_ROLE, _msgSender()))` before the original readiness check.
- `meta.json`: `changed_functions` is `["_afterCall"]`.

读过的失败证据：

- `result.json`: `valid=0`, `put_valid=0`, `r1r2=0`, `raw=0`; only attempted units are `schedule` and `scheduleBatch`; `units_scheduled=20`.
- `result.json`: `completion_status=early-stop-no-output`, reason is `no output after 119.6s Stage 2; stopped before remaining units`.
- `schedule/driver.log`: all 13 claims are `named-obstacle`, no claim was put to the solver.
- `scheduleBatch/driver.log`: all 65 claims are `named-obstacle`, no claim was put to the solver.

归因：

这个 case 的目标修改同样在私有 `_afterCall`，但能够到达 `_afterCall` 的入口是 `execute` 和 `executeBatch`，不是 `schedule` 或 `scheduleBatch`。当前 runner/scheduler 只尝试了 `schedule` 和 `scheduleBatch` 两个入口就因为 Stage2 没有产物而 early-stop，剩余 18 个 scheduled units 没跑，包括真正 target-reaching 的 `execute/executeBatch`。

`schedule` 和 `scheduleBatch` 的源码都带 `onlyRole(PROPOSER_ROLE)`，并且进入 `_schedule` 后还要满足 `!isOperation(id)` 和 `delay >= getMinDelay()`。当前 path coverage 对这些 role/require 前置条件给出的结果是 all-named-obstacle，所以这两个入口既不能覆盖 `_afterCall`，也不能产出任何可认证 region。用它们作为前两个尝试 unit 后触发 early-stop，是这个 case 变成 no-valid 的直接原因。

应该修哪里：

1. VeriPUT unit scheduler：对 `changed_functions=["_afterCall"]` 的 case，必须把 `execute`/`executeBatch` 提到 `schedule/scheduleBatch` 前面。`schedule/scheduleBatch` 是建立 timelock state 的辅助入口，不是 oracle target wrapper。
2. VeriPUT runner early-stop：不能在只尝试了非 target-reaching units 后触发 `early-stop-no-output`。early-stop 条件必须检查“是否已经尝试过所有 target-reaching wrappers”；这里没有尝试 `execute/executeBatch`，所以不应该停。
3. VeriPUT harness/prestate synthesis：本 case 新增 oracle 是 `hasRole(TIMELOCK_ADMIN_ROLE, msg.sender)`，所以执行 wrapper 时需要构造或 pin admin-role state，同时仍要满足 `_timestamps[id]` readiness。只调 `schedule/scheduleBatch` 不会检查新增 oracle。
4. ESBMC/path coverage obstacle handling：`schedule/scheduleBatch` 的 all-named-obstacle 说明 role/require 前置条件被当成结构性障碍。这个问题会污染 scheduler 的反馈：它把“辅助入口不可枚举”误用成“整个 subject 无产物”。runner 应该把这种结果标记为 auxiliary-obstacle，并继续 target wrapper。

2026-08-11 rerun 后的新证据：

- 代码提交 `66785318ec` 后，`execute` 和 `executeBatch` 都已按 `internal-target-wrapper` 调度，`sequence_strategy={"scope":"whole","max_tx":2}`；实际 ESBMC 命令带 `--solidity-max-tx 2`。
- 当前 RQ1 结果从 no-valid 提升为 `valid-no-PUT`，但 valid artifact 仍只是 `final_deploy_concrete_fallback`；`put_valid=0`、`valid_put_with_R1_or_R2=0`。
- 最新 `cert/shards/001-execute.jsonl`: `scope=whole`, `max_tx=2`, `bucket=KILLED`, `exit=124`, `unit_timeout_s=59`, `wall_s=66.0`, `certified={}`, `not_certified={}`, `witnessed=null`。
- 最新 `cert/shards/013-executeBatch.jsonl`: `scope=whole`, `max_tx=2`, `bucket=KILLED`, `exit=124`, `unit_timeout_s=36`, `wall_s=39.7`, `certified={}`, `not_certified={}`, `witnessed=null`。
- `auto_cheap_stage2_retry.reason=esbmc-no-cov-report`，active workdir 没有 `cov-report.json` / `cov-ce-journal.json`，因此没有 Stage2 row 可以进入 Stage4。
- `put/final_deploy_concrete_fallback/put-summary.json` 中 `puts_emitted=0`、`concrete_replays_emitted=1`，当前 valid 是 deploy-only safety net。

更新后的归因：

原先“target wrapper 没被尝试”的问题已修，但这次仍没产生 PUT，是因为 wrapper 被 Stage2 adaptive cap 提前杀掉，而不是因为 `max_tx` 或 Stage4 materializer。`execute` 拿到约 59 秒，`executeBatch` 拿到约 36 秒；两者都在产出 path coverage report 前退出 124。由于没有 witness/certified/not-certified fallback，后续只能写 deploy-only concrete fallback。

下一处代码修复点：

1. `notes/coverage/scripts/rq1_veriput_run.py::_effective_stage2_unit_timeout_cap_s`：对 `priority_reason=="internal-target-wrapper"` 禁用或提高 adaptive cap。这个 case 的 target wrapper 是唯一能触达 `_afterCall` 的入口，不能和普通 expensive unit 一样被 fair-share 截断。
2. `notes/coverage/scripts/rq1_veriput_run.py`：target wrapper 的 `KILLED + no cov-report` 应触发同一 wrapper 的 budget retry，而不是只记录 `auto_cheap_stage2_retry` 后继续低价值 units。
3. `notes/coverage/scripts/unit_schedule.py`：把 `execute`/`executeBatch` target-wrapper group 放在所有 getter/schedule/cancel 之前连续执行，避免第二个 wrapper 在 subject 尾部只拿到 36 秒。

## 003. bugfix124/acfix_077_L1Block - L1Block

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_077_L1Block/flat.sol`
- `Semver.version`: lines 4-6
- `L1Block.DEPOSITOR_ACCOUNT`, state fields, `setL1BlockValues`, `depositorAccount`: lines 10-42
- `diff.patch`: the guard changed from `msg.sender == DEPOSITOR_ACCOUNT` to `msg.sender == depositorAccount`, the original state assignments were removed/commented away, and `address public depositorAccount` was added after the function.

读过的失败证据：

- `result.json`: `valid=0`, `put_valid=0`, `raw=0`; attempted units are `setL1BlockValues` and inherited `version`; `stage2_wall_s=4.505`, `stage4_wall_s=0`.
- `certify-results.jsonl`: `setL1BlockValues` has `witnessed=3`, coordinates include `_batcherHash.length`, `_hash.length`, `msg.sender`, `state.depositorAccount`, but all witnessed paths are `NOT_CERTIFIED`.
- `setL1BlockValues__pf78/driver.log`: command was run with `--ce-collection-only`; it explicitly says CE evidence was persisted, but no region was certified and no test was emitted.
- `ce-collection.json`: `status=witnessed`, with counterexamples including `msg.sender`, `state.depositorAccount`, and ABI/env pins.
- `version/driver.log`: inherited pure getter produced `NO GENERALISABLE COORDINATE` because only `state.DEPOSITOR_ACCOUNT` was relevant and it is a constant, not a test-settable coordinate.

归因：

这个 case 的目标函数非常小，真正有价值的 oracle 是 `setL1BlockValues` 里 `msg.sender == depositorAccount` 与原始 `DEPOSITOR_ACCOUNT` 常量保护的差异。VeriPUT/ESBMC 已经能枚举到相关 witness，并且选出了 `msg.sender` 和 `state.depositorAccount` 这两个关键坐标；这说明不是“没有 CE”。

no-valid 的直接原因是调用策略和结果物化：`setL1BlockValues` 的有效 witness 被 `--ce-collection-only` 停在 CE 收集阶段，没有进入认证/Stage4 materialization；随后 `version` 是 inherited pure getter，与漏洞无关，只能得到 constant-coordinate point，不可能生成强 PUT。最终 `stage4_wall_s=0`，所以连 concrete replay test 都没有被写出。

这里还存在一个 Solidity 源码层面的特殊点：`depositorAccount` 是新增的 public state variable，默认值为 0；当前 witness 中 `state.depositorAccount=0`，`msg.sender=0/1` 可以区分通过/拒绝路径。这个状态坐标是可由测试构造或通过 deployment/default state 固定的，不应该被当作无法物化的抽象证据丢弃。

应该修哪里：

1. VeriPUT runner：`--ce-collection-only` 只能作为信息收集模式，不能作为 RQ1 生成模式的终点。对已经 `status=witnessed` 且有可设坐标的 target unit，必须继续进入 certification 和 Stage4，至少生成 concrete fallback。
2. VeriPUT materializer：当 region 未认证但 `concrete_fallback=true` 且 witness check 不是明确 invalid 时，应保留 raw concrete replay candidate，并走 Foundry/source double oracle。当前 `raw=0` 是物化缺口。
3. VeriPUT coordinate policy：`state.depositorAccount` 是普通 state var，不是 constant/immutable；应允许作为 state coordinate 或 deployment/default-state pin。不能让 inherited `version` 上的 `state.DEPOSITOR_ACCOUNT` constant NO-COORDINATE 覆盖掉 target unit 的可设坐标。
4. Scheduler：继承来的 `Semver.version` 与 `changed_functions=["setL1BlockValues"]` 无关，应降权或跳过。它只能制造 constant-coordinate noise，不能帮助生成漏洞回归测试。

## 004. bugfix124/acfix_3_5_077_L1Block - L1Block

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_3_5_077_L1Block/flat.sol`
- `Semver.version`: lines 4-6
- `L1Block.setL1BlockValues` and `onlyDepositor`: lines 29-54
- `diff.patch`: the original inline `require(msg.sender == DEPOSITOR_ACCOUNT)` was moved into a new `onlyDepositor` modifier; the body still assigns all L1 fields.

读过的失败证据：

- `result.json`: `valid=0`, `put_valid=0`, `raw=0`, `stage4_wall_s=0`; attempted units are `setL1BlockValues` and inherited `version`.
- `setL1BlockValues/driver.log`: path enumeration succeeded with 2 witnessed paths, body path `enc=6` reaches `onlyDepositor`.
- `setL1BlockValues/driver.log`: free coordinates include `_basefee`, `_batcherHash.length`, `_hash.length`, `_l1FeeOverhead`, `_l1FeeScalar`, `_number`, `_sequenceNumber`, `_timestamp`, and `msg.sender`.
- `setL1BlockValues/generalise-result.json`: `certified=[]`, `not_certified` contains two entries and both have `concrete_fallback=true`.
- `setL1BlockValues/generalise-result.json`: body path `enc=6` decision is the modifier guard at line 52, `!(!(msg.sender == DEPOSITOR_ACCOUNT))`; witness has `msg.sender=4294967295`.
- `version/driver.log`: inherited pure getter again reports constant `state.DEPOSITOR_ACCOUNT` as no-generalizable-coordinate.

归因：

这个 case 已经不是 coverage 失败。ESBMC/VeriPUT 成功枚举到了 `setL1BlockValues` 的 reject/body 两条路径，并且 body path 精确落在新增 `onlyDepositor` modifier 的 guard 上。区域没有认证成功，因为 single-point witness check 返回 `UNKNOWN`，但 driver 明确把两条 not-certified paths 标成 `concrete_fallback=true`。

no-valid 的直接原因是 Stage4/materialization 没接住 fallback：`result.json` 里 `stage4_wall_s=0`、`raw=0`，说明这些 concrete fallback 没有被转成 raw Foundry/replay artifact，更没有进入 valid double oracle。这里至少应该有一个 concrete replay candidate；是否能变成 PUT 是后续问题，但 no-valid 不应该发生。

这里和 003 的差别是：003 主要被 `--ce-collection-only` 截断；004 已经完整 generalise 了，只是认证失败后 fallback 没有物化。

应该修哪里：

1. VeriPUT Stage4/materializer：读取 `generalise-result.json.not_certified[*].concrete_fallback=true`，为每个 target unit 生成 concrete replay raw test；不能只处理 `certified` region。
2. VeriPUT result adoption：只要 concrete fallback 经过 source/reference double oracle 不违例，就应该写入 `raw_tests/valid_tests`，即使它不是 PUT。当前 `raw=0` 是 adoption/materialization 漏接。
3. Certification wrapper：`witness_check=UNKNOWN` 不能被当作“无产物”。它表示不能证明 region，但 driver 已经保留 CE；RQ1 目标要求先 100% valid，因此应降级到 concrete。
4. Scheduler：继承 `version` 的 NO-COORDINATE 不能影响 `setL1BlockValues` 的 target fallback 输出。对 changed function 已有 witnessed fallback 时，应立即 materialize，而不是被后续 getter 结果拖成 no-output。
