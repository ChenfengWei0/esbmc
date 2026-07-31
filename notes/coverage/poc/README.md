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

## The set

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

## What has been run so far

Only 01-03, and only against a build with uncommitted changes in it — recorded
here because the provenance is a defect, not a footnote. Those runs are to be
repeated against a known commit.

| configuration | paths | F | bounded-holds |
|---|---|---|---|
| `Tiny` `--focus-function withdraw` tx=1 | 5 | 3 | 2 |
| `Tiny` whole contract tx=1 | 8 | 6 | 2 |
| `Tiny` whole contract **tx=2** | 8 | **8** | **0** |
| `Tiny` whole contract tx=3 | 8 | 8 | 0 |
| `Tiny2` `--focus-function withdraw` tx=1 | 5 | **5** | 0 |
| `Tiny3` whole contract tx=1 | 7 | 5 | 2 |
| `Tiny3` whole contract **tx=2** | 7 | **7** | 0 |
