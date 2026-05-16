# Minimal pins for the `--contract` coverage-scoping bug

Created 2026-05-15. Small, single-file reproducers that pin every
observed unexpected branch-coverage behaviour from
`COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md`. Replaces reliance on the
1648-line flattened EscrowDst pilot for the scoping defect.

All numbers below are **captured** (run this session), not inferred.
Args mirror the pilot exactly (coverage ⇒ `--k-induction`, never
`--unwind`; per `feedback_coverage_must_use_kinduction`). Each contract
is ≤ 25 lines; runs sub-second (no k-induction budget burn).

| Dir | `--contract` | Observed today | Correct (semantics-b) | Mode | Pinned regex |
|---|---|---|---|---|---|
| `cov_scope_single_contract_pass` | C | `Branches : 2 / Reached : 2 / 100%` | 2 | **CORE** | `^Branches : 2$` `^Reached : 2$` `^Branch Coverage: 100%$` |
| `cov_scope_uncalled_library_knownbug` | C | `Branches : 4 / Reached : 0` | 2 | KNOWNBUG | `^Branches : 2$` |
| `cov_scope_sibling_contract_knownbug` | C | `Branches : 4 / Reached : 0` | 2 | KNOWNBUG | `^Branches : 2$` |
| `cov_scope_modifier_crosscontract_knownbug` | B | `Branches : 6 / Reached : 0` | 2 | KNOWNBUG | `^Branches : 2$` |

## What each pins

- **single_contract_pass (positive control / the required PASS).**
  One contract, one `if/else`, no out-of-scope code. ESBMC counts
  exactly C's branch and drives both directions → `2 / 2 / 100%`.
  Proves the coverage pipeline is correct *in the absence of
  out-of-scope code* — isolating every KNOWNBUG below to the
  `--contract` scoping dimension alone, not coverage in general.

- **uncalled_library_knownbug.** `library L.f` has a branch C never
  calls. Under `--contract C` the denominator must be C's branch only
  (2). Observed 4 (= C's 2 + L's 2). Pins the "unreachable library
  inflates `Branches:`" finding (root-cause doc §1, Link 1-5).

- **sibling_contract_knownbug.** Two independent contracts, no
  inheritance; `--contract C` must not count `Other`'s branch.
  Observed 4. Pins "non-target sibling contract inflates `Branches:`"
  — the direct minimal form of the user's "A's branches leak under
  `--contract B`" scenario.

- **modifier_crosscontract_knownbug.** The user's explicit case:
  contract `A` defines `modifier gate` containing a branch and does
  **not** use it; `A` also has an internal branchy `bumpInternal`;
  `B is A` uses `gate`. Under `--contract B` only B-reachable branches
  should count — `setB`'s spliced `gate` `if` (2). Observed 6: the
  spliced `gate` branch (correctly B's) **plus** A's
  unreachable-from-B internals. Pins both the inflation and (via the
  source-comment + root-cause doc §2b) the latent location
  mis-attribution: the spliced `gate` branch is B's behaviour but its
  instruction `location` points into A's source lines
  (`solidity_convert_modifier.cpp:1190-1192`) — which is why any
  location/line-range scoping is unsound and Fix-B must key on the
  reachable-function set, never `it->location`.

## KNOWNBUG semantics (source-read `testing_tool.py:279-290`)

A KNOWNBUG is in its expected state iff **not all** regexes match
(`FAIL_MODES`). Each `^Branches : 2$` does NOT match today's `4`/`6`
output → stable KNOWNBUG PASS. When Fix-B corrects scoping the output
becomes `Branches : 2` → all match → harness `exit(77)` "reclassify as
CORE" → the intended auto-flip signal. The desired value (2) is exact
by construction (each contract's in-scope reachable part is exactly one
`if` = 2 instrumented claims), so no fabricated number is pinned.

## Run

```bash
cd build && cmake . && ctest -R cov_scope_ --output-on-failure
# 2026-05-15: 4/4 PASS, 1.37 s total.
```

## UPDATE 2026-05-16 — file-level decision + S-D shipped

All 4 re-pinned to **CORE** at the file-level triple
`^Branches : 4$` / `^Reached : 2$` / `^Branch Coverage: 50%$`
(single_contract_pass stays `2/2/100%`). `ctest -R cov_scope_` →
4/4 PASS, 1.39 s. Fix = one line in `goto_coverage.cpp:291`
(`total_branch = all_claims.size()` — file-level dedup of
inheritance/modifier physical copies; see `FIX_B_PLAN.md` §1b).
Line-D effect: modifier_crosscontract `Branches 6→4`;
sibling/uncalled unaffected (no duplication).

## Residual (CORRECTED 2026-05-16)

- **The `Reached : 0` "second symptom" was a MISDIAGNOSIS.** It was a
  non-final k-induction iteration line; the *final* verdict was always
  `Reached : 2` even on the pre-fix binary (verified old-vs-new).
  There is **no** separate numerator/Line-N bug for these cases —
  `sibling`/`uncalled_library` were never buggy under the file-level
  metric; `modifier_crosscontract` only needed the Line-D denominator
  dedup. Earlier text in this file and `KNOWNBUGS.md` claiming a
  distinct numerator symptom is withdrawn.
- The exact 6 = ? decomposition for the modifier case (gate split vs
  per-derived `bumpInternal` duplication) is not fully diagnosed; the
  pinned property is only `> 2`, which is unambiguous.
