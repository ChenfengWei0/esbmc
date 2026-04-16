# call_3 KNOWNBUG Investigation

## What the test wants to express

```solidity
function callwrap(address called) public {
    uint _balance = address(this).balance;
    called.call("");                          // arbitrary external call
    assert(_balance == address(this).balance);
}
```

The intent: "an arbitrary external call may change `this.balance` (re-entry,
fallback that transfers to us, etc.), so the post-call equality assertion
must report a violation under the over-approximate unbound model."

## Why it now hangs

Until the SMTChecker-style balance fix, `address(this).balance` in unbound
mode returned a **fresh `nondet_uint`** on every read via
`get_aux_property_function`.  The two reads were independent symbols, so the
solver trivially produced an inequality witness — `VERIFICATION FAILED`
landed in milliseconds for the wrong reason (not because external calls
were modelled, but because the read itself was inconsistent).

After the fix (`solidity_convert_expr.cpp::AddressMemberCall`), both reads
alias the same `this->$balance` SSA cell.  In unbound mode `called.call("")`
is modelled as `_ESBMC_Nondet_Extcall_<self>` — it can re-enter our public
functions, but **none of those functions modify `this->$balance` in the
current model** (no transfer/send/call{value:} side-effect emission, no
`coinbase`/`selfdestruct` injection).  So the balance is provably equal
across the call, the assert holds, and the verifier searches forever for
a counterexample under k-induction → 60s timeout.

## Why this is the right outcome

The new behaviour is *correct on the model we have*: under "external calls
do not transfer ETH back into us", balance is invariant across `call("")`.
Reporting `FAILED` only because of inconsistent reads was a soundness
illusion.

## What it would take to make this CORE again

Pick **one** of:

1. **External-call balance modelling**: emit a nondet `+= delta` on
   `this->$balance` after every unbound `call("")` to over-approximate
   "callee may have transferred ETH to us".  Restores `FAILED` here but
   risks new false positives elsewhere.
2. **Re-entry path that explicitly modifies balance**: have the
   `_ESBMC_Nondet_Extcall_<self>` harness include a nondet payable receive
   path that may credit `this->$balance`.  Same effect, narrower blast
   radius.
3. **Drop the test**: accept that this property is unverifiable without
   (1) or (2) and rewrite the contract under `--bound` with an explicit
   payable callee whose constructor transfers back.

(1) and (2) are part of the prerequisite work for TOD-Balance — which
needs balance-side-effect modelling anyway.  Lifting this back to CORE
is tracked alongside that work.
