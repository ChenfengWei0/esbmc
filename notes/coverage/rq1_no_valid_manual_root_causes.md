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

## 2026-08-11 round status before next no-valid investigation

Canonical no-valid inventory used for this round: `notes/coverage/rq1_no_valid_each_case.json`, `count=205`.  Earlier ad-hoc directory scans are invalid because they include redo/superseded subject directories.

Recovered since the manual investigation started:

- `bugfix124/acfix_077_L1Block`: recovered from no-valid to valid PUT with R1/R2.  The fix path was Stage2/Stage4 fallback/materialization for witnessed but not-certified `setL1BlockValues` paths plus result adoption.  Current RQ1 artifact has `valid>0`, `put_valid>0`, `r1r2>0`.
- `bugfix124/acfix_3_5_077_L1Block`: recovered from no-valid to valid PUT with R1/R2.  Same family as above, but the target guard is the introduced `onlyDepositor` modifier; current artifact has `valid>0`, `put_valid>0`, `r1r2>0`.
- `bugfix124/acfix_032_CVE_2021_39167`: recovered only from no-valid to valid concrete fallback.  It still has `put_valid=0`, `r1r2=0`.  Latest run proved `scope=whole/max_tx=2` and wrapper budget uncapping are active, but Stage2 killed `execute` before evidence, so only deploy-only fallback was emitted.
- `bugfix124/acfix_033_CVE_2021_39168`: same as `acfix_032`; valid concrete fallback only, no PUT/R1/R2.

First repair round writeback check (`#001`-`#004`):

- Written back to RQ1: 4/4.
- `bugfix124/acfix_032_CVE_2021_39167`: `valid=1`, `put_valid=0`, `r1r2=0`; artifact is `put/final_deploy_concrete_fallback/put-summary.json`.
- `bugfix124/acfix_033_CVE_2021_39168`: `valid=1`, `put_valid=0`, `r1r2=0`; artifact is `put/final_deploy_concrete_fallback/put-summary.json`.
- `bugfix124/acfix_077_L1Block`: `valid=1`, `put_valid=1`, `r1r2=1`; artifact is `put/bugfix124__acfix_077_L1Block__setL1BlockValues/put-summary.json`.
- `bugfix124/acfix_3_5_077_L1Block`: `valid=1`, `put_valid=1`, `r1r2=1`; artifact is `put/bugfix124__acfix_3_5_077_L1Block__setL1BlockValues/put-summary.json`.

Unresolved from the previous round:

- Timelock PUT/R1/R2 is still unresolved.  The latest concrete-only result exposed a runner bug: after `execute` consumed most of the budget, the hard Stage4 reserve skipped the remaining `internal-target-wrapper` alternative (`executeBatch`).  Commit `489222633e` changes reserve from subject-global to per-job: the first wrapper still leaves reserve for another wrapper, while the last wrapper consumes the remaining Stage2 budget instead of preserving time for deploy-only fallback.  This must be checked before spending another ESBMC run.
- The deeper Timelock semantic obstacle may still remain after `executeBatch` is attempted: `_beforeCall/_afterCall` requires scheduled operation state, role/timestamp/hash consistency, and array/calldata coordinates.  Before rerunning, the next static check must confirm that the wrapper group can produce at least one concrete witness or fallback row, not merely that both wrappers are scheduled.

Next manual investigation batch: canonical no-valid rows 5-12.  No ESBMC runs are allowed during this investigation; each case must be grounded in saved result/cert logs plus the target `flat.sol` source, and must state exactly what code should be fixed before any rerun.

2026-08-11 state sync after adoption repair:

- Pre-repair no-valid baseline remains 205.
- `rq1_artifact_audit.py --rewrite` rewrote stale adoption summaries from retained artifacts.
- `rq1_case_batch.py sync-results` now scans canonical and historical redo/incomplete/superseded/adopted subject result directories and chooses the strongest result per canonical subject.
- Current baseline-state split after sync: `NO_VALID=197`, `VALID_NO_PUT=4`, `VALID_PUT_NO_R1R2=1`, `VALID_PUT_R1R2=3`.
- Newly removed from no-valid by this sync: `acfix_032_CVE_2021_39167`, `acfix_033_CVE_2021_39168`, `acfix_077_L1Block`, `acfix_3_5_077_L1Block`, `acfix_fixlink_DnGmxBatchingManager`, `acfix_fixlink_Product`, `acfix_fixlink_Product2`, `acfix_real_FlashGovernanceArbiter`.
- Within rows 005-012: row 005 is `VALID_PUT_R1R2`; rows 007 and 008 are `VALID_NO_PUT`; row 009 is `VALID_PUT_NO_R1R2`; rows 006, 010, 011, and 012 remain no-valid.
- `gate --batch-id manual-005-012 --start-index 5 --end-index 12` now passes because all eight rows have structured ground truth in `rq1_case_state.json`.

