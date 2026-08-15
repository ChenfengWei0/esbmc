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

The deterministic classifier
`notes/coverage/scripts/rq3_raw_valid_repair_inventory.py` reconstructed all
356 real-repair rows and ran exact `--match-path` plus signature-aware
`--match-test '^name\('` Forge checks for the 248 Failure/no-status rows.  The
same inventory hash was reproduced in two independent runs:

```text
10517bdcb60a96364879831edf687ce5646526baa63510647a2a498db980b935
```

The exact machine-readable inventory and human summary are stored at:

```text
/home/samson/workspace/VeriPUT/Results/RQ3/adoption-bundles/
  rq3-raw-valid-repair-20260815/repair-inventory.{json,md}
```

The 356 rows have no unclassified remainder:

```text
runtime implementationAuthority sender mismatch: 107
constructor dependency not etched/mocked:          52
missing result-bound structured oracle:            42
oracle bound to the wrong selected call:            41
zero-address constructor fixture:                   26
other constructor/setup revert:                     17
weak, non-strict revert oracle:                     15
non-payable to payable-contract cast:               12
constructor owner/caller mismatch:                  11
constructor domain-constraint violation:             9
type(C).runtimeCode on immutable contract:            6
duplicate generated local identifier:                4
missing path_function/enc identity:                   4
revert oracle not adjacent to target call:            3
invalid generated ABI/type syntax:                    2
missing file-level import symbol:                     2
deploy-only row lacks authenticated policy oracle:    2
invalid normal-exit marker shape:                     1
total:                                               356
```

All 115 rows previously marked Forge Failure reproduce as a `setUp()` failure;
none is an unexplained target-test failure.  The 133 no-status rows resolve to
107 identical ERC-3643 implementation-authority reverts plus 26 deterministic
compile failures.  This makes generator-cluster repair possible without
case-by-case rediscovery.

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

### Phase 2: authenticate and repair the remaining raw-only tests

1. Exclude the two deploy-only smoke tests from deliverable raw accounting;
   retain them as diagnostics.  This changes raw from 2995 to 2993.
2. Restore the 499 hash-bound persisted siblings.  The authoritative state is
   then raw 2993, valid 2639, raw-only 354.
3. Apply the sealed authenticity partition: 288 rows have a non-empty,
   hash-bound certified CE and are repairable; 66 are unauthenticated fallback
   artifacts (62 empty-CE structural gates and four constructor fallbacks) and
   must remain diagnostics rather than deliverable raw tests.
4. Repair the 288 authenticated rows by their shared generator root cause,
   without treating Forge success as CE authentication.
5. Fix the shared generator defect for each class before parallel case repair.
6. Run the exact RQ3 test with machine-readable Forge output, requiring exactly
   one intended test `Success` and rejecting `No tests found`.
7. Rebuild results and repeat until the published RQ3 audit proves
   `raw == valid == 2927` after the explicit diagnostic scope corrections.

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

Subsequent durable checkpoints are:

```text
1da5ff2f85 [solidity] harden RQ3 republish transaction
fdb6a8e9b9 [solidity] audit RQ3 raw replay authenticity
```

The 499-row transaction committed successfully with raw 2993, valid 2639,
raw-only 354, and valid-only 0.  The sealed authenticity partition SHA-256 is
`c0ecd6798553f577d911be2e708dfaf04c3ba3b645449f938d2d580a46ddc6b1`.
