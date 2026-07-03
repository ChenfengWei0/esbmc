# Foundry cheatcode approximation — implementation spec (for Codex review)

Corrects v4. The user's rule: we do NOT model cheatcodes precisely and we do NOT
report inconclusive — we **over-approximate**. Two explicit modes, user must pick
one for Foundry input or ESBMC refuses. Precise modeling of a cheatcode is only an
optional optimization (fewer spurious FAILEDs), never a prerequisite for a useful
result.

Grounded in real code: `solidity_convert_call.cpp` (handle_foundry_cheatcode ~
:820-1000), `solidity_convert_expr.cpp` (member-call dispatch ~:3778-3860,
cheatcode interception), `solidity_convert.cpp` (feature-gate prescan ~:207-214),
`solidity_language.cpp` / `esbmc_parseoptions` (options), and real forge-std
v1.16.2 (`interface VmSafe` + `interface Vm is VmSafe`).

## 1. Two modes + refuse-to-verify

Option: `--foundry-cheatcode-approx {over|prune}` (single option, two values).
- Detection of "Foundry input": the AST prescan (same place as
  `uses_revert_observation`, solidity_convert.cpp:207) sets `is_foundry_unit` when
  the source references the `Vm`/`VmSafe` interface, the cheatcode address
  `0x7109709ECfa91a80626fF3989D68f67F5b1DD12D`, or inherits forge-std `Test`.
- Enforcement: if `is_foundry_unit && !option set` → **hard error, refuse to
  verify**: "Foundry cheatcodes detected. Choose an approximation posture:
  --foundry-cheatcode-approx over (sound SUCCESSFUL, possibly-spurious FAILED) or
  prune (never false-FAILED, possibly-vacuous SUCCESSFUL)." No silent default.
- Non-Foundry Solidity is unaffected (option ignored when `!is_foundry_unit`).

## 2. Gate scope (fix Codex blocker #1 + major #3)

The cheatcode interception must fire for a call whose receiver is the cheatcode
handle, recognized by ANY of:
- base contract type name `== "Vm"` **OR `== "VmSafe"`** (v4 only checked "Vm";
  StdUtils uses `VmSafe constant vm` → escaped);
- any interface transitively inheriting `VmSafe` (walk linearizedBaseContracts);
- the receiver resolving to the cheatcode address constant `0x7109...12D`.
Indirect/function-pointer invocation of a cheatcode (solidity_convert_expr.cpp
:2941/:2995 lower these to nondet): treat a call through a function-typed value
whose type is a `Vm`/`VmSafe` member as a cheatcode too; if that is not
detectable, it falls to nondet-return which is a valid over-approx in `over` mode
but an UNSOUND no-op in `prune` mode → in `prune` mode, an undetected cheatcode
escape must be caught by a conservative backstop (see §5).

## 3. Disposition of a recognized cheatcode call

Partition by which interface declares the function (from the bundled Vm.sol):

| kind | over mode | prune mode |
|---|---|---|
| **Modeled set** (warp/roll/assume/expectRevert/… conformance-tested) | precise model (same in both) | precise model |
| **VmSafe / view-pure cheatcode**, unmodeled (toString, computeCreateAddress, sign, envUint, parseJson, …) | **nondet return only, NO state havoc** (they cannot mutate observable state) | `ASSUME(false)` prune |
| **Vm / state-mutating cheatcode**, unmodeled (store, etch, deal, mockCall, prank, roll, …) | **HAVOC set (see §4) + nondet return** | `ASSUME(false)` prune |

VmSafe-vs-Vm is read from the bundled interface (function's declaring interface /
stateMutability), not guessed.

## 4. The HAVOC set for `over` mode (the crux — attack this)

When an unmodeled **state-mutating** cheatcode is reached, over-approximation must
include every state the real cheatcode could have produced. Proposed havoc set:

- **Env globals**: `msg_sender, msg_value, tx_origin, tx_gasprice, block_timestamp,
  block_number, block_basefee, block_chainid, block_coinbase, block_difficulty,
  block_prevrandao` ← nondet-assign each.