## 005. bugfix124/acfix_fixlink_DnGmxBatchingManager - DnGmxBatchingManager

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_fixlink_DnGmxBatchingManager/flat.sol`
- `DnGmxBatchingManager` state and modifiers: lines 362-414.
- `executeBatchDeposit`: lines 527-542.
- `_executeVaultUserBatchDeposit`: lines 621-635 and following.

读过的失败证据：

- `certify-results.jsonl`: only one row, `unit=executeBatchDeposit`, `bucket=KILLED`, `exit=124`, `unit_timeout_s=599`, `wall_s=599`, `coords=[]`, `witnessed=null`.
- `driver.log`: the command was a focused one-tx Stage2 run with 12 GiB and full 599s; it printed only setup lines and `[run] TIMEOUT after 599s`, with no `cov-report.json`, no `cov-ce-journal.json`, and no partial witness.
- The saved command path points at `/home/samson/workspace/VeriPUT/Results/BugFix124/.../flat.sol`, but the actual source now lives under `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/.../flat.sol`.  The artifact is still readable through canonical workdirs, but the result evidence itself saved a stale source path.

归因：

This is not a Stage4/materialization problem because Stage2 produced no path evidence at all.  The target function is small at the surface but calls through multiple external/model-heavy operations: `onlyKeeper`, timestamp cooldown, `_unpause`, `sGlp.transfer`, `_executeVaultUserBatchDeposit`, `dnGmxJuniorVault.deposit`, fixed-point casts, and nested mapping writes.  A focused max-tx=1 run spent the entire subject budget inside ESBMC before the path coverage report existed.

The immediate no-valid reason is `KILLED before first evidence`.  Unlike Timelock, there is no second wrapper alternative and no existing concrete fallback.  The stale source path in the driver log is also a tooling problem: future root-cause automation should not depend on saved command paths when canonical prepared-subject paths differ.

应该修哪里：

1. `scripts/solidity_path_generalise.py`: add a cheap preflight / early-salvage mode for target units with external-call-heavy bodies.  If full `--path-cov-probe` produces no report after a small budget, retry with fewer path identity dimensions: no emit expansion, lower call-depth, or function-body-only probe before spending the full 600s.
2. `notes/coverage/scripts/rq1_veriput_run.py`: do not allocate the whole subject budget to a single `KILLED before evidence` target without a staged cheap probe first.  This is exactly the class where a 60s preflight would have said “no cov-report yet; try degraded probe or skip”.
3. Result evidence persistence: store canonical `PreparedSubject.flat_sol` and `workdir` paths separately from the historical command string, so later manual diagnosis does not chase stale `/Results/BugFix124` paths.

## 006. bugfix124/acfix_fixlink_MStableYieldSource - MStableYieldSource

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_fixlink_MStableYieldSource/flat.sol`
- `MStableYieldSource` state: immutable `savings`, immutable `mAsset`, and `mapping(address => uint256) imBalances` at lines 369-372.
- `supplyTokenTo`: lines 411-417.
- `redeemToken`: lines 419-435.

读过的失败证据：

- `supplyTokenTo/driver.log`: one witnessed path, `enc=2`, synthetic ABI value gate class.  Free coordinates were `mAssetAmount`, `to`, and `state.imBalances[to]`.  The path was not certified because auto-pinning `msg.value=0` excluded the path's own ABI-value-gate counterexample (`msg.value` in the CE was 1).  The log explicitly says this path falls back to its concrete counterexample test.
- `redeemToken/driver.log`: only setup lines and `[run] TIMEOUT after 133s`; no witness/cov report.
- `result.json`: no valid/raw test despite `supplyTokenTo` having a not-certified path that the driver itself marked as concrete-fallback material.

归因：

This case has two separate failures.  `redeemToken` is a late-budget timeout, but `supplyTokenTo` already had enough evidence for at least a concrete replay candidate.  The target path is an ABI value-gate/revert path of a nonpayable function; it cannot be a useful PUT region under `msg.value=0`, but it is still a valid concrete counterexample/replay path and the driver says so.  The no-valid outcome is therefore caused by Stage4/result adoption failing to materialize `not_certified` concrete-fallback rows.

应该修哪里：

1. `notes/coverage/scripts/put_all.py` and/or `rq1_veriput_run.py`: treat `not_certified_details[*].concrete_fallback=true` and prose “falls back to its concrete counterexample test” as raw concrete candidates, even when the reason is ABI-value-gate/nonpayable `msg.value` exclusion.
2. `scripts/solidity_path_put.py`: concrete replay materializer must be able to render a revert/value-gate replay with the original `msg.value` when the path is intentionally outside the auto-pinned slice.
3. Scheduler/runtime: after one target unit has concrete fallback evidence, do not let a second unit timeout prevent Stage4 from running.  Stage4 should run on partial candidates before the subject ends.

