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

def walk(node, b2l, blocks, out):
    """Recursively walk a compact-JSON AST node, emitting one entry per
    decision point per METHODOLOGY.md §2.  `out[file]` is a set of flat
    line numbers (one per decision point; multiple decisions on the same
    line collapse to one entry per spec)."""
    if node is None: return
    if isinstance(node, list):
        for child in node: walk(child, b2l, blocks, out)
        return
    if not isinstance(node, dict): return

    nt = node.get("nodeType")
    src = node.get("src")
    line = src_to_line(b2l, src) if src else None

    if nt in DECISION_NODE_KINDS and line is not None:
        out[file_at_flat_line(blocks, line)].add(line)
    elif nt == "FunctionCall" and line is not None:
        callee = node.get("expression", {})
        if callee.get("nodeType") == "Identifier" and callee.get("name") in ("require", "assert"):
            out[file_at_flat_line(blocks, line)].add(line)
    elif nt == "BinaryOperation" and line is not None:
        if node.get("operator") in ("&&", "||"):
            out[file_at_flat_line(blocks, line)].add(line)

    for v in node.values():
        if isinstance(v, (list, dict)):
            walk(v, b2l, blocks, out)

# ---------- top-level driver ---------------------------------------------

def canonical_decisions(flat_path):
    """Return dict[original_file] -> set[(flat_line, kind)]."""
    b2l = byte_to_line(flat_path.read_bytes())
    blocks = parse_flat_file_blocks(flat_path)
    solast = flat_path.with_suffix(".sol.solast")
    if not solast.exists():
        solast = Path(str(flat_path) + ".solast")
    ast = extract_ast_json(solast)
    out = defaultdict(set)
    walk(ast, b2l, blocks, out)
    return dict(out), blocks

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
