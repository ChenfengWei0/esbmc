# Override / inheritance-dedup diagnostic (file-level decision)

Date 2026-05-15. Authorised diagnostic step (no `src/` change, no
`test.desc` change, no commit). Turns two Hypotheses into Claims so the
Fix-B rewrite (file-level aggregation) and the 3 `cov_scope_*` pin
expected-value re-derivation can proceed on facts.

User decision locked: **report file/project-level coverage**;
`--contract C_i` is only a cost-partitioning driver; denominator = the
file's distinct source branches; numerator = union across per-contract
runs. Per-contract is NOT the reported unit.

## Evidence (goto dumps, this session, `--goto-functions-only`)

### Existing `cov_scope_modifier_crosscontract_knownbug`

| Symbol (mangled) | Branch | Location comment | AST node id |
|---|---|---|---|
| `sol:@C@A@F@bumpInternal#29` | `p > 3` | `file modifier_crosscontract.sol line 17` | `#29` |
| `sol:@C@B@F@bumpInternal#29` | `p > 3` | `file modifier_crosscontract.sol line 17` | `#29` |
| `sol:@C@B@F@setB_gate#0` | `z > 100` | `file modifier_crosscontract.sol line 12` | `#0` |

⇒ **Claim (b):** non-override inherited code is physically copied per
derived contract (`@C@A@` vs `@C@B@`), but both copies' instrumented
branch instructions carry **identical `file:line` AND identical AST
node id `#29`**. One source branch → 2 physical copies → file-level
denominator double-count. Dedup key `(file,line)` is directly usable.
Modifier splice (`setB_gate`) is symbol-keyed under the *using*
contract B (correct) and its branch carries the modifier-*definition*
source line (12); modifier synthetics get node id `#0`, so the robust
universal key is `(file,line)`, not `#NN`.

### Diagnostic sources (kept in /tmp, reproducible below)

`/tmp/ov_nosuper/contract.sol` — `A.f(v) virtual {if(v>7)…}` (line 5),
`B is A`, `B.f(v) override {if(v>9)…}` (line 7), `--contract B`.
`/tmp/ov_super/contract.sol` — same, but `B.f` calls `super.f(v)`
before its own branch.

| Case | Symbol | Branch | Location |
|---|---|---|---|
| nosuper | `sol:@C@A@F@f#23` | `v > 7` | `contract.sol line 5` |
| nosuper | `sol:@C@B@F@f#49` | `v > 9` | `contract.sol line 7` |
| super | `sol:@C@A@F@f#23` | `v > 7` | `contract.sol line 5` |
| super | `sol:@C@B@F@f#55` | `v > 9` | `contract.sol line 7` |
| super | `B.f` body | `FUNCTION_CALL: f((A *)this, v)` → calls the single `@C@A@F@f#23` | — |

⇒ **Claim (a):** override produces exactly **two distinct branch
sites** — `A.f` (`#23`, line 5) and `B.f` (`#NN`, line 7) — distinct
AST node ids, distinct source lines, distinct guards. **Not merged, not
spuriously duplicated.** `super.f(v)` lowers to a **direct
`FUNCTION_CALL` to the single existing `@C@A@F@f#23`** — **no B-local
third copy**. The "super third-copy double-count" risk is **disproved**.
Override is the *benign* case for the duplication axis.

## Consequences (for the next, separately-authorised stage)

1. **Answers the user's override question:** current coverage *does*
   correctly distinguish override (`A.f`≠`B.f` by construction). A
   `--contract B` run drives `B.f` (the override) via the dispatcher;
   `A.f#23` is reached only via `super` or an independent `--contract A`
   run. Correct file-level semantics: in nosuper, `A.f`'s branch is
   file-level *uncovered* unless A is independently driven; in super it
   becomes covered via B (the union model).
2. **The file-level defect is solely non-override inheritance
   duplication**, deduplicable by `(file,line)` (Claim b: identical for
   the duplicate, distinct 5≠7 for the override, identical line-12 for
   all modifier splices).
3. **Fix-B rewrite (file-level):** denominator = count of distinct
   `(file,line)` branch sites across the whole unit (folds inherited
   copies + modifier use-site splices; keeps override pairs distinct);
   numerator = union of certified branches across the per-`--contract`
   cost-partitioned runs; reachability (Fix-B BFS) demoted to "which
   sites a given run can certify", no longer the denominator definer.
4. **The 3 `cov_scope_*` KNOWNBUG pins encode the now-rejected
   per-contract metric** (`^Branches : 2$`). File-level correct
   targets re-derived from the symbol inventory (goto-dump this
   session): **all three → `Branches:4 / Reached:2 / 50%`**.
   **SELF-CORRECTION:** an earlier draft of this point said
   `modifier_crosscontract → Branches:2 after dedup`; that was WRONG.
   Dedup folds the *duplicate `#29` copy* of `bumpInternal`
   (`@C@A@`+`@C@B@` 6→4), it does NOT remove `bumpInternal` itself —
   it is a legitimate source decision, merely *uncovered* under
   `--contract B`. And the key new finding: **`sibling` /
   `uncalled_library` need NO denominator fix** — their whole-unit
   denominator (4) is *already* the correct file-level value; their
   sole defect is the numerator (`Reached:0`→2). Only
   `modifier_crosscontract` has a denominator defect (the `#29`
   duplication, 6→4). Re-pin is a separate authorised stage.
   Dedup key refined: **AST node id `#NN` from the mangled symbol**
   (primary; `#29` identical for the dup, `#23/#49/#36/#18` distinct
   for non-dups), source-location fallback only for `#0` synthetics
   (modifier splices). Full re-plan: `FIX_B_PLAN.md`.

## Reproduce

```bash
mkdir -p /tmp/ov_nosuper /tmp/ov_super
# (sources: A.f virtual if(v>7) line5; B.f override if(v>9) line7;
#  ov_super additionally: super.f(v) as first stmt of B.f)
for d in ov_nosuper ov_super; do
  (cd /tmp/$d && /usr/local/bin/solc --ast-compact-json contract.sol > contract.solast)
  timeout 60 release-bundle/bin/esbmc /tmp/$d/contract.solast --sol contract.sol \
    --contract B --branch-coverage-claims --goto-functions-only > /tmp/$d/goto.txt 2>&1
  grep -nE '\(sol:@.*@F@f#|file contract.sol line [0-9]+ function f$|super|FUNCTION_CALL: .*\bf\(' /tmp/$d/goto.txt
done
# modifier dedup key:
timeout 60 release-bundle/bin/esbmc \
  regression/esbmc-solidity/cov_scope_modifier_crosscontract_knownbug/contract.solast \
  --sol contract.sol --contract B --branch-coverage-claims --goto-functions-only 2>&1 \
  | grep -nE 'bumpInternal|setB_gate|p > 3|z > 100|line 1[27]'
```