## 007. bugfix124/acfix_fixlink_Product - Product

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_fixlink_Product/flat.sol`
- Tuple-return / tuple-assignment sources include `PositionLib.settled` lines 1414-1418, `VersionedPositionLib.sync` lines 1562-1568, `VersionedAccumulatorLib.accumulate` lines 1603-1611, and `_accumulatePositionFee` lines 1676-1684.
- Many target methods call these libraries through `settleAccount`, `openTake/openMake/closeTake/closeMake`, and view helpers.

读过的失败证据：

- 48 units failed in about 6s each with `bucket=NO-WITNESS-UNKNOWN` and diagnostic `esbmc-no-cov-report: Unexpected tuple exit=6`.
- `initialize/driver.log` and `openTake/driver.log`: ESBMC reaches `Converting`, prints many approximation warnings, then terminates with `ERROR: Unexpected tuple` / `ERROR: CONVERSION ERROR`; no cov report is possible.

归因：

This is an ESBMC Solidity frontend conversion failure, not a VeriPUT scheduling issue and not solver timeout.  The Product contract uses tuple returns and destructuring in core libraries.  ESBMC aborts during conversion before symbolic execution.  Any rerun before fixing tuple lowering is wasted for every Product/Product2 unit.

应该修哪里：

1. `src/solidity-frontend/solidity_convert_tuple.cpp`: support tuple RHS forms produced by function calls returning multiple values and conditional tuple expressions, especially assignment of `(Position memory newPosition, bool settled) = ...` and `(accumulatedPosition, accumulatedFee) = ...`.
2. `src/solidity-frontend/solidity_convert_expr.cpp` / call conversion if the tuple node is emitted by a function-call expression rather than a tuple literal.
3. `scripts/solidity_path_generalise.py`: detect `Unexpected tuple` as a frontend hard error and do not spend retries on every unit in the same contract; cache the subject-level frontend blocker.

## 008. bugfix124/acfix_fixlink_Product2 - Product

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_fixlink_Product2/flat.sol`
- Same Product implementation pattern as 007: tuple-return libraries and destructuring in position/version accounting.

读过的失败证据：

- Same as 007: 48 `NO-WITNESS-UNKNOWN` rows, all with `Unexpected tuple exit=6` before coverage report generation.

归因：

Same ESBMC frontend tuple blocker as 007.  Product2 should be counted in the same repair bucket.  It is not a separate region/scheduler problem.

应该修哪里：

Same as 007.  A single ESBMC tuple lowering fix should theoretically cover both Product and Product2, and likely more no-valid contracts using Solidity struct tuple returns.

## 009. bugfix124/acfix_real_FlashGovernanceArbiter - FlashGovernanceArbiter

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/acfix_real_FlashGovernanceArbiter/flat.sol`
- `setGoverned`: lines 204-208, array-length guard and loop writing `governed[governables[i]]`.
- `flashEnabled` modifier: lines 211-214.
- `assertGovernanceApproved`: lines 218-237, `transferFrom`, pending decision mapping, timestamp/epoch checks, and state writes.

读过的失败证据：

- `setGoverned/driver.log`: ESBMC solved many path/probe claims and found a failed probe witness, then exited `-11` before writing cov-report.  This is a crash after partial all-witnesses work, not a simple timeout.
- `assertGovernanceApproved/driver.log`: salvaged one witnessed path from partial `cov-ce-journal.json`.  Free coordinates were `emergency`, `sender`, `target`, `state.governed[msg.sender]`, and `state.pendingFlashDecision[target][sender].unlockTime`.
- The not-certified detail has `witness_check=FAILED`: the single-point check refuted the path and the prose says `NO TEST IS EMITTED FOR IT`.  Its JSON sidecar nevertheless carried `concrete_fallback=true`; that is a bookkeeping bug, not a valid replay candidate.

归因：

There are two root causes.  `setGoverned` is an ESBMC crash after partial coverage solving, probably triggered by calldata dynamic arrays plus mapping writes inside the loop.  `assertGovernanceApproved` is not a safe fallback: certification refuted the only witnessed path at the single point, so the correct fix is to prevent the JSON from advertising it as `concrete_fallback=true` and then seek a different, non-refuted path/unit.

应该修哪里：

1. ESBMC: investigate `setGoverned` crash in Solidity path coverage/all-witnesses over calldata arrays and mapping writes.  The crash happens after a failed probe, so the coverage engine should at least flush `cov-ce-journal.json` before abnormal exit.
2. `scripts/solidity_path_generalise.py`: when ESBMC exits `-11` after witness lines, preserve the partial CE journal as a subject artifact instead of losing the unit as pure `NO-WITNESS-UNKNOWN`.
3. `certify_all.py`: never mark `witness_check=FAILED` / `NO TEST IS EMITTED` rows as concrete fallback.
4. `rq1_veriput_run.py`: if the only partial witness is refuted, continue to another target-relevant unit or weaker probe instead of going straight to deploy-only fallback.

## 010. bugfix124/ct_5_Proposals_can_be_cancelled - DAO

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/ct_5_Proposals_can_be_cancelled/flat.sol`
- `cancelProposal`: lines 84-91.
- Helpers called by it: `hasMinority` lines 162-170 and `isEqual(bytes,bytes)` lines 171-177.
- Source dependencies: several mappings keyed by proposal id and external `iVAULT(VAULT).totalWeight()` in the emitted event.

读过的失败证据：

- `cancelProposal/driver.log`: ESBMC solved many branch/path claims successfully for about 120s, then timed out.  It was not stuck at conversion and not a no-path case.
- The log shows `cancelProposal:path:1015` and many branch/exits were solved, but no final coverage report was written before timeout.

归因：

