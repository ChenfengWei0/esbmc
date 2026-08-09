# VeriPUT Same-Day Execution Plan

Last refreshed: 2026-08-09.

## Strict 24-Hour Plan, Latest Baseline

Use this section as the authoritative same-day plan.  Older sections below are
historical snapshots.

Latest canonical queue after commits `76633c4a4d` and `2ec887988a`:

- Total subjects: 509
- `valid-PUT-with-R1R2`: 153
- `valid-PUT-no-R1R2`: 52
- `valid-no-PUT`: 37
- `PUT-with-R1R2-but-no-width`: 1
- `no-valid`: 266

Derived rates:

- Valid: 242 / 509 = 47.5%
- PUT among valid: 205 / 242 = 84.7%
- R1/R2 among valid PUT: 153 / 205 = 74.6%
- R1/R2 among all valid: 153 / 242 = 63.2%

Hard target for a 70% raw-valid RQ1 table: at least 357 valid subjects, so the
tool needs +115 valid subjects from the current no-valid pool.  The practical
same-day target is not to magically convert all 266 no-valid rows; it is to
convert the highest-yield 115+ rows and give every remaining row a terminal,
machine-readable reason.

### Non-Negotiable Gates

- No blind ESBMC reruns.
- A case may enter ESBMC only after a named mechanism predicts a changed result.
- Fuzz and Foundry dry runs are refuters only.  They can reject bad regions,
  guards, and R1/R2 candidates before ESBMC; they cannot prove validity.
- A PUT counted in RQ1 needs both verifier-backed assertions and a Foundry
  replay on the reference contract.  The Foundry replay is outside the 600s
  ESBMC generation budget.
- Datasets under `/home/samson/workspace/VeriPUT/Datasets` remain read-only.
- Every result JSON must include timing, raw/valid artifact paths, concrete vs
  PUT, and R0/R1/R2 labels or the precise failure reason.

### 24-Hour Schedule

| Window | Work | Exit condition |
|---|---|---|
| T+0:00-T+0:30 | Freeze baseline, refresh queues, commit notes only.  No ESBMC. | Latest counts and failure buckets recorded. |
| T+0:30-T+2:00 | Inspect all non-terminal buckets by artifact, not rerun: 266 no-valid, 37 no-PUT, 52 PUT-no-R1/R2, 1 R1/R2-no-width. | Each bucket has a named blocker and a candidate mechanism or archive policy. |
| T+2:00-T+5:00 | Code-only repair pass.  Priorities: width provenance, concrete-to-PUT upgrade, path/constructor guard replay, observable state coordinate recovery, no-witness region fallback. | Python tests pass; dry-run/fuzz refuters reject obviously bad candidates. |
| T+5:00-T+6:00 | One representative validation per changed mechanism.  Maximum one 600s ESBMC run per mechanism sample. | Continue only if sample improves; otherwise stop that mechanism and record why. |
| T+6:00-T+9:00 | Batch only direct-hit siblings for successful mechanisms, with controlled concurrency. | Stop a bucket after 3 consecutive same-reason failures or <20% yield in the first 12 direct-hit cases. |
| T+9:00-T+11:00 | Second code-only repair pass using the first batch failures.  No broad reruns. | New patch either targets a dominant failure reason or is dropped. |
| T+11:00-T+15:00 | Main conversion batch.  First target is +115 valid.  Prefer no-valid rows with existing witness/raw hints, then timeouts/killed rows only if the patch reduces path space. | Valid count reaches 357, or all high-yield buckets are exhausted with terminal reasons. |
| T+15:00-T+18:00 | PUT-strengthening batch: convert the 37 valid-no-PUT rows and the 52 PUT-no-R1/R2 rows only when the new mechanism can produce certified width/R1/R2. | Record PUT/concrete and R0/R1/R2 deltas; do not rerun concrete-only rows without width provenance. |
| T+18:00-T+21:00 | Final targeted rescue: path-goal-cap, OOM/killed, and model-chain rows only if a concrete code fix exists. | Otherwise archive as terminal failures; no speculative 600s spending. |
| T+21:00-T+24:00 | Result normalization and audit.  No debugging unless a serialization bug corrupts artifacts. | All 509 subjects have canonical JSON, raw/valid archives, timing, oracle-class labels, and terminal status. |

### Bucket Strategy

- `no-valid` (266): primary source for the +115 valid target.  Start with rows
  that already produced raw candidates, replay projects, driver diagnostics, or
  timeouts likely affected by a region/guard/path-space fix.  Do not start with
  low-evidence no-witness rows.
- `valid-no-PUT` (37): only rerun after a certified width strategy exists.
  Passing Foundry alone is not enough to claim PUT.
- `valid-PUT-no-R1R2` (52): use the existing R0 proof as anchor, then attempt
  R1/R2 by fuzz-refuting candidate coordinates before ESBMC certification.
- `PUT-with-R1R2-but-no-width` (1): archive unless width provenance can be
  rendered without changing the methodology.

### Concurrency Policy

- Start with `--jobs 4`; raise to `--jobs 6-8` only while system memory remains
  stable.  Prefer per-process memory caps around 12-16GiB unless the case has a
  known larger need.
