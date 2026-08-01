# The hand-written proof-of-concept set

Every contract here fits on one screen, every run takes about a second, and
every expected answer can be counted by reading the source. That is the point:
a benchmark tells you a number, and a contract you wrote tells you whether the
number is right.

The set exists because three hours of matrix work on real benchmarks reached a
conclusion that ten lines of hand-written Solidity overturned in five seconds.
The matrix crossed the transaction bound only under `--focus-function`, where
raising it cannot help by construction, and crossed scope only at tx=1 — so the
one cell that mattered, whole-contract at tx=2, was never run.

## Rules

* **One property per contract.** A contract that would demonstrate two things
  demonstrates neither when the answer is surprising.
* **The expected outcome is written in the file, before the run.** A prediction
  recorded afterwards is not a prediction.
* **Each file names the failure it exists to catch**, not just the feature it
  exercises. Several of these are here because the failure has already happened
  once on a real contract and cost hours to attribute.

## The set — P series: the method's own shapes

These were written to ask a question about the method before a benchmark could
answer it ambiguously.

| # | contract | isolates | the failure it would catch |
|---|---|---|---|
| 01 | `Tiny.sol` | cross-function state, one hop | the entry-state blocker, and whether the tx bound buys it |
| 02 | `Tiny2.sol` | constructor-established state | control for 01: is the obstacle the state or the call? |
| 03 | `Tiny3.sol` | a decision-free predecessor | control for 01: does the predecessor's own decisions matter? |
| 04 | `P04_Chain2.sol` | a two-hop setup chain | whether the bound buys N-1 calls or ORDER buys them |
| 05 | `P05_Hole.sol` | punched region, `x != 42` | a region that shrinks past the hole instead of punching it |
| 06 | `P06_Product.sol` | two independent coordinates | one coordinate pinned to its counterexample, still "certified" |
| 07 | `P07_Sender.sol` | `msg.sender`, an env coordinate | equality-constrained address degenerating during shrink |
| 08 | `P08_Value.sol` | `msg.value` + the synthesised non-payable gate | counting a synthesised gate as a real decision |
| 09 | `P09_TimeLock.sol` | `block.timestamp` against stored state | a product region cannot express a relation between two coordinates |
| 10 | `P10_Signed.sol` | a signed coordinate | a silent degradation to an unsigned box instead of a loud refusal |
| 11 | `P11_Inner.sol` | internal-call inlining | the expansion factor, on a contract where the answer is 4 |
| 12 | `P12_UnitCallee.sol` | a public callee: the double identity | a RED test — the inlined copy keeping the entry guard |
| 13 | `P13_Exits.sol` | four distinct exit kinds (R0) | "normal exit" asserted where the chain reverts |
| 14 | `P14_Ladder.sol` | the assertion ladder to a bounded delta | the ladder stopping at `!=`; unsigned delta wrapping |
| 15 | `P15_Loop.sol` | an input-controlled loop vs `--unwind 4` | truncation reported as "the path does not hold" |
| 16 | `P16_Mapping.sol` | nested mappings, hence the solver choice | the per-claim cost gap that makes st1inch unsolvable |
| 17 | `P17_Modifier.sol` | modifier renaming | `--focus-function` selecting a different unit than intended |
| 18 | `P18_Unchecked.sol` | checked/unchecked, and `a / 0` | `type(uintN).max` flowing into an assertion |
| 19 | `P19_ReturnShapes.sol` | decisions inside a `return`, crossed as ternary × literal/call arms × plain/nested | a decision the DFS walks past, so two executions collapse to one `enc` — no crash, just a smaller tidier path set |
| 20 | `P20_Tx2Attribution.sol` | tx=2 method attribution, with deliberately disjoint guards | the mis-attribution the tool warns about at `--solidity-max-tx >= 2`, in the one configuration that reaches paths nothing else reaches |
| 21 | `P21_ExternalCall.sol` | an external call = nondet RE-ENTRY into this contract's own dispatcher | the shape that motivated the pass's own `--unwind` default (measured 944 unwindings, OOM); also the emitter's hardest case, a mock with a nondet return |
| 22 | `P22_Inherit.sol` | inheritance, `virtual`/`override`, a base-declared unit | `useHook` inlining the BASE body — an ordinary path count, green tests, wrong contract; virtual dispatch is excluded from the canonical decision set so the baseline cannot flag it |
| 23 | `P23_LibraryUsing.sol` | `library` + `using ... for`, with a LOOP inside it | the `--no-simplify` interaction that took F from 2 to 0 with exit 0 and no specific warning; also the library-only scope difference between the two metrics |
| 24 | `P24_RevertInCallee.sol` | a revert INSIDE an inlined callee | the exit census reading the unit's own frame, so a reverting path is recorded as normal and the test asserts the call succeeds |
| 25 | `P25_ExitMatrix.sol` | the exit dimension as a CROSS PRODUCT: where (body/modifier/callee) × how (7 forms) | an exit kind that silently shares another's rendering; names the four exits that cannot appear at all because there is no Panic modelling |
| 26 | `P26_TypeMatrix.sol` | the input-TYPE dimension: bool, address, uint8, uint128, int128, bytes32, enum | silent widening — a bytes32 or address handed an interval, which certifies and then fuzzes over a range meaningless for the type |