This is a partial-progress timeout where useful coverage work existed but was not flushed into a usable CE journal/report.  The target function has string/bytes equality via `sha256(part1) == sha256(part2)`, proposal mapping state, and an external event argument `iVAULT(VAULT).totalWeight()`.  Full path-cov over every branch/exit exceeded 120s, and the runner had no staged partial fallback for already-solved claims.

应该修哪里：

1. ESBMC/goto coverage: path coverage should stream or periodically flush `cov-ce-journal.json` as claims are solved, not only at clean completion.  This would turn partial progress into concrete fallback candidates.
2. `scripts/solidity_path_generalise.py`: add a timeout-wrapper salvage path that parses solved/failed claim output or partial journal and emits a partial generalise result instead of `KILLED`.
3. Scheduler: for bytes/string-heavy DAO units, use a cheap first pass with lower probe/exit budget; full path-cov can be deferred if cheap pass yields no candidate.

## 011. bugfix124/pop_001_Multicall - Multicall

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_001_Multicall/flat.sol`
- `library Multicall`: line 3818.
- `function multicall(State storage state, bytes[] calldata data) internal returns (bytes[] memory results)`: lines 3822-3840.

读过的失败证据：

- `result.json`: `status=no-units`, `reason=target contract has no schedulable public/external units`, `units_hint=["multicall"]`, `contract=Multicall`.
- `unit-schedule.json`: `source.summary.units=0`, `summary.subjects=0`, `jobs=0`, no skipped rows.
- Source confirms why: `Multicall` is a library and its only target function is `internal` with a `State storage` parameter.  It is not callable as a public/external ABI unit.

归因：

This is not an ESBMC failure.  VeriPUT's subject/unit discovery only schedules public/external contract methods.  The target here is an internal library function, so the current framework has no harness wrapper and produces no jobs.

应该修哪里：

1. `notes/coverage/scripts/subject_unit_manifest.py` and `unit_schedule.py`: distinguish “no public/external unit” from “target is internal library function”.  The latter needs a generated harness, not a no-units final failure.
2. Add a VeriPUT harness-generation path for library/internal target functions: synthesize a small contract with `State` storage and a public wrapper calling `Multicall.multicall(state, data)`.
3. Until that harness exists, mark this case as unsupported-by-current-caller-model rather than rerunning.  ESBMC cannot discover a callable target that the runner never emits.

## 012. bugfix124/pop_009_PrivatePool - PrivatePool

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_009_PrivatePool/flat.sol`
- `PrivatePool.buy`: lines 2179-2242, array inputs, Merkle multiproof, loops, transfers, royalty calls.
- `PrivatePool.sell`: lines 2245-2301, similar array/proof/external-call structure.
- `PrivatePool.execute`: lines 2358-2361, `target.call{value: msg.value}(data)`.
- `sumWeightsAndValidateProof`: lines 2500-2518, dynamic arrays and Merkle proof verification.

读过的失败证据：

- `buy/driver.log`: path coverage degradation withdrew 13 call points, but `buy` still needed `360000 probe claims (36 branch arms x 10000 physical exits)`, exceeding `--path-cov-max-goals 10000`; driver refused instead of truncating.
- `sell` has the same source shape and same cap family.
- `execute/driver.log`: salvaged one witnessed path from partial `cov-ce-journal.json`; free coordinate was `target`, but the single-point check was `FAILED` and the prose says `NO TEST IS EMITTED FOR IT`.  Its JSON `concrete_fallback=true` is the same bookkeeping bug as rows 013/018, not a valid fallback.

归因：

This is a mixed but code-level clear case.  For `buy/sell`, coverage strategy is too expensive: dynamic arrays, Merkle multiproof, loops, events, and external transfers explode the probe universe even after call-point degradation.  For `execute`, Stage2 did not have a safe concrete fallback; it had a refuted witness mislabeled as fallback in JSON.

应该修哪里：

1. `scripts/solidity_path_generalise.py`: when `--path-cov-max-goals` refuses a unit, fallback to a shard/truncated-probe mode rather than returning no witness.  For `buy/sell`, a sound weaker target is better than zero output: function-entry/body/revert class, capped branch subset, or no-emits/no-callee identity.
2. `src/goto-programs/goto_coverage.cpp`: if possible, support explicit probe sharding so the driver can ask for a slice of the probe universe instead of all `branch arms x exits` at once.
3. `certify_all.py`: prevent refuted `execute` rows from being advertised as concrete fallback.
4. `rq1_veriput_run.py`: when `buy/sell` refuse and `execute` is refuted, retry a cheap body/revert-class probe or a simpler target-relevant unit instead of accepting the deploy-only safety net as the only artifact.

