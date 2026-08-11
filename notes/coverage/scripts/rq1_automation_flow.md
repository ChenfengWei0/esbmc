# RQ1 VeriPUT 自动化流程

## 当前 RQ1 批处理算法

当前 no-valid 修复任务按 `notes/coverage/rq1_no_valid_each_case.json` 中修正后的
`205` 个修复前 no-valid case 推进。这个队列是 no-valid 债务基线；如果一个 case
生成并写回 `valid>0`，它就从 no-valid 债务中移出。若它仍然不是 PUT 或没有
R1/R2，则转入质量债务，而不是继续算 no-valid。

脚本层“100%”只表示流程闭环已经由代码强制执行，不表示 205 个 case 都已经
valid。脚本层 100% 的权威状态文件是：

- `notes/coverage/rq1_case_state.json`

该文件记录每个 case 的 `state`、结构化 `ground_truth`、历史 settle、preflight、
oracle details 和 repair ticket。MD 是人类审计视图；真实 gate/settle 不再靠
MD 关键词判断。

脚本层 100% 必须由命令证明：

```sh
python3 notes/coverage/scripts/rq1_case_batch.py audit
```

该 audit 不能只检查函数存在；它必须在临时目录中行为级演练：

- `seed-ground-truth` 只生成占位，不能通过 gate；
- 填满结构化 ground truth 后 gate 通过；
- 7-case batch 必须被 exactly-8 gate 拒绝；
- scheduler preflight 必须接受 target unit，拒绝 wrong-contract job；
- `put-summary.json` 中的 R1/R2 和 coordinate 必须能被提取；
- `settle` 必须写 state、ledger、manual-md marker upsert 和 repair ticket。

只有该 audit 打印 `自动化闭环审计：PASS` 时，才能说“脚本闭环 100%”。
这仍然不代表真实 205 个 no-valid case 已经全部恢复。

每一批固定推进 8 个 case，默认入口为：

```sh
python3 notes/coverage/scripts/rq1_case_batch.py init-state

python3 notes/coverage/scripts/rq1_case_batch.py seed-ground-truth \
  --batch-id manual-XXX-YYY --start-index XXX --end-index YYY

python3 notes/coverage/scripts/rq1_case_batch.py gate \
  --batch-id manual-XXX-YYY --start-index XXX --end-index YYY

python3 notes/coverage/scripts/rq1_case_batch.py start \
  --batch-id manual-XXX-YYY --start-index XXX --end-index YYY

python3 notes/coverage/scripts/rq1_case_batch.py supervise \
  --batch-id manual-XXX-YYY --start-index XXX --end-index YYY

python3 notes/coverage/scripts/rq1_case_batch.py settle \
  --batch-id manual-XXX-YYY --start-index XXX --end-index YYY
```

`start` 默认强制经过 `gate`；如果本批不是 8 个，或 MD 中任一 case 缺少
结构化 ground truth，脚本必须拒绝启动 ESBMC。

### 1. 固定账本

- 初始 no-valid 基线：`205`。
- 每个 case 只能处于以下状态之一：
  `NO_VALID`、`VALID_NO_PUT`、`VALID_PUT_NO_R1R2`、`VALID_PUT_R1R2`。
- 每次状态变化必须由脚本写入 `rq1_case_state.json`、batch settlement/ledger，
  并同步更新 manual MD。
- 禁止把当前 artifact scan 数字当成修复前基线。

### 2. 每批 8 个

- 从修正后的 no-valid 表中按编号连续取 8 个。
- 不跳号，不混入队列外 case。
- `rq1_case_batch.py` 的 batch-size gate 默认要求 exactly 8。

### 3. 静态调查先行

跑前必须在 `notes/coverage/rq1_no_valid_manual_root_causes.md` 为每个 case 写入：

- 读过的源码。
- 读过的失败证据。
- 归因。
- 应该修哪里。
- 预期 path。
- 预期 input/state/env region。
- 预期 oracle。
- 预期 R1/R2 强度。

缺任意一项，`rq1_case_batch.py gate/start` 必须失败。ESBMC 不能替代这一步。

结构化字段必须写入 `rq1_case_state.json` 的对应 case：

