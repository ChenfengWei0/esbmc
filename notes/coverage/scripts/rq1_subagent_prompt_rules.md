# RQ1 Subagent Hard Rules

Every spawned subagent must follow these rules.

1. Inspect prior failure records before editing:
   - `/tmp/veriput_no_valid_root_causes.tsv`
   - relevant `driver.log`, `result.json`, `certify-results.jsonl`,
     `put.json`, `cov-report.json`, and `cov-ce-journal.json` under
     `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`
   - any root-cause notes under `notes/`

2. Inspect the concrete owning source code before editing:
   - ESBMC Solidity frontend files for frontend/cov-report failures
   - VeriPUT runner/certify/PUT scripts for scheduling/materialization failures
   - never infer the fix from category names alone

3. Do not use fresh ESBMC/RQ1 runs as root-cause discovery.
   Py compile, syntax checks, `git diff --check`, and read-only grep/sed/cat are
   allowed. Fresh ESBMC/RQ1 runs are reserved for supervised local/remote workers.

4. Every completion report must include:
   - failure records inspected
   - source files inspected
   - code-level root cause
   - fix target and changed paths
   - theoretical coverage contribution
   - confirmation that no Datasets files were modified

5. Write scopes are exclusive. Do not edit outside the assigned paths.

6. Cross-review is mandatory after any write-mode subagent patch:
   - a completed write-mode patch starts with `review_status: pending`
   - an independent review agent must inspect the diff against adjacent patches,
     the shared call paths, and the progress-ledger claim before integration
   - the reviewer must report conflicts, soundness risks, changed paths, and
     whether the theoretical coverage claim should stand
   - a patch with unresolved conflicts or missing review must not be used as a
     reason to stop code repair, must not be synced as "fully integrated", and
     must remain visible in the watchdog report

7. Theoretical progress is not monotonic:
   - worker repair tickets from covered categories subtract from net
     `theoretical_progress` when a later run still has no valid test
   - valid-but-no-PUT and PUT-but-no-R1/R2 results are quality debt and must
     spawn repair work for PUT/R1/R2 strength
   - do not keep reporting an old coverage number after validation contradicts
     it; update the ledger and investigate the failed subject/code path

8. Autonomous repair dispatch is mandatory:
   - run `rq1_repair_dispatcher.py` after worker results or repair tickets
   - if it emits assignments, spawn or reuse subagents for those assignments
   - if spawning is blocked by capacity, close completed agents first and report
     the capacity blocker explicitly
   - do not let the main agent manually serialize all repair work when dispatch
     assignments exist

9. Review assignments are mandatory after write assignments:
   - when two or more write-mode subagents touch adjacent or shared call paths,
     schedule a separate review assignment before treating either patch as
     integrated
   - the review assignment must be read-only unless it explicitly receives an
     integration write scope
   - a reviewer must check the concrete diff, shared helper APIs, ledger count,
     and whether a later worker result contradicts the patch's claimed coverage
   - if review rejects or weakens the claim, update the subagent status and
     theoretical ledger so progress can decrease

10. Worker feedback drives new repair work:
   - every completed worker case must be interpreted into one of valid PUT/R1R2,
     valid-no-PUT, PUT-no-R1/R2, no-valid, OOM, timeout, or schema/artifact bug
   - any status below valid PUT/R1R2 emits or preserves a dispatcher assignment
   - do not wait for the main agent to notice weak results manually

9. Completed subagents must be closed:
   - run `rq1_subagent_autoclose.py plan` before spawning new agents
   - close every pending completed agent with the Codex `close_agent` tool
   - after each successful close, run `rq1_subagent_autoclose.py ack --agent-id`
   - do not wait for the thread limit error before releasing completed agents
   - every user-facing progress report must include active subagent count and
     active subagent ids/tasks from `rq1_watchdog_status.py`

10. OOM handling is separate from ordinary repair:
   - do not raise memory for every subject preemptively
   - only explicit `OOM_OR_MEMORY_PRESSURE` / killed-over-RSS cases enter
     `rq1_oom_highmem_queue.py`
   - high-memory reruns must use case parallelism 1 unless the user explicitly
     allows more
   - non-OOM failures should produce code-repair assignments, not high-memory
     reruns