- Timeout remains 600s plus 60s wrapper grace.
- OOM/killed rows are recorded and not retried today unless a memory/path-space
  reduction patch lands.

## Ground Rules

- Do not mutate `/home/samson/workspace/VeriPUT/Datasets`.
- Do not run broad sweeps without a named mechanism that can change the result.
- Foundry replay is the double-oracle guard and stays outside ESBMC generation
  timeout accounting.
- Fuzz is only a refuter.  It may kill bad regions, guards, and R2 candidates;
  it never proves a test valid.
- A rerun is allowed only when the case's `rerun_policy` permits it after a
  concrete code change.

## Canonical Current State

Generated by:

```sh
python3 notes/coverage/scripts/rq1_veriput_queue.py \
  --result-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --out-dir /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/triage-queues
```

Quality counts after the ClockBox artifact-specific rerun:

- `valid-PUT-with-R1R2`: 153
- `valid-PUT-no-R1R2`: 52
- `valid-no-PUT`: 32
- `PUT-with-R1R2-but-no-width`: 1
- `no-valid`: 271

Queue counts:

- `Done`: 153
- `P0`: 85
- `P1`: 1
- `P2`: 141
- `Archive`: 129

## One-Day Work Order

1. Keep all archived categories out of ESBMC until final failure recording.
   This removes 344 low-yield rows from the active debug loop.
2. The old 6 actionable rows have been resolved or reclassified.  ClockBox was
   fixed and rerun once; the other five require new oracle/width strategies and
   are not ESBMC-rerun candidates today.
3. For each action class, inspect artifacts first and write the mechanism in
   notes before touching ESBMC.
4. After a code fix, run exactly one representative case from that class.  If
   it improves, batch only direct-hit siblings.  If it does not, archive that
   class with the observed reason.
5. Final two hours are for result normalization, not debugging: every case must
   have raw/valid artifacts, time stats, PUT/concrete label, R0/R1/R2 labels,
   and a failure reason.

## Resolved Actionable Rows

### dynamic mapping/array/string oracle unsupported today

- `peer182 / peer_solar__array-utils`
- `peer182 / peer_solar__Greeter2`
- `real203 / ensdomains__ens-contracts__StandaloneReverseRegistrar`

Policy: archive today; no rerun until a dynamic slot/string/array oracle
strategy exists.  Current artifacts explicitly refuse whole mapping/dynamic
array candidates and, for these cases, the remaining observable state is
dynamic `address[]`, `mapping(uint8 => string)`, or `mapping(address =>
string)`.

### oracle-only / no rendered width

- `bugfix124 / acfix_021_CVE_2018_19832`

Policy: archive today.  The artifact has verifier-backed R1/R2 assertions and
Forge `Success`, but `fuzz_params=0` and `rendered_width={}`.  Counting it as a
PUT would change the methodology rather than improve the tool.

### artifact-specific no-valid

- `peer182 / peer_ccsolbmc__ClockBoxContract`: fixed by commit
  `f6fa1831f9` and rerun once.  New result is
  `raw=1 valid=1 put=0/0 concrete=1/1`, bucket `valid-no-PUT`,
  wall `326.928s`.
- `peer182 / peer_soltg__short_circuit_or_inside_branch`: archive today.  It
  has raw PUT artifacts, but all rendered widths are point-width
  (`rendered_width={'a': 1}`) and one double-oracle replay already fails in
  Foundry.  No further ESBMC run can fix that without a new width/replay
  strategy.

Policy: no generic rerun.

## Current Today-Action Counts

- `done`: 153
- `archive_concrete_fallback`: 31
- `archive_dynamic_oracle_unsupported_today`: 3
- `archive_no_valid_width_or_replay_failed`: 1
- `archive_oracle_only_no_width`: 1
- `archive_r1r2_unobservable`: 40
- `archive_no_candidate_assertion`: 9
- `archive_no_observable_width`: 1
- `archive_no_witness`: 69
- `archive_timeout_or_killed`: 90
- `archive_low_evidence_no_valid`: 111

There are no remaining `repair_*`, `inspect_*`, or `rerun_*` today actions in
the canonical queue.

### resolved stale identity reruns

- `bugfix124 / pop_032_PuttyV2`
- `peer182 / peer_ccsolbmc__BERNIE`
- `peer182 / peer_ccsolbmc__HOTDOGE`
- `peer182 / peer_ccsolbmc__KOALA`

Status: rerun once after the triage commits with 600s generation timeout,
60s wrapper grace, 12GiB memory, and `--redo`.  All four are now real
`budget-exhausted/no-valid/raw=0` rows.  Do not rerun them again without a new
region strategy.

## Explicit Non-Goals Today

- Do not convert concrete fallback non-payable value gates into PUTs unless a
  certified width strategy is added.  Otherwise the PUT count rises while the
  proof claim weakens.
- Do not chase R1/R2 on rollback paths.  R0 exit assertions are valid; the lost
  post-state rungs are mostly unobservable after revert.
- Do not run all P2 no-valid cases.  Most are no-witness, timeout, killed, or
  low-evidence rows and need a new region strategy before rerun.
