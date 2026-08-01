# D33 — one line in the constructor doubles a unit's claims, and the two instantiations share ONE claim key

**Measured 2026-08-01.** Generated 20-line fixture, must-flip pair, backend held
fixed at `--z3 --tuple-node-flattener` (st1inch's own configuration).

## The observation that started it

`run_stats.py --brief` over all 22 st1inch unit logs — no new ESBMC run, the logs
were already on disk:

| ratio VCC/path | units | extcall lines |
|---|---|---|
| **4.00** | `deposit`, `depositFor`, `depositWithPermit`, `depositForWithPermit`, `rescueFunds` | **4** |
| **2.00** | **`setFeeReceiver`, and only it** | **0** |
| 1.00 | the other 15 | 0 |

The 4.00 group is explained: an external call is modelled as nondet re-entry into
the contract's own dispatcher, and one instrumented assert is instantiated once
per re-entry level — the level count is 4, matching `--unwind 4`, and those five
units are exactly the ones that call out (`safeTransferFrom`, `safeTransfer`,
`Address.sendValue`). The `strloop` column is a constant 59 across the whole
benchmark, so string modelling explains none of the variation and is ruled out.

`setFeeReceiver` at 2.00 with **zero** external-call lines was the one cell
nothing accounted for. And the corpus hands over a minimal pair for free:

```
setMaxLossRatio    external   5 paths   5 VCCs
setFeeReceiver     public     5 paths  10 VCCs
```

## The cause, read from the source

`St1inch.sol:99-110`:

```solidity
constructor(IERC20 oneInch_, uint256 expBase_, address feeReceiver_) … {
    if (_votingPowerAt(…)) revert ExpBaseTooBig();
    if (_votingPowerAt(…)) revert ExpBaseTooSmall();
    setFeeReceiver(feeReceiver_);          // <-- HERE
    ONE_INCH = oneInch_;
}

function setFeeReceiver(address feeReceiver_) public onlyOwner { … }
```

**The constructor calls the unit**, and `setFeeReceiver` is `public`. Every other
setter on this contract is `external`, which Solidity forbids calling internally
— so the constructor *cannot* call them, and they cannot show this shape. That is
the whole of the minimal pair.

## MUST-FLIP, one line apart

Generated fixture, everything byte-identical except the presence of
`setFeeReceiver(feeReceiver_);` in the constructor:

| cell | paths | VCCs | ratio | keys solved >1× | outcomes |
|---|---|---|---|---|---|
| constructor does NOT call the unit | 4 | 4 | **1.00** | 0 | — |
| constructor CALLS the unit | 4 | **8** | **2.00** | **1** | **DISAGREE** |

One line of Solidity doubles the claim count and produces a claim key whose two
solves return different things. That is st1inch's signature exactly: ratio 2.00,
extcall 0.

## What this means

**The unit's body has two identities and the instrumentation cannot tell them
apart.** It executes once in CONSTRUCTOR scope and once under the dispatcher; the
instrumented asserts run in both, and both instantiations are recorded under one
`claim_sig`. They are not the same query — the constructor-scope one runs with
the deployment argument and a partially-initialised contract, the dispatcher one
with a nondet argument and the full entry state — which is why D32 saw `path:13`
decide in 0.011 s on one and return `out of memory` on the other.

⇒ **A reported U reason for such a unit depends on solve order** and on which
instantiation the verdict-preservation logic happened to catch first. The
`solver-unknown 3` on `setFeeReceiver` is not a clean count of paths the solver
cannot decide.

⇒ This is the project's recorded `unit-body-double-identity` hazard, appearing on
a real benchmark for the first time with a measurement attached.

## The question this raises and does NOT answer

**Does the Foundry emitter ever render a case built from the CONSTRUCTOR-scope
instantiation?** If it does, that case is a call the ABI cannot make — the
constructor-scope execution is not a transaction — and it would be a RED test
with nothing marking it, which is the one outcome this pipeline must never
produce. If it does not, what stops it, and is that mechanism deliberate?

Not checked here. It needs the emitter's provenance path read end to end, and
this note is a measurement of the instrumentation, not of the emitter. It is
written down because "the callee is named" has already once been read as "the
decisions are lost" in this project, and the same discipline applies to
"instrumented twice" versus "emitted twice".

## Scope

One contract, one unit, one fixture, plus the 22-log table that motivated it. The
4.00 group's explanation is inherited from the re-entry model's documented
behaviour and is corroborated here by the `extcall` column, not independently
re-derived.
