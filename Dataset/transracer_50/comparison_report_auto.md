# ESBMC `--tod-race-check=auto` vs TransRacer paper Table 2 (post-fix)

Re-run of the 33 TransRacer-filtered contracts with the patched
ESBMC build (F4 + HARNESS_ORDER + F2 landed on the solidity branch).
Previous run's HARNESS_ORDER_BUG / HARNESS_EMIT_BUG categories now
reach verdicts directly.

## Pipeline status

- Contracts exercised: **33**
- Contracts reaching a final verdict (DONE): **31**
- Contracts with no candidate pair (NO_PAIRS): **2**
- Contracts with pipeline errors (CRASH/OOM/TIMEOUT/etc): **0**
- Pairs verified in total: **561**
- Of which TOD found: **16**, clean: **515**, error: **30**

## Per-contract results

| contract | paper TRBD IS | paper TRBD US | status | pairs | tod_found | clean | error | wall sec |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| XCTCrowdSale | 1 | 0 | NO_PAIRS | - | - | - | - | 0.1 |
| BMUS | 0 | 1 | DONE | 2 | 0 | 2 | 0 | 1.3 |
| RippleAlpha | 1 | 2 | DONE | 7 | 0 | 7 | 0 | 3.4 |
| PlayCash | 0 | 2 | DONE | 11 | 0 | 11 | 0 | 4.7 |
| Xpense | 0 | 2 | DONE | 11 | 0 | 11 | 0 | 4.1 |
| BB | 0 | 2 | DONE | 11 | 0 | 11 | 0 | 4.9 |
| WEBN | 1 | 3 | DONE | 7 | 1 | 6 | 0 | 7.9 |
| COW | 0 | 1 | DONE | 11 | 0 | 11 | 0 | 6.8 |
| Aavio | 1 | 1 | NO_PAIRS | - | - | - | - | 0.1 |
| Freedom | 2 | 0 | DONE | 11 | 0 | 11 | 0 | 5.5 |
| HubrisOne | 1 | 2 | DONE | 7 | 1 | 6 | 0 | 8.2 |
| MADANA | 1 | 1 | DONE | 1 | 0 | 0 | 1 | 1.4 |
| GOG | 0 | 2 | DONE | 32 | 0 | 32 | 0 | 17.1 |
| MediBloc | 1 | 2 | DONE | 6 | 0 | 6 | 0 | 3.0 |
| Simmitri | 1 | 2 | DONE | 9 | 0 | 9 | 0 | 4.8 |
| ProofOfReview | 0 | 1 | DONE | 6 | 0 | 6 | 0 | 3.1 |
| HSD | 0 | 1 | DONE | 12 | 0 | 12 | 0 | 8.4 |
| MATOX | 1 | 0 | DONE | 27 | 0 | 27 | 0 | 23.4 |
| Dragon | 0 | 1 | DONE | 19 | 0 | 19 | 0 | 10.9 |
| EthernetCash | 0 | 1 | DONE | 17 | 0 | 17 | 0 | 11.8 |
| Viewly | 0 | 1 | DONE | 1 | 0 | 1 | 0 | 0.7 |
| Char | 1 | 2 | DONE | 11 | 11 | 0 | 0 | 7.6 |
| CityToken | 0 | 2 | DONE | 21 | 0 | 0 | 21 | 13.8 |
| ROD | 2 | 4 | DONE | 35 | 3 | 32 | 0 | 22.4 |
| grip | 0 | 1 | DONE | 17 | 0 | 17 | 0 | 12.5 |
| TokensWarContract | 0 | 1 | DONE | 21 | 0 | 21 | 0 | 20.4 |
| Dentacoin | 0 | 1 | DONE | 32 | 0 | 32 | 0 | 47.1 |
| LAAR | 1 | 3 | DONE | 56 | 0 | 56 | 0 | 32.9 |
| INRD | 1 | 1 | DONE | 8 | 0 | 0 | 8 | 14.6 |
| CSTK_CLT | 0 | 1 | DONE | 48 | 0 | 48 | 0 | 43.9 |
| Yihaa | 0 | 1 | DONE | 8 | 0 | 8 | 0 | 3.1 |
| IgfContract | 1 | 2 | DONE | 51 | 0 | 51 | 0 | 21.1 |
| Scale | 1 | 3 | DONE | 45 | 0 | 45 | 0 | 71.2 |

## Comparison delta (vs pre-fix 2026-04-17 run)

| metric | pre-fix | post-fix | delta |
|---|---:|---:|---:|
| TOD found (pair count) | 2 | 16 | +14 |
| Clean (pair count) | 19 (contract-level) | 515 | n/a |
| Contracts hitting HARNESS_ORDER_BUG | 7 | 0 | −7 |
| Contracts hitting HARNESS_EMIT_BUG | 1 | 0 | −1 |
| Contracts hitting FRONTEND_ADDR_BUG | 2 | 0 | −2 (both now reach a verdict) |
| Contracts with CRASH | 1 | 0 | −1 |

## Per-contract TOD-FOUND detail

### WEBN (1/7 pairs flagged TOD)

- `TOD_decreaseApproval_transferFrom` → SUCCESSFUL -> TOD_transfer_transferFrom FAILED  (TOD vulnerability between transfer and transferFrom)

### HubrisOne (1/7 pairs flagged TOD)

- `TOD_decreaseApproval_transferFrom` → SUCCESSFUL -> TOD_transfer_transferFrom FAILED  (TOD vulnerability between transfer and transferFrom)

### Char (11/11 pairs flagged TOD)

- `TOD_approve_increaseApproval` → FAILED (TOD vulnerability between approve and increaseApproval)
- `TOD_approve_decreaseApproval` → FAILED (TOD vulnerability between approve and decreaseApproval)
- `TOD_confirmOwnership_issue` → FAILED (TOD vulnerability between confirmOwnership and issue)
- `TOD_confirmOwnership_setAllowIssuance` → FAILED (TOD vulnerability between confirmOwnership and setAllowIssuance)
- `TOD_confirmOwnership_setAllowTransfers` → FAILED (TOD vulnerability between confirmOwnership and setAllowTransfers)
- `TOD_confirmOwnership_setListener` → FAILED (TOD vulnerability between confirmOwnership and setListener)
- `TOD_confirmOwnership_transferOwnership` → FAILED (TOD vulnerability between confirmOwnership and transferOwnership)
- `TOD_decreaseApproval_increaseApproval` → FAILED (TOD vulnerability between decreaseApproval and increaseApproval)
- `TOD_issue_setAllowIssuance` → FAILED (TOD vulnerability between issue and setAllowIssuance)
- `TOD_setAllowTransfers_transfer` → FAILED (TOD vulnerability between setAllowTransfers and transfer)
- `TOD_setAllowTransfers_transferFrom` → FAILED (TOD vulnerability between setAllowTransfers and transferFrom)

### ROD (3/35 pairs flagged TOD)

- `TOD_approveBurn_decreaseBurnApproval` → FAILED (TOD vulnerability between approveBurn and decreaseBurnApproval)
- `TOD_approveBurn_increaseBurnApproval` → FAILED (TOD vulnerability between approveBurn and increaseBurnApproval)
- `TOD_decreaseBurnApproval_increaseBurnApproval` → FAILED (TOD vulnerability between decreaseBurnApproval and increaseBurnApproval)
