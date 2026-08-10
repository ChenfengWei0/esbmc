# RQ1 VeriPUT 自动化流程

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