- `target_contract`
- `target_functions`
- `expected_units`
- `expected_path`
- `expected_region`
- `expected_oracle`
- `expected_r1r2`
- `root_cause`
- `fix_targets`
- `source_files_read`
- `evidence_files_read`

`seed-ground-truth` 只生成占位，不会让 gate 通过。

### 4. 批量修代码

- 先合并 8 个 case 的共享根因，再修改 ESBMC 或 VeriPUT 脚本。
- 优先级为：
  `VALID_PUT_R1R2 > VALID_PUT_NO_R1R2 > VALID_NO_PUT > NO_VALID fallback`。
- 禁止改一个 case 就跑完整队列。

### 5. 跑前验收

`monitor/settle` 必须读取 `unit-schedule.json` 并记录 scheduler preflight：

- schedule 是否存在。
- job 数量。
- `rq1_case_state.json` 里结构化 `expected_units` 是否进入 schedule。
- 是否有非 target contract 的 job。
- internal-target-wrapper 的 `sequence_strategy`、`max_tx`、`scope`。

如果 worker 已经在跑，而 scheduler preflight 失败，`monitor --stop-on-hard-decision`
必须把该 subject 作为硬早停对象。

### 6. 并发执行

- 每批只跑这 8 个。
- 本机默认 5 并发。
- 远程 `invmut-w2` 默认 3 并发。
- `rq1_worker_supervisor.py` 负责把 manifest 拆成本地/远程不重叠 shard。
- 远程必须 rsync ESBMC/VeriPUT、build ESBMC、启动 worker、pull/adopt 回写结果。

### 7. 持续监督

`rq1_case_batch.py monitor/supervise` 必须报告：

- 当前 stage：not-running、Stage1/wrapper、Stage2/certify、Stage4/PUT、final/adopted。
- 当前 valid/PUT/R1R2。
- `certify-results.jsonl` tail、bucket、timeout unit。
- `put-summary.json` 的 valid rows、PUT rows、R1/R2 rows。
- scheduler preflight。
- 活进程数和本机内存。

### 8. 早停

以下情况必须进入硬早停/代码修复，而不是继续等完整 timeout：

- 已经生成 `VALID_PUT_R1R2`。
- 已经 valid 但 no-PUT。
- 已经 PUT 但 no-R1/R2。
- 进程结束且仍 no-valid。
- Stage2 多个 KILLED 且无 certified region。
- scheduler preflight 失败。
- target unit 未调度或调度到错误 contract。

当前实现中，`monitor --stop-on-hard-decision` 按 subject kill 匹配进程；
`supervise` 会周期性调用 monitor，发现硬早停后停止并结算。本机和远程都按
subject 字符串尝试精确 kill；远程通过 ssh 执行 subject-filtered kill。

### 9. 结果判定

每个 case 结束后按以下规则判定：

- `valid=0` -> `NO_VALID`
- `valid>0, put=0` -> `VALID_NO_PUT`
- `put>0, r1r2=0` -> `VALID_PUT_NO_R1R2`
- `put>0, r1r2>0` -> `VALID_PUT_R1R2`

这个分类由 `rq1_case_batch.py settle` 写入 batch ledger。

### 10. 写回规则

只要 `valid>0` 就写回 RQ1，并从 no-valid 债务移出。写回必须保留：

- `result.json`
- `put.json` / `put-summary.json`
- raw tests
- valid tests
- concrete/PUT 类型
- 时间统计
- ESBMC 参数
- source/reference double oracle 结果
- R0/R1/R2 oracle tags

### 11. 失败回扣

如果 settle 后结果低于 `VALID_PUT_R1R2`：

- 写入 repair ticket。
- 记录 `NO_VALID` / `VALID_NO_PUT` / `VALID_PUT_NO_R1R2`。
- 从理论覆盖中扣回。
- 下一轮回到静态调查和代码修复，不能继续当作已解决。

### 12. 质量债务队列

- `VALID_NO_PUT` 进入 no-PUT 修复队列。
- `VALID_PUT_NO_R1R2` 进入 R1/R2 修复队列。
- no-valid 优先级最高，但 valid 后的质量债务不能丢失。

### 13. 每轮结算

每轮必须运行 `settle`。它必须写：

