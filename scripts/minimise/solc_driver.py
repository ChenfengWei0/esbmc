"""solc subprocess driver: compile check + AST parse + error extraction.

The Phase 1 closure loop calls `compile` to check whether the current
candidate source compiles; on failure it uses `parse_missing_symbols`
to decide which identifiers to pull in next. The driver assumes a
single `solc` binary on PATH, pinned to 0.8.x (project policy — see
`feedback_upgrade_to_08.md`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class MissingSymbol:
    """An identifier referenced by retained code but not yet defined."""

    name: str
    source_file: str
    source_line: int

    def __hash__(self) -> int:
        return hash((self.name, self.source_file, self.source_line))


@dataclass
class CompileResult:
    ok: bool
    stderr: str
    ast: Optional[dict] = None
    missing: List[MissingSymbol] = field(default_factory=list)


_IDENT_NOT_FOUND = re.compile(
    r"Error:\s*(?:Identifier not found or not unique|Undeclared identifier)[.:]"
    r"[^\n]*\n\s*-->\s*([^:]+):(\d+):\d+[^\n]*\n(?:[^\n]*\n)+?"
    r"\s*\d+\s*\|\s*[^\n]*\n\s*\|\s*\^+\s*([A-Za-z_][A-Za-z0-9_]*)?",
    re.MULTILINE,
)

# Solidity error format is: "Error: ...\n  --> file:line:col\n   |\n L | code\n   | ^^^"
# We scan for these blocks and extract the identifier at the caret.
_ERROR_BLOCK = re.compile(
    r"Error:\s*(?P<msg>.*?)\n\s*-->\s*(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+)\n"
    r"\s*\|\n"
    r"\s*\d+\s*\|\s*(?P<code>[^\n]*)\n"
    r"\s*\|\s*(?P<caret>\^+)",
    re.MULTILINE | re.DOTALL,
)


def _extract_missing(stderr: str) -> List[MissingSymbol]:
    """Return identifiers that solc reported as undefined."""

    missing: List[MissingSymbol] = []
    for m in _ERROR_BLOCK.finditer(stderr):
        msg = m.group("msg").strip()
        if "Identifier not found" not in msg and "Undeclared identifier" not in msg:
            continue
        code = m.group("code")
        col = int(m.group("col"))
        caret = m.group("caret")
        # Caret length tells us how many chars of `code` make up the symbol.
        start = col - 1
        end = start + len(caret)
        if end > len(code):
            end = len(code)
        ident = code[start:end].strip()
        if not ident:
            # Fall back: nearest identifier in the code slice
            ident_match = re.search(r"[A-Za-z_][A-Za-z0-9_]*", code[start:])
            if not ident_match:
                continue
            ident = ident_match.group(0)
        missing.append(
            MissingSymbol(
                name=ident,
                source_file=m.group("file"),
                source_line=int(m.group("line")),
            )
        )
    return missing


class SolcDriver:
    def __init__(self, solc_binary: Optional[str] = None) -> None:
        self.binary = solc_binary or shutil.which("solc") or "solc"

    def version(self) -> str:
        res = subprocess.run(
            [self.binary, "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("Version:"):
                return line.split("Version:", 1)[1].strip()
        return res.stdout.strip()

    def compile(self, sources: List[Path], check_ast: bool = True) -> CompileResult:
        """Run `solc --ast-compact-json` on the given sources."""

        cmd = [self.binary, "--ast-compact-json", *[str(s) for s in sources]]
        res = subprocess.run(cmd, capture_output=True, text=True)
        ok = res.returncode == 0
        stderr = res.stderr or ""
        ast = None
        missing: List[MissingSymbol] = []
        if ok and check_ast:
            ast = _parse_ast_stdout(res.stdout)
        else:
            missing = _extract_missing(stderr)
        return CompileResult(ok=ok, stderr=stderr, ast=ast, missing=missing)


_AST_HEADER = re.compile(r"^=======\s*(?P<path>.+?)\s*=======\s*$", re.MULTILINE)


def _parse_ast_stdout(stdout: str) -> Optional[dict]:
    """solc --ast-compact-json output is a sequence of blocks:

        ======= <filename> =======
        { ...compact JSON AST... }

    Split on the header line, keyed by the filename only (no path), to
    match the AST against sources supplied by short name.
    """

    asts: dict = {}
    headers = list(_AST_HEADER.finditer(stdout))
    for i, m in enumerate(headers):
        file_path = m.group("path").strip()
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(stdout)
        body = stdout[body_start:body_end].strip()
        if not body:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        from os.path import basename
        asts[basename(file_path)] = parsed
    return asts or None
