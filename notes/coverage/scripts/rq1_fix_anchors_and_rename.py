#!/usr/bin/env python3
"""Fix missing anchors and rename all anchor functions.

This script does two things:
1. Synthesizes anchors for the 2 PUT files that have NO anchors at all
2. Renames ALL test_ce_anchor_* and test_structural_anchor_* functions to
   test_concrete_replay_{path_suffix} where path_suffix matches the PUT test's
   suffix (e.g., test_put_XXX_path6 -> test_concrete_replay_path6)

This is a DRY-RUN by default. Pass --execute to actually modify files.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_migrate import _case_dirs  # noqa: E402

DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def extract_path_suffix(test_name: str) -> str | None:
    """Extract path suffix from test name, e.g. 'path6' or 'path1p1'."""
    match = re.search(r"(path\w+)", test_name)
    if match:
        return match.group(1)
    return None


def find_contract_end(content: str) -> int | None:
    """Find the position of the contract's closing brace (last '}}' in file)."""
    last_brace = content.rfind("}")
    second_last = content.rfind("}", 0, last_brace)
    if second_last < 0:
        return None
    return second_last


def extract_target_call(body: str) -> str | None:
    """Extract the target function call from PUT body."""
    # Look for c0.functionName(args) pattern
    match = re.search(r"c0\.(\w+)\s*\(([^)]*)\)", body)
    if match:
        return f"    c0.{match.group(1)}({match.group(2).strip()});"
    return None


def extract_function_params_and_body(content: str, test_name: str) -> tuple[str, str] | None:
    """Extract parameters and body from a PUT test function."""
    # Find the function signature
    pattern = r"(function\s+" + re.escape(test_name) + r"\s*\(([^)]*)\)\s*public\s*\{)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None

    params_str = match.group(2).strip()

    # Find the function body (balanced braces starting from first {)
    brace_start = match.end() - 1  # position of opening {
    depth = 0
    i = brace_start
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                body = content[brace_start + 1 : i]
                return params_str, body
        i += 1

    return None


def synthesize_anchor_function(test_name: str, content: str) -> tuple[str, str] | None:
    """Synthesize a test_concrete_replay function from PUT body."""
    path_suffix = extract_path_suffix(test_name)
    if not path_suffix:
        return None

    # Extract contract type from c0 declaration (e.g., 'StaxLPStaking c0;')
    c0_match = re.search(r'(\w+)\s+c0;', content)
    if not c0_match:
        return None
    contract_type = c0_match.group(1)

    result = extract_function_params_and_body(content, test_name)
    if not result:
        return None

    params_str, body = result

    # Extract target call from body
    target_call = extract_target_call(body)
    if not target_call:
        target_call = f"    // TODO: target call extraction failed for {test_name}"

    anchor_name = f"test_concrete_replay_{path_suffix}"

    new_function = f"""
  // Concrete replay basis for this PUT path.
  function {anchor_name}() public {{
    {contract_type} c0 = new {contract_type}();
    c0.setUp();
{target_call}
  }}
"""

    return anchor_name, new_function


def rename_anchor_in_content(content: str) -> tuple[str, dict[str, str]]:
    """Rename all test_ce_anchor_* and test_structural_anchor_* to test_concrete_replay_{path_suffix}.

    Returns (new_content, mapping_of_old_to_new_names).
    """
    # Find all anchor function names in this file
    anchor_pattern = r"(?:test_ce_anchor_|test_structural_anchor_)(\w+)"
    old_anchor_names = re.findall(anchor_pattern, content)

    if not old_anchor_names:
        return content, {}

    # Deduplicate while preserving order
    seen = set()
    unique_old_names = []
    for name in old_anchor_names:
        full_name = f"test_ce_anchor_{name}" if "test_ce_anchor_" in content else f"test_structural_anchor_{name}"
        # Actually, we need to find the exact old name
        pass

    # Better approach: find all occurrences of anchor patterns with their full names
    all_old_names = set()
    for match in re.finditer(r"\b(test_(?:ce|structural)_anchor_\w+)\b", content):
        all_old_names.add(match.group(1))

    if not all_old_names:
        return content, {}

    # Find all PUT tests and their path suffixes in this file
    put_pattern = r"test_put_\w+_path\w+"
    put_tests = re.findall(put_pattern, content)
    
    # Deduplicate PUT tests while preserving order
    seen_puts = set()
    unique_puts = []
    for test in put_tests:
        if test not in seen_puts:
            seen_puts.add(test)
            unique_puts.append(test)

    path_suffixes = [extract_path_suffix(t) for t in unique_puts]
    path_suffixes = [s for s in path_suffixes if s]  # filter None

    # Assign new names to anchors
    mapping = {}
    sorted_old_names = sorted(all_old_names)
    for i, old_name in enumerate(sorted_old_names):
        if i < len(path_suffixes):
            new_name = f"test_concrete_replay_{path_suffixes[i]}"
        else:
            # If more anchors than PUT tests, use the last PUT test's suffix
            new_name = f"test_concrete_replay_{path_suffixes[-1]}" if path_suffixes else f"test_concrete_replay_anchor{i}"

        mapping[old_name] = new_name

    # Replace anchor names in content
    for old_name, new_name in mapping.items():
        content = re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, content)

    return content, mapping


