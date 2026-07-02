property: `vm.warp(123)` then `block.timestamp==123`.
classification: correct-test (real forge_truth = PASS once vm.warp is honored)
forge_truth: PASS
esbmc_expected: CORRECT (VERIFICATION SUCCESSFUL)
CONSERVATIVENESS ANCHOR: vm.warp is currently an unmodeled no-op, so timestamp stays nondet and ESBMC
  reports a FALSE VERIFICATION FAILED. The never-false-WRONG invariant REQUIRES CORRECT here.
current_status: KNOWNBUG — ESBMC outputs FAILED (wrong). Flips to CORE when the vm.* recognition +
  hard-taint gate (design-plan F1.0/F1.c) OR real vm.warp modeling lands.
complexity: {cheatcodes:1(vm.warp), assertions:1(native), PUT:no, call-depth:0, revert-data:no}
invocation-note: will migrate to `--foundry` once the flag exists; uses --contract/--focus-function today.
