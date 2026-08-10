#!/usr/bin/env python3
"""Fail-closed preflight for the external Codex host bridge."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
from pathlib import Path


COMMANDS = {
    "spawn": "VERIPUT_HOST_SPAWN_COMMAND",
    "close": "VERIPUT_HOST_CLOSE_COMMAND",
    "interrupt": "VERIPUT_HOST_INTERRUPT_COMMAND",
    "resource": "VERIPUT_HOST_RESOURCE_COMMAND",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing-interrupt", action="store_true")
    args = parser.parse_args()
    checks = {}
    missing = []
    for label, env_name in COMMANDS.items():
        raw = os.environ.get(env_name, "")
        tokens = shlex.split(raw) if raw else []
        executable = shutil.which(tokens[0]) if tokens else None
        ready = bool(tokens and executable)
        checks[label] = {"env": env_name, "configured": bool(raw),
                         "executable": executable, "ready": ready}
        if not ready and not (label == "interrupt" and args.allow_missing_interrupt):
            missing.append(env_name)
    doc = {"schema": "veriput-rq1-host-preflight/v1", "ready": not missing,
           "checks": checks, "missing": missing}
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
