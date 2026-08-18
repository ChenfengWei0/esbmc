#!/usr/bin/env python3
"""Shared path guards for VeriPUT benchmark runners."""

from __future__ import annotations

import os
from pathlib import Path


VERIPUT_ROOT = Path(os.environ.get("VERIPUT_ROOT", Path.cwd()))


def is_under(path: Path, root: Path) -> bool:
    """Return whether path resolves under root without requiring it to exist."""
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def protected_roots(veriput_root: Path = VERIPUT_ROOT) -> list[Path]:
    """Directories that benchmark runners may read but must not write."""
    root = Path(veriput_root)
    return [root / "Datasets", root / "Results"]


def allowed_result_roots(veriput_root: Path = VERIPUT_ROOT) -> list[Path]:
    """Result subtrees that are generated tool artifacts, not shared inputs."""
    root = Path(veriput_root)
    return [
        root / "Results" / "RQ1" / "VeriPUT",
        root / "Results" / "RQ1_KInduction_Fair600",
    ]


def ensure_path_not_protected(label: str, path: str | os.PathLike[str] | None):
    """Refuse a planned write path under the shared VeriPUT data roots."""
    if not path:
        return
    p = Path(path)
    for allowed in allowed_result_roots():
        if is_under(p, allowed):
            return
    for root in protected_roots():
        if is_under(p, root):
            raise ValueError(
                f"{label} must not be under {root}; choose an external path")


def argv_value(argv: list, flag: str) -> str | None:
    """Read an argparse-style option value from `--flag value` or `--flag=value`."""
    prefix = flag + "="
    for pos, arg in enumerate(argv):
        text = str(arg)
        if text == flag:
            if pos + 1 >= len(argv):
                raise ValueError(f"{flag} is missing a value")
            return str(argv[pos + 1])
        if text.startswith(prefix):
            return text[len(prefix):]
    return None
