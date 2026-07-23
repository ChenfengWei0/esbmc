# Re-entrancy is never allowed to complete, so the violation is missed

Re-pinned CORE -> KNOWNBUG. `97e37211bc` flipped both `reentrance_2` and
`reentrance_13` to CORE via `--incremental-bmc`; they no longer reproduce their
violation and report `VERIFICATION UNKNOWN` instead of `VERIFICATION FAILED`.
The regression predates 2026-07-11 (a static binary from that date behaves
identically) and had been masked by the local 120s regression timeout, which
fired before the UNKNOWN was ever printed.

## Root cause (measured, not inferred)

A re-entrant `Bank.withdraw` frame always gets `success == false` back from its
own `msg.sender.call{value: amount}("")`. It therefore reverts; the revert
propagates out through `$call#1`'s `aux49` (`RETURN !(bool)aux49`) and makes the
OUTER frame's `require(success)` fail too, so the outer frame reverts before
reaching its post-condition. The post-condition is consequently never evaluated
in a state where a nested withdraw has drained the contract -- which is exactly
the state the assertion is meant to catch.

Evidence, using a `uint private depth` counter incremented on entry to
`Bank.withdraw` (`--incremental-bmc --bound --cvc5`, ladder run to k=50):

| probe | assertion | placement | result |
|---|---|---|---|
| 15 | `assert(depth < 1)` | after `require(success)` | FAILED at k=1 — the counter works |
| 16 | `assert(depth < 2)` | after the call, BEFORE `require(success)` | FAILED at k=2 — a nested frame does run its own call and return |
| 14 | `assert(depth < 2)` | after `require(success)` | never violated to k=50 — no nested frame ever passes it |

Two further probes bound the diagnosis:

- Replacing the attacker's unbounded guard with a single-shot one
  (`bool done; if (!done) { done = true; target.withdraw(msg.value); }`) makes
  ESBMC report the REAL violation -- `new_contract_balance ==
  old_contract_balance - amount` -- at k=2. Keeping the balance guard and
  adding the flag (`if (address(target).balance > 0 && !done)`) also finds it.
  So re-entrancy detection itself works; what fails is the unbounded-recursion
  shape.
- Plain BMC with an assumption-based recursion cut
  (`--unwind 3/5 --no-unwinding-assertions`) also finds nothing, so the path is
  genuinely infeasible in the model rather than merely cut away by the bound.

Ingredients that ARE individually reachable (each shown by an `assert(false)` or
inverted-assertion probe): `receive()` is entered; the guarded
`target.withdraw(msg.value)` branch is entered; that call returns normally; the
path after `require(success)` is reachable; `address(this).balance >= 2*amount`
and `== 2*amount` hold at withdraw entry; and Bank's assertions are in the claim
set. Only the COMBINATION "a nested frame completed" + "outer call succeeded" is
unreachable.

Note when reading probes that place `assert(false)` inside `receive()`: it is an
external payable function, so the dispatcher can call it as a TOP-LEVEL
transaction. Such a probe proves top-level reachability, not re-entrancy; the
depth counter above is what distinguishes the nested frame.

## Why the nested call always fails: the handle never sees the drain

The attacker re-enters only while `address(target).balance > 0`, so the
recursion can terminate cleanly only once that balance reaches 0. It never
does. Instrumenting `receive()` as

```solidity
uint before = address(target).balance;
if (before > 0) {
    target.withdraw(msg.value);
    assert(address(target).balance == before);   // never violated, k<=50
}
```

shows the balance read through the interface handle is UNCHANGED after a nested
`withdraw` returns -- even though `$call#1` debits `this->$balance` inside the
callee and `address(this).balance` does change there. The handle
(`IBank public target`, bound from a nondet address via `IBank(_target)`) and
the object the dispatch actually invokes are different model objects:
`IBank_withdraw` selects the callee by `base->_ESBMC_bind_cname` (`Bank` ->
`withdraw(&_ESBMC_Object_Bank, ...)`, `IBank` -> `withdraw(&_ESBMC_Object_IBank,
...)`), while `address(target).balance` dereferences the stored pointer -- which
a counterexample shows as `.target={ .pointer_object=nil, .pointer_offset=nil }`.

So the guard is effectively constant-true, the recursion runs to the unwind
bound on every path, no frame terminates cleanly, and the outer
`require(success)` never passes. That is the single mechanism behind every
observation above, and it also explains why the `bool done` attacker works: its
termination does not depend on observing the drain.

## Where to look next

`_ESBMC_sol_reverted_flag` propagation through the nested
`$call#1` -> `receive` -> `IBank_withdraw` -> `withdraw` cycle, and its
interaction with the recursion cut in `goto_symext::symex_function_call_code`
(src/goto-symex/symex_function.cpp:343-358). The cut has two different
semantics -- with unwinding assertions it emits `claim(false)` and then does
`cur_state->source.pc++`, i.e. SKIPS the call and continues; with
`--no-unwinding-assertions` it assumes the negated path guard, pruning instead.
Those two treatments leave the revert flag in different states, and the
recursion bound equals `max_unwind` (`get_unwind_recursion`, same file line 30),
so every k step re-runs the interaction at a new depth.

`reentrance_13` is the checks-effects-interactions-correct variant of the same
benchmark (balance decremented before the call) whose ghost-variable
post-condition is still broken by re-entrancy; it fails for the same reason and
is pinned alongside this one.
