# RQ3 Raw/Valid Repair and RQ1 Anchor Replacement Plan

Updated: 2026-08-15

This document is the durable execution record for repairing the RQ3 ablation
before replacing the RQ3-derived anchors already inserted into RQ1.  Counts in
this file are logical `(file, test)` rows from the three published RQ3 dataset
roots, not recursive physical `.t.sol` file counts.

## Required invariants

RQ3 is the concrete-replay ablation.  Its generated test is a mechanical
translation of a counterexample and must execute its authenticated oracle on
the source program.  The published RQ3 dataset must therefore satisfy:

```text
raw concrete tests == valid concrete tests
```

RQ1 anchor replacement may start only after that equality is true.  Every RQ1
logical PUT must then have exactly one anchor selected from the frozen RQ3
valid set.  The replacement key is the frozen five-tuple:

```text
(case, path_function, unit, enc, piece)
```

Raw artifacts, unindexed physical files, cross-encoder matches, and global
path/unit fallbacks are not admissible replacement inputs.

## Current RQ3 state

The published roots are:

```text
Results/RQ3/VeriExploit/No_Cer_Reg/{peer182,bugfix124,real203}/subjects
```

Current authoritative result-row counts:

```text
cases:                   547
raw concrete tests:     2995
valid concrete tests:   2140
raw-valid gap:           855
cases with a gap:        112
```

All 2140 valid rows have `forge_status=Success` and
`valid_reference_test=true`.

The 855-row gap partitions as follows:

```text
Forge-green/reference-valid siblings already persisted:  499
Forge-green rows rejected for missing structured oracle:  106
Forge Failure raw rows:                                  115
raw rows with no Forge status:                           133
other non-valid raw rows:                                  2
total:                                                    855
```

## Confirmed root causes

### 1. Case-wide quarantine loses 605 valid tests

Eighty-one cases have `completion_status=persistence-error`.  In those cases,
605 concrete tests already have both `forge_status=Success` and
`valid_reference_test=true`, but the final `valid_tests` array is empty.  Of
those 605 rows, 499 were successfully persisted; 106 were the failing rows
that lacked an authenticated structured oracle.

The persistence layer processes all concrete rows for the case.  If one row
lacks a structured execution-result oracle or otherwise cannot be persisted,
the runner quarantines every valid row in that case instead of rejecting only
the failing row.

Representative case: `peer182/peer_ccsolbmc__HotinuFinance`.

```text
raw tests:                    21
Forge-success tests:          21
persistable tests:            20
unpersistable receive replay:  1
final valid tests:             0
```

The failing `receive` replay reports:

```text
replay has no execution-result assertion or revert oracle;
replay assertions are not data-dependent on the target result;
concrete replay lacks structured witness oracle provenance
```

The correct behavior is per-test rejection: retain the 20 valid/persisted rows
and reject only the one invalid replay.  Across all 81 cases, this accounting
repair restores 499 rows without ESBMC or Forge reruns.  The 106 rejected rows
still require an oracle-generation repair before `raw == valid` can hold.

### 2. Another 250 raw tests did not pass the Forge/reference gate

The raw arrays are populated before the final Forge/reference-test gate.  They
currently retain 115 Forge failures, 133 tests with no Forge status, and two
other non-valid rows.  Under the RQ3 ablation invariant these are generator or
execution defects to repair, not publishable raw tests.

### 3. The existing RQ3 audit omits the raw/valid invariant

`run_rq3_no_cer_reg.py --audit-only` currently checks structural concrete
records and PUT leakage.  It reports `ok=true` even when `2995 != 2140`.
The audit must fail whenever raw and valid logical test sets differ and must
report exact missing identities.

## How the incorrect RQ1 insertion was performed

The RQ1 population contained 1286 retained logical PUT test units.  The old
matcher built its RQ3 candidate pool from:

1. `raw_artifacts`;
2. `valid_artifacts`;
3. generic `artifacts`;
4. recursive RQ3 `put.json` files;
5. recursive physical `.t.sol` files.

It then tried these match tiers:

```text
exact identity             807
same path function         400
same unit                   48
global path function         1
global contract + unit       4
missing                     26
```

Candidate scoring preferred Forge success but did not require it.  The bulk
operation therefore inserted 843 anchors consisting of:

```text
valid RQ3 source:             380
Failure/raw/no-status source: 463
```

The resulting `1286/1286 exactly one anchor` audit is only a structural count;
it is not a validity result.

## Execution order

### Phase 1: repair RQ3 accounting and persistence

1. Add a raw/valid set-equality gate to the existing RQ3 audit command.
2. Change persistence publication from case-wide quarantine to per-test
   rejection while preserving valid, successfully persisted sibling rows.
3. Rebuild the 81 persistence-error result rows from their retained artifacts.
4. Verify that the expected 499 persisted sibling rows move into `valid_tests`
   without rerunning ESBMC, while the 106 oracle-invalid rows remain explicitly
   rejected.

### Phase 2: repair the remaining 356 raw tests

1. Repair the 106 Forge-green rows whose generated test lacks an authenticated
   structured execution-result oracle.
2. Split the 115 Forge failures by compile failure, runtime oracle failure, and
   target-not-executed failure.
3. Split the 133 no-status rows by never-emitted, never-run, timeout, and stale
   path.
4. Resolve the remaining two non-valid rows whose status/reference fields
   disagree.
5. Fix the shared generator defect for each class before parallel case repair.
6. Run the exact RQ3 test with machine-readable Forge output, requiring exactly
   one intended test `Success` and rejecting `No tests found`.
7. Rebuild results and repeat until the published RQ3 audit proves
   `raw == valid == 2995`, or records an explicit experiment-scope correction
   approved by the user.

### Phase 3: mechanically replace invalid RQ1 anchors

Write one replacement tool with these hard gates:

1. Input RQ3 set contains only published `valid_tests` from a frozen snapshot.
2. Match only the exact frozen five-tuple; no cross-encoder or global fallback.
3. Require one zero-parameter RQ3 concrete test with an authenticated oracle.
4. Compare-before-write the current RQ1 source and require exactly one generated
   anchor owned by the prior mapping operation.
5. Replace only that anchor function, preserving the RQ1 PUT function verbatim.
6. Run exact RQ1 PUT fuzz256 and exact anchor Forge tests and require one
   machine-readable `Success` for each.
7. Update source/anchor evidence atomically; restore the source preimage on any
   failure.

The first replacement population is the 463 anchors selected from non-valid
RQ3 candidates.  After replacement, rerun the full 1286-unit one-PUT/one-anchor
audit and the frozen test-level inventory.

## Checkpoints

The matcher/mapper/validator checkpoint commit is:

```text
64adf870ad [solidity] checkpoint RQ3 anchor mapping
```

No new RQ1 anchor replacement is permitted between that checkpoint and the
successful completion of the RQ3 raw/valid equality gate.
