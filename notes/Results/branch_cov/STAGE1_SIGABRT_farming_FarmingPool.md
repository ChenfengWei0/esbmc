# STAGE1 — FarmingPool SIGABRT (Yul struct/value reinterpret)

Companion to the active plan
`esbmc-solidity-hardhat-coverage-branch-effervescent-whistle.md`
(ACTIVE section). This records the *current* symptom; it is NOT a
ctest-parsed file.

## Reproduce (foreground, k-induction, bounded — never `--unwind`)

```
build/src/esbmc/esbmc \
  regression/esbmc-solidity/cov_pilot_farming_FarmingPool/contract.solast \
  --sol contract.sol --contract FarmingPool --branch-coverage-claims \
  --k-induction --unlimited-k-steps --memlimit 4g --timeout 60 --no-assertions
```

## Observed (2026-05-18, this session)

```
... Unwinding recursion _ESBMC_Nondet_Extcall_FarmingPool iteration 2 (2 max)
ERROR: Looking up index of nonexistant member "getTotalSupply" in struct/union "struct BytesStatic"
timeout: the monitored command dumped core
```

SIGABRT at `src/irep2/irep2_type.cpp:288`
(`struct_union_data::get_component_number`). Net effect: FarmingPool
branch coverage = 0/60 (total blocker, `TWO_TRACK_AGGREGATE.md`
finding 1).

## Root cause (one-liner)

1inch `FarmingLib` packs a memory `Info` pointer into a `bytes32`
context via inline assembly (`assembly { self := ctx }` /
`assembly { ctx := self }`). `bytes32` → `struct BytesStatic`
(`solidity_convert_type.cpp:1061`). In the YulAssignment `dst := src`
(rhs = `YulIdentifier`) fast-path
(`src/solidity-frontend/solidity_convert_stmt.cpp:1885-1896`,
collapse at **line 1891**), `solidity_gen_typecast`
(`typecast.cpp:15-19`, just `c_typecastt::implicit_typecast`) has no
rule to bridge two different struct tags, so the cast is a no-op and a
type-mismatched `code_assignt(self:struct Info, ctx:struct BytesStatic)`
is emitted. Component-wise struct copy (`util/migrate.cpp:200-224`)
then looks `getTotalSupply` up against `BytesStatic` → abort.

## KNOWNBUG pin rationale (Stage A)

`regression/esbmc-solidity/cov_pilot_farming_FarmingPool/test.desc`
keeps its existing regexes `^Branches : 24$` / `^Branch Coverage: [1-9]`
(KNOWNBUG). Under `testing_tool.py:279-290` KNOWNBUG semantics, those
describe the *desired-fixed* output: they do NOT match the aborting
run, so the KNOWNBUG test currently PASSES; a real frontend fix that
emits non-zero coverage makes both regexes match → `exit(77)` → the
KNOWNBUG→CORE flip signal. The abort line is deliberately NOT a desc
regex — matching the current output would force an immediate FAIL.
`.desc` files are positional (no comment lines), so the current
symptom is recorded only here.

## Post-fix outcome (Stage C, 2026-05-18)

Fix landed in `src/solidity-frontend/solidity_convert_stmt.cpp`
(`dst := src` fast-path: incompatible struct-tag reinterpret →
nondet-of-declared-type + `[approx]` warning). Empirically:

- **SIGABRT is fixed at root.** Minimal repro abort →
  `VERIFICATION SUCCESSFUL`; FAIL dual → `VERIFICATION FAILED`
  (value-imprecision documented); FarmingPool no longer aborts and
  cleanly emits `Branches : 24`. All 44 inline-assembly/Yul
  regression tests + the PASS/FAIL dual: green, zero content
  regressions.
- **Residual, SEPARATE, out-of-scope finding surfaced.** With the
  crash removed, FarmingPool's coverage k-induction does not
  converge: it reports `Reached : 0 / Branch Coverage: 0%` and a
  standalone run needs ~180–240 s wall. Under ctest's 180 s
  per-test wall it now **TIMES OUT** (previously it fast-aborted in
  ~13 s). This is the pre-existing upstream `Reached:0` blocker
  (memory: `St1inch/Farming OUT-of-scope=upstream Reached:0`; the
  original Stage-0 pilot already recorded FarmingPool
  `Branches:338, Reached:0`) — the SIGABRT was *masking* it. It is
  explicitly **out of scope** for this stage (a distinct finding,
  not the Yul-reinterpret bug).
- Consequence: `cov_pilot_farming_FarmingPool` cannot honestly flip
  KNOWNBUG→CORE (coverage still 0 %), and it can no longer fast-abort
  to a stable green KNOWNBUG either (it now ctest-times-out). The
  genuine, honest proof of *this* fix is the
  `yul_struct_reinterpret_{pass,fail}` dual.

### Pin resolution (user-directed)

The regression harness (`regression/testing_tool.py:137`
`UNSUPPORTED_OPTIONS = ["--timeout","--memlimit"]`,
`generate_run_argument_list` pops flag+value) **strips `--timeout`**
from the desc; the only wall is the global `ESBMC_REGRESS_TIMEOUT`
(180 s here, no per-test override). So a desc `--timeout` cannot
bound the run. Per user decision (2026-05-18), the pin is stabilised
by **capping the k-induction ladder**: desc `--unlimited-k-steps` →
`--max-k-step 15` (pure k-induction; not `--unwind`, not a
soundness-skip). Measured: `--max-k-step {3,6,15}` → ~15 s clean
self-exit (rc=0); `--max-k-step 50` (tool default) → 178 s, killed
(does not fit). At N=15 esbmc emits no coverage summary (the k-cap is
reached before a coverage pass completes — the faithful record of the
residual non-convergence), so neither `^Branches : 24$` nor
`^Branch Coverage: [1-9]` matches → **stable KNOWNBUG-PASS**
(`ctest` 16.31 s, ~12× margin under the 180 s wall). The flip target
is unchanged: if the out-of-scope `Reached:0`/non-convergence is ever
fixed *and* converges within k≤15, the pin auto-flips. Not masked —
it honestly records "FarmingPool branch-coverage k-induction does not
complete a pass within a bounded budget".

## Minimal standalone repro

`regression/esbmc-solidity/yul_struct_reinterpret_knownbug/`
(~50 SLOC, solc 0.8.30). Verified this session to abort with the
byte-identical message
`ERROR: Looking up index of nonexistant member "getTotalSupply" in struct/union "struct BytesStatic"`.
Pinned KNOWNBUG with flip target `^VERIFICATION SUCCESSFUL$`.
