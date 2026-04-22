"""Append-only manifest writer.

Every phase flushes its fragment immediately. If the minimiser is
killed mid-run, the partial manifest is still useful for diagnosis.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class ManifestBuilder:
    out_path: Path
    oracle: Dict[str, Any]
    input_meta: Dict[str, Any]
    phase_0: Dict[str, Any] = field(default_factory=dict)
    phase_1: Dict[str, Any] = field(default_factory=dict)
    phase_2: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)

    def flush(self) -> None:
        root: Dict[str, Any] = {
            "schema_version": 1,
            "tool": "esbmc-minimise",
            "oracle": self.oracle,
            "input": self.input_meta,
        }
        if self.phase_0:
            root["phase_0"] = self.phase_0
        if self.phase_1:
            root["phase_1"] = self.phase_1
        if self.phase_2:
            root["phase_2"] = self.phase_2
        if self.result:
            root["result"] = self.result
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text(json.dumps(root, indent=2) + "\n")

    def record_phase(self, which: str, payload: Dict[str, Any]) -> None:
        if which == "phase_0":
            self.phase_0 = payload
        elif which == "phase_1":
            self.phase_1 = payload
        elif which == "phase_2":
            self.phase_2 = payload
        else:
            raise ValueError(f"unknown phase: {which!r}")
        self.flush()

    def finalise(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.flush()
