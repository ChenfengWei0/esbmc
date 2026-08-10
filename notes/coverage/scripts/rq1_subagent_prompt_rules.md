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
