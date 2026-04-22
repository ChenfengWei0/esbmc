"""solc subprocess driver: compile check + AST parse.

Assumes a single `solc` binary on PATH pinned to 0.8.x (project policy
— see `feedback_upgrade_to_08.md`).

Note on Phase 1 dependency resolution. The implementation spec's first
draft called for parsing solc's textual error messages to identify
missing identifiers and pull them into the closure. In practice the
AST-walker path (phases/phase1_closure._add_dependencies) is strictly
better: it is deterministic, independent of solc's stderr format, and
handles overloads by fully-qualified id. The closure loop therefore
doesn't inspect stderr at all — it only cares whether `compile` returned
`ok`. We keep stderr on the result for logging and debugging.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class CompileResult:
    ok: bool
    stderr: str
    ast: Optional[dict] = None


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
        ast = _parse_ast_stdout(res.stdout) if ok and check_ast else None
        return CompileResult(ok=ok, stderr=stderr, ast=ast)


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
