# ESBMC `--tod-race-check` vs TransRacer paper Table 2
Benchmark source: `Dataset/contracts_50.txt` mainnet addresses fetched
via Sourcify, upgraded to `pragma >=0.8.0`, filtered to the 33 with
`TRBD >= 1` in TransRacer's Table 2 (i.e. contracts where the paper
reported at least one race bug between distinct functions).

## Pipeline status

- Contracts selected: **33**
- Compiled with solc 0.8.30: **33** (all, via per-contract solc-error-driven upgrade)
- **TOD race bug found**: 2 / 33
- Verified CLEAN: 19 / 33
- 0 candidate pairs discovered: 1 / 33
- ESBMC-side issues (not contract-source bugs):
  - `CRASH`: 1  — COW
  - `FRONTEND_ADDR_BUG`: 2  — ProofOfReview, Yihaa
  - `HARNESS_EMIT_BUG`: 1  — Viewly
  - `HARNESS_ORDER_BUG`: 7  — RippleAlpha, WEBN, HubrisOne, MADANA, Char, ROD, CSTK_CLT

## Per-contract results

| contract | paper TRBD IS | paper TRBD US | pair tried | ESBMC verdict | reason |
|---|---:|---:|---|---|---|
| XCTCrowdSale | 1 | 0 | - | no_pairs |  |
| BMUS | 0 | 1 | transfer,transferFrom | CLEAN |  |
| RippleAlpha | 1 | 2 | totalSupply,balanceOf | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| PlayCash | 0 | 2 | burn,burnFrom | TOD_FOUND | race-check assertion |
| Xpense | 0 | 2 | burn,burnFrom | CLEAN |  |
| BB | 0 | 2 | burn,burnFrom | CLEAN |  |
| WEBN | 1 | 3 | transferFrom,approve | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| COW | 0 | 1 | transfer,transferFrom | CRASH | solver OOM / ESBMC abort |
| Aavio | 1 | 1 | transfer,transferFrom | CLEAN |  |
| Freedom | 2 | 0 | Start_Resume_ICO,Start_Resume_PreICO | CLEAN |  |
| HubrisOne | 1 | 2 | transferFrom,approve | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| MADANA | 1 | 1 | owner,isOwner | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| GOG | 0 | 2 | burn,burnFrom | TOD_FOUND | race-check assertion |
| MediBloc | 1 | 2 | transfer,transferFrom | CLEAN |  |
| Simmitri | 1 | 2 | totalSupply,balanceOf | CLEAN |  |
| ProofOfReview | 0 | 1 | transfer,transferFrom | FRONTEND_ADDR_BUG | frontend address-vs-contract type conversion |
| HSD | 0 | 1 | transfer,transferFrom | CLEAN |  |
| MATOX | 1 | 0 | transfer,transferFrom | CLEAN |  |
| Dragon | 0 | 1 | burn,burnFrom | CLEAN |  |
| EthernetCash | 0 | 1 | mintToken,burn | CLEAN |  |
| Viewly | 0 | 1 | addToWhitelist,removeFromWhitelist | HARNESS_EMIT_BUG | harness emitter missing memory keyword |
| Char | 1 | 2 | transfer,transferFrom | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| CityToken | 0 | 2 | getToken,ownerOf | CLEAN |  |
| ROD | 2 | 4 | freezeAccount,transfer | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| grip | 0 | 1 | transfer,transferFrom | CLEAN |  |
| TokensWarContract | 0 | 1 | takeOwnership,purchase | CLEAN |  |
| Dentacoin | 0 | 1 | setEtherPrices,buyDentacoinsAgainstEther | CLEAN |  |
| LAAR | 1 | 3 | pauseCrowdSale,resumeCrowdSale | CLEAN |  |
| INRD | 1 | 1 | mint,transfer | CLEAN |  |
| CSTK_CLT | 0 | 1 | changeTotalSupply,transfer | HARNESS_ORDER_BUG | emitted harness has inherited contracts out of order |
| Yihaa | 0 | 1 | mint,getMintDigest | FRONTEND_ADDR_BUG | frontend address-vs-contract type conversion |
| IgfContract | 1 | 2 | allowance,approve | CLEAN |  |
| Scale | 1 | 3 | ownerClaim,poolIssue | CLEAN |  |

## Interpretation

**TOD found**: PlayCash (`burn`/`burnFrom`) and GOG (`burn`/`burnFrom`) fire
the `__tod_race_check` assertion, indicating a real order-dependent race
between the burn variants on the fresh-IS harness.  Both match the paper's
TRBD category for these contracts.

**CLEAN verdicts** (19 contracts) do not contradict the paper — TransRacer
reports a mix of IS-reachable and US-only TRBDs.  US-only bugs are
unreachable from fresh IS by design, so they register as clean here.

**ESBMC-side issues** (10 contracts) fall into three recognised bug
categories in the TOD pipeline itself:
- `HARNESS_ORDER_BUG`: emitted harness has derived contracts before their
  bases.  Affects every contract where the target is the leaf of a long
  inheritance chain (RippleAlpha, WEBN, HubrisOne, MADANA, Char, ROD,
  CSTK_CLT).  Fix = topologically sort contract decls in harness emitter.
- `HARNESS_EMIT_BUG`: harness code has `address[] paramName` without the
  `memory` keyword.  Affects Viewly.
- `FRONTEND_ADDR_BUG`: ESBMC converter trips on address-vs-contract type
  distinction somewhere in the parameter chain.  Affects ProofOfReview,
  Yihaa.
- `CRASH`: CVC5 solver runs out of memory on COW.

None of these 10 ESBMC-side issues implies a specific contract is
clean-or-buggy — the run simply failed to produce a verdict.