## The set — D series: reductions of failures that already happened

Every D file was carved out of a failure observed on a real benchmark or on
another PoC. Several are single-factor controls for one another, and the
refuted candidates are kept in the file headers so they are not re-proposed.

| # | contract | isolates | provenance |
|---|---|---|---|
| D01 | `D01_StringState.sol` | a `string` state variable | suspect 1 of 5 for the st1inch death — REFUTED |
| D02 | `D02_StructWithMapping.sol` | a struct field that is a mapping | suspect 2 of 5 — REFUTED |
| D03 | `D03_StructWithDynArray.sol` | a struct field that is a dynamic array | suspect 3 of 5 — SIGABRT on all three backends, and it trips the tool's own INTERNAL DEFECT check |
| D04 | `D04_AddressSetShape.sol` | the exact 1inch `AddressSet` shape: struct-in-struct + mapping | suspect 4 of 5 |
| D05 | `D05_RecursiveStruct.sol` | a genuinely self-referential `struct Node { Node[] kids; }` | suspect 5 of 5, and the CONTROL — the calibration point for the other four |
| D06 | `D06_PlainDynArray.sol` | dynamic array as a PLAIN state variable, no struct | narrows D03, axis 1: is the struct wrapper load-bearing? |
| D07 | `D07_StructDynArrayNoPush.sol` | the declaration without the constructor's `push` | narrows D03, axis 2: the declaration or the write? |
| D08 | `D08_StructFixedArray.sol` | `uint256[3]` instead of `uint256[]` | narrows D03, axis 3: dynamic vs fixed length |
| D09 | `D09_ValueGate.sol` | one unit, no source decision — the ONLY decision is the synthetic ABI value gate | 55 of 63 payload-vs-path contradictions. ANSWERED in the file: the env payload was harvested from the FIRST assignment, not the last before entry |
| D10 | `D10_WrapNotPanic.sol` | one checked `+`, no branch | the model wraps where Solidity 0.8 panics ⇒ a RED test. Carries the measured cost of C1 (2^k: aqua ×2, st1inch ×5.4e8) and the decision for (c). The `require(amt > 0)` is load-bearing — without it the solver picks 0 and the test is green |
| D11 | `D11_Bytes32Equality.sol` | one `bytes32` parameter against a constant | both paths reported an IDENTICAL `b`; root cause was a shared pending queue with two readers disagreeing about ownership. Keeps two earlier WRONG explanations so they are not re-proposed |
| D12 | `D12_Bytes32LengthFree.sol` | an ABI `bytesN` argument's `.length` is not pinned | separate from D11 and deliberately so. The fix was written, measured, and REVERTED — it traded a soundness gap for a recoverability gap. Pinned as a KNOWNBUG |
| D13 | `D13_Z3TupleNotWellFounded.sol` | two library structs with the same SHORT name become one z3 tuple sort | reduced from 4874 lines. The key is now read out of `z3_conv.cpp:1030-1031` rather than inferred. `--cov-report-json` is part of the reproduction: without it slicing removes both state variables and the colliding sort is never built |
| D14 | `D14_SolverUnknown.sol` | st1inch's 59 `solver-unknown` | **NOT COMMITTED and NOT MINIMAL** — a 4770-line checkpoint from a reduction that was killed. The leading suspect (the `_EXP_TABLE` chain) was refuted by D17 in 1.2 s, so this needs restarting from a different hypothesis |
| D16 | `D16_OnlyByOverflow.sol` | a path reachable ONLY by overflowing | written because `--path-cov-arith-resolve`'s PROOF arm had nothing to fire on: on D10 and Tiny2 the re-solve comes back SAT, so `arith_revert_only_paths` stays empty and the emitter refusal could not be measured. The sibling path is the control against over-refusing |
| D17 | `D17_ExpChain.sol` | fixed-point chain LENGTH, 30 steps vs 3, single factor | the control for D14's leading suspect. Written BEFORE resuming the reducer, on the project's own rule that a hand-written PoC precedes automated reduction |
| D18 | `D18_QualifiedError.sol` | a custom-error `revert`, QUALIFIED vs unqualified, four functions differing in one property | `expr.cpp:688-695` turns a MemberAccess call to an `ErrorDefinition` into `code_skipt()`, so the qualified spelling may not prune its path — which would enumerate a reverting path as a NORMAL exit and render it as a test asserting the call succeeds. The expectation is DIFFERENTIAL (the spellings must agree with each other), so it needs no knowledge of `uses_revert_observation`, whose value under path coverage was unknown when the file was written |

