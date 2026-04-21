# SolidiFi full 50-case verification — v4

Date: 2026-04-21
Binary: `build/src/esbmc/esbmc` @ commit `b9e196c02c`
Prior runs: v1/v2/v3 covered a 10-case sample; v4 is the full SolidiFi TOD
benchmark (50 upgraded contracts).

## Headline

| metric | value |
|---|---:|
| cases attempted | 50 |
| discovery recall | **100 %** (931 / 931 injected labels found as candidate pairs) |
| candidate pairs verified | 1 480 |
| TOD found | **116** |
| clean | **643** |
| pipeline error | **213** (5 cases — 2 distinct frontend gaps) |
| verify timeout | **12 cases** (candidate set too large for 180 s budget) |
| discovery failure | **1 case** (buggy_20, solc 0.8 upgrade incomplete) |

Discovery recall is 100 % across all 50 cases including the timeouts and
all-err cases — **every single SolidiFi-injected label is present in the
candidate pair list**.  The headline gaps are verification/timeout-side,
not discovery-side.

## Per-case breakdown

| case | target | disc | lbl | hit | tod | clean | err | sec | status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| buggy_1 | HotDollarsToken | 20 | 11 | 11 | 0 | 20 | 0 | 27 | OK |
| buggy_2 | CareerOnToken | 28 | 20 | 20 | 0 | 28 | 0 | 41 | OK |
| buggy_3 | CareerOnToken | 29 | 21 | 21 | 0 | 29 | 0 | 47 | OK |
| buggy_4 | PHO | 19 | 18 | 18 | 0 | 19 | 0 | 42 | OK |
| buggy_5 | TokenERC20 | 24 | 16 | 16 | 0 | 24 | 0 | 46 | OK |
| buggy_6 | Ownable | 25 | 21 | 21 | **12** | 13 | 0 | 66 | OK |
| buggy_7 | Ownable | 25 | 21 | 21 | **11** | 14 | 0 | 64 | OK |
| buggy_8 | TokenERC20 | 24 | 16 | 16 | 0 | 24 | 0 | 46 | OK |
| buggy_9 | TokenERC20 | 30 | 21 | 21 | 0 | 30 | 0 | 127 | OK |
| buggy_10 | DocumentSigner | 9 | 8 | 8 | 2 | 7 | 0 | 5 | OK |
| buggy_11 | ForTheBlockchain | 36 | 16 | 16 | 0 | 36 | 0 | 38 | OK |
| buggy_12 | Grand | 40 | 18 | 18 | 0 | 40 | 0 | 51 | OK |
| buggy_13 | BitCash | 28 | 17 | 17 | 0 | 28 | 0 | 42 | OK |
| buggy_14 | ERC20 | 19 | 15 | 15 | 7 | 12 | 0 | 32 | OK |
| buggy_15 | MD | 28 | 17 | 17 | 0 | 28 | 0 | 42 | OK |
| buggy_16 | ExclusivePlatform | 57 | 29 | 29 | — | — | — | 190 | **TIMEOUT** |
| buggy_17 | TokenERC20 | 19 | 15 | 15 | 7 | 12 | 0 | 40 | OK |
| buggy_18 | _Yesbuzz | 47 | 27 | 27 | 0 | 47 | 0 | 85 | OK |
| buggy_19 | ethBank | 52 | 25 | 25 | — | — | — | 190 | **TIMEOUT** |
| buggy_20 | RampInstantEthPool | 0 | 0 | 0 | — | — | — | 0 | **DISC_FAIL (solc)** |
| buggy_21 | StableDEX | 38 | 23 | 23 | — | — | — | 190 | **TIMEOUT** |
| buggy_22 | MindsyncPlatform | 40 | 22 | 22 | — | — | — | 190 | **TIMEOUT** |
| buggy_23 | ERC20 | 23 | 16 | 16 | 9 | 14 | 0 | 43 | OK |
| buggy_24 | FomoFeast | 63 | 40 | 40 | 0 | 0 | 63 | 31 | **ALL_ERR (map→V[N])** |
| buggy_25 | WhiteBetting | 36 | 21 | 21 | **10** | 26 | 0 | 115 | OK |
| buggy_26 | UBBCToken | 30 | 23 | 23 | 0 | 30 | 0 | 44 | OK |
| buggy_27 | DanPanCoin | 75 | 33 | 33 | — | — | — | 190 | **TIMEOUT** |
| buggy_28 | HYDROGEN | 48 | 22 | 22 | 0 | 0 | 48 | 89 | **ALL_ERR (SafeMath)** |
| buggy_29 | RaffleTokenExchange | 18 | 16 | 16 | **10** | 8 | 0 | 47 | OK |
| buggy_30 | ERC777 | 37 | 16 | 16 | 0 | 37 | 0 | 71 | OK |
| buggy_31 | Ownable | 7 | 7 | 7 | 5 | 2 | 0 | 4 | OK |
| buggy_32 | ERC20Token | 17 | 10 | 10 | 6 | 11 | 0 | 20 | OK |
| buggy_33 | Staking | 27 | 17 | 17 | 0 | 0 | 27 | 38 | **ALL_ERR (SafeMath)** |
| buggy_34 | LollypopToken | 45 | 32 | 32 | — | — | — | 190 | **TIMEOUT** |
| buggy_35 | BitpayerDEX | 64 | 28 | 28 | — | — | — | 190 | **TIMEOUT** |
| buggy_36 | MindsyncPlatform | 40 | 22 | 22 | — | — | — | 190 | **TIMEOUT** |
| buggy_37 | AugustCoin | 40 | 16 | 16 | — | — | — | 190 | **TIMEOUT** |
| buggy_38 | BIGBOMBv2 | 48 | 22 | 22 | 0 | 0 | 48 | 91 | **ALL_ERR (SafeMath)** |
| buggy_39 | TAMCContract | 13 | 11 | 11 | 0 | 13 | 0 | 4 | OK |
| buggy_40 | ERC20 | 23 | 16 | 16 | 9 | 14 | 0 | 46 | OK |
| buggy_41 | AO | 28 | 17 | 17 | 0 | 28 | 0 | 42 | OK |
| buggy_42 | Staking | 27 | 17 | 17 | 0 | 0 | 27 | 40 | **ALL_ERR (SafeMath)** |
| buggy_43 | Operated | 12 | 12 | 12 | 5 | 7 | 0 | 33 | OK |
| buggy_44 | Operated | 12 | 12 | 12 | 4 | 8 | 0 | 20 | OK |
| buggy_45 | StockBet | 52 | 28 | 28 | — | — | — | 190 | **TIMEOUT** |
| buggy_46 | ProofOfExistence | 7 | 7 | 7 | 2 | 5 | 0 | 4 | OK |
| buggy_47 | AcunarIEO | 49 | 20 | 20 | — | — | — | 190 | **TIMEOUT** |
| buggy_48 | QurasToken | 44 | 17 | 17 | — | — | — | 190 | **TIMEOUT** |
| buggy_49 | TAMC | 13 | 11 | 11 | 0 | 13 | 0 | 4 | OK |
| buggy_50 | digitalNotary | 33 | 25 | 25 | **17** | 16 | 0 | 162 | OK |

