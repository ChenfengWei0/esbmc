# Multi-dim clone KNOWNBUG — raw-C/C++ repros

Both programs mirror the same shape as the parent `contract.sol`
(multi-dim outer array + fresh outer alloc_array + per-slot arrcpy of
inner rows + read through nested pointer).  Both PASS in plain C/C++
mode, proving that the backend (goto-symex + any SMT solver in our
support set) handles the pattern correctly.

```
esbmc raw_u256_c.c   --unwind 4 --no-unwinding-assertions --no-standard-checks --force-malloc-success --bitwuzla
esbmc raw_u256_cpp.cpp --unwind 4 --no-unwinding-assertions --no-standard-checks --force-malloc-success --bitwuzla
```

The Solidity version of the same shape FAILS under `--bound --cvc5`
(and `--bitwuzla`).  The regression test.desc here is KNOWNBUG; it
flips to CORE once the Solidity-frontend specific trigger is
resolved.