- `notes/coverage/rq1_runs/<batch>/settlement.json`
- `notes/coverage/rq1_case_state.json`
- `notes/coverage/rq1_batch_ledger.json`
- `notes/coverage/rq1_no_valid_manual_root_causes.md` 的 batch settlement 段；
  该段用 batch id marker upsert，不能重复 append
- 对低于 `VALID_PUT_R1R2` 的 case 写 repair ticket

### 14. 禁止项

- 禁止未通过 ground-truth gate 就启动 worker。
- 禁止跑未调查 case。
- 禁止泛跑队列外 case。
- 禁止成功写回但不更新文档。
- 禁止把 `197` 这类当前 artifact audit 数字当修复前基线。
- 禁止用 ESBMC 探索代替源码和失败日志调查。

## 旧 subagent/autopilot 约束

本文档是 `rq1_autopilot_daemon.py` 执行的硬流程。仓库内 Python 脚本不能直接调用
Codex host 级 `spawn_agent` / `wait_agent` / `close_agent`；该边界通过
`rq1_host_bridge.py` 的显式外部命令协议处理，缺少 bridge 时必须报告 blocked，
不得伪造成功。

- 仓库脚本负责：决策、门禁、状态汇总、变化触发打印、completion/review 入账、
  理论覆盖清单、worker 启动许可、失败反馈。
- `rq1_host_bridge.py` 负责：顺序消费 host action、调用注入的 host bridge 命令、
  写入结果、动态 lease/running、close ack；没有注入命令时保留 blocked 状态。
- `rq1_autopilot_daemon.py` 负责：持续调用 feedback、autopilot、host bridge 和
  mandatory status，并以 lock 防止重复 daemon。
- 禁止手写替代状态。每次状态必须来自脚本输出。

## 固定循环

每一轮只按以下顺序推进。

1. 收敛完成线程

   - 收到 subagent completion 后，立即调用：
     `rq1_completion_ingest.py`
   - completion 只允许入账为 `pending review`，净理论推进必须是 `0`。
   - completion 必须包含：检查过的失败记录、检查过的代码、代码根因、修改文件、
     静态检查、Datasets 未修改状态、理论 delta。

2. 收敛 review 结果

   - 收到 review 后，立即调用：
     `rq1_review_ingest.py`
   - review 必须包含：
     `changed_code`、`prior_failure`、`correctness_argument`、`verdict`、
     `theory_delta`、`commit decision`、`next_action`。
   - 缺字段则 review 无效，patch 保持 `pending`。
   - `needs-work` / `rejected`：理论 delta 记 `0` 或回扣，自动生成后续 repair。
     该 patch 的 review_round 固定为 1；后续 repair 必须使用新的 patch_id。
   - `accepted`：必须有 commit sha；没有 commit sha 不得进入 net theory。

3. 关闭完成线程

   - 运行：
     `rq1_subagent_autoclose.py plan`
   - 对每个 pending close，主 agent 调 host `close_agent`。
   - close 成功或 host 返回 not found 后，必须运行：
     `rq1_subagent_autoclose.py ack --agent-id <id>`
   - pending close 未清零时，禁止派发新 subagent。

4. 生成控制动作

   - 运行：
     `rq1_agent_control.py --format text --only-changes --max-spawn 5`
   - 无数字变化时不打印固定状态。
   - 有变化时必须打印中文固定报告，至少包括：
     active subagents、pending close、non-medium、write conflict、stale、
     repair assignments、review assignments、theory manifest case 数、
     review 汇总、自动动作。

5. 派发 subagent

   - 只消费 `rq1_agent_control.py` 输出的 `spawn_agent` 动作。
   - 每个新 subagent 必须显式 `reasoning_effort=high`。
   - 每个新 subagent 必须显式 `model=gpt-5.6-luna`；ledger 会拒绝其它型号。
   - 派发后立即顺序登记，不能并行写 ledger：
     `rq1_subagent_orchestrator.py lease ...`
     `rq1_subagent_orchestrator.py running ...`
   - 最低 active 阈值是 `5`。低于 5 且有可派任务时，必须继续派发。
   - write-mode 任务必须有独占 `write_scope`；readonly review 可并发。