- **Balances**: the EOA balance map + tracked contracts' `$balance` ← nondet.
- **Target contract storage**: the receiver-under-test contract's state vars
  (`_ESBMC_Object_<C>` fields, incl. mappings/arrays) ← havoc.
- **Return value** (if any) ← nondet.

Open questions for review (soundness of SUCCESSFUL depends on these):
1. **Cross-contract storage**: `vm.store(otherAddr, …)` / `vm.etch` can mutate
   ANY account, not just the receiver-under-test. Must `over` havoc ALL tracked
   contracts' storage (and unknown addresses)? If yes it's very coarse (most
   FAILEDs spurious); if no, SUCCESSFUL is unsound for cross-account cheatcodes.
   Candidate: havoc all tracked `_ESBMC_Object_*` + env + balances; document that
   storage of *untracked* addresses is already nondet on read.
2. **Is field-level havoc reachable via existing machinery?** ESBMC has a havoc
   helper for modified-set (per memory `reference_field_level_modified_set`); can
   we reuse it to havoc a contract's whole state, or must we emit per-field nondet
   assigns? Which is cheaper/correct?
3. **`mockCall`/`expectRevert`/`expectEmit`** change *future call outcomes*, not
   current storage. Over-approxing them by havocing storage is wrong-shaped.
   mockCall → the mocked call should return nondet (already the unbound default);
   expectEmit/expectCall → they assert on *subsequent* calls; over-approx =
   drop the expectation (no constraint) → sound for SUCCESSFUL (weaker property).
   Does dropping an expectEmit make a passing test spuriously pass? (It removes a
   check the test intended → over-approx SUCCESSFUL could hide that the event was
   never emitted. Is that acceptable, or must expectEmit be modeled/tainted?)

## 5. prune-mode backstop + assertions (always)

- **Assertions are NEVER approximated.** `assertEq/assertTrue/assertApproxEq*/…`
  (both StdAssertions and the `vm.assertApproxEq*` cheatcode forms) always lower to
  a real `assert(<comparison>)` in BOTH modes (handle_forge_std_assert). They are
  the property under test, not a cheatcode effect. `assertApproxEqAbs/Rel` need the
  tolerance comparison modeled (|a-b| <= tol, or *rel). Codex #6 fix.
- **prune-mode escape backstop**: any cheatcode call that reaches the nondet-no-op
  external path WITHOUT being recognized (VmSafe/indirect escape) would be an
  unsound no-op in prune mode. Backstop: in prune mode, gate on the receiver's
  interface-origin, and if a call's callee is declared in Vm/VmSafe but slipped the
  member gate, still prune. If undetectable → documented residual (prune mode is
  the "never false-FAILED" mode, so a missed prune = a possible vacuous pass, which
  is prune-mode's known posture anyway).

## 6. Result reporting posture (per mode)

The result banner must state the posture so a SUCCESSFUL/FAILED is not misread:
- `over`: on SUCCESSFUL print "(sound over-approximation: holds under all cheatcode
  behaviors)"; on FAILED print "(may be spurious — cheatcode over-approximation
  admits states real forge would not; confirm with `forge test`)".
- `prune`: on SUCCESSFUL print "(bounded: paths through unmodeled cheatcodes were
  pruned; not a full proof)"; on FAILED print "(real up to modeled cheatcodes)".

## 7. Options doc / helper documentation

Rigorously document both postures + the havoc set in: the `--help` text, a comment
block in the cheatcode C model helper, and `docs/claude/solidity/`. No mode is a
default; the choice and its soundness consequence are the user's.

## Review targets (attack hardest)
- §4 havoc set soundness: does `over` SUCCESSFUL actually hold under EVERY real
  cheatcode behavior, given cross-account store/etch and expectEmit/mockCall shapes?
- §2/§5: can any cheatcode reach a silent no-op in EITHER mode (VmSafe, indirect,
  low-level, inside a helper body)?
- §3 VmSafe=view assumption: is EVERY VmSafe function truly state-non-mutating from
  the contract's observable perspective, or do some have observable effects?
