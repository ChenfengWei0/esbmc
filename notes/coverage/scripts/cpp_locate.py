#!/usr/bin/env python3
"""Report EVERY occurrence of a literal across a C/C++ tree, with the smallest
enclosing brace block for each.

---- WHY A TOOL RATHER THAN A SEARCH ---------------------------------------

This workspace bans every addressable-subset read: no grep, no head, no offset
Read. The reason is not style -- four separate wrong conclusions in this project
came from reasoning about a file from a matched prefix. What is permitted is
STRUCTURED LOCATION FOLLOWED BY A WHOLE-BLOCK READ, and that is what this is:

  * every occurrence is reported, never the first N, so the count is a checksum
    over the tree rather than the result of a search that stopped early;
  * what is printed around a hit is a COMPLETE brace block, chosen as the
    smallest one that fits the cap -- never a fixed window of lines, which is a
    prefix by another name;
  * a block too large to print whole is NOT truncated. Its span is printed and
    the reader is told to raise the cap, because a half-printed function is
    exactly the artefact the ban exists to prevent.

The brace walk is literal- and comment-aware, so a `{` inside a string or a
`//` comment cannot open a block.

⛔ A ZERO-HIT RESULT IS A RESULT, AND IT IS PRINTED AS ONE. "I did not find the
string" is not "the string is not there" -- it is also "I spelled it wrong" or
"it is split across source lines by the compiler's line breaks". The exit code
distinguishes them so a caller cannot read silence as absence.

usage:
    cpp_locate.py <literal> <root> [more roots ...] [--cap N] [--ext .cpp,.h]
    cpp_locate.py <literal> <root> --list-only     # file:line only, no blocks
"""
import os
import sys

EXTS = (".cpp", ".c", ".h", ".hpp", ".cc", ".inl")


def blocks_of(src):
    """[(start_line, end_line, depth)] for every brace block, literal-aware."""
    stack, blocks = [], []
    i, n, line, state = 0, len(src), 1, None
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "\n":
            line += 1
            if state == "line-comment":
                state = None
            i += 1
            continue
        if state == "line-comment":
            i += 1
            continue
        if state == "block-comment":
            if ch == "*" and nxt == "/":
                state = None
                i += 2
                continue
            i += 1
            continue
        if state in ('"', "'"):
            if ch == "\\":
                i += 2
                continue
            if ch == state:
                state = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            state = "line-comment"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            state = "block-comment"
            i += 2
            continue
        if ch in ('"', "'"):
            state = ch
            i += 1
            continue
        if ch == "{":
            stack.append(line)
            i += 1
            continue
        if ch == "}":
            if stack:
                blocks.append((stack.pop(), line, len(stack)))
            i += 1
            continue
        i += 1
    return blocks


def walk(roots, exts):
    for root in roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "build", "CMakeFiles")]
            for fn in sorted(filenames):
                if fn.endswith(exts):
                    yield os.path.join(dirpath, fn)


def by_line(path, lineno, cap):
    """Print the SMALLEST complete brace block containing `lineno`.

    Structured location by line, followed by a WHOLE-block read -- not an
    offset read. The distinction is the point: an offset read gives you N lines
    starting somewhere arbitrary, so the top and bottom of what you see are cut
    mid-construct and you reason about a fragment. This gives you a syntactic
    unit with both its braces, or it gives you nothing and says so.
    """
    src = open(path, errors="replace").read()
    lines = src.splitlines()
    if not 1 <= lineno <= len(lines):
        print("%s has %d lines; %d is outside it" % (path, len(lines), lineno))
        return 2
    blocks = blocks_of(src)
    enclosing = sorted([b for b in blocks if b[0] <= lineno <= b[1]],
                       key=lambda b: b[1] - b[0])
    print("%s:%d is inside %d nested block(s):" % (path, lineno, len(enclosing)))
    for a, b, d in enclosing:
        print("    lines %6d-%-6d (%5d lines, depth %d)" % (a, b, b - a + 1, d))
    for a, b, _d in enclosing:
        if b - a + 1 <= cap:
            print("--- smallest block within the %d-line cap: %d-%d ---"
                  % (cap, a, b))
            for k in range(a, b + 1):
                print("%6d\t%s" % (k, lines[k - 1]))
            return 0
    print("every enclosing block exceeds the %d-line cap; NOTHING printed "
          "rather than a truncated body. Re-run with --cap." % cap)
    return 3


def main(argv):
    args = argv[1:]
    cap, exts, list_only = 200, EXTS, False
    if "--line" in args:
        i = args.index("--line")
        n = int(args[i + 1])
        args = args[:i] + args[i + 2:]
        if "--cap" in args:
            j = args.index("--cap")
            cap = int(args[j + 1])
            args = args[:j] + args[j + 2:]
        return by_line(args[0], n, cap)
    if "--list-only" in args:
        list_only = True
        args.remove("--list-only")
    if "--cap" in args:
        i = args.index("--cap")
        cap = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if "--ext" in args:
        i = args.index("--ext")
        exts = tuple(args[i + 1].split(","))
        args = args[:i] + args[i + 2:]
    if len(args) < 2:
        sys.exit(__doc__)
    want, roots = args[0], args[1:]

    hits, scanned = [], 0
    for path in walk(roots, exts):
        scanned += 1
        try:
            src = open(path, errors="replace").read()
        except OSError:
            continue
        if want not in src:
            continue
        lines = src.splitlines()
        blocks = None
        for k, ln in enumerate(lines):
            if want not in ln:
                continue
            if blocks is None:
                blocks = blocks_of(src)
            hits.append((path, k + 1, lines, blocks))

    print("scanned %d file(s) under %s for %r"
          % (scanned, ", ".join(roots), want))
    print("%d occurrence(s) in %d file(s)"
          % (len(hits), len({h[0] for h in hits})))
    for path, ln, _lines, _b in hits:
        print("    %s:%d" % (path, ln))
    if not hits:
        print("⛔ ZERO occurrences. This is NOT evidence the concept is absent: "
              "the literal may be spelled differently, or split across source "
              "lines. Try a shorter distinctive substring before concluding.")
        return 2
    if list_only:
        return 0

    for path, ln, lines, blocks in hits:
        enclosing = sorted([b for b in blocks if b[0] <= ln <= b[1]],
                           key=lambda b: b[1] - b[0])
        print("=" * 92)
        print("%s:%d -- %d nested block(s)" % (path, ln, len(enclosing)))
        chosen = None
        for a, b, d in enclosing:
            if b - a + 1 <= cap:
                chosen = (a, b, d)
                break
        if chosen is None:
            for a, b, d in enclosing:
                print("    lines %6d-%-6d (%d lines, depth %d)"
                      % (a, b, b - a + 1, d))
            print("    every enclosing block exceeds the %d-line cap; NOTHING "
                  "printed rather than a truncated body. Re-run with --cap." % cap)
            continue
        a, b, _d = chosen
        for k in range(a, b + 1):
            print("%6d\t%s" % (k, lines[k - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
