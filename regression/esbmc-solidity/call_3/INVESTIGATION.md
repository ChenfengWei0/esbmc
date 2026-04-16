# call_3 KNOWNBUG Investigation

## What the test wants to express

```solidity
function callwrap(address called) public {
    uint _balance = address(this).balance;
    called.call("");                          // low-level call, NO value
    assert(_balance == address(this).balance);
}
```

The intent: "an arbitrary external call may change `this.balance` (the
callee could `selfdestruct(payable(address(this)))`, coinbase could credit
us, the callee could `transfer` ETH to us via our receive, etc.), so
under a sound over-approximation the post-call equality assertion must
report a violation."

## Why it used to report FAILED (and why that was illusory)

Before the SMTChecker-style balance fix
(`solidity_convert_expr.cpp::AddressMemberCall`),
`address(this).balance` in unbound mode returned a fresh `nondet_uint`
on every read via `get_aux_property_function`.  The two reads of
`address(this).balance` were therefore independent SSA symbols, so the
solver trivially produced an inequality witness — `VERIFICATION FAILED`
landed in milliseconds purely because the read itself was inconsistent.
Replacing `called.call("")` with a `// no-op` would produce the **same
FAILED result**: the bug being "detected" had nothing to do with any
side-effect of the external call.

## Why it now reports SUCCESSFUL

After the fix both reads alias the same `this->$balance` SSA cell.
For this assert to *correctly* report FAILED we need a path that
actually mutates `$balance` between the two reads.  In the current model:

- `called.call("")` is a low-level call **with no value** — it does not
  move ETH out of `this` by itself.
- ESBMC's `transfer`/`send` builtins **do** model balance correctly
  (`solidity_convert_call.cpp::get_transfer_definition`, lines ~3039–3270:
  `this.$balance -= _val; target.$balance += _val;` plus payable
  receive/fallback dispatch).  But neither `callwrap` nor `modifystorage`
  invokes them, so no internal path can change `this->$balance`.
- In `--unbound` mode `called.call("")` is replaced by
  `_ESBMC_Nondet_Extcall_<self>` (nondet re-entry into our own public
  functions).  Re-entry into either `callwrap` or `modifystorage` still
  cannot change `$balance` — they have no value-transferring code.
- ESBMC does **not** over-approximate "the external callee may credit
  our balance via selfdestruct / coinbase / arbitrary transfer to us".

So under the present model the assert is provably true; reporting
SUCCESSFUL is internally consistent.

## What it would take to lift this back to CORE

Pick **one** (each is an over-approximation that closes the soundness
gap for this property):

1. **Nondet `$balance += delta` after every unbound external call**:
   inject a single `this->$balance = this->$balance + nondet_uint();`
   step into the `_ESBMC_Nondet_Extcall_<self>` harness so the model
   admits "callee might have transferred ETH to us".  Restores FAILED
   here at the cost of new false positives in any test that asserts
   balance invariants across unbound calls.
2. **Nondet payable receive in the re-entry harness**: equivalent
   effect, narrower blast radius — only contracts whose callee surface
   *could* receive ETH get the nondet credit.
3. **Selfdestruct/coinbase model**: thread the existing `selfdestruct`
   builtin so the destroyed contract's balance is credited to a chosen
   recipient (currently modelled as `exit(0)`, see
   `solidity_builtins.c::selfdestruct`).  Plus a coinbase nondet credit
   per transaction.  Largest scope, most accurate.

(1) and (2) are part of the prerequisite work for **TOD-Balance**, which
needs `$balance` to be writable by the external-call surface anyway.
Lifting `call_3` back to CORE is tracked alongside that work.

## Test args

`--max-k-step 3` was added so the test terminates within the 60s ctest
timeout.  Without an explicit step bound, k-induction otherwise loops
forever searching for a counterexample that under the current model
does not exist.