def process_file(file_path: Path, needs_synthesis: bool = False, dry_run: bool = True) -> dict:
    """Process a single file: add missing anchors and rename existing ones."""
    result = {
        "file": str(file_path),
        "actions": [],
        "errors": [],
    }

    try:
        content = file_path.read_text()
    except Exception as e:
        result["errors"].append(f"Failed to read file: {e}")
        return result

    original_content = content

    # Step 1: If no anchors at all, synthesize for each PUT test
    if needs_synthesis:
        has_ce_anchor = bool(re.search(r"\btest_ce_anchor_\w+", content))
        has_structural_anchor = bool(re.search(r"\btest_structural_anchor_\w+", content))

        if not has_ce_anchor and not has_structural_anchor:
            # Find PUT tests in this file and synthesize anchors for each
            put_tests = re.findall(r"(test_put_\w+_path\w+)", content)
            if put_tests:
                seen_tests = set()
                for test_name in put_tests:
                    if test_name in seen_tests:
                        continue
                    seen_tests.add(test_name)
                    synthesized = synthesize_anchor_function(test_name, content)
                    if synthesized:
                        anchor_name, new_func = synthesized
                        contract_end = find_contract_end(content)
                        if contract_end is not None:
                            new_content = (
                                content[:contract_end] + new_func + "\n" + content[contract_end:]
                            )
                            result["actions"].append(
                                f"Added {anchor_name} for PUT {test_name}"
                            )
                            content = new_content
                        else:
                            result["errors"].append(f"Could not find contract end in file")
                    else:
                        result["errors"].append(f"Failed to synthesize anchor for {test_name}")

    # Step 2: Rename all anchors (ce and structural) to test_concrete_replay_{path_suffix}
    new_content, name_mapping = rename_anchor_in_content(content)
    if name_mapping:
        result["actions"].extend(
            f"Renamed {old} -> {new}" for old, new in name_mapping.items()
        )
        content = new_content

    # Write back if not dry run and there are actions
    if result["actions"] and not dry_run:
        file_path.write_text(content)
        result["status"] = "modified"
    elif result["actions"]:
        result["status"] = "would modify"
    else:
        result["status"] = "no changes needed"

    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path, default=DEFAULT_RESULTS_ROOT
    )
    parser.add_argument("--execute", action="store_true", help="Actually modify files")
    args = parser.parse_args(argv)

    reconcile_json = Path("/tmp/rq1-frozen-obligation-reconcile.json")
    if not reconcile_json.exists():
        print("Error: /tmp/rq1-frozen-obligation-reconcile.json not found.")
        print("Run rq1_frozen_obligation_reconcile.py first.")
        return 1

    with open(reconcile_json) as f:
        data = json.load(f)

    # Find all PUT files that need processing
    files_to_process = []
    processed_files = set()

    for item in data["identities"]["PUT_BACKED"]:
        identity = tuple(item["identity"])

        for row in item["rows"]:
            file_path = Path(str(row.get("file", "")))
            if not file_path.exists():
                continue

            recorded_kind = row.get("kind", "")
            test_name = str(row.get("test", ""))

            is_put = recorded_kind == "put" or test_name.startswith("test_put_")
            if not is_put:
                continue

            # Check if this file needs processing
            content = file_path.read_text()
            has_ce_anchor = bool(re.search(r"\btest_ce_anchor_\w+", content))
            has_structural_anchor = bool(re.search(r"\btest_structural_anchor_\w+", content))
            has_any_anchor = has_ce_anchor or has_structural_anchor

            key = str(file_path)
            if key not in processed_files:
                processed_files.add(key)
                files_to_process.append({
                    "file": file_path,
                    "identity": list(identity),
                    "needs_synthesis": not has_any_anchor,  # No anchors at all
                    "has_ce_anchor": has_ce_anchor,
                    "has_structural_anchor": has_structural_anchor,
                })

    print(f"Found {len(files_to_process)} unique PUT files to process")
    needs_synthesis = sum(1 for f in files_to_process if f["needs_synthesis"])
    print(f"  Files needing anchor synthesis: {needs_synthesis}")
    print(f"  Files with ce_anchor only: {sum(1 for f in files_to_process if not f['needs_synthesis'] and f['has_ce_anchor'])}")
    print(f"  Files with structural_anchor only (no ce_anchor): {sum(1 for f in files_to_process if not f['needs_synthesis'] and not f['has_ce_anchor'] and f['has_structural_anchor'])}")

    # Process each file
    results = []
    for file_info in files_to_process:
        result = process_file(
            file_info["file"],
            needs_synthesis=file_info.get("needs_synthesis", False),
            dry_run=not args.execute,
        )
        result["identity"] = file_info["identity"]
        results.append(result)

        if result["status"] != "no changes needed":
            print(f"  {file_info['file'].relative_to(args.results_root)}: {result['status']}")
            for action in result.get("actions", [])[:3]:
                print(f"    - {action}")
            for error in result.get("errors", []):
                print(f"    ERROR: {error}")

    # Summary
    modified = sum(1 for r in results if r["status"] == "modified")
    would_modify = sum(1 for r in results if r["status"] == "would modify")
    no_changes = sum(1 for r in results if r["status"] == "no changes needed")

    print(f"\nSummary:")
    print(f"  Modified: {modified}")
    print(f"  Would modify (dry run): {would_modify}")
    print(f"  No changes: {no_changes}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
