#!/usr/bin/env python3
"""
ast_decisions.py -- canonical-decision counter per METHODOLOGY.md §2.

Walks the solc compact-JSON AST of a flat .sol file, identifies every
branching construct, and buckets them by the ORIGINAL source file they
belong to via `// File <path>` markers in the flat.

Output: dict[original_file_path] -> set[(flat_line, decision_kind)]

Run as a CLI for diagnostics:
  python3 ast_decisions.py <flat.sol>
"""
import json, re, sys
from collections import defaultdict
from pathlib import Path

# ---------- AST extraction ------------------------------------------------

def extract_ast_json(solast_path):
    """solc compact-JSON output begins with a header line and a
    `======= <path> =======` separator, then the JSON.  Strip preamble."""
    text = solast_path.read_text()
    # Find the line that starts with '{' (the JSON root) on its own line.
    m = re.search(r"^\{", text, re.M)
    if not m:
        raise RuntimeError(f"No JSON AST in {solast_path}")
    return json.loads(text[m.start():])

# ---------- flat-line bookkeeping -----------------------------------------

def byte_to_line(flat_bytes):
    """Return a list `offsets` such that the line number (1-indexed) of
    byte position p is `bisect_right(offsets, p)`.

    IMPORTANT: solc's `src` field uses BYTE offsets, not character offsets.
    On UTF-8 source containing multi-byte chars (e.g. `'` U+2019, 3 bytes),
    char-based counting drifts by the number of multi-byte chars.  Pass
    the raw bytes from `Path.read_bytes()`, not a decoded str.
    """
    offsets = [0]
    NL = 0x0A
    for i, b in enumerate(flat_bytes):
        if b == NL:
            offsets.append(i + 1)
    return offsets

def line_of(b2l, byte_pos):
    import bisect
    return bisect.bisect_right(b2l, byte_pos)

def parse_flat_file_blocks(flat_path):
    """Return list of (start_flat_line, end_flat_line, original_file_marker)
    by scanning file-boundary comment markers in the flat.

    Supports both flattener formats:
      hardhat: `// File contracts/St1inch.sol`
      hardhat: `// File @openzeppelin/contracts/access/Ownable.sol@v5.1.0`
      forge:   `// src/Aqua.sol`
      forge:   `// lib/openzeppelin-contracts/contracts/utils/Errors.sol`
      forge:   `// contracts/libraries/ProxyHashLib.sol`

    Strip `@version` suffix on hardhat-style.
    """
    lines = flat_path.read_text().splitlines()
    hardhat_re = re.compile(r"^// File\s+(\S+?)(@[\w.\-]+)?\s*$")
    forge_re   = re.compile(r"^// (\S+\.sol)\s*$")
    blocks = []
    for i, raw in enumerate(lines, 1):
        m = hardhat_re.match(raw)
        if m:
            blocks.append([i, None, m.group(1)])
            continue
        m = forge_re.match(raw)
        if m:
            blocks.append([i, None, m.group(1)])
    # Set end lines
    eof = len(lines)
    for i, blk in enumerate(blocks):
        blk[1] = (blocks[i+1][0] - 1) if i+1 < len(blocks) else eof
    # Add an implicit pre-block (lines before any marker) if needed
    if not blocks or blocks[0][0] > 1:
        first = blocks[0][0] if blocks else eof
        blocks.insert(0, [1, first-1, "<preamble>"])
    return [tuple(b) for b in blocks]

def file_at_flat_line(blocks, flat_line):
    for start, end, name in blocks:
        if start <= flat_line <= end:
            return name
    return None

# ---------- decision walker ----------------------------------------------

DECISION_NODE_KINDS = {
    "IfStatement":      "if",
    "Conditional":      "ternary",
    "WhileStatement":   "while",
    "ForStatement":     "for",
    "DoWhileStatement": "do_while",
}

def src_to_line(b2l, src):
    """`src` field is 'start:length:source_idx'."""
    parts = src.split(":")
    return line_of(b2l, int(parts[0]))

