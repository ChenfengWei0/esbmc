#!/usr/bin/env python3
"""Stage mechanically recoverable CE anchors from existing RQ1 PUT tests.

This is deliberately a manifest-only tool.  It never edits a Solidity source,
put.json, or result.json.  A candidate is accepted only when one parameterless
``test_cov_*`` has one adjacent claim matching the requested row and the
source contains no existing anchor.  Ambiguous or parameterised tests are
reported as refusals rather than guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


FUNC_RE = re.compile(r"\bfunction\s+(test_cov_[A-Za-z0-9_$]*)\s*\(([^)]*)\)")
CLAIM_RE = re.compile(r"^\s*//\s*claim:\s*(\S.*?)\s*$", re.M)
ANCHOR_RE = re.compile(r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_$]*)\s*\(")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function_span(source: str, start: int) -> tuple[int, int] | None:
    opening = source.find("{", start)
    if opening < 0:
        return None
    depth = 0
    quoted: str | None = None
    escaped = False
    for pos in range(opening, len(source)):
        char = source[pos]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quoted:
                quoted = None
            continue
        if char in "\"'":
            quoted = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    return None


def adjacent_claims(source: str, start: int) -> list[str]:
    prefix = source[:start].splitlines()
    lines: list[str] = []
    while prefix and (not prefix[-1].strip() or prefix[-1].lstrip().startswith("//")):
        lines.append(prefix.pop())
    return CLAIM_RE.findall("\n".join(reversed(lines)))


def candidate(path: Path, expected_claim: str | None = None,
              expected_unit: str | None = None) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8", errors="replace")
    result: dict[str, Any] = {
        "source": str(path), "source_sha256": sha(path), "status": "refused",
    }
    anchors = ANCHOR_RE.findall(source)
    if anchors:
        result["reason"] = "anchor-already-present"
        result["existing_anchors"] = sorted(set(anchors))
        return result
    matches = list(FUNC_RE.finditer(source))
    if not matches:
        result["reason"] = "no-test_cov-function"
        return result
    if len(matches) != 1:
        result["reason"] = "ambiguous-test_cov-function-count"
        result["test_cov_count"] = len(matches)
        return result
    match = matches[0]
    test = match.group(1)
    if match.group(2).strip():
        result["reason"] = "test_cov-is-parameterised"
        result["test"] = test
        return result
    claims = adjacent_claims(source, match.start())
    if len(claims) != 1:
        result["reason"] = "test_cov-claim-is-not-unique"
        result["test"] = test
        result["claims"] = claims
        return result
    if expected_claim is not None and claims[0] != expected_claim:
        result["reason"] = "claim-does-not-match-requested-identity"
        result.update(test=test, claims=claims, expected_claim=expected_claim)
        return result
    body = source[match.start():function_span(source, match.start())[1]]  # type: ignore[index]
    if expected_unit and not re.search(r"\b" + re.escape(expected_unit) + r"\b", body):
        result.update(reason="test_cov-does-not-call-requested-unit", test=test,
                      claim=claims[0], expected_unit=expected_unit)
        return result
    digest = hashlib.sha256((str(path) + "\0" + body).encode()).hexdigest()[:16]
    anchor = "test_ce_anchor_" + digest
    result.update(status="ready", test=test, claim=claims[0], anchor_test=anchor,
                  function_sha256=hashlib.sha256(body.encode()).hexdigest(),
                  anchor_source=body.replace("function " + test + "(",
                                             "function " + anchor + "(", 1))
    return result


def rows_from_result(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result_path in root.rglob("result.json"):
        try:
            document = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        put = document.get("put") if isinstance(document, dict) else None
        tests = put.get("valid_tests", []) if isinstance(put, dict) else []
        for test in tests:
            if not isinstance(test, dict) or not test.get("is_put"):
                continue
            source_path = Path(str(test.get("file") or ""))
            if not source_path.is_file() or source_path.suffix != ".sol":
                continue
            claims = test.get("claims") or test.get("claim")
            if isinstance(claims, list):
                claim = claims[0] if len(claims) == 1 else None
            else:
                claim = claims
            rows.append({"result": str(result_path), "identity": {
                "path_function": test.get("path_function"), "unit": test.get("unit"),
                "enc": test.get("enc"), "piece": test.get("piece")},
                         "source": str(source_path), "claim": claim,
                         "unit": test.get("unit")})
    # A source can be present in multiple result snapshots.  Keep the first
    # occurrence so the manifest remains one-to-one and deterministic.
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["source"], row)
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--claim")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    items: list[dict[str, Any]] = []
    if args.source:
        items = [{"source": str(path), "claim": args.claim, "unit": None}
                 for path in args.source]
    else:
        items = rows_from_result(args.root)
    if args.limit:
        items = items[:args.limit]
    manifest = {"schema": "veriput-rq1-mechanical-anchor-staging/v1",
                "read_only": True, "root": str(args.root), "rows": []}
    for item in items:
        path = Path(item["source"])
        row = candidate(path, item.get("claim"), item.get("unit"))
        row.update(result=item.get("result"), identity=item.get("identity"))
        manifest["rows"].append(row)
    counts: dict[str, int] = {}
    for row in manifest["rows"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    manifest["counts"] = counts
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