## 013. bugfix124/pop_018_PrivatePool - PrivatePool

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_018_PrivatePool/flat.sol`
- `PrivatePool.initialize`: lines 1814-1849.
- `PrivatePool.buy/sell`: lines 1855-1990.
- `PrivatePool.execute`: lines 2030-2044, `onlyOwner`, `target.call{value: msg.value}(data)`, and revert bubbling.

读过的失败证据：

- Only attempted unit is `execute`, `bucket=NOT-CERTIFIED`, `wall_s=226.9`.
- `execute/driver.log` salvaged one witnessed path from partial `cov-ce-journal.json`; free coordinate was `target`.
- The single-point check was explicitly `FAILED`: log says `REFUTED at the single point: this path gets NO test`, then the printed reason says `AND NO TEST IS EMITTED FOR IT`.
- `certify-results.jsonl` still carried `not_certified_details["6"].concrete_fallback=true, witness_check=FAILED, ce present`; this was a bookkeeping bug, not a valid fallback.

归因：

This case is not recoverable by simply consuming concrete fallback.  The only witness comes from `execute`, whose path identity depends on an external low-level call result and on dropped/unexpressible state pins (`owner`, implementation/metadata/protocol fee fields).  Certification found a refuting single point, so the witness is not safe even as a concrete replay under the driver's own rule.

The no-valid cause is therefore twofold: the scheduler only got evidence for an untrustworthy `execute` path, and `certify_all.py` mislabeled that refuted CE as `concrete_fallback=true`.  Future runs need a different unit or a weaker but non-refuted coverage target; the existing `execute` row must not be adopted.

应该修哪里：

1. `notes/coverage/scripts/certify_all.py`: do not mark `witness_check=FAILED` or `NO TEST IS EMITTED` rows as concrete fallback, even if a CE exists.
2. `scripts/solidity_path_generalise.py`: make the prose and JSON agree.  A refuted single-point witness should not end with “falls back to its concrete counterexample test”.
3. `unit_schedule.py` / `rq1_veriput_run.py`: after a refuted `execute` low-level-call path, do not stop the subject; try simpler target-relevant setters/getters or body-entry fallback before final deploy-only fallback.

## 014. bugfix124/pop_032_PuttyV2 - PuttyV2

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_032_PuttyV2/flat.sol`
- `setBaseURI`: lines 991-995.
- `setFee`: lines 997-1002.
- `fillOrder`: lines 1015-1115, including signature check, cancellation mapping, whitelist, order hashing, ERC20/ERC721 transfers, events.
- `exercise`: lines 1117 onward; same order/hash/asset-family dependencies.

读过的失败证据：

- `fillOrder`: `DRIVER-REFUSED`, path probe goal cap.  Even after 25 call points were withdrawn, the unit needed `560000 probe claims (56 branch arms x 10000 physical exits)`, above `--path-cov-max-goals 10000`.
- `setBaseURI`: one witnessed path but `NO-COORDINATE`; all useful quantities were immutables/constants or unsupported aggregate hashes (`ORDER_TYPE_HASH`, `_HASHED_NAME`, `_TYPE_HASH`, etc.).  Driver says it should fall back to concrete.
- `setFee`: `KILLED` after about 208s with no cov report.

归因：

`fillOrder` is a coverage strategy explosion, not a solver proof failure: the source has signature recovery, EIP712 hashing, multiple require gates, mapping reads, event expansion, ERC20/ERC721 transfer helpers, and four order-shape branches.  The current probe mode refuses instead of sharding/truncating, so it produces no evidence.

`setBaseURI` should have produced at least a concrete replay, but the no-coordinate fallback was not adopted.  This is separate from `fillOrder`; even if `fillOrder` remains too large, the subject should not be no-valid.

应该修哪里：

1. `scripts/solidity_path_generalise.py` / `src/goto-programs/goto_coverage.cpp`: implement capped probe sharding/truncated path probe when a unit exceeds `--path-cov-max-goals` after call-point degradation.
2. `put_all.py` / `rq1_veriput_run.py`: ensure `NO-COORDINATE` rows with witnessed point paths are materialized as concrete replay candidates and adopted if Foundry double oracle passes.
3. `rq1_veriput_run.py`: after a large unit is refused, immediately run Stage4 for any already witnessed no-coordinate/simple unit instead of spending the remaining budget on another expensive unit.

## 015. bugfix124/pop_033_PrivatePool - PrivatePool

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_033_PrivatePool/flat.sol`
- `initialize`: lines 1813-1848.
- `flashFee`: lines 2228-2234.

读过的失败证据：

- `flashFee`: `NO-COORDINATE`; the only witnessed path was the nonpayable ABI value-gate reject, and the only state quantities were constructor-fixed immutables/constants.
- `initialize`: three witnessed paths.  `enc=2` is pin-excluded ABI value gate (`msg.value` CE 1 outside pinned `[0,0]`) and should be concrete fallback.  `enc=14/15` have `witness_check=UNKNOWN`, not PUT-certified.
- `result.json` still has no raw/valid output.

归因：

This is a Stage4/materialization/adoption miss.  `initialize` has a legitimate concrete replay candidate for the ABI value gate and potentially best-effort UNKNOWN concrete candidates, but no artifact was emitted.  It does not require fixing the large `buy/sell` PrivatePool path first.

应该修哪里：

1. `put_all.py` and `rq1_veriput_run.py`: accept pin-excluded `concrete_fallback=true` with CE even when `witness_check` is null; this is not a proof, only a concrete replay candidate for Foundry double oracle.
2. `certify_all.py`: keep `FAILED` rows out of concrete fallback, but preserve `UNKNOWN`/pin-excluded rows as concrete-only candidates.
3. `rq1_veriput_run.py`: Stage4 should run after `initialize` evidence and not wait for unrelated expensive units.

## 016. bugfix124/pop_042_VaultAdapter - VaultAdapter

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_042_VaultAdapter/flat.sol`
- `checkAccess(bytes4 _selector)`: line 709.
- `initialize`: line 801.
- `rate`: line 806.
- `setSlopes(address, SlopeData memory) external checkAccess(this.setSlopes.selector)`: line 830.
- `setLimits(...) external checkAccess(this.setLimits.selector)`: line 836.

