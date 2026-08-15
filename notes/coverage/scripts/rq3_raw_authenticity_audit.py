#!/usr/bin/env python3
"""Partition live RQ3 raw-only tests by counterexample authenticity."""

# pylint: disable=missing-function-docstring,too-many-branches
# pylint: disable=too-many-locals,too-many-statements

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

DATASETS = ("bugfix124", "peer182", "real203")
ENV_ALIASES = {
    "msg_value": "msg.value",
    "msg_sender": "msg.sender",
    "msg_sig": "msg.sig",
    "msg_data": "msg.data",
    "tx_gasprice": "tx.gasprice",
    "tx_origin": "tx.origin",
    "block_basefee": "block.basefee",
    "block_blobbasefee": "block.blobbasefee",
    "block_chainid": "block.chainid",
    "block_coinbase": "block.coinbase",
    "block_difficulty": "block.difficulty",
    "block_gaslimit": "block.gaslimit",
    "block_number": "block.number",
    "block_prevrandao": "block.prevrandao",
    "block_timestamp": "block.timestamp",
}


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def normalized_value(value: Any) -> str:
    text = str(value)
    try:
        return str(int(text, 0))
    except (TypeError, ValueError):
        return text


def named_values(raw: Any) -> list[tuple[str, Any]]:
    if isinstance(raw, dict):
        return [(str(name), value) for name, value in raw.items()]
    if isinstance(raw, list):
        return [(str(item["name"]), item.get("value")) for item in raw
                if isinstance(item, dict) and item.get("name") is not None]
    return []


def claim_counterexample(claim: dict[str, Any]) -> dict[str, str]:
    ce: dict[str, str] = {}
    for section in ("env", "inputs", "extcall_returns"):
        for name, value in named_values(claim.get(section) or {}):
            ce[ENV_ALIASES.get(name, name)] = normalized_value(value)
    for name, value in named_values(claim.get("entry_storage") or {}):
        ce["state." + name] = normalized_value(value)
    if claim.get("return_value_known") and claim.get("return_value") is not None:
        ce["return"] = normalized_value(claim["return_value"])
    return ce


def normalized_ce(ce: dict[str, Any]) -> dict[str, str]:
    return {str(name): normalized_value(value) for name, value in ce.items()}


def concrete_rows(row: dict[str, Any], name: str) -> list[dict[str, Any]]:
    values = row.get(name) or row.get(name.replace("_artifacts", "_tests")) or []
    return [
        value for value in values if isinstance(value, dict) and (
            value.get("kind") == "concrete" or value.get("is_concrete") is True)
    ]


def artifact_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("file") or ""), str(row.get("test") or "")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(errors="replace"))


def exact_claims(report_path: Path, path_function: str, enc: int) -> list[dict[str, Any]]:
    if not report_path.is_file():
        return []
    report = load_json(report_path)
    return [
        claim for claim in report.get("claims") or []
        if isinstance(claim, dict) and claim.get("path_function") == path_function
        and str(claim.get("path_id")).split("#", 1)[0] == str(enc) and claim.get("status") == "F"
    ]


