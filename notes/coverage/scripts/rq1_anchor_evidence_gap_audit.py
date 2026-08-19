#!/usr/bin/env python3
"""Audit retained CE-anchor evidence without changing canonical artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from rq1_put_ce_anchor_backfill import _claim_ce_matches
from rq1_final_test_inventory import obligations

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE.parent / "rq1_put_ce_anchor_backfill.frozen.json"
DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_OUTPUT = HERE.parent / "rq1_anchor_evidence_gap_audit.json"
DEFAULT_LEDGER = HERE.parent / "rq1_ce_obligations.frozen.json"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_hash(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(rendered.encode("utf-8"))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        value = json.loads(text)
        values = value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        values = []
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [value for value in values if isinstance(value, dict)]


def _same_piece(identity_piece: str, candidate: Any) -> bool:
    normalized = "" if candidate is None else str(candidate)
    return identity_piece == normalized or (identity_piece == "" and normalized == "1")


def _subject_dir(result_root: Path, case: str) -> Path:
    benchmark, subject = case.split("/", 1)
    return result_root / benchmark / "subjects" / subject


def _claims(document: Any) -> Iterable[dict[str, Any]]:
    if isinstance(document, dict):
        for claim in document.get("claims") or []:
            if isinstance(claim, dict):
                yield claim


def _journal_witnesses(document: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(document, dict):
        return
    for witness in (document.get("witnesses") or {}).values():
        if isinstance(witness, dict):
            yield witness


def _base_path_id(value: Any) -> str:
    return str(value).split("#", 1)[0]


def _source_tests(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    source = raw.decode("utf-8", errors="replace")
    result = []
    matches = list(re.finditer(r"\bfunction\s+(test_[A-Za-z0-9_]+)\s*\(", source))
    for index, match in enumerate(matches):
        prefix = source[:match.start()].splitlines()
        adjacent = []
        while prefix and (not prefix[-1].strip() or prefix[-1].lstrip().startswith("//")):
            adjacent.append(prefix.pop())
        metadata = "\n".join(reversed(adjacent))
        claims = re.findall(r"^\s*//\s*claim:\s*(.+?)\s*$", metadata, re.M)
        fingerprints = re.findall(
            r"^\s*//\s*witness-fingerprint-sha256:\s*([0-9a-f]{64})\s*$",
            metadata,
            re.M,
        )
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[match.start():end]
        result.append({
            "path": str(path),
            "source_sha256": _sha256_bytes(raw),
            "test": match.group(1),
            "function_sha256": _sha256_bytes(body.encode("utf-8")),
            "claims": claims,
            "fingerprints": fingerprints,
        })
    return result


def _index_subject(subject_dir: Path) -> dict[str, Any]:
    index: dict[str, Any] = {
        "claims": defaultdict(list),
        "journals": defaultdict(list),
        "certifications": defaultdict(list),
        "tests": defaultdict(list),
        "file_counts": Counter(),
    }
    for path in subject_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "cov-report.json":
            index["file_counts"]["reports"] += 1
            for claim in _claims(_load_json(path)):
                key = (str(claim.get("path_function")), str(claim.get("path_id")))
                index["claims"][key].append({
                    "path": str(path),
                    "file_sha256": _sha256_bytes(path.read_bytes()),
                    "value_sha256": _json_hash(claim),
                    "value": claim,
                })
        elif path.name == "cov-ce-journal.json":
            index["file_counts"]["journals"] += 1
            for witness in _journal_witnesses(_load_json(path)):
                key = (str(witness.get("path_function")),
                       _base_path_id(witness.get("path_id")))
                index["journals"][key].append({
                    "path": str(path),
                    "file_sha256": _sha256_bytes(path.read_bytes()),
                    "value_sha256": _json_hash(witness),
                })
        elif path.name == "certify-results.jsonl":
            index["file_counts"]["certifications"] += 1
            for record in _load_records(path):
                details = (record.get("stage2_observed_certified_details")
                           or record.get("certified_details") or {})
                if not isinstance(details, dict):
                    continue
                for enc, detail in details.items():
                    if not isinstance(detail, dict) or detail.get("verdict") != "CERTIFIED":
                        continue
                    key = (str(record.get("path_function")), str(record.get("unit")),
                           str(enc))
                    index["certifications"][key].append({
                        "path": str(path),
                        "file_sha256": _sha256_bytes(path.read_bytes()),
                        "record_sha256": _json_hash(record),
                        "value_sha256": _json_hash(detail),
                        "value": detail,
                        "piece": detail.get("piece"),
                    })
        elif path.name.endswith(".cov.t.sol"):
            index["file_counts"]["emitted_sources"] += 1
            for test in _source_tests(path):
                for claim_label in test.pop("claims"):
                    index["tests"][claim_label].append(dict(test))
    return index


def _summarize_candidates(candidates: list[dict[str, Any]], hash_field: str) -> dict[str, Any]:
    hashes = sorted({str(candidate[hash_field]) for candidate in candidates})
    return {
        "physical_count": len(candidates),
        "distinct_content_count": len(hashes),
        "content_sha256": hashes,
        "candidates": [{key: value for key, value in candidate.items() if key != "value"}
                       for candidate in candidates],
    }


def _audit_entry(entry: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    identity = [str(value) for value in entry["identity"]]
    _case, path_function, unit, enc, piece = identity
    claims = list(index["claims"].get((path_function, enc), []))
    journals = list(index["journals"].get((path_function, enc), []))
    certifications = [candidate for candidate in index["certifications"].get(
        (path_function, unit, enc), []) if _same_piece(piece, candidate.get("piece"))]
    label = f"{path_function}:path:{enc}"
    tests = list(index["tests"].get(label, []))

    claim_summary = _summarize_candidates(claims, "value_sha256")
    journal_summary = _summarize_candidates(journals, "value_sha256")
    cert_summary = _summarize_candidates(certifications, "value_sha256")
    test_summary = _summarize_candidates(tests, "function_sha256")
    compatible_pairs = []
    for claim in claims:
        for certification in certifications:
            matches, _reason = _claim_ce_matches(
                claim["value"], certification["value"].get("ce") or {})
            if matches:
                compatible_pairs.append((claim, certification))
    chains = []
    for claim, certification in compatible_pairs:
        claim_fingerprint = claim["value"].get("foundry_testcase_fingerprint_sha256")
        for test in tests:
            test_fingerprints = test.get("fingerprints") or []
            # Modern evidence uses the solver fingerprint. Legacy evidence is
            # admissible only when neither side claims to carry one; the exact
            # adjacent claim identity and all content hashes remain sealed.
            if claim_fingerprint:
                if test_fingerprints != [claim_fingerprint]:
                    continue
                binding = "solver-witness-fingerprint"
            else:
                if test_fingerprints:
                    continue
                binding = "legacy-adjacent-claim"
            chains.append({
                "claim_sha256": claim["value_sha256"],
                "certification_sha256": certification["value_sha256"],
                "test_function_sha256": test["function_sha256"],
                "binding": binding,
            })
    chain_seals = sorted({_json_hash(chain) for chain in chains})

    gaps = []
    if claim_summary["distinct_content_count"] == 0:
        gaps.append("missing-claim")
    elif claim_summary["distinct_content_count"] > 1:
        gaps.append("multiple-claim-content")
    if cert_summary["distinct_content_count"] == 0:
        gaps.append("missing-cert")
    elif cert_summary["distinct_content_count"] > 1:
        gaps.append("multiple-cert-content")
    if test_summary["distinct_content_count"] == 0:
        gaps.append("missing-exact-test")
    elif test_summary["distinct_content_count"] > 1:
        gaps.append("multiple-emitted-source-content")
    if claims and certifications and not compatible_pairs:
        gaps.append("claim-cert-ce-mismatch")
    if claims and certifications and tests and not chains:
        gaps.append("claim-test-binding-mismatch")
    if len(chain_seals) > 1:
        gaps.append("multiple-exact-evidence-chains")
    recoverable = len(chain_seals) == 1
    return {
        "identity": identity,
        "status": "pure-log-closable" if recoverable else "not-pure-log-closable",
        "gaps": gaps,
        "exact_chain_count": len(chain_seals),
        "exact_chain_sha256": chain_seals,
        "claims": claim_summary,
        "journals": journal_summary,
        "certifications": cert_summary,
        "emitted_tests": test_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()
    manifest = _load_json(args.manifest)
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        parser.error("manifest has no entries list")

    by_subject: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("identity"), list):
            parser.error("manifest contains a malformed entry")
        by_subject[_subject_dir(args.result_root, str(entry["identity"][0]))].append(entry)

    rows = []
    scanned_files = Counter()
    for subject_dir in sorted(by_subject, key=str):
        index = _index_subject(subject_dir)
        scanned_files.update(index["file_counts"])
        rows.extend(_audit_entry(entry, index) for entry in by_subject[subject_dir])
    rows.sort(key=lambda row: row["identity"])
    row_map = {tuple(row["identity"]): row for row in rows}
    generalized, unresolved, no_put = obligations(args.result_root)
    ledger_document = _load_json(args.ledger)
    ledger = {
        tuple(str(value) for value in identity)
        for identity in ((ledger_document or {}).get("obligations") or [])
        if isinstance(identity, list) and len(identity) == 5
    }
    current = generalized | unresolved | no_put
    ledger_only = ledger - current
    unresolved_statuses = Counter(
        row_map[identity]["status"] if identity in row_map else "physical-row-absent"
        for identity in unresolved)
    # The frozen 1808 ledger contains 18 historical generalized identities
    # whose physical PUT row is no longer readable. They remain unresolved
    # evidence obligations and cannot be recovered by a filesystem-only scan.
    unresolved_statuses["ledger-only-unreadable"] += len(ledger_only)
    unresolved_blockers = Counter()
    for identity in unresolved:
        row = row_map.get(identity)
        if row is None or row["status"] == "pure-log-closable":
            continue
        gaps = set(row["gaps"])
        if "missing-claim" in gaps:
            blocker = "missing-claim"
        elif "missing-cert" in gaps:
            blocker = "missing-cert"
        elif "missing-exact-test" in gaps:
            blocker = "missing-exact-test"
        elif {"claim-cert-ce-mismatch", "claim-test-binding-mismatch"} & gaps:
            blocker = "identity-or-ce-binding-mismatch"
        elif "multiple-exact-evidence-chains" in gaps:
            blocker = "ambiguous-exact-chain"
        else:
            blocker = "other"
        unresolved_blockers[blocker] += 1
    unresolved_blockers["ledger-only-unreadable"] += len(ledger_only)
    statuses = Counter(row["status"] for row in rows)
    gap_counts = Counter(gap for row in rows for gap in row["gaps"])
    output = {
        "schema": "veriput-rq1-anchor-evidence-gap-audit/v1",
        "scope": {
            "manifest": str(args.manifest),
            "manifest_sha256": _sha256_bytes(args.manifest.read_bytes()),
            "result_root": str(args.result_root),
            "identity": ["case", "path_function", "unit", "enc", "piece"],
            "subjects": len(by_subject),
            "obligations": len(entries),
            "read_only_scan": True,
        },
        "counts": {
            **dict(sorted(statuses.items())),
            "gap_kinds": dict(sorted(gap_counts.items())),
            "scanned_files": dict(sorted(scanned_files.items())),
            "partitions": {
                "current_generalized": len(generalized),
                "current_unresolved_physical": len(unresolved),
                "current_no_put": len(no_put),
                "frozen_ledger": len(ledger),
                "ledger_only_unreadable": len(ledger_only),
            },
            "unresolved_evidence": dict(sorted(unresolved_statuses.items())),
            "unresolved_exclusive_blockers": dict(sorted(unresolved_blockers.items())),
        },
        "ledger_only_identities": [list(identity) for identity in sorted(ledger_only)],
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(output["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