读过的失败证据：

- Six units all fail before `cov-report.json`.
- `initialize/driver.log` and `setSlopes/driver.log` both reach GOTO program creation and then abort in path coverage insertion:
  `ERROR: function call: argument sol:@C@VaultAdapter@F@setSlopes_checkAccess@_selector#1836 type mismatch: got unsigned int, expected struct`.

归因：

This is an ESBMC Solidity frontend/path-coverage instrumentation type bug.  The modifier argument is a selector expression (`this.setSlopes.selector`) whose source type is `bytes4`.  The generated modifier wrapper symbol expects a struct-shaped argument, while the call site passes an unsigned integer selector.  Because path coverage instruments the whole contract, even `initialize` and `rate` die when the malformed `setSlopes_checkAccess` helper is present.

应该修哪里：

1. ESBMC Solidity modifier lowering: ensure modifier parameters of type `bytes4` / selector expressions keep scalar `bytes4`/uint32-compatible type, not a struct placeholder.
2. `src/solidity-frontend` call conversion: when lowering `this.f.selector`, emit a value whose type matches the modifier parameter exactly.
3. `scripts/solidity_path_generalise.py`: cache this as a subject-level frontend blocker so every unit is not retried separately before the ESBMC fix.

## 017. bugfix124/pop_046_CVXStaker - CVXStaker

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_046_CVXStaker/flat.sol`
- `CvxPoolInfo public cvxPoolInfo`: line 368.
- `getReward(bool claimExtras)`: lines 485-491.
- Reward reads use `cvxPoolInfo.rewards` and `rewardTokens` dynamic array.

读过的失败证据：

- `getReward/driver.log` salvaged one witnessed path from partial `cov-ce-journal.json`.
- It refused `state.cvxPoolInfo` as unsupported aggregate coordinate, though it could name one scalar field (`token`).
- Driver result is `NO-COORDINATE` and explicitly says this is a coordinate-kind result whose witnessed point falls back to concrete counterexample test.

归因：

This is not an ESBMC crash.  The target unit was witnessed, but the coordinate model could not express the struct-valued state dependency `cvxPoolInfo` as a settable region.  For RQ1 valid coverage, this should still emit a concrete replay.  For PUT/R1R2 strength, the missing feature is struct-field coordinate expansion for source state structs used by the unit (`cvxPoolInfo.rewards`, `pId`, `token`), not another solver run.

应该修哪里：

1. `put_all.py` / `rq1_veriput_run.py`: make no-coordinate witnessed rows materialize concrete replay reliably.
2. `scripts/solidity_path_generalise.py`: when an aggregate state coordinate has scalar fields, propose field-level coordinates for fields actually read by the target instead of collapsing the whole struct to unsupported.
3. For later PUT strength: extend R1/R2 state region generation over `state.cvxPoolInfo.<field>` where Solidity AST proves the field is a scalar.

## 018. bugfix124/pop_048_PrivatePool - PrivatePool

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_048_PrivatePool/flat.sol`
- `buy`: lines 2179-2232.
- `sell`: lines 2236-2301.
- `execute`: lines 2349-2363.

读过的失败证据：

- `buy`: no cov report; after degradation, it still needed `320000 probe claims (32 branch arms x 10000 physical exits)`, above cap.
- `sell`: same family, `280000 probe claims`.
- `execute`: one witnessed path, but single-point check `FAILED`; detail still had `concrete_fallback=true`, which is a bookkeeping bug.  The prose says `NO TEST IS EMITTED FOR IT`.

归因：

The main target units `buy/sell` are blocked by coverage probe universe explosion.  The fallback-looking `execute` row must not be used because certification refuted the path at the witness point.  This case needs probe sharding/truncation or a weaker but non-refuted target class; it cannot be fixed by adopting `execute` concrete fallback.

应该修哪里：

1. `goto_coverage.cpp` / `solidity_path_generalise.py`: support sharded/truncated path probes for units over `--path-cov-max-goals`.
2. `certify_all.py`: never mark `witness_check=FAILED` rows as `concrete_fallback=true`.
3. `rq1_veriput_run.py`: if a large unit is refused and the only fallback is refuted, retry with a cheap body/revert-class target rather than accepting deploy-only as the sole artifact.

