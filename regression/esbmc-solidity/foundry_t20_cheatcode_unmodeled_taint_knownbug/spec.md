property: `vm.fee(7)` then `block.basefee == 7`.
classification: correct-test (real forge_truth = PASS once vm.fee is honored)
forge_truth: PASS
esbmc_expected: CORRECT (VERIFICATION SUCCESSFUL)
CONSERVATIVENESS ANCHOR: vm.fee is an unmodeled cheatcode, so block.basefee stays nondet and ESBMC
  reports a FALSE VERIFICATION FAILED. The never-false-WRONG invariant REQUIRES CORRECT here.
current_status: KNOWNBUG — ESBMC outputs FAILED (wrong). Flips to CORE when the vm.* hard-taint gate
  (design-plan F1.0) OR real vm.fee modeling lands. Replaces the former vm.warp anchor now that
  vm.warp is modeled.
complexity: {cheatcodes:1(vm.fee, UNMODELED), assertions:1(native), PUT:no, call-depth:0}
