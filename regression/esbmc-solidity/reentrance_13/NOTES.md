# Re-entrancy is never allowed to complete, so the violation is missed

Re-pinned CORE -> KNOWNBUG together with `reentrance_2`; see
`../reentrance_2/NOTES.md` for the full diagnosis and the probe table.

This variant is the checks-effects-interactions-CORRECT bank (the balance is
decremented before the external call). Its assertion is a ghost-variable
post-condition, `new_user_balance == old_user_balance - amount`, which a
re-entrant call still breaks: the nested frame decrements
`balances[msg.sender]` a second time, so the outer frame observes a value
lower than `old - amount`.

The attacker here has no guard at all -- `receive()` calls
`target.withdraw(msg.value)` unconditionally -- so the recursion is bounded only
by the unwind bound and by the `require`s inside `withdraw`. The failure mode is
the same as `reentrance_2`: a nested `withdraw` frame never gets `success ==
true` from its own low-level call, so it reverts, the revert propagates to the
caller's `require(success)`, and the post-condition is never evaluated on a
re-entered state. The run reports `VERIFICATION UNKNOWN` after exhausting the
`--incremental-bmc` ladder.
