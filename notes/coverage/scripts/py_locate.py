#!/usr/bin/env python3
"""Locate a literal (or a line) in a Python file and print the SMALLEST
enclosing AST node WHOLE.

The Python counterpart of cpp_locate.py, and it exists for the same reason:
this workspace bans grep, head, and offset reads because reasoning from a
matched prefix has produced four separate wrong conclusions here. Structured
location followed by a whole-node read is permitted, and is strictly better --
what you get back is a complete function or statement with its own indentation
intact, not N lines starting somewhere arbitrary.

Every occurrence is reported, so the count is a checksum over the file. A node
larger than the cap is NOT truncated: its span is printed and the reader is
told to raise the cap.

⛔ ZERO HITS IS A REPORTED RESULT, NOT SILENCE. "Not found" is also "spelled
differently" -- the exit code says which so a caller cannot read absence into
it.

usage:
    py_locate.py <file.py> --str <literal> [--cap N] [--out K]
    py_locate.py <file.py> --line <N> [--cap N] [--out K]

--out K asks for the K-th SMALLEST enclosing node instead of the smallest
(K counts from 0, so --out 0 is the default). The smallest node containing a
line is very often the one-line statement itself, and the question being asked
is usually about the loop or the branch AROUND it. Without this the only way
out was to raise --cap until some ancestor 800 lines wide became the "smallest
that fits", which prints a whole function to see six lines of context.
"""
import ast
import sys


def spans(tree):
    """[(start, end, kind, name)] for every node that has a line range."""
    out = []
    for node in ast.walk(tree):
        a = getattr(node, "lineno", None)
        b = getattr(node, "end_lineno", None)
        if a is None or b is None:
            continue
        name = getattr(node, "name", "")
        out.append((a, b, type(node).__name__, name))
    return out


def show(path, lines, all_spans, lineno, cap, out=0):
    enclosing = sorted([s for s in all_spans if s[0] <= lineno <= s[1]],
                       key=lambda s: s[1] - s[0])
    print("=" * 92)
    print("%s:%d -- %d enclosing node(s)" % (path, lineno, len(enclosing)))
    # The hit line ITSELF, always. A one-line statement -- `X = {...}` -- has no
    # enclosing node of two lines or more, so the block chooser below skipped
    # straight past it to some huge parent and printed nothing usable. Showing
    # the line costs one row and removes a whole class of "found it, could not
    # see it".
    print("%6d\t%s" % (lineno, lines[lineno - 1]))
    # The named definitions are what a reader orients by, so they are listed
    # even when a smaller expression node is the one printed.
    for a, b, kind, name in enclosing:
        if kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef"):
            print("    in %s %s (lines %d-%d)" % (kind, name, a, b))
    # Candidates are the multi-line enclosing nodes that fit, smallest first;
    # --out K steps outward through them. Nodes with an IDENTICAL span (an
    # `Expr` wrapping its `Call`, say) are collapsed, otherwise stepping out
    # once would reprint the same lines under a different node kind and K
    # would not mean "one level further out".
    cands, seen = [], set()
    for a, b, kind, name in enclosing:
        if b - a + 1 >= 2 and b - a + 1 <= cap and (a, b) not in seen:
            seen.add((a, b))
            cands.append((a, b, kind, name))
    if out >= len(cands) and cands:
        print("    --out %d asked for an enclosing node further out than the "
              "%d that fit the %d-line cap; showing the outermost that fits. "
              "Raise --cap to reach past it." % (out, len(cands), cap))
        out = len(cands) - 1
    chosen = cands[out] if cands else None
    if chosen is None:
        for a, b, kind, name in enclosing:
            print("    node %-18s lines %6d-%-6d (%d lines)"
                  % (kind, a, b, b - a + 1))
        print("    no enclosing node fits the %d-line cap; NOTHING printed "
              "rather than a truncated body. Re-run with a bigger --cap." % cap)
        return
    a, b, kind, name = chosen
    print("--- enclosing node #%d (of %d) within the %d-line cap: %s %s, "
          "lines %d-%d ---" % (out, len(cands), cap, kind, name, a, b))
    for k in range(a, b + 1):
        print("%6d\t%s" % (k, lines[k - 1]))


def main(argv):
    args = argv[1:]
    # ⛔ --help before anything opens a file. It used to fall through to
    # `open(args[0])` and die with a FileNotFoundError naming '--help', which
    # reads like the tool is broken rather than like the user asked for usage.
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    cap = 200
    if "--cap" in args:
        i = args.index("--cap")
        cap = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    out = 0
    if "--out" in args:
        i = args.index("--out")
        out = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    path = args[0]
    src = open(path, errors="replace").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    all_spans = spans(tree)

    if "--line" in args:
        n = int(args[args.index("--line") + 1])
        show(path, lines, all_spans, n, cap, out)
        return 0
    if "--str" in args:
        want = args[args.index("--str") + 1]
        hits = [k + 1 for k, ln in enumerate(lines) if want in ln]
        print("%s: %d line(s) contain %r -> %s"
              % (path, len(hits), want,
                 ", ".join(str(h) for h in hits) or "none"))
        if not hits:
            print("⛔ ZERO occurrences. NOT evidence the concept is absent -- "
                  "try a shorter distinctive substring.")
            return 2
        for h in hits:
            show(path, lines, all_spans, h, cap, out)
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
