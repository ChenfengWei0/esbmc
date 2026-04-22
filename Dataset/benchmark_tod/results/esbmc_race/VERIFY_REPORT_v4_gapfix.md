# SolidiFi v4 gap-fix re-verify — 5 ALL_ERR cases

Date: 2026-04-22
Binary: `build/src/esbmc/esbmc` @ commit `6ae0e57c3f`
Source: v4 report `VERIFY_REPORT_v4.md` identified 5 ALL_ERR cases caused
by two distinct frontend gaps. Both gaps are now fixed:

- Gap B (`2614c484c2`) — `get_sol_builtin_ref` no longer hijacks user
  functions whose name collides with a C stdlib.h symbol (SafeMath.div
  had been bound to `c:@F@div` returning `div_t`).
- Gap A (`6ae0e57c3f`) — `mapping(K => T[N])` now dispatches through a
  new `map_fixed_arr_get` helper with lazy zero-init.

This rerun targets only the 5 previously-ALL_ERR cases.

## Delta vs v4

| case | v4 (tod/clean/err) | v4.1 (tod/clean/err/wall) | change |
|---|---|---|---|
| buggy_24 (FomoFeast)   | 0 / 0 / 63 | **discovery 100 %, verify timeout @190s** | frontend OK (Gap A) — verify budget short |
| buggy_28 (HYDROGEN)    | 0 / 0 / 48 | **discovery 100 %, verify timeout @190s** | frontend OK (Gap B) — verify budget short |
| buggy_33 (Staking)     | 0 / 0 / 27 | **discovery 100 %, verify timeout @191s** | frontend OK (Gap B) — verify budget short |
| buggy_38 (BIGBOMBv2)   | 0 / 0 / 48 | **discovery 100 %, verify timeout @190s** | frontend OK (Gap B) — verify budget short |
| buggy_42 (Staking)     | 0 / 0 / 27 | **10 / 16 / 1 / 128s**                     | ✅ fully unblocked — 10 TOD found |

### Aggregate (these 5 cases only)

| metric | v4 | v4.1 |
|---|---:|---:|
| total candidate pairs | 213 | 213 |
| TOD found | 0 | **10** |
| clean | 0 | **16** |
| err | 213 | **1** (solver OOM on 1 pair, not frontend) |
| timeout | 0 | **186** (4 cases didn't fit in 180s verify budget) |

## Interpretation

**Both gaps are confirmed fixed at the frontend level.** All 5 cases
now reach discovery recall 100 % and begin verification — none trip
the old "unsupported mapping value type" or "SafeMath@F@mul@a type
mismatch" errors.

**buggy_42 proves the fix flips real verdicts**, not just lets the
pipeline run. Pattern-B reward/claim races on Staking are detected
correctly (10 TOD on 27 pairs).

**The 4 timeout cases need more verify budget, not more frontend work.**
Candidate-pair counts:

| case | pairs | per-pair budget @ jobs=2 / 180s |
|---|---:|---|
| buggy_24 | 63 | 5.7s — too tight |
| buggy_28 | 48 | 7.5s — marginal |
| buggy_38 | 48 | 7.5s — marginal |
| buggy_33 | 27 | 13.3s — Staking SMT load overshoots |

Bumping `--tod-jobs=4` or `VERIFY_TIMEOUT=600` would close them; the
choice is an evaluation-infra decision, not a verifier change.

## The 1 remaining err (buggy_42)

`TOD_changeStakeTokens_startStaking` pair: `ERROR: Out of memory` +
`SMT solver failed` under CVC5. This is a genuine solver resource
issue (the Staking methods have expensive arithmetic chains that
swell the bitvector formula), not a frontend/conversion bug. Would
need a different solver (Bitwuzla has been faster on SolidiFi pairs
in practice) or more memory.

## Next-step candidates

1. Re-run with `--tod-jobs=4` + `--verify-timeout=600` to close the 4
   timeout cases — estimated 20–40 extra TOD flags.
2. Do a full 50-case v5 verification to fold all of v4 + these 5
   into a single aggregate table.
3. Move on to TransRacer 33-contract rerun for Stage 1+2 library
   coverage (separate benchmark scope).