def _owner_label(node):
    """`Contract.name (kind, visibility)` for a definition node, or None.

    The AST already knows, for every decision, which contract and which
    function encloses it, and whether that function is a constructor, a
    modifier, or an externally-callable method. The walker used to discard all
    of it and return bare line numbers, which pushes the question "is this
    decision reachable through a dispatcher at all?" out of the tool and into
    whoever is reading the line numbers by hand.

    That question is not a detail -- it separates the two kinds of shortfall
    this project keeps having to tell apart. A decision inside a `constructor`
    or inside a library with no dispatcher is UNREACHABLE BY CONSTRUCTION for a
    unit-based enumeration (a scope limit, stated), while a decision inside a
    public method that simply was not reached is a budget or a solver limit (a
    result). Both look identical as a bare flat line.
    """
    nt = node.get("nodeType")
    if nt == "ContractDefinition":
        # `contractKind` TRAVELS WITH THE NAME, and it has to.
        #
        # Visibility alone does not say whether a dispatcher can enter a
        # function: `ImmutablesLib.protocolFeeAmountCd` is declared `external`
        # and is still unreachable, because a LIBRARY has no dispatcher at all
        # -- which is the same fact `pathcov_collect.py` already records as
        # `skipped: "library-has-no-dispatcher"`. A consumer that buckets on
        # visibility and not on this would call eight such decisions a reach
        # failure on EscrowDst and EscrowSrc apiece, and print a real-shortfall
        # count of 4 where the true count is 0.
        kind = node.get("contractKind")
        return (f"{node.get('name')}[{kind}]" if kind and kind != "contract"
                else node.get("name"))
    if nt == "FunctionDefinition":
        kind = node.get("kind") or ("constructor" if node.get("isConstructor")
                                    else "function")
        name = node.get("name") or kind
        return f"{name} ({kind}, {node.get('visibility')})"
    if nt == "ModifierDefinition":
        return f"{node.get('name')} (modifier)"
    return None


def walk(node, b2l, blocks, out, owners=None, ctx=("", "")):
    """Recursively walk a compact-JSON AST node, emitting one entry per
    decision point per METHODOLOGY.md §2.  `out[file]` is a set of flat
    line numbers (one per decision point; multiple decisions on the same
    line collapse to one entry per spec).

    `owners`, when given, is filled with flat_line -> "Contract.fn (kind, vis)"
    for each decision, taken from the nearest enclosing definition nodes.
    """
    if node is None: return
    if isinstance(node, list):
        for child in node: walk(child, b2l, blocks, out, owners, ctx)
        return
    if not isinstance(node, dict): return

    nt = node.get("nodeType")
    src = node.get("src")
    line = src_to_line(b2l, src) if src else None

    label = _owner_label(node)
    if label is not None:
        ctx = (label, "") if nt == "ContractDefinition" else (ctx[0], label)

    hit = False
    if nt in DECISION_NODE_KINDS and line is not None:
        hit = True
    elif nt == "FunctionCall" and line is not None:
        callee = node.get("expression", {})
        if callee.get("nodeType") == "Identifier" and callee.get("name") in ("require", "assert"):
            hit = True
    elif nt == "BinaryOperation" and line is not None:
        if node.get("operator") in ("&&", "||"):
            hit = True

    if hit:
        out[file_at_flat_line(blocks, line)].add(line)
        if owners is not None and line not in owners:
            # FIRST WRITER WINS, and that is the innermost enclosing
            # definition: the walk descends, so by the time a decision node is
            # reached `ctx` already names the function it sits in. A later,
            # shallower write would relabel it with an outer scope -- and
            # because two decisions on the same line collapse to ONE entry by
            # spec, the second decision on a shared line must not be able to
            # overwrite the first one's owner either.
            c, f = ctx
            owners[line] = f"{c}.{f}" if c and f else (f or c or "<top level>")

    for v in node.values():
        if isinstance(v, (list, dict)):
            walk(v, b2l, blocks, out, owners, ctx)

# ---------- top-level driver ---------------------------------------------

def canonical_decisions_owned(flat_path):
    """`canonical_decisions`, plus flat_line -> enclosing definition label.

    Separate entry point rather than a changed return arity: the 2-tuple is
    consumed by collect.py and by the locked baseline pipeline, and the
    denominator those produce is LOCKED (METHODOLOGY 8.2 -- re-running must not
    change `branchesTotal`). Adding a third element to the existing function
    would make every caller's unpack a place the lock could break.
    """
    b2l = byte_to_line(flat_path.read_bytes())
    blocks = parse_flat_file_blocks(flat_path)
    solast = flat_path.with_suffix(".sol.solast")
    if not solast.exists():
        solast = Path(str(flat_path) + ".solast")
    ast = extract_ast_json(solast)
    out = defaultdict(set)
    owners = {}
    walk(ast, b2l, blocks, out, owners)
    return dict(out), blocks, owners


def canonical_decisions(flat_path):
    """Return dict[original_file] -> set[(flat_line, kind)]."""
    by_file, blocks, _ = canonical_decisions_owned(flat_path)
    return by_file, blocks

def cli():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <flat.sol>"); sys.exit(2)
    flat = Path(sys.argv[1])
    by_file, blocks = canonical_decisions(flat)
    print("FLAT BLOCKS:")
    for s, e, name in blocks:
        print(f"  [{s:>5}..{e:<5}]  {name}")
    print()
    print("CANONICAL DECISIONS PER ORIGINAL FILE:")
    total = 0
    for fname in sorted(by_file):
        n = len(by_file[fname])
        print(f"  {n:>4}  {fname}")
        total += n
    print(f"  ---- TOTAL: {total}")

if __name__ == "__main__":
    cli()
