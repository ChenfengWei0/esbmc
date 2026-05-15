# Stage 2C-followup — aqua pilots re-pinned to post-2C symptoms

Generated 2026-05-15. Companion to `STAGE2C_2d_RESULT.md` (the Stage 2C
fix closure). Authoritative post-2C state of the `cov_pilot_aqua*`
pins. Supersedes the aqua rows of `KNOWNBUGS.md` / `PILOT_FINDINGS.md`
(those remain valid as the Stage-0/1 2026-05-14 snapshot).

## What Stage 2C changed for the pilots

Stage 2C (commit `3d6d424b73`) fixed the `bare smt_sort (id=4)`
SMT-backend abort on K≥2 nested array/mapping-of-struct. Re-running
every `cov_pilot_aqua*` with its exact `test.desc` flags (coverage
mode, `--k-induction`, `--timeout` per-desc) shows the `bare smt_sort`
wall is gone everywhere. One pilot flipped to a real verdict; the rest
now stop at a **different, independent, pre-existing** wall that lives
**upstream of the SMT backend** (goto-symex / IR), so it is
architecturally impossible for the Stage 2C change (confined to
`src/solvers/smt/`, which runs *after* symex) to have caused it.

## Empirical per-pilot table (captured 2026-05-15, not inferred)

| Pilot | r/w | pre-2C pin | post-2C symptom | pin now |
|---|---|---|---|---|
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_readonly` | **read-only** | `bare smt_sort` | **`Branch Coverage: 75%`** (4 br, 3 reached), clean exit | **CORE** `^Branch Coverage: 75%$` (flipped) |
| `cov_pilot_aqua2A_3lvl_addr_bytes32_addr_struct` | write | `bare smt_sort` | `value_set.cpp:1258 value_sett::assign: base_type_eq(rhs,lhs)` | KNOWNBUG (stable) |
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_singlefield` | write | `bare smt_sort` | `irep2_expr.cpp:366 assert_type_compat_for_with: is_array_type(b)` | KNOWNBUG (stable) |
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_struct_aborts` | write | `bare smt_sort` | `irep2_expr.cpp:366 is_array_type(b)` | KNOWNBUG (stable) |
| `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_uint256` | write | `bare smt_sort` | `irep2_expr.cpp:366 is_array_type(b)` | KNOWNBUG (stable) |
| `cov_pilot_aqua2A_4lvl_all_addr_struct` | write | `bare smt_sort` | `irep2_expr.cpp:366 is_array_type(b)` | KNOWNBUG (stable) |
| `cov_pilot_aqua2A_5lvl_addr_addr_bytes32_addr_addr_struct` | write | `bare smt_sort` | `irep2_expr.cpp:366 is_array_type(b)` | KNOWNBUG (stable) |
| `cov_pilot_aqua_Aqua` | write | `bare smt_sort` | `irep2_expr.cpp:366 is_array_type(b)` | KNOWNBUG (stable) |

`ctest -R cov_pilot_` (build/, 2026-05-15, single run, mem 4096):
**25 / 25 PASS** — readonly verified stable as CORE; the 7 KNOWNBUG
aqua pins still satisfied (regex `^Branch Coverage:` does not match
while they abort → baseline stable, no spurious flip); the 4 non-aqua
pilots (`farming`/`EscrowDst`/`st1inch`, `Reached: 0`) and the 14 LOP
`MakerTraitsLib` per-function pins unchanged.

## Findings

1. **Stage 2C win is real and isolated to a realistic shape.** The
   only read-only deep-nested-mapping-of-struct pilot
   (`_readonly`, a 4-level `mapping(addr=>addr=>bytes32=>addr=>Balance)`
   read loop) now completes end-to-end: a genuine KNOWNBUG→CORE flip
   driven by the struct-of-arrays fix.
2. **The remaining wall is the deep-nested-mapping WRITE, not
   struct-of-arrays.** Every still-KNOWNBUG pilot performs a storage
   write through a 3–5-level mapping ref (`b.tokensCount = 0xff` /
   `_b[...] = 0xff`). It reproduces with a **non-struct `uint256`
   value** (`cov_pilot_aqua2A_4lvl_..._uint256`), which the Stage 2C
   struct-of-arrays path never touches — proving the blocker is the
   nested-mapping write lowering itself, not the 2C representation.
3. **Two distinct upstream walls**:
   - `src/pointer-analysis/value_set.cpp:1258` `value_sett::assign`
     `assert(base_type_eq(rhs->type, lhs_type, ns))` — 3-level case.
   - `src/irep2/irep2_expr.cpp:366` `assert_type_compat_for_with`
     `assert(is_array_type(b))` — 4/5-level cases + aqua/Aqua.
   Both fire during goto-symex (SSA / value-set construction), strictly
   before `smt_convt` is invoked (`bmct::generate_smt_from_equation`).
   The Stage 2C diff is entirely in `src/solvers/smt/` → cannot be the
   cause; these are pre-existing symex/IR limitations on deep nested
   mapping storage-ref writes.

## Scope

**Diagnosis done — see `STAGE2C_FOLLOWUP_DIAG.md`** (root cause:
`solidity_convert_expr.cpp:4203` nested-mapping access types the
intermediate `index_exprt` with the under-nested `get_type_description`
`t` instead of `array.type().subtype()` as the direct-access path at
:4174 does; ≥3-level write only). **Fix is OUT OF SCOPE for
this followup** (a separate, separately-authorised stage, per
`feedback_strict_stage_authorization`). This followup only: (a)
recorded the empirical post-2C symptom per pilot; (b) flipped the one
genuine win to CORE with an exact-value regex; (c) re-pinned the 7
remaining KNOWNBUGs with accurate documentation so a future fix of the
*new* wall is a detectable flip. No `src/` change; no `test.desc`
regex change except the `_readonly` flip; no commit unless asked.
