# RQ1 VeriPUT 自动化流程

本文档是主 agent 必须执行的硬流程。仓库内 Python 脚本不能直接调用
Codex host 级 `spawn_agent` / `wait_agent` / `close_agent`，所以自动化边界
如下：

- 仓库脚本负责：决策、门禁、状态汇总、变化触发打印、completion/review 入账、
  理论覆盖清单、worker 启动许可、失败反馈。
- 主 agent 负责：严格消费脚本输出的 host-level action queue，实际调用
  `spawn_agent` / `wait_agent` / `close_agent`，并把结果回写脚本 ledger。
- 禁止主 agent 手写替代状态。每次状态必须来自脚本输出。

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
     `rq1_agent_control.py --format text --only-changes --max-spawn 10`
   - 无数字变化时不打印固定状态。
   - 有变化时必须打印中文固定报告，至少包括：
     active subagents、pending close、non-medium、write conflict、stale、
     repair assignments、review assignments、theory manifest case 数、
     review 汇总、自动动作。

5. 派发 subagent

   - 只消费 `rq1_agent_control.py` 输出的 `spawn_agent` 动作。
   - 每个新 subagent 必须显式 `reasoning_effort=medium`。
   - 派发后立即顺序登记，不能并行写 ledger：
     `rq1_subagent_orchestrator.py lease ...`
     `rq1_subagent_orchestrator.py running ...`
   - 最低 active 阈值是 `10`。低于 10 且有可派任务时，必须继续派发。
   - write-mode 任务必须有独占 `write_scope`；readonly review 可并发。

6. review 通过后的 commit

   - 只有 `rq1_review_ingest.py` 判定 accepted 且记录 commit sha 后，才能把对应
     patch 算入 net theory。
   - review 通过后必须触发 commit；commit sha 必须回写 subagent ledger。
   - review 不通过时不得提交该 patch，并必须派 repair。

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

## 资源最大化规则

- active subagents 目标：至少 `10`。
- subagent 总容量：`24`。
- 本机 worker 和远程 worker 只在 theory manifest 合法时启动。
- 如果资源未最大化，状态报告必须给出具体原因，例如：
  pending close、active below 10、write conflict、stale agent、theory manifest empty、
  worker stopped by gate。

## 禁止项

- 禁止直接跑未覆盖清单里的 broad no_valid worker。
- 禁止用新 ESBMC/RQ1 运行替代代码级原因分析。
- 禁止修改 `/home/samson/workspace/VeriPUT/Datasets`。
- 禁止 review 未通过就提交。
- 禁止无 commit sha 就把 patch 算作 net theory。
- 禁止并行写 `/tmp/veriput_rq1_subagents.json`。

