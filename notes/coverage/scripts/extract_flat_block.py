#!/usr/bin/env python3
"""Write out one original file's block from a flattened .sol, verbatim.

The corpus runs on FLATTENED inputs, and for st1inch the flat is the only real
Solidity on disk -- `notes/coverage-comparison/st1inch/src/artifacts/contracts/
*.sol/` are hardhat build-artifact DIRECTORIES holding JSON, not sources.

So to look at one contract's code you have to cut its `// File <path>` block out
of the flat. This does that by reading the WHOLE flat and using the same block
parser the measurement pipeline uses (`ast_decisions.parse_flat_file_blocks`), so
the extract and the denominator agree about where a block starts and ends.

The output carries a header naming the flat, the original path, and the FLAT LINE
RANGE -- because every number this project reports about that code (canonical
decision lines, the tool's own warnings, the `decisions[].line` field) is in flat
coordinates, and an extract without them is a file you cannot cross-reference.

Usage:
    python3 extract_flat_block.py <flat.sol> --list
    python3 extract_flat_block.py <flat.sol> <marker-substring> <out.sol>
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ast_decisions import parse_flat_file_blocks  # noqa: E402


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    flat = Path(argv[1])
    if not flat.exists():
        sys.exit(f"missing {flat}")
    blocks = parse_flat_file_blocks(flat)
    lines = flat.read_text().splitlines()

    if argv[2] == "--list":
        print(f"## {len(blocks)} block(s) in {flat}\n")
        for s, e, m in blocks:
            print(f"    {s:>6}-{e:<6} ({e - s + 1:>5} lines)  {m}")
        return 0

    if len(argv) < 4:
        sys.exit(__doc__)
    want, out = argv[2], Path(argv[3])
    hits = [b for b in blocks if b[2] and want in b[2]]
    if not hits:
        sys.exit(f"no block matches {want!r}. Run with --list to see them all -- "
                 f"an empty extract is not an empty file.")
    if len(hits) > 1:
        sys.exit("that substring matches several blocks, and picking one would "
                 "be a guess:\n  " + "\n  ".join(m for _, _, m in hits))
    s, e, marker = hits[0]
    body = lines[s - 1:e]
    header = [
        f"// EXTRACTED from {flat}",
        f"// original path : {marker}",
        f"// FLAT LINES    : {s}-{e}   ({e - s + 1} lines)",
        "//",
        "// Every number this project reports about this code is in FLAT",
        "// coordinates -- canonical decision lines, the tool's own warnings, and",
        "// the report's `decisions[].line`. To convert a line N in THIS file to a",
        f"// flat line, add {s - 1}.",
        "",
    ]
    out.write_text("\n".join(header + body) + "\n")
    print(f"wrote {out}  ({e - s + 1} lines, flat {s}-{e})  <- {marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
