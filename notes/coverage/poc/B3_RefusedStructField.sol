// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES candidate 2 of the farming/deposit coords-gate: branching on a
/// SCALAR FIELD of a struct that also contains a mapping, i.e. a field the
/// coordinate classifier REFUSES because of the shape of its container.
///
/// WHERE IT COMES FROM. FarmingPool holds `FarmingLib.Data private _farm`,
/// whose two members are FarmAccounting.Info (four scalars) and
/// UserAccounting.Info (two scalars plus `mapping(address => int256)
/// corrections`). deposit reaches
///     if (duration > 0) { ... }                 // FarmAccounting, uint32
///     if (block.timestamp != checkpoint) { ... } // UserAccounting, uint40
/// and BOTH live inside `_farm`. On the deposit arms `state._farm` is REFUSED
/// as a coordinate, so neither field constrains the region and neither shows up
/// in the payload the shrink loop compares.
///
/// WHY D02_StructWithMapping DOES NOT COVER THIS. D02 declares the shape but
/// its only unit, `setFeeReceiver`, never reads a struct field -- it was built
/// to reproduce a z3 `datatype is not well-founded` error, and it branches on
/// `msg.sender == owner`. Nothing there asks whether a scalar field of such a
/// struct can be a coordinate.
///
/// EXPECTED, `probe`: the driver prints `state.d.duration` (or the whole
/// `state.d`) among its REFUSED coordinates, and the two paths' counterexamples
/// then agree on `amount` and `msg.sender` -- the coordinate gate.
///
/// ⛔ HOW TO RUN IT, and the first attempt got this wrong. Under `--focus
/// --max-tx 1` NOTHING CAN WRITE `d.duration`: the dispatcher is restricted to
/// the unit itself, so the field keeps its zero-value at entry, only the `else`
/// side of `d.duration > 0` is reachable, and the driver witnesses TWO paths
/// where ctrlParam witnesses three. With no sibling to separate from, the one
/// live path certifies trivially -- and reading that as "the refused field is
/// harmless" would be reading a run in which the discriminating object was
/// never built. The measured tell is the path count, not the verdict:
///     ctrlParam  3 witnessed paths   1 certified / 2 not
///     ctrlPlain  2 witnessed paths   1 certified / 1 not
///     probe      2 witnessed paths   1 certified / 1 not
/// so ctrlPlain and probe are not comparable to ctrlParam either.
/// REQUIRED: `--scope setDuration,probe --max-tx 2`, which is the only shape
/// that buys a witness for state a different function has to write.
///
/// TWO NEGATIVE CONTROLS, because two different things could be responsible:
///   * `ctrlPlain` branches on a scalar state variable of the same type that is
///     NOT inside any struct. If this one also gets refused, the mapping in the
///     container is not what matters -- the type is.
///   * `ctrlParam` branches on the parameter. It must certify; if it does not,
///     the run measured the harness rather than the candidate.
contract B3_RefusedStructField {
    struct D {
        uint32 duration;
        mapping(address => int256) corrections;
    }

    D internal d;
    uint32 public plainDuration;
    uint256 public tag;

    /// ⛔ THE FIRST VERSION TOOK ONE PARAMETER AND WROTE BOTH FIELDS FROM IT:
    ///     function setDuration(uint32 v) { d.duration = v; plainDuration = v; }
    /// Under that setter the REFUSED field is exactly recoverable from a FREE
    /// coordinate, so `probe` certified with regions IDENTICAL, value for
    /// value, to `ctrlPlain`'s -- and reading that as "the refusal is harmless"
    /// would be reading a discriminator whose two outcomes were made equal by
    /// construction. The two fields are now written from INDEPENDENT
    /// parameters, so no free coordinate determines d.duration.
    function setDuration(uint32 v, uint32 w) external {
        d.duration = v;
        plainDuration = w;
    }

    function probe(uint256 amount) external {
        if (d.duration > 0) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    function ctrlPlain(uint256 amount) external {
        if (plainDuration > 0) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    function ctrlParam(uint256 amount) external {
        if (amount > 100) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
