// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// WHAT THIS ISOLATES: at which moment does the post-state assertion ladder read
// a state variable on a path that ENDS IN A REVERT?
//
// WHY IT MATTERS. On the corpus, FarmingPool.setDistributor path enc=13 exits
// through a rollback, and one run printed BOTH of these:
//
//   WARNING: path enc=13 exits through a ROLLBACK revert. The rollback IS
//            modelled, so the values below are the correctly RESTORED state
//   ✗ FAILED: 'setDistributor:path:13#eq__distributor at'   (post == pre refuted)
//   ✓ PASSED: 'setDistributor:path:13#ne__distributor at'   (post != pre holds)
//
// with the region pinning state._distributor to [0,0]. If the state really is
// restored, `post == pre` cannot be refuted. One of the two is wrong, and which
// one decides whether the defect is the warning's wording or the read point.
//
// ⛔ WHAT IS **NOT** ON THE TABLE. Moving the revert-path assertion to AFTER the
// failing operation. The assertion is planted before it on purpose: a revert
// undoes everything, so an assertion after it is unreachable on every input and
// the verifier reports it as holding -- which reads exactly like a proof that
// the path cannot be taken. That rule stays.
//
// PRE-REGISTERED READING, written before the run:
//   * ladder says `v: post == pre` HOLDS on the reverting path
//       -> the rollback IS in effect at the read point; the warning is truthful
//          and the corpus contradiction has some other cause, to be chased
//          separately.
//   * ladder says `v: post != pre` HOLDS and `post == pre` REFUTED
//       -> the ladder reads the value BETWEEN the write and the rollback. That
//          state exists in no test and on no chain, so every layer-2/3 rung on
//          a reverting path is a claim about an unobservable moment, and the
//          warning's "correctly RESTORED state" is false.
//
// NEGATIVE CONTROL, without which the run proves nothing: the NON-reverting
// path of the same unit must produce a DIFFERENT table. Both paths write `v`;
// only one rolls back. If the two tables are identical the ladder is not
// distinguishing them at all and neither reading above may be drawn.

contract N04_RollbackRead {
    uint256 public v;

    // x <= 100 : writes v and RETURNS      -> post != pre is the truth
    // x >  100 : writes v and REVERTS      -> on chain, v is restored, so the
    //                                         only truth a test can see is
    //                                         post == pre
    function setThenMaybeRevert(uint256 x) external {
        v = x;
        if (x > 100) {
            revert();
        }
    }
}
