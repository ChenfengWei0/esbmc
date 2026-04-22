"""Oracle tuple + comparison + JSON I/O.

The oracle is the equivalence criterion between verifier runs on the
original program and runs on successive reductions. Two oracles are
equal iff all four components match; the function-relative line offset
is stable under the reductions the minimiser performs because function
bodies are never modified.

See docs/minimise/algorithm.md §2 and scripts/minimise/ALGORITHM.md §2.1
for the motivation and the JSON schema.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class Oracle:
    """Equivalence oracle tuple `(c, f, t, ℓ)`."""

    contract: str
    function: str
    bug_type: str
    in_function_offset_lines: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TraceMethod:
    contract: str
    function: str

    def qualified(self) -> str:
        return f"{self.contract}.{self.function}"


@dataclass
class ViolationInfo:
    """Parsed `--dump-violation-info` payload."""

    oracle: Oracle
    original_function: Optional[str]
    trace_methods: List[TraceMethod]
    locked_symbols: List[str]
    source_files: List[str]
    violation_location: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ViolationInfo":
        data = json.loads(Path(path).read_text())
        if data.get("schema_version") != 1:
            raise ValueError(
                f"Unsupported --dump-violation-info schema version: "
                f"{data.get('schema_version')!r}"
            )
        if not data.get("violated", False):
            raise ValueError(
                "ViolationInfo: the referenced run did not report a "
                "violation; oracle cannot be extracted."
            )
        oracle = Oracle(
            contract=data["oracle"]["contract"],
            function=data["oracle"]["function"],
            bug_type=data["oracle"]["bug_type"],
            in_function_offset_lines=int(
                data["oracle"]["in_function_offset_lines"]
            ),
        )
        trace = [
            TraceMethod(tm["contract"], tm["function"])
            for tm in data.get("trace_methods", [])
        ]
        return cls(
            oracle=oracle,
            original_function=data.get("original_function"),
            trace_methods=trace,
            locked_symbols=list(data.get("locked_symbols", [])),
            source_files=list(data.get("source_files", [])),
            violation_location=dict(data.get("violation_location", {})),
        )


def oracles_match(a: Oracle, b: Oracle) -> bool:
    """Component-wise equality. Used as the reduction-step oracle check."""

    return a == b
