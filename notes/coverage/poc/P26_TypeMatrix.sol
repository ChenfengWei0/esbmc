// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ISOLATES: the INPUT TYPE dimension. Before this file the whole set used
// `uint256` almost exclusively, plus one `int256` and one fixed array — so the
// coordinate machinery was being judged on a single type.
//
// Each function takes one type and branches on it, so the region for that path
// is a set over exactly that type and nothing else is in the way.
//
// WHAT EACH ONE IS FOR, and every entry names a mechanism that exists or is
// known to be missing:
//
//   bool       S5 exists specifically for it: `bool_type2t::get_width()`
//              returns 8, so the domain has to be special-cased to [0,1] or the
//              interval machinery reasons over a byte. Implemented, never
//              exercised on a hand-written contract.
//   address    the equality-constrained coordinate. An address pinned by `==`
//              was recorded degenerating into ~160 rounds of bisection; on the
//              real contract that instance turned out to be `immutable` and so
//              was pinned rather than generalised. Here it is a plain
//              parameter, so there is nowhere for it to hide.
//   uint8      bit width decides the ladder's range and `path_cov_fits_type`.
//              A ladder built for 256 bits on an 8-bit coordinate is the
//              cheapest place for an off-by-one to be visible.
//   uint128    a width that is neither the default nor tiny.
//   int128     signed AND narrow. `coord_expressible` refuses signedbv today,
//              so the expected result is a loud refusal naming the coordinate.
//   bytes32    fixed-size bytes: ordered comparison is not meaningful, so an
//              interval over it is either wrong or must be refused.
//   enum       lowered to uint8 with a RESTRICTED domain — a value outside the
//              range is not merely unlikely, it reverts.
//
// EXPECTED per function: paths witnessed, and a region whose SHAPE suits the
// type — an interval for the numeric ones, `{0,1}` or a pin for bool, a pin or
// an explicit refusal for address and bytes32.
//
// THE FAILURE TO WATCH FOR is silent widening: a bytes32 or address coordinate
// treated as a 256-bit integer and handed an interval. Every point in it walks
// the path, so it certifies; the emitted test then fuzzes over a range that is
// meaningless for the type, and nothing in the report says so.
//
// Deliberately ABSENT, because the renderer reports them UNSUPPORTED and the
// question there is whether the refusal is clean rather than what the region
// looks like: `bytes`, `string`, dynamic arrays, `struct`. Those belong in a
// separate refusal-quality contract.
contract P26_TypeMatrix {
    enum Kind {
        Low,
        Mid,
        High
    }

    uint256 public tag;

    function takeBool(bool b) external {
        if (b) {
            tag = 1;
        } else {
            tag = 2;
        }
    }

    function takeAddress(address a) external {
        require(a != address(0));
        if (a == address(0xBEEF)) {
            tag = 3;
        } else {
            tag = 4;
        }
    }

    function takeU8(uint8 x) external {
        if (x > 200) {
            tag = 5;
        } else {
            tag = 6;
        }
    }

    function takeU128(uint128 x) external {
        if (x > 1000) {
            tag = 7;
        } else {
            tag = 8;
        }
    }

    function takeI128(int128 x) external {
        if (x < -1000) {
            tag = 9;
        } else {
            tag = 10;
        }
    }

    function takeBytes32(bytes32 b) external {
        if (b == bytes32(uint256(1))) {
            tag = 11;
        } else {
            tag = 12;
        }
    }

    function takeEnum(Kind k) external {
        if (k == Kind.High) {
            tag = 13;
        } else {
            tag = 14;
        }
    }
}
