# The invocation matrix, round 1 — the tx/strategy dimension on `Aqua.safeBalances`

Every corpus number produced so far comes from ONE cell:

```
--contract C --focus-function f --solidity-max-tx 1
   (no --unwind; the path-coverage pass installs 4 itself)
   (no bounding strategy at all)
   (slicing on, protected by a 183-symbol exemption list)
   (simplification on: 6938 VCC -> 1822)
   outer timeout 180 s, --memlimit 8g
```

That cell was never compared against any other, so there was no evidence it was
the right one — only evidence that it did not crash. This is round 1 of closing
that gap. Target: `aqua__Aqua.flat.sol`, `--contract Aqua --focus-function
safeBalances`, 300 s per cell, 8 g.

| cell | exit | report | paths | units | F | U | F w/ inputs | wall s | peak child MB |
|---|---|---|---|---|---|---|---|---|---|
| `tx1` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 3.3 | 276 |
| `tx2` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 6.0 | 352 |
| `tx0` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 3.1 | 352 |
| `--coverage-multi-tx --unwind 4` | 1 | yes | 2846 | 6 | 2 | 2844 | 2/2 | 5.1 | 353 |
| `--coverage-multi-tx --incremental-bmc` | **0** | yes | 2846 | 6 | 2 | 2844 | 2/2 | **146.7** | **2745** |
| `--coverage-multi-tx --k-induction` | **0** | yes | 2846 | 6 | 2 | 2844 | 2/2 | 9.0 | 2745 |
| `--coverage-multi-tx` alone | **-6** | no | - | - | - | - | - | 0.1 | abort |
| `--coverage-multi-tx --solidity-max-tx 1` | **-6** | no | - | - | - | - | - | 0.1 | abort |

## Four results

**1. On this unit the transaction dimension changes nothing.** Six legal cells,
identical output: 2846 paths, 6 units, F = 2, U = 2844, both F claims carrying
inputs. What differs is cost — up to 44x wall and 10x peak memory. This is the
invocation contract's §2 prediction holding in a measurement: under
`--focus-function f` every transaction is another call to `f`, so a path guarded
by state that only another public function establishes is unreachable at every
tx bound, and buying more transactions buys nothing.

**ONE UNIT, AND ITS NAME IS PART OF THE CLAIM.** `safeBalances` is a reader with
almost no state guarding it — 2 F against 9 `bounded-holds` in the corpus
collection. The units where the tx dimension COULD matter are the ones with a
large `bounded-holds` count (`Aqua.dock` 2/61, `FarmingPool.deposit` 7/147,
`FarmingPool.withdraw` 7/96). A null result here does not transfer to them and
must not be generalised until they are run.

**2. The two illegal cells are illegal by design, and the tool says why.**

```
ERROR: --coverage-multi-tx keeps the unbounded multi-transaction dispatcher loop
live; it needs a global bounding strategy. Add --incremental-bmc (recommended:
discovers the transaction depth dynamica...

ERROR: --coverage-multi-tx is incompatible with --solidity-max-tx: an explicit
tx bound forces a deterministic unroll whose Foundry reconstruction is
unreliable. Bound the live dispatcher loop with --...
```

Both abort in 0.1 s with a diagnostic. They are in the matrix rather than
omitted from it, because "this combination is refused" is a result.

**3. EXIT CODE IS NOT COMPARABLE ACROSS STRATEGIES.** `tx1` / `tx2` / `tx0` /
`multi-tx+unwind` exit **1** (`VERIFICATION FAILED`, which under coverage means
a claim was refuted, i.e. a path was witnessed — the success signal). But
`multi-tx+incremental-bmc` and `multi-tx+k-induction` exit **0** with the same
F = 2 and the same report.

Any collector that reads success from the exit code silently flips meaning when
the strategy changes. `pathcov_collect.py` records `exitCode` but keys nothing on
it; `option_matrix.py` prints it beside the report contents rather than instead
of them. Both are correct today, and this is written down so that neither is
"simplified" later into an exit-code check.

**4. `--focus-function` does not reduce what is INSTRUMENTED.** All six cells
report 2846 paths across 6 units — the whole contract's path set, identical to
the whole-contract run. Focus narrows which unit the dispatcher enters, not what
gets instrumented, so 2844 of the claims are `unit-not-entered`. That is why a
per-method collection's per-report `paths_total` is a contract-level number and
must never be read as "this unit has 2846 paths".

## What round 1 does NOT answer

* the same matrix on a state-guarded unit (`dock`, `deposit`, `withdraw`) —
  running;
* the slicing dimension: is the 183-symbol exemption list complete, i.e. does
  `--no-slice` change the report at all? The `F w/ inputs` column is in the
  table for this, and on this unit it is 2/2 in every cell — but 2 claims is not
  a test of an exemption list;
* the simplification dimension: 6938 VCC -> 1822, and what happens to a
  path-coverage claim that is simplified away is unread;
* whether any of this transfers to the whole-contract configuration, which died
  of solver OOM at 8 g after solving 5100+ claims and discarding all of them.