## 019. bugfix124/pop_049_Cooler - Cooler

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_049_Cooler/flat.sol`
- `owner()`: line 565.
- `requestLoan`: line 587.
- `rollLoan`: line 659.
- `collateralFor`: line 787.
- `getLoan`: line 822.
- Structs `Request` and `Loan`: lines 544-560.

读过的失败证据：

- 23 units all fail before `cov-report.json`.
- `requestLoan/driver.log` and even pure `owner/driver.log` abort during GOTO program creation:
  `arith_2ops::arith_2ops ... Assertion p2 || (is_bv_type(t) == is_bv_type(v1->type) && t->get_width() == v1->type->get_width()) failed`.
- The log has many fixed-point/clone/assembly approximations, but the actual fatal error is a bitvector width/type mismatch in arithmetic expression construction.

归因：

This is an ESBMC frontend/IR construction crash, subject-wide.  Since even `owner()` triggers it, the crash is caused by converting/instrumenting some contract-level helper or cloned-library arithmetic, not by `requestLoan` search complexity.  Rerunning any unit before fixing the arithmetic-width mismatch is wasted.

应该修哪里：

1. ESBMC Solidity conversion / migrate path: find the arithmetic expression in Cooler conversion that creates an `arith_2ops` result type whose width differs from operand 1.  The likely source is mixed-width fixed-point math or generated clone/factory assembly lowering.
2. `src/util/migrate.cpp` and Solidity expression conversion: insert explicit casts before constructing binary arithmetic when Solidity operands have been widened/narrowed differently.
3. `solidity_path_generalise.py`: cache the `arith_2ops width assert` as a subject-level hard frontend error.

## 020. bugfix124/pop_058_PuttyV2 - PuttyV2

读过的源码：

- `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_058_PuttyV2/flat.sol`
- `setBaseURI`: line 987.
- `setFee`: line 993.
- `fillOrder`: line 1001.
- `exercise`: line 1091.
- `hashOppositeOrder/hashOrder`: lines 1274-1282.

读过的失败证据：

- 24 units all fail before `cov-report.json`.
- `fillOrder`, `hashOrder`, and `setFee` logs all end at the same converted statement around `fillOrder` line 1024, then print `migrate expr failed`.
- The failing statement is under the `require(SignatureChecker.isValidSignatureNow(order.maker, orderHash, signature), "Invalid signature")` branch and involves rollback/state-save code for mappings and generated contract pools.

归因：

This is not the same as row 014's probe-cap refusal.  In this version, ESBMC fails during migrate/IR conversion before coverage can run, and it fails subject-wide because path coverage sees the `fillOrder` rollback branch even when the focus function is `setFee` or `hashOrder`.

The code-level blocker is rollback/migrate handling for a complex `require` condition involving `SignatureChecker`, EIP712 hash, calldata bytes signature, and generated state-save arrays.  The frontend emits an expression/code shape that `migrate.cpp` cannot translate.

应该修哪里：

1. `src/util/migrate.cpp`: handle the code/ifthenelse/rollback expression shape emitted for Solidity revert rollback, rather than aborting with `migrate expr failed`.
2. Solidity path coverage insertion: when focus is a simple unit (`setFee`, getter), avoid migrating unrelated `fillOrder` rollback-save code if it is outside the focused dispatcher slice.
3. `solidity_path_generalise.py`: cache `migrate expr failed` as a subject-level frontend blocker and do not spend per-unit retries until the ESBMC migrate fix lands.

<!-- RQ1_BATCH_SETTLEMENT_BEGIN manual-005-012-novalid -->

## Batch settlement: manual-005-012-novalid

- case_count: 4
- new valid: 0
- new PUT: 0
- new R1/R2: 0
- bucket_counts: {"NO_VALID": 4, "put": 0, "r1r2": 0, "valid": 0}

- `bugfix124/acfix_fixlink_MStableYieldSource`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_MStableYieldSource/result.json
- `bugfix124/ct_5_Proposals_can_be_cancelled`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/ct_5_Proposals_can_be_cancelled/result.json
  - oracle_tags: {"R0": 2, "R1": 10}
  - oracle_tags: {"R0": 2, "R1": 10}
  - oracle_tags: {"R0": 2, "R1": 10}
- `bugfix124/pop_001_Multicall`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_001_Multicall/result.json
- `bugfix124/pop_009_PrivatePool`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_009_PrivatePool/result.json

<!-- RQ1_BATCH_SETTLEMENT_END manual-005-012-novalid -->

<!-- RQ1_BATCH_SETTLEMENT_BEGIN manual-005-012-novalid-r2 -->

## Batch settlement: manual-005-012-novalid-r2

- case_count: 4
- new valid: 1
- new PUT: 1
- new R1/R2: 1
- bucket_counts: {"NO_VALID": 3, "VALID_PUT_R1R2": 1, "put": 1, "r1r2": 1, "valid": 1}

- `bugfix124/acfix_fixlink_MStableYieldSource`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/acfix_fixlink_MStableYieldSource/result.json
- `bugfix124/ct_5_Proposals_can_be_cancelled`: VALID_PUT_R1R2 valid=14 put=4 r1r2=4 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/ct_5_Proposals_can_be_cancelled/result.json
  - oracle_tags: {"R0": 2, "R1": 10}
  - oracle_tags: {"R0": 2, "R1": 10}
  - oracle_tags: {"R0": 2, "R1": 10}
  - oracle_tags: {"R0": 2, "R1": 6, "R2": 6}
  - coordinates: {"_usdv": 1, "_vader": 1, "_vault": 1, "true": 1}
- `bugfix124/pop_001_Multicall`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_001_Multicall/result.json
- `bugfix124/pop_009_PrivatePool`: NO_VALID valid=0 put=0 r1r2=0 result=/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/bugfix124/subjects/pop_009_PrivatePool/result.json

<!-- RQ1_BATCH_SETTLEMENT_END manual-005-012-novalid-r2 -->

<!-- RQ1_PRE_RUN_READINESS_BEGIN manual-005-012-novalid-r3 -->

## Pre-run readiness: manual-005-012-novalid-r3

Rolling batch now selects these 8 current `NO_VALID` cases: 006
`acfix_fixlink_MStableYieldSource`, 011 `pop_001_Multicall`, 012
`pop_009_PrivatePool`, 013 `pop_018_PrivatePool`, 014 `pop_032_PuttyV2`,
015 `pop_033_PrivatePool`, 016 `pop_042_VaultAdapter`, and 017
`pop_046_CVXStaker`.

Before any rerun, the following historical-prevention files have been read and
recorded in `rq1_case_state.json`:

- Concrete fallback / Stage4 adoption:
  `notes/coverage/scripts/put_all.py`,
  `scripts/solidity_path_put.py`,
  `notes/coverage/scripts/rq1_veriput_run.py`,
  `notes/coverage/scripts/certify_all.py`.
- Internal-library/no-unit scheduling:
  `notes/coverage/scripts/subject_unit_manifest.py`,
  `notes/coverage/scripts/unit_schedule.py`,
  `notes/coverage/scripts/rq1_veriput_run.py`.
- Path-coverage goal-cap / sampling:
  `scripts/solidity_path_generalise.py`,
  `src/goto-programs/goto_coverage.cpp`,
  `notes/coverage/scripts/certify_all.py`.
- Modifier selector type mismatch:
  `src/solidity-frontend/solidity_convert_modifier.cpp`,
  `src/solidity-frontend/solidity_convert_ref.cpp`,
  `src/solidity-frontend/solidity_convert_call.cpp`.

Code-level prevention already applied before this readiness pass:

- `certify_all.py` and `solidity_path_generalise.py` no longer classify the
  newer ESBMC message `Sampling ... instead of refusing` as the old hard
  `path-coverage-probe-goal-cap` refusal.  This prevents `PrivatePool` /
  `PuttyV2` runs from being marked no-cov-report when ESBMC actually sampled
  the exit universe and can still provide refutation witnesses.
- `rq1_case_batch.py` readiness now refuses to start unless every case lists
  `last_failure`, `why_static_missed`, `prevention_code_change`, and
  `prevention_files_read`; every `fix_targets` entry must appear in
  `prevention_files_read` and exist on disk.
- `rq1_case_batch.py` rolling mode skips already-fixed cases and fills forward
  to exactly 8 `NO_VALID` cases before any worker can start.
- Remote readiness uses the same ESBMC/VeriPUT paths and `LD_LIBRARY_PATH` as
  the remote worker and checks the 3 x 6 GiB + 2 GiB memory budget before
  launch.

Per-case rerun prevention summary:

- `acfix_fixlink_MStableYieldSource`: last rerun reached Stage4 for two units
  but both had `valid_rows=0`; this was missed because the previous gate did
  not force inspection of Stage4 summaries before rerun.  Prevention is the
  Stage4-row monitor/readiness plus rereading concrete fallback adoption code.
- `pop_001_Multicall`: no-units is caused by an internal library target with a
  `State storage` parameter; this was missed because no-units was still treated
  like an ordinary terminal schedule.  Prevention is explicit review of
  manifest/scheduler/no-unit fallback code before any rerun.
- `pop_009_PrivatePool`: path-cov explosion and a refuted `execute` witness
  were known, but the outer diagnostic parser did not distinguish new ESBMC
  sampling from old hard refusal.  Prevention is the sampling diagnostic fix
  plus scheduler wrong-target preflight.
- `pop_018_PrivatePool`: `execute` had `witness_check=FAILED`, so any
  `concrete_fallback=true` sidecar is inadmissible; rerun must seek another
  unit.  Prevention is rejecting FAILED / `NO TEST IS EMITTED` fallback rows.
- `pop_032_PuttyV2`: `fillOrder` is path-cov expensive, but `setBaseURI`
  already had no-coordinate concrete evidence.  Prevention is sampling
  diagnostic handling plus no-coordinate concrete fallback materialization.
- `pop_033_PrivatePool`: `initialize` had pin-excluded concrete fallback and
  `flashFee` no-coordinate evidence, but Stage4 did not adopt it.  Prevention
  is authenticated pin-excluded/no-coordinate fallback review before rerun.
- `pop_042_VaultAdapter`: frontend/path-coverage insertion failed on selector
  modifier argument type mismatch.  Prevention is checking selector packing and
  modifier formal coercion paths before rerun.
- `pop_046_CVXStaker`: witnessed `getReward` path was no-coordinate due to an
  aggregate `cvxPoolInfo` state dependency; rerun should first materialize
  concrete fallback, while struct-field coordinate expansion remains PUT/R1R2
  quality work.

<!-- RQ1_PRE_RUN_READINESS_END manual-005-012-novalid-r3 -->