6. 主审后的 commit

   - completed patch 的 review 由主 agent 完成，不派 review subagent；主审必须
     读取失败 artifacts、diff 和相邻调用路径，并可直接在该 exclusive scope 修订。
   - accepted、needs-work、rejected 都必须自动尝试 commit 本次实际文件改动；
     没有文件改动时不伪造 commit。
   - 只有 accepted 且记录 commit sha 后，才能把对应 patch 算入 net theory。
   - review 不通过时理论仍为 0/回扣，并必须派新的 repair patch。
   - 同一个 patch 最多主审一次；主审修改后的最终 patch 直接进入 validation
     queue 或回扣，不再产生 review-repair-review 循环。

7. 理论覆盖清单

   - 运行：
     `rq1_theory_covered_cases.py --out /tmp/veriput_rq1_theory_covered_cases.tsv`
   - worker 只允许跑该 TSV 中的 case。
   - theory manifest 为空时，禁止启动 worker。
   - worker 失败、无 PUT、无 R1/R2 后，必须回扣理论覆盖并派 repair。

8. worker 门禁

   - worker 只能在以下条件全部满足时启动：
     pending close 为 0；
     active subagents 达到最低阈值或没有待派动作；
     无 write conflict；
     无 stale agent；
     theory manifest 非空。
   - 本机和远程 worker 都必须挂内存/进程/progress watchdog。
   - worker 只跑 theory-covered case，不再泛跑所有 no_valid。

9. 失败反馈

   - 任何 worker 产出低于预期：
     no valid、valid but no PUT、PUT but no R1/R2、OOM、timeout、artifact missing，
     都必须进入 repair ticket。
   - repair dispatcher 必须基于 ticket 重新生成 subagent 任务。
   - 理论覆盖必须有增有减；被 worker 反证的 case 从 net theory 中扣除。

10. 自动运行入口

   - 启动持续控制：
     `python3 notes/coverage/scripts/rq1_autopilot_daemon.py --interval-s 30`
   - 单轮审计：
     `python3 notes/coverage/scripts/rq1_autopilot_daemon.py --once`
   - 停止：创建 `/tmp/veriput_rq1_autopilot.stop`。
   - host bridge 的 spawn/close 命令分别由
     `VERIPUT_HOST_SPAWN_COMMAND` / `VERIPUT_HOST_CLOSE_COMMAND` 注入；资源探针
     和超限 interrupt 分别由 `VERIPUT_HOST_RESOURCE_COMMAND` /
     `VERIPUT_HOST_INTERRUPT_COMMAND` 注入。`rq1_host_preflight.py` 会在 daemon
     中检查这些命令；缺失时 host action 阻塞且状态为 hard fail。worker action
     默认调用 `rq1_worker_supervisor.py`。
   - subagent watchdog 以 RSS、runtime、heartbeat 三项门禁监督 agent；超过阈值
     自动写入 interrupt action，host bridge 成功后再 close/ack。
   - worker 反馈由 `rq1_feedback_controller.py --scan` 写入：
     `/tmp/veriput_rq1_feedback_events.jsonl`、
     `/tmp/veriput_rq1_theory_blocks.jsonl` 和 repair tickets；theory manifest
     下一轮自动排除 active block。

## 资源最大化规则

- active subagents 目标：至少 `5`，对应 `local-1..3` 与 `remote-1..2` 五个
  theory-worker 槽位。每个被 review 接受的 repair patch 必须绑定一个独立槽位，
  然后才允许该槽位运行其 theory manifest case。
- subagent 总容量：`24`。
- 本机 worker 和远程 worker 只在 theory manifest 合法时启动。
- worker supervisor 默认本机 3 个 pump、远程 2-case 并发；每个进程有独立
  state/progress/log，远程 pump 负责 rsync/adopt 回写。
- 如果资源未最大化，状态报告必须给出具体原因，例如：
  pending close、active below 3、write conflict、stale agent、theory manifest empty、
  worker stopped by gate。

## 禁止项

- 禁止直接跑未覆盖清单里的 broad no_valid worker。
- 禁止用新 ESBMC/RQ1 运行替代代码级原因分析。
- 禁止修改 `/home/samson/workspace/VeriPUT/Datasets`。
- 禁止 review 未通过就提交。
- 禁止无 commit sha 就把 patch 算作 net theory。
- 禁止并行写 `/tmp/veriput_rq1_subagents.json`。
