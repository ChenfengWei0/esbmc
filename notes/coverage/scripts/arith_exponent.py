#!/usr/bin/env python3
"""How many CHECKED arithmetic operations sit inside each unit, per benchmark.

WHY THIS NUMBER DECIDES A DESIGN. The implementation plan's decision C1 lowers a
checked `+`/`-`/`*` to a real two-exit branch (`if (overflow) revert`), so that
an overflow becomes its own enumerated path with a revert exit and the
non-overflow path's counterexample can no longer wrap. That is the right
SEMANTICS -- it is what the chain does, and it is the only thing that stops the
emitter shipping a test the chain rejects (notes/coverage/poc/D10_WrapNotPanic).

It is also a MULTIPLIER on the path count, and the path count is already the
binding resource: the collector caps a unit at 10000 paths, and on st1inch 12
units had call points WITHDRAWN from their path identity to fit that cap. Each
checked operation that becomes a decision doubles the paths through the code
after it. So "is C1 affordable" is not a matter of taste; it is 2^k for a k this
script measures.

WHAT IT COUNTS, and what it deliberately does not:

  * BinaryOperation nodes with operator +, -, * whose result type is an integer.
    Division and modulo are excluded: they already have their own zero check and
    C1 is about the wrap, not about div-by-zero.
  * `unchecked { }` blocks are EXCLUDED, because inside them the chain wraps too
    and the model already agrees. Detected structurally by walking with an
    `unchecked` depth, not by a text search.
  * Compound assignments (`x += y`) count once -- solc represents them as
    Assignment with operator `+=`, which is a separate node kind, so they are
    matched separately rather than missed.
  * Only functions that are UNITS (public or external) are reported on their
    own, but the count for a unit INCLUDES the internal functions it calls,
    because internal calls are physically inlined into the caller's path
    identity. Without that the number would understate the exponent by exactly
    the part path coverage inlines.

Usage: arith_exponent.py <flat.solast> [<flat.solast> ...]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ARITH_BIN = {"+", "-", "*"}
ARITH_ASSIGN = {"+=", "-=", "*="}


def walk(node, unchecked_depth, hit, calls, fn_defs):
    """Recursive descent that tracks `unchecked` depth structurally."""
    if isinstance(node, list):
        for x in node:
            walk(x, unchecked_depth, hit, calls, fn_defs)
        return
    if not isinstance(node, dict):
        return

    nt = node.get("nodeType")
    if nt == "UncheckedBlock":
        unchecked_depth += 1
    elif nt == "BinaryOperation" and unchecked_depth == 0:
        op = node.get("operator")
        ts = (node.get("typeDescriptions") or {}).get("typeString", "")
        if op in ARITH_BIN and ("int" in ts):
            hit.append((op, node.get("src")))
    elif nt == "Assignment" and unchecked_depth == 0:
        op = node.get("operator")
        ts = (node.get("typeDescriptions") or {}).get("typeString", "")
        if op in ARITH_ASSIGN and ("int" in ts):
            hit.append((op, node.get("src")))
    elif nt == "FunctionCall":
        exp = node.get("expression") or {}
        ref = exp.get("referencedDeclaration")
        if isinstance(ref, int):
            calls.add(ref)

    for k, v in node.items():
        if k == "nodeType":
            continue
        if isinstance(v, (dict, list)):
            walk(v, unchecked_depth, hit, calls, fn_defs)


def collect_functions(node, out):
    if isinstance(node, list):
        for x in node:
            collect_functions(x, out)
        return
    if not isinstance(node, dict):
        return
    if node.get("nodeType") == "FunctionDefinition" and node.get("id") is not None:
        out[node["id"]] = node
    for k, v in node.items():
        if isinstance(v, (dict, list)):
            collect_functions(v, out)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        # `solc --ast-compact-json` prefixes the JSON with `======= <file>
        # =======` and `JSON AST:` banners, so the file is not parseable as-is.
        # Cut at the first `{` at column 0 rather than skipping a fixed number
        # of lines: the banner's shape depends on the solc version, and a
        # fixed skip would silently mis-parse under a different one.
        raw = Path(path).read_text()
        start = raw.find("\n{")
        ast = json.loads(raw[start + 1:] if start >= 0 else raw)
        fn_defs = {}
        collect_functions(ast, fn_defs)

        own = {}   # id -> (name, visibility, direct hits, direct calls)
        for fid, fn in fn_defs.items():
            hit, calls = [], set()
            walk(fn.get("body"), 0, hit, calls, fn_defs)
            own[fid] = (fn.get("name") or fn.get("kind") or "?",
                        fn.get("visibility"), hit, calls)

        def total(fid, seen):
            """Direct hits plus every reachable internal callee's, once each.
            Recursion is cut by `seen`, which also means a recursive function's
            own body is counted once -- an undercount, and the safe direction
            for an argument that C1 is EXPENSIVE."""
            if fid in seen or fid not in own:
                return 0
            seen.add(fid)
            _n, _v, hit, calls = own[fid]
            return len(hit) + sum(total(c, seen) for c in calls)

        rows = []
        for fid, (name, vis, hit, calls) in own.items():
            if vis not in ("public", "external"):
                continue
            k = total(fid, set())
            rows.append((k, name, len(hit)))
        rows.sort(reverse=True)

        print(f"===== {Path(path).name}")
        if not rows:
            print("  no public/external function found")
            continue
        print(f"  {'k':>4}  {'2^k':>12}  {'direct':>6}  unit")
        for k, name, direct in rows:
            print(f"  {k:>4}  {2 ** k if k < 60 else '>1e18':>12}  "
                  f"{direct:>6}  {name}")
        worst = rows[0]
        print(f"  ----- worst unit `{worst[1]}`: k={worst[0]}, so C1 multiplies "
              f"its path count by up to {2 ** worst[0] if worst[0] < 60 else '>1e18'}")


if __name__ == "__main__":
    sys.exit(main())
