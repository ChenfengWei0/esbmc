# AUTO-REVIEW-RUNNER-001

Status: completed by main thread because no callable subagent spawn/wait tool is
visible in this session.

Scope: `notes/coverage/scripts/rq1_veriput_run.py` and runner-related RQ1
artifacts. No ESBMC, ctest, pytest, RQ1, `certify_all.py`, `put_all.py`,
`solidity_path_put.py`, or benchmark case was run. Datasets were not modified.

Reviewed patch IDs:

- `runner-budget-stage4`: needs-work for theory. The Stage2/Stage4 reserve
  mechanism is plausible, but current canonical feedback still invalidates
  covered runner-budget subjects. Net theory delta: `+0/204`.
- `runner-no-output-continuation`: needs-work for theory. Continuation after
  no-output/no-candidate is plausible, but current canonical feedback still
  contains no-valid rows from the claimed categories. Net theory delta:
  `+0/204`.
- `a18-runner-result-adoption-tags`: accepted as accounting/adoption support
  only. It does not by itself prove new no-valid coverage. Net theory delta:
  `+0/204`.
- `final-concrete-fallback-review-gate-oracle-metadata`: needs-work for
  no-valid/PUT theory. The final deploy-only fallback may improve concrete
  valid coverage, but it is explicitly quality debt and cannot justify PUT or
  R1/R2 claims. Oracle metadata preservation is useful after the aggregate
  double-count fix below. Net theory delta: `+0/204`.

Code-level finding:

- `_merge_oracle_metadata(row, rec, stats)` counted oracle classes from
  `assertion_oracles` and then counted the same source's
  `oracle_class_counts`/`oracle_class_combo_counts` again. This could inflate
  JSON oracle statistics. It did not change the boolean valid PUT R1/R2
  presence check, but it made R1/R2 ratio summaries less trustworthy.

Fix applied:

- `notes/coverage/scripts/rq1_veriput_run.py`: aggregate oracle class/count
  metadata now uses detailed `assertion_oracles` as authoritative for that
  source and only uses aggregate count fields to fill labels/combinations not
  already represented by details.

Theory policy:

- Do not restore the `runner-budget-stage4` or
  `runner-no-output-continuation` claimed `102/204` until fresh canonical
  results show those claimed categories no longer produce no-valid outputs.
- The current honest net remains `0/204`; pending/provisional coverage is not
  enough for RQ1 planning.