There is no D15 file. D15 is the KILLED-unit investigation, and it resolved into
`killed_triage.py` plus a single remaining target (`EscrowDst.publicWithdraw`)
rather than into a contract.

## What has been run

**2026-08-01, current build.** The certification sweep (`certify_poc.py`) ran 39
of these contracts, which contain 68 units:

| outcome | units |
|---|---|
| CERTIFIED | 36 |
| NOT-CERTIFIED | 13 |
| KILLED | 14 |
| NO-PATH | 4 |
| NO-COORDINATE | 1 |

**CE certification rate 60/144 = 41.7%**, where the denominator counts only
paths that GOT A VERDICT. KILLED and NO-PATH contribute nothing rather than
zero — recording a budget outcome as a search result is the shape this corpus
has repeatedly been caught by.

⚠ **The 14 KILLED are the loudest number in that table and they are still
unexplained.** The `-u` fix that keeps a killed run's driver log landed AFTER
this sweep started, so all fourteen pieces of evidence were eaten by stdout
buffering. They have to be re-run before any of them can be triaged.

⚠ `D16` and `D17` postdate the sweep and are not in those counts.

**Earlier, 01-03 only, against a build with uncommitted changes in it** —
recorded here because the provenance is a defect, not a footnote. These runs are
to be repeated against a known commit.

| configuration | paths | F | bounded-holds |
|---|---|---|---|
| `Tiny` `--focus-function withdraw` tx=1 | 5 | 3 | 2 |
| `Tiny` whole contract tx=1 | 8 | 6 | 2 |
| `Tiny` whole contract **tx=2** | 8 | **8** | **0** |
| `Tiny` whole contract tx=3 | 8 | 8 | 0 |
| `Tiny2` `--focus-function withdraw` tx=1 | 5 | **5** | 0 |
| `Tiny3` whole contract tx=1 | 7 | 5 | 2 |
| `Tiny3` whole contract **tx=2** | 7 | **7** | 0 |
