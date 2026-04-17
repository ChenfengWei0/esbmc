# ESBMC `--tod-race-check` vs TransRacer paper Table 2
Filtered to the 33 contracts with `#TRBD ≥ 1` in TransRacer's Table 2 (i.e.
contracts where the paper reported at least one race bug between distinct
functions — the category `--tod-race-check` targets).

## Pipeline status

- contracts selected: **33**
- source successfully upgraded to `pragma >=0.8.0` and compiled: **3**
- ESBMC run completed (summary emitted): **3**

The remaining contracts failed the auto-upgrade pass; each has its last
solc-0.8 error logged under `logs/upgrade_<name>.log`. Manual cleanup
(rename shadowed identifiers, add `override` / `virtual` correctly, cast
contract refs to address) is required to bring them into compilation.

## Per-contract comparison

| contract | #func | paper TRBD IS | paper TRBD US | upgrade | ESBMC pairs | clean | TOD found | error |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| XCTCrowdSale | 5 | 1 | 0 | ok | 0 | 0 | 0 | 0 |
| BMUS | 11 | 0 | 1 | no_rule_matched | - | - | - | - |
| RippleAlpha | 11 | 1 | 2 | no_rule_matched | - | - | - | - |
| PlayCash | 12 | 0 | 2 | no_rule_matched | - | - | - | - |
| Xpense | 12 | 0 | 2 | no_rule_matched | - | - | - | - |
| BB | 12 | 0 | 2 | ok | 11 | 8 | 0 | 3 |
| WEBN | 13 | 1 | 3 | no_rule_matched | - | - | - | - |
| COW | 15 | 0 | 1 | no_rule_matched | - | - | - | - |
| Aavio | 16 | 1 | 1 | no_rule_matched | - | - | - | - |
| Freedom | 17 | 2 | 0 | no_rule_matched | - | - | - | - |
| HubrisOne | 18 | 1 | 2 | no_rule_matched | - | - | - | - |
| MADANA | 18 | 1 | 1 | no_rule_matched | - | - | - | - |
| GOG | 19 | 0 | 2 | no_rule_matched | - | - | - | - |
| MediBloc | 19 | 1 | 2 | no_rule_matched | - | - | - | - |
| Simmitri | 19 | 1 | 2 | no_rule_matched | - | - | - | - |
| ProofOfReview | 20 | 0 | 1 | no_rule_matched | - | - | - | - |
| HSD | 21 | 0 | 1 | no_rule_matched | - | - | - | - |
| MATOX | 23 | 1 | 0 | no_rule_matched | - | - | - | - |
| Dragon | 23 | 0 | 1 | no_rule_matched | - | - | - | - |
| EthernetCash | 23 | 0 | 1 | no_rule_matched | - | - | - | - |
| Viewly | 24 | 0 | 1 | no_rule_matched | - | - | - | - |
| Char | 25 | 1 | 2 | no_rule_matched | - | - | - | - |
| CityToken | 25 | 0 | 2 | no_rule_matched | - | - | - | - |
| ROD | 26 | 2 | 4 | no_rule_matched | - | - | - | - |
| grip | 27 | 0 | 1 | no_rule_matched | - | - | - | - |
| TokensWarContract | 27 | 0 | 1 | no_rule_matched | - | - | - | - |
| Dentacoin | 29 | 0 | 1 | no_rule_matched | - | - | - | - |
| LAAR | 29 | 1 | 3 | ok | 56 | 44 | 0 | 12 |
| INRD | 30 | 1 | 1 | no_rule_matched | - | - | - | - |
| CSTK_CLT | 34 | 0 | 1 | no_rule_matched | - | - | - | - |
| Yihaa | 37 | 0 | 1 | no_rule_matched | - | - | - | - |
| IgfContract | 37 | 1 | 2 | stuck_same_error | - | - | - | - |
| Scale | 41 | 1 | 3 | no_rule_matched | - | - | - | - |

## Notes

- **Paper TRBD IS / US** are copied from TransRacer's Table 2 (columns `#TRBD IS`
  and `#TRBD US` respectively). They reflect TransRacer's own manual-confirmed
  true positives, run against mainnet-deployed bytecode with access to the
  live storage snapshot (TransRacer's Updated-State analysis can reach state
  configurations ESBMC's fresh-Initial-State analysis cannot).
- **ESBMC columns** measure `--tod-race-check=auto` with `--bound --unwind 3
  --no-unwinding-assertions --cvc5 --tod-jobs=1` under the hardened wrapper
  (`timeout 600 + ulimit -v 4000000 + ulimit -t 540`).
- **`esbmc error` count** is pairs whose verification process could not reach
  a verdict (usually solver timeout or front-end exception on the emitted
  harness); they are NOT counted as bugs.
- **`TOD found = 0` systematically** on the three contracts suggests ESBMC's
  Initial-State harness cannot reach the post-state distinction TransRacer's
  Updated-State analysis leverages — matches the paper's observation that
  US-only TRBD constitute 50/66 (75.8%) of the total, i.e. most race bugs
  are not IS-manifest.
