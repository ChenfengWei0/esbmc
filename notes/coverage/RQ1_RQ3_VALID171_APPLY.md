# RQ1/RQ3 Tiered Anchor Apply: 171

Date: 2026-08-15

This records the first mechanical RQ1 anchor transaction after the RQ3
publication repair.  The transaction selected only RQ3 rows that were
currently published as valid (`forge_status=Success` and
`valid_reference_test=true`) and used the existing tiered mapper semantics.

## Result

- RQ1 targets written: **171** (171 unique source files)
- RQ1 targets refused in the same snapshot: **292**
- Mapping tiers: `same-path-function=163`, `global-contract-unit=7`,
  `global-path-function=1`
- Every written source has exactly one generated `test_ce_anchor_*` function.
- All 171 post-write source hashes match their staged hashes.
- No Forge or ESBMC run was performed by this mechanical source transaction.

The 292 refused rows remain pending RQ3 repair/validation.  They are not
declared impossible and are not silently dropped.

## Seals

- Stage report:
  `/tmp/rq1-tier-valid171-stage-report-20260815b.json`
- Stage report SHA256:
  `7f86d65674732da2473fb76d7fba4fe06df797eb6d821d8859466e9b895d35d7`
- Apply report:
  `/tmp/rq1-tier-valid171-apply-20260815b.json`
- Apply report SHA256:
  `8c3ed451295d9a7dc58b76fcaefc2d314da6188e4d363b127ea19fce043f403a`
- Apply status: `committed`

The generated RQ1 Results tree is outside this Git repository; this note is
the tracked handoff and does not include unrelated worktree changes.