def certification_rows(path: Path, unit: str, path_function: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("unit") == unit and row.get("path_function") == path_function:
            rows.append(row)
    return rows


def details_for(rows: list[dict[str, Any]], enc: int) -> list[dict[str, Any]]:
    details = []
    for row in rows:
        for field in ("certified_details", "not_certified_details", "not_certified_ce_fallbacks"):
            for key, detail in (row.get(field) or {}).items():
                if (str(key).split("#", 1)[0] == str(enc) and isinstance(detail, dict)):
                    details.append({
                        "row": row,
                        "field": field,
                        "key": str(key),
                        "detail": detail,
                    })
    return details


def choose_detail(details: list[dict[str, Any]], stage2_source: str) -> dict[str, Any] | None:
    preferred = ("certified_details",) if stage2_source == \
        "certified-region-concrete-fallback" else (
            "not_certified_details", "not_certified_ce_fallbacks",
            "certified_details")
    for field in preferred:
        candidates = [item for item in details if item["field"] == field]
        if candidates:
            return candidates[0]
    return details[0] if details else None


def identity_key(identity: dict[str, Any]) -> tuple[Any, ...]:
    return (identity.get("dataset"), identity.get("case"), identity.get("path_function"),
            identity.get("unit"), identity.get("enc"), identity.get("piece"))


def current_raw_only(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    output = {}
    for dataset in DATASETS:
        for result_path in sorted((root / dataset / "subjects").glob("*/result.json")):
            document = load_json(result_path)
            row = document.get("row") or document
            valid = {artifact_key(item) for item in concrete_rows(row, "valid_artifacts")}
            for artifact in concrete_rows(row, "raw_artifacts"):
                key = artifact_key(artifact)
                if key not in valid:
                    output[key] = artifact
    return output


def markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# RQ3 Raw-Only Counterexample Authenticity Partition",
        "",
        f"Partition SHA-256: `{report['partition_sha256']}`",
        "",
        "Forge success is not used as counterexample authentication. A row is CE-authenticated "
        "only when its exact identity has a non-empty certification CE, an exact `F` report "
        "claim, and verifier/project source-hash continuity.",
        "",
        "## Counts",
        "",
        f"- Current raw-only rows: {counts['current_raw_only']}",
        f"- Excluded pending publication recovery: {counts['excluded_recoverable_siblings']}",
        f"- Classified remainder: {counts['classified_remainder']}",
        f"- Authenticated CE, repairable: {counts['authenticated_ce_repairable']}",
        f"- Unauthenticated fallback/diagnostic: "
        f"{counts['unauthenticated_fallback_diagnostic']}",
        f"- Ambiguous: {counts['ambiguous']}",
        f"- Unique exact six-field identities: {counts['exact_identity_unique']}",
        f"- Cert/report CE exact after normalization: {counts['ce_report_exact']}",
        f"- Cert/report CE compatible projection: "
        f"{counts['ce_report_compatible_projection']}",
        f"- Cert/report CE typed representation difference: "
        f"{counts['ce_report_typed_representation_difference']}",
        "",
        "## Unauthenticated Rows",
        "",
        f"- Synthetic source-rule certifications with empty CE: "
        f"{counts['synthetic_empty_ce']}",
        f"- Source-constructor fallbacks with no path CE: "
        f"{counts['source_constructor_fallback']}",
        "",
        "These 66 rows are not candidates for CE replay repair. They require an explicit "
        "experiment-scope decision: remove them from RQ3 raw, or regenerate them from an "
        "authenticated ESBMC counterexample.",
        "",
        "## Evidence",
        "",
        "Every JSON row contains the exact six-field identity plus file/test identity; current "
        "SHA-256 values for result, put, generated test, certification JSONL and Solidity "
        "source; and hashes of the selected certification row/detail, CE, report and exact "
        "report claim. Null evidence is retained explicitly on unauthenticated rows.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--republish-plan", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    inventory = load_json(args.inventory)
    plan = load_json(args.republish_plan)
    raw_only = current_raw_only(args.root)
    recoverable = {
        tuple(key)
        for case in plan.get("cases") or []
        for key in case.get("publishable_keys") or []
    }
    remainder = set(raw_only) - recoverable
    entries = {artifact_key(item): item for item in inventory.get("entries") or []}
    if len(raw_only) != 853 or len(recoverable) != 499 or len(remainder) != 354:
        raise SystemExit("unexpected live RQ3 population")
    if set(entries) & remainder != remainder:
        raise SystemExit("repair inventory does not cover the live 354-row remainder")

    rows = []
    for key in sorted(remainder):
        entry = entries[key]
        identity = entry["identity"]
        source_path = Path(entry["file"])
        put_path = Path(entry["put_json"])
        result_path = Path(entry["result_json"])
        put = load_json(put_path)
        result = load_json(result_path)
        result_row = result.get("row") or result
        verifier_inputs = ((result_row.get("verifier_input_identity") or {}).get("inputs") or [])
        verifier_input = verifier_inputs[0] if verifier_inputs else {}
        verifier_flat = Path(str(verifier_input.get("flat") or ""))
        project_flat = source_path.parent.parent / "src" / "flat.sol"
        cert_path = Path(str(result_row.get("cert_jsonl") or ""))
        path_function = identity.get("path_function")
        unit = str(identity.get("unit") or "")
        enc = int(identity.get("enc") or 0)
        cert_rows = certification_rows(cert_path, unit, str(path_function or "")) \
            if path_function else []
        details = details_for(cert_rows, enc)
        selected = choose_detail(details, str(entry.get("stage2_source") or ""))
        detail = selected["detail"] if selected else {}
        cert_row = selected["row"] if selected else None
        ce = normalized_ce(detail.get("ce") or {})

        report_path = None
        report_claim = None
        candidates = []
        if cert_row and cert_row.get("enumeration_report"):
            candidates.append(Path(cert_row["enumeration_report"]))
        candidates.append(put_path.parent / "emit" / "cov-report.json")
        for candidate in candidates:
            claims = exact_claims(candidate, str(path_function or ""), enc) \
                if path_function else []
            if claims:
                report_path = candidate
                report_claim = claims[0]
                break
        report_ce = claim_counterexample(report_claim or {})
        cert_subject_flat = Path(str((cert_row or {}).get("subject", {}).get("flat_sol") or ""))
        report_claim_source = Path(str((report_claim or {}).get("file") or ""))

        stored_flat_sha = verifier_input.get("flat_sha256")
        artifact_hash_match = all((
            sha256(source_path) == entry["hashes"].get("source_sha256"),
            sha256(put_path) == entry["hashes"].get("put_json_sha256"),
            sha256(result_path) == entry["hashes"].get("result_json_sha256"),
        ))
        source_hash_match = bool(stored_flat_sha and sha256(verifier_flat) == stored_flat_sha
                                 and sha256(project_flat) == stored_flat_sha
                                 and sha256(cert_subject_flat) == stored_flat_sha)
        exact_ce_binding = bool(ce and report_claim and source_hash_match and artifact_hash_match
                                and path_function)
        explicit_synthetic = bool(
            selected and not ce
            and (cert_row.get("synthetic_certified") is True
                 or str(detail.get("certification_source") or "").startswith("structural-")))
        constructor_fallback = entry.get("stage2_source") == \
            "source_constructor_revert_fallback" and not path_function

        if exact_ce_binding:
            classification = "authenticated_ce_repairable"
            reason = "non-empty exact-identity CE with exact F report claim and source hashes"
        elif explicit_synthetic:
            classification = "unauthenticated_fallback_diagnostic"
            reason = "synthetic source-rule certification has an empty CE"
        elif constructor_fallback:
            classification = "unauthenticated_fallback_diagnostic"
            reason = "source-constructor fallback has no path_function or ESBMC CE"
        else:
            classification = "ambiguous"
            reason = "required CE/report/source binding is incomplete or inconsistent"

        ce_hash = canonical_sha256(ce) if ce else None
        report_ce_hash = canonical_sha256(report_ce) if report_ce else None
        if ce_hash == report_ce_hash and ce_hash is not None:
            ce_report_relation = "exact"
        elif ce and report_ce:
            shared = set(ce) & set(report_ce)
            shared_equal = all(ce[name] == report_ce[name] for name in shared)
            ce_report_relation = "compatible_projection" if shared_equal else \
                "typed_representation_difference"
        else:
            ce_report_relation = None

        rows.append({
            "classification": classification,
            "classification_reason": reason,
            "identity": identity,
            "artifact_identity": {"file": str(source_path), "test": entry["test"]},
            "generator_classification": {
                "repair_group": entry.get("repair_group"),
                "root_cause_cluster": entry.get("root_cause_cluster"),
                "stage2_source": entry.get("stage2_source"),
                "stage2_witness_check": entry.get("stage2_witness_check"),
            },
            "forge_observation_not_authentication": {
                "forge_status": entry.get("forge_status"),
                "valid_reference_test": entry.get("valid_reference_test"),
            },
            "bindings": {
                "live_raw_only": key in raw_only,
                "excluded_from_recoverable_499": key not in recoverable,
                "artifact_hashes_match_inventory": artifact_hash_match,
                "source_hash_continuity": source_hash_match,
                "exact_certification_detail": selected is not None,
                "nonempty_certification_ce": bool(ce),
                "exact_f_report_claim": report_claim is not None,
                "ce_report_relation": ce_report_relation,
                "synthetic_certification": explicit_synthetic,
            },
            "paths": {
                "result_json": str(result_path),
                "put_json": str(put_path),
                "generated_source": str(source_path),
                "certification_jsonl": str(cert_path) if cert_path.is_file() else None,
                "report_json": str(report_path) if report_path else None,
                "verifier_flat_source": str(verifier_flat) if verifier_flat.is_file() else None,
                "project_flat_source": str(project_flat) if project_flat.is_file() else None,
                "certification_flat_source": str(cert_subject_flat)
                if cert_subject_flat.is_file() else None,
                "report_claim_source": str(report_claim_source)
                if report_claim_source.is_file() else None,
            },
            "hashes": {
                "result_json_sha256": sha256(result_path),
                "put_json_sha256": sha256(put_path),
                "generated_source_sha256": sha256(source_path),
                "certification_jsonl_sha256": sha256(cert_path),
                "certification_row_sha256": canonical_sha256(cert_row) if cert_row else None,
                "certification_detail_sha256": canonical_sha256(detail) if selected else None,
                "counterexample_sha256": ce_hash,
                "report_json_sha256": sha256(report_path) if report_path else None,
                "report_claim_sha256": canonical_sha256(report_claim) \
                    if report_claim else None,
                "report_counterexample_sha256": report_ce_hash,
                "recorded_verifier_flat_sha256": stored_flat_sha,
                "current_verifier_flat_sha256": sha256(verifier_flat),
                "project_flat_sha256": sha256(project_flat),
                "certification_flat_sha256": sha256(cert_subject_flat),
                "report_claim_source_sha256": sha256(report_claim_source),
            },
            "certification": {
                "field": selected["field"] if selected else None,
                "key": selected["key"] if selected else None,
                "verdict": detail.get("verdict") if selected else None,
                "source": detail.get("certification_source") or detail.get("source")
                if selected else None,
                "ce_coordinate_count": len(ce),
            },
            "put_stage4_kind": put.get("stage4_kind"),
        })

    rows.sort(key=lambda item: identity_key(item["identity"]) +
              (item["artifact_identity"]["file"], item["artifact_identity"]["test"]))

    def count(classification: str) -> int:
        return sum(row["classification"] == classification for row in rows)

    relation_counts = {
        name: sum(row["bindings"]["ce_report_relation"] == name for row in rows)
        for name in ("exact", "compatible_projection", "typed_representation_difference")
    }
    report = {
        "schema": "veriput-rq3-raw-authenticity-partition/v1",
        "root": str(args.root.resolve()),
        "inputs": {
            "repair_inventory": str(args.inventory.resolve()),
            "repair_inventory_sha256": sha256(args.inventory),
            "republish_plan": str(args.republish_plan.resolve()),
            "republish_plan_sha256": sha256(args.republish_plan),
            "republish_plan_raw_only_sha256": plan.get("new_raw_only_sha256"),
        },
        "policy": {
            "canonical_write":
            False,
            "forge_is_authenticator":
            False,
            "identity_fields": ["dataset", "case", "path_function", "unit", "enc", "piece"],
            "authenticated_requires": [
                "nonempty exact certification CE", "exact path F report claim",
                "verifier/project flat source hash continuity",
                "unchanged result/put/generated-source hashes"
            ],
        },
        "counts": {
            "current_raw_only":
            len(raw_only),
            "excluded_recoverable_siblings":
            len(recoverable),
            "classified_remainder":
            len(rows),
            "authenticated_ce_repairable":
            count("authenticated_ce_repairable"),
            "unauthenticated_fallback_diagnostic":
            count("unauthenticated_fallback_diagnostic"),
            "ambiguous":
            count("ambiguous"),
            "synthetic_empty_ce":
            sum(row["bindings"]["synthetic_certification"] for row in rows),
            "source_constructor_fallback":
            sum(row["generator_classification"]["stage2_source"] ==
                "source_constructor_revert_fallback" for row in rows),
            "exact_identity_unique":
            len({identity_key(row["identity"])
                 for row in rows}),
            "ce_report_exact":
            relation_counts["exact"],
            "ce_report_compatible_projection":
            relation_counts["compatible_projection"],
            "ce_report_typed_representation_difference":
            relation_counts["typed_representation_difference"],
        },
        "rows": rows,
    }
    digest_document = dict(report)
    report["partition_sha256"] = canonical_sha256(digest_document)
    if report["counts"] != {
            "current_raw_only": 853,
            "excluded_recoverable_siblings": 499,
            "classified_remainder": 354,
            "authenticated_ce_repairable": 288,
            "unauthenticated_fallback_diagnostic": 66,
            "ambiguous": 0,
            "synthetic_empty_ce": 62,
            "source_constructor_fallback": 4,
            "exact_identity_unique": 354,
            "ce_report_exact": 259,
            "ce_report_compatible_projection": 24,
            "ce_report_typed_representation_difference": 5,
    }:
        raise SystemExit(f"unexpected partition: {report['counts']}")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps({
            "partition_sha256": report["partition_sha256"],
            **report["counts"]
        },
                   sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