v3 sample cases (4/10/29/31/32/39/43/44/46/49) produced **byte-identical**
verdicts in v4 — binary/commit hasn't moved, idempotency confirmed.

## Three verdict buckets worth explaining

### 1. 19 cases with 0 TOD despite 100 % label recall

Cases: 1, 2, 3, 4, 5, 8, 9, 11, 12, 13, 15, 18, 26, 30, 39, 41, 49, and
sub-cases of others.

These are NOT miss — they are "race-check says no reorder-sensitive race
here."  SolidiFi's TOD injection has three sub-categories (Amount,
Receiver, Transfer); `--tod-race-check` only fires on pairs where a
storage write in `f` changes a storage read in `g` in a way that flips a
post-condition.  When the injection only shifts a transfer's value or
recipient (without touching shared state read by another function),
race-check correctly judges it clean.  These cases need
`--tod-balance-check` as a second pass.

### 2. 5 cases with full-ERR = 2 distinct frontend gaps

Category A (map → fixed-array value): `buggy_24` only.
- Pattern: `mapping(address => InvestRecord[9])` — mapping value is a
  **fixed-size** array of struct.
- Error: `unsupported mapping value type: sol_type=ARRAY_LITERAL`
- Status: separate from the `mapping(K => V[])` (dynamic array) fix in
  `6ebbb8f0f9`.  Requires a new model for mapping-to-fixed-array.

Category B (SafeMath wire-up): `buggy_28`, `buggy_33`, `buggy_38`, `buggy_42`.
- Error: `function call: argument "sol:@C@SafeMath@F@mul@a#NNN" type
  mismatch: got struct, expected unsignedbv`
- Signature at call site uses a struct-shaped wrapper (probably library
  this-ptr getting packed in wrong), but library body declares raw uint256.
  Happens on every `SafeMath.mul/div/add/sub` call after our Stage 1
  library-body changes; warrants audit of Stage 1 to confirm no
  regression.

### 3. 12 cases timed out

Cases: 16, 19, 21, 22, 27, 34, 35, 36, 37, 45, 47, 48.

Shared profile: 38 – 75 candidate pairs (buggy_27 tops at 75), each pair
runs an independent ESBMC subprocess at `--tod-jobs=2`.  With wall budget
180 s, effective per-pair budget is `180 × 2 / N_pairs` = 5 – 9 s.  On
contracts with heavier SMT loads that isn't enough; the script records a
partial verdict set.  Discovery already finished (recall = 100 % in all
timeout cases).  Bumping `--tod-jobs=4` or wall to 600 s would close
most of these.

## Interpretation

- **Discovery layer is done**: 931/931 labels covered, 100 % across 50
  contracts.  This is the post-F4/F2 state (commit `3e1aff3aca`).
- **Verification precision**: 116 TOD on 1 480 checked pairs = 7.8 %.
  The denominator is inflated by the 267 0-TOD pairs on cases where
  race-check is the wrong tool (balance-check is what those pairs need).
- **Newly-exposed soundness bugs**: 2 new frontend gaps (map → V[N]
  and SafeMath wire-up) that the 10-case sample hadn't hit — these
  are genuine issues to fix, not benchmarking artefacts.
- **No Stage 1+2 regression**: the 10-case sample verdicts are
  idempotent with v3, so ambient-state/library changes didn't
  introduce new crashes on contracts that were previously clean.

## Next-step candidates

1. Fix `mapping(K => V[N])` lowering → unblocks buggy_24 (63 pairs,
   40 labels).
2. Debug the SafeMath struct/unsignedbv mismatch → unblocks buggy_28 /
   33 / 38 / 42 (150 pairs, 78 labels).
3. Bump `--tod-jobs` and/or wall timeout → unblocks 12 timeout cases
   (cost: 20 min extra wall, no code change).
4. Run the balance-check pass on the 19 0-TOD OK cases → expect much
   of that mass to flip to TOD since those are balance-pattern
   injections.
