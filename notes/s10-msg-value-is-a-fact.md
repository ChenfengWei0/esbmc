# S10: `msg.value` on a non-payable unit, and the largest measured yield lever so far

## The controlled pair

Same contract, same driver, same ladder, same budgets. The ONLY difference is
`--no-auto-pin-value`:

| | certified | pins carried by each region |
|---|---|---|
| auto-pin ON (new default) | **4 of 5** | **1** (`msg.value == 0`) |
| auto-pin OFF (previous behaviour) | **0 of 5** | 0 |

And the four regions are the paths' EXACT domains:

    enc=6 : x in [0, 39]              enc=14: x in [45, 255]
    enc=30: x in [42, 42]             enc=31: x in [40, 44] \ {42}

against `require(x >= 40); require(x <= 44); require(x != 42)`. The fifth is the
ABI-value-gate revert path, whose whole domain is `msg.value != 0`; the pin
excludes it and it is reported EMPTY, which is the cost and is printed.

## Why this is NOT "--pin-env made the default"

`--pin-env` pins every environment quantity the witnessed paths agree on --
fifteen of them on this contract. That is a real change of MEANING: each region
becomes a statement about one environment slice, true only where
`block.timestamp` happens to equal the value the solver returned. It is off by
default for that reason and it should stay off.

This pins ONE quantity, on units whose source declares that the quantity cannot
be anything else. A non-payable function's compiler-inserted gate reverts every
call carrying value, so **no input with `msg.value != 0` reaches the body**.
Nothing reachable is excluded, and the region stays universally quantified over
`block.timestamp`, `msg.sender`, `tx.origin` and the rest.

Compare the two runs' region text and the difference is the whole argument:

    --pin-env   x in [0, 39], block.basefee == 0, block.blobbasefee == 0,
                block.chainid == 0, block.coinbase == 0, block.difficulty == 0,
                block.gaslimit == 0, block.number == 2^256-1, ... (15 pins)

    S10         x in [0, 39], msg.value == 0

Same certification, incomparably stronger statement.

## Read, not inferred

`function_mutability` reads `stateMutability` off each `FunctionDefinition` in
the solc AST, for the same reason `state_mutability` reads variable mutability
rather than inferring it: "every counterexample has `msg.value == 0`" is true of
a non-payable function and equally true of a payable one nobody happened to send
value to. Inferring would be the "saw nothing else, therefore it is this" move
this project has got wrong repeatedly.

The overload tie-break goes the OPPOSITE way to `state_mutability`'s, on
purpose. There the risky move is dropping a coordinate that really is settable,
so the settable reading wins. Here the risky move is pinning a quantity that
really can vary, so **payable** wins -- the reading that declines to act.

## The default is deliberately NOT the conservative one

Every other flag added this session (`--level0`, `--max-holes`,
`--max-region-pieces`) defaults OFF, because each is POLICY and a policy that
silently changes what a default run reports is a policy nobody chose. This one
defaults ON, and the distinction is exactly the one above: it is not a policy,
it is a fact about the contract being read out of the contract. `--pin-env`
remains off.

## What this implies for numbers already reported

The reach gate's "refuted with no single-coordinate cut available" bucket is a
number the evaluation leans on. On this contract that bucket was 5 of 5 with the
pin off and 1 of 5 with it on -- and most real Solidity is non-payable. So the
certification rates measured on the benchmarks BEFORE this change are
understated by an unknown amount, and re-measuring them is now a prerequisite
for quoting any of them.

Stated as a prerequisite rather than as an estimate: one contract is one
contract, and the size of the effect on the real corpus has not been measured.
