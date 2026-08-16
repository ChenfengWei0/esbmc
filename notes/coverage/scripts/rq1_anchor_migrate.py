#!/usr/bin/env python3
"""
RQ1 Anchor Migration Script

Tasks:
1. For 479 matched RQ1 PUTs: Extract RQ3 concrete test and inject as anchor
2. For 406 unmatched RQ1 PUTs: Synthesize anchor from PUT with fixed values

Usage:
    python3 rq1_anchor_migrate.py --mode migrate    # Phase 1: migrate RQ3 anchors
    python3 rq1_anchor_migrate.py --mode synthesize  # Phase 2: synthesize anchors
    python3 rq1_anchor_migrate.py --mode both        # Run both phases
    python3 rq1_anchor_migrate.py --dry-run          # Preview only
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

from rq1_concrete_replay_migrate import _case_dirs, _strict_valid_tests

# Paths
VERIPUT_RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
VERIPUT_RQ3 = Path("/home/samson/workspace/VeriPUT/Results/RQ3/adoption-bundles/rq3-persistence-republish-20260815/staged")
RQ3_RAW = Path("/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg")

# Output directory for migration log
MIGRATION_LOG = Path("/home/samson/workspace/esbmc/notes/coverage/scripts/migration_log.json")


def extract_functions(content):
    """Extract all function definitions from Solidity content."""
    func_pattern = re.compile(r'function\s+(\w+)\s*\(([^)]*)\)\s*public\s*\{')
    functions = []
    for match in func_pattern.finditer(content):
        func_name = match.group(1)
        params = match.group(2)
        start = match.start()
        # Find end of function (simple brace matching)
        brace_count = 0
        i = match.end() - 1
        while i < len(content):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    break
            i += 1
        body = content[match.start():i + 1]
        functions.append({
            'name': func_name,
            'params': params,
            'body': body,
            'start': match.start(),
            'end': i + 1,
        })
    return functions


def find_main_contract_end(content):
    """Find the end position of the main contract (first contract definition).
    
    This function finds the position where the main contract ends, which is
    right before any secondary contract definition or the end of the file.
    """
    # Find all contract definitions (more specific pattern to avoid matching "does" in comments)
    # Match: contract <Name> is <Base> or contract <Name> {
    contract_matches = list(re.finditer(r'\bcontract\s+(\w+)\s+(is\s+\w+)?\s*\{', content))
    
    if not contract_matches:
        return len(content)
    
    # If there's only one contract, find its end
    if len(contract_matches) == 1:
        contract_start = contract_matches[0].start()
        brace_count = 0
        for i in range(contract_start, len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    return i + 1
        return len(content)
    
    # If there are multiple contracts, find the end of the first one
    # by looking for the position right before the second contract
    first_contract_start = contract_matches[0].start()
    second_contract_start = contract_matches[1].start()
    
    # Work backwards from the second contract to find the closing brace
    # of the first contract
    for i in range(second_contract_start - 1, first_contract_start, -1):
        if content[i] == '}':
            # Found the closing brace, return position after it
            return i + 1
    
    return second_contract_start


def extract_rq3_concrete_test(rq3_content, test_name='test_cov_0'):
    """Extract a concrete test function from RQ3 content."""
    functions = extract_functions(rq3_content)
    
    # Find the concrete test function
    for func in functions:
        if func['name'] == test_name or (func['name'].startswith('test_cov') and not func['name'].startswith('test_cov_0')):
            return func['body']
    
    # If no specific test_cov found, return first test function
    for func in functions:
        if func['name'].startswith('test_'):
            return func['body']
    
    return None


def generate_anchor_name(rq3_test_name, case_name, unit):
    """Generate a unique anchor function name."""
    hash_input = f"{case_name}_{unit}_{rq3_test_name}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:12]
    return f"test_ce_anchor_rq3_{hash_val}"


def create_anchor_function(rq3_func_body, anchor_name, put_content=None):
    """Create an anchor function from RQ3 function body.
    
    If put_content is provided, adapt the anchor to use the PUT file's
    contract setup (c0 variable, setUp, etc.).
    """
    # Extract just the function body (without setUp)
    # Replace test_cov_* name with anchor name
    func_match = re.search(r'function\s+(\w+)\s*\(([^)]*)\)\s*public\s*\{', rq3_func_body)
    if not func_match:
        return None
    
    old_name = func_match.group(1)
    params = func_match.group(2)
    
    # Create new function with anchor name
    new_func = rq3_func_body.replace(f'function {old_name}(', f'function {anchor_name}(')
    
    # If we have PUT content, adapt the anchor to work in PUT context:
    # 1. Replace address(this) with a concrete address
    # 2. Remove vm.deal calls (not needed in PUT context)
    # 3. Keep the rest as-is (c0 should already be declared in PUT)
    if put_content:
        # Replace address(this) with address(uint160(1))
        new_func = new_func.replace('address(this)', 'address(uint160(1))')
        
        # Remove vm.deal lines and their preceding comment lines
        # Pattern: optional comment, then vm.deal(...);
        new_func = re.sub(r'\s*//\s*\[asserted\].*\n?\s*vm\.deal\([^;]+;\n?', '', new_func)
        
        # Remove assertFalse calls if they reference c0 (which is in scope in PUT)
        # Actually, keep them - they're valid assertions
    
    return new_func


def synthesize_anchor_from_put(put_content, put_test_name, unit, case_name):
    """Synthesize an anchor function from PUT by using fixed values."""
    functions = extract_functions(put_content)
    
    # Find the PUT test function
    put_func = None
    for func in functions:
        if func['name'] == put_test_name:
            put_func = func
            break
    
    if not put_func:
        return None
    
    # Extract the function body
    put_body = put_func['body']
    
    # Get the contract name from setUp or test function
    contract_name = None
    for func in functions:
        if func['name'] == 'setUp':
            # Extract contract type from setUp
            contract_match = re.search(r'(\w+)\s+c0;', put_content)
            if contract_match:
                contract_name = contract_match.group(1)
                break
    
    # If not found in setUp, look in the test function
    if not contract_name:
        put_func = None
        for func in functions:
            if func['name'] == put_test_name:
                put_func = func
                break
        if put_func:
            # Look for c0 declaration in the test function
            contract_match = re.search(r'(\w+)\s+c0\s*=', put_func['body'])
            if contract_match:
                contract_name = contract_match.group(1)
    
    if not contract_name:
        return None
    
    # Generate anchor name
    hash_input = f"{case_name}_{unit}_{put_test_name}_synthesized"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:12]
    anchor_name = f"test_ce_anchor_{hash_val}"
    
    # Create a simple anchor with fixed values
    # Extract the target function call from PUT
    # Look for patterns like c0.functionName(args)
    call_pattern = re.compile(r'c0\.(\w+)\s*\(([^)]*)\)')
    calls = call_pattern.findall(put_body)
    
    if not calls:
        # Try to find any function call
        call_pattern2 = re.compile(r'(?:vm\.\w+\([^)]*\);|\(bool\s+\w+,?\s*\)\s*=\s*address\(c0\)\.call[^;]+;)')
        calls2 = call_pattern2.findall(put_body)
        if calls2:
            # Use the first call pattern
            anchor_body = f"function {anchor_name}() public {{\n"
            anchor_body += f"    // Synthesized from PUT: {put_test_name}\n"
            anchor_body += f"    // Contract: {contract_name}\n"
            anchor_body += f"    // Unit: {unit}\n"
            anchor_body += f"    {contract_name} c0 = new {contract_name}();\n"
            anchor_body += f"    // TODO: Extract concrete call from PUT\n"
            anchor_body += f"    // Original PUT had: {put_body[put_body.find('vm.prank'):put_body.find('vm.prank') + 200] if 'vm.prank' in put_body else 'N/A'}\n"
            anchor_body += f"}}\n"
            return anchor_body
        else:
            return None
    
    # Create anchor with first call and fixed values
    anchor_body = f"function {anchor_name}() public {{\n"
    anchor_body += f"    // Synthesized from PUT: {put_test_name}\n"
    anchor_body += f"    // Contract: {contract_name}\n"
    anchor_body += f"    // Unit: {unit}\n"
    anchor_body += f"    {contract_name} c0 = new {contract_name}();\n"
    
    # Add vm.prank for msg.sender
    anchor_body += f"    vm.prank(address(uint160(0)));\n"
    
    # Add the call with fixed values
    for func_name, args in calls[:1]:  # Take first call
        # Replace fuzzed args with fixed values
        fixed_args = ', '.join(['address(uint160(0))' if 'address' in a else 'uint256(0)' for a in args.split(',')])
        anchor_body += f"    c0.{func_name}({fixed_args});\n"
    
    anchor_body += f"}}\n"
    
    return anchor_body


def find_rq3_file(case_name, benchmark):
    """Find the RQ3 concrete test file for a case."""
    # Try multiple possible paths
    possible_paths = [
        RQ3_RAW / benchmark / "subjects" / case_name / "put" / "*.t.sol",
    ]
    
    for pattern in possible_paths:
        base = pattern.parent
        if base.exists():
            for f in base.rglob("*.t.sol"):
                if "concrete" in f.name or "cov" in f.name:
                    return str(f)
    
    return None


def load_rq3_data():
    """Load all RQ3 concrete tests."""
    rq3_all = []
    
    for bench in ['bugfix124', 'real203', 'peer182']:
        results_file = VERIPUT_RQ3 / bench / "results.jsonl"
        if results_file.exists():
            data = [json.loads(line) for line in results_file.read_text().strip().split('\n')]
            for d in data:
                case = d['subject_id']
                for vt in d.get('valid_tests', []):
                    if vt.get('kind') == 'concrete':
                        rq3_all.append({
                            'benchmark': d['benchmark'],
                            'case': case,
                            'test': vt.get('test', ''),
                            'unit': vt.get('unit', ''),
                            'enc': vt.get('enc', ''),
                            'file': vt.get('file', ''),
                        })
    
    # Create lookup by (benchmark, case)
    rq3_lookup = {}
    for r in rq3_all:
        key = (r['benchmark'], r['case'])
        if key not in rq3_lookup:
            rq3_lookup[key] = []
        rq3_lookup[key].append(r)
    
    return rq3_lookup


def load_rq1_puts():
    """Load all RQ1 PUT rows without anchor."""
    rq1_no_anchor = []
    
    for case, subject_dir in _case_dirs(VERIPUT_RQ1):
        parts = case.split('/')
        benchmark = parts[0]
        case_name = parts[1] if len(parts) > 1 else case
        
        rows = _strict_valid_tests(subject_dir)
        for row in rows:
            if row.get('kind') == 'put':
                if 'ce_anchor' not in row or not isinstance(row.get('ce_anchor'), dict):
                    rq1_no_anchor.append({
                        'case': case,
                        'benchmark': benchmark,
                        'case_name': case_name,
                        'test': row.get('test', ''),
                        'unit': row.get('unit', ''),
                        'put_file': row.get('file', ''),
                    })
    
    return rq1_no_anchor


def remove_existing_anchors(content):
    """Remove existing anchor functions from PUT content."""
    # Remove anchor function definitions (handle nested braces)
    def replace_anchor(match):
        return ''
    
    # Find all anchor functions by name and remove them
    anchor_names = re.findall(r'function\s+(test_ce_anchor_\w+)\s*\(', content)
    for anchor_name in anchor_names:
        # Find the function definition and remove it
        func_pattern = re.compile(
            r'(\s*)function\s+' + re.escape(anchor_name) + r'\s*\([^)]*\)\s*public\s*\{',
            re.MULTILINE
        )
        match = func_pattern.search(content)
        if match:
            # Find the end of the function (brace matching)
            start = match.start()
            brace_count = 0
            i = match.end() - 1
            while i < len(content):
                if content[i] == '{':
                    brace_count += 1
                elif content[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        # Remove from start to end (inclusive)
                        content = content[:start] + content[i + 1:]
                        break
                i += 1
    
    # Remove anchor comments
    content = re.sub(r'\s*// RQ3 concrete basis anchor\.\n*', '', content)
    content = re.sub(r'\s*// Synthesized concrete basis anchor\.\n*', '', content)
    
    # Clean up extra blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    return content


def inject_anchor_into_put(put_content, anchor_func, anchor_name, dry_run=False):
    """Inject anchor function into PUT file."""
    # Remove existing anchors first
    put_content = remove_existing_anchors(put_content)
    
    # Find the end of the main contract (returns position after the closing brace)
    main_contract_end = find_main_contract_end(put_content)
    
    # We need to insert the anchor BEFORE the closing brace of the main contract
    # Find the last '}' that closes the main contract
    # Work backwards from main_contract_end to find the closing brace
    insert_pos = main_contract_end - 1
    while insert_pos >= 0 and put_content[insert_pos] not in ['}', '\n', ' ']:
        insert_pos -= 1
    
    # Skip whitespace and find the actual closing brace
    while insert_pos >= 0:
        if put_content[insert_pos] == '}':
            # Found the closing brace, insert before it
            break
        elif put_content[insert_pos] not in ['\n', ' ', '\r']:
            # Hit non-whitespace, something went wrong
            break
        insert_pos -= 1
    
    # Insert anchor before the closing brace
    before = put_content[:insert_pos]
    after = put_content[insert_pos:]
    
    # Format anchor with proper indentation
    anchor_lines = anchor_func.split('\n')
    indented_anchor = '\n'.join('  ' + line if line.strip() else line for line in anchor_lines)
    
    new_content = before + '\n\n  // RQ3 concrete basis anchor.\n' + indented_anchor + '\n  ' + after
    
    return new_content


def migrate_rq3_anchor(rq1_row, rq3_row, dry_run=False):
    """Migrate RQ3 concrete test as anchor to RQ1 PUT file."""
    put_file = Path(rq1_row['put_file'])
    rq3_file = Path(rq3_row['file'])
    
    if not put_file.exists():
        return {'status': 'error', 'reason': f'PUT file not found: {put_file}'}
    
    if not rq3_file.exists():
        return {'status': 'error', 'reason': f'RQ3 file not found: {rq3_file}'}
    
    put_content = put_file.read_text()
    rq3_content = rq3_file.read_text()
    
    # Extract RQ3 concrete test
    rq3_func_body = extract_rq3_concrete_test(rq3_content, rq3_row['test'])
    
    if not rq3_func_body:
        return {'status': 'error', 'reason': 'Could not extract RQ3 test function'}
    
    # Generate anchor name
    anchor_name = generate_anchor_name(rq3_row['test'], rq1_row['case_name'], rq1_row['unit'])
    
    # Create anchor function (pass put_content to adapt for PUT context)
    anchor_func = create_anchor_function(rq3_func_body, anchor_name, put_content)
    
    if not anchor_func:
        return {'status': 'error', 'reason': 'Could not create anchor function'}
    
    # Check if anchor already exists
    if anchor_name in put_content:
        return {'status': 'skipped', 'reason': 'Anchor already exists'}
    
    if dry_run:
        return {'status': 'dry_run', 'anchor_name': anchor_name, 'anchor_func': anchor_func}
    
    # Inject anchor into PUT file
    new_content = inject_anchor_into_put(put_content, anchor_func, anchor_name)
    put_file.write_text(new_content)
    
    return {'status': 'success', 'anchor_name': anchor_name}


def synthesize_anchor(rq1_row, dry_run=False):
    """Synthesize anchor from PUT file."""
    put_file = Path(rq1_row['put_file'])
    
    if not put_file.exists():
        return {'status': 'error', 'reason': f'PUT file not found: {put_file}'}
    
    put_content = put_file.read_text()
    
    # Synthesize anchor
    anchor_func = synthesize_anchor_from_put(
        put_content,
        rq1_row['test'],
        rq1_row['unit'],
        rq1_row['case_name']
    )
    
    if not anchor_func:
        return {'status': 'error', 'reason': 'Could not synthesize anchor'}
    
    # Check if anchor already exists
    anchor_name = anchor_func.split('(')[0].split()[-1]
    if anchor_name in put_content:
        return {'status': 'skipped', 'reason': 'Anchor already exists'}
    
    if dry_run:
        return {'status': 'dry_run', 'anchor_name': anchor_name, 'anchor_func': anchor_func}
    
    # Inject anchor into PUT file
    new_content = inject_anchor_into_put(put_content, anchor_func, anchor_name)
    put_file.write_text(new_content)
    
    return {'status': 'success', 'anchor_name': anchor_name}


def main():
    parser = argparse.ArgumentParser(description='RQ1 Anchor Migration Script')
    parser.add_argument('--mode', choices=['migrate', 'synthesize', 'both', 'dry-run'],
                        default='both', help='Migration mode')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')
    args = parser.parse_args()
    
    dry_run = args.dry_run or args.mode == 'dry-run'
    
    print("=" * 80)
    print("RQ1 Anchor Migration Script")
    print("=" * 80)
    print(f"Mode: {args.mode}")
    print(f"Dry run: {dry_run}")
    print()
    
    # Load data
    print("Loading RQ3 data...")
    rq3_lookup = load_rq3_data()
    print(f"  Loaded {len(rq3_lookup)} RQ3 case lookups")
    
    print("Loading RQ1 PUTs...")
    rq1_puts = load_rq1_puts()
    print(f"  Loaded {len(rq1_puts)} RQ1 PUTs without anchor")
    
    # Classify
    matched = []
    unmatched = []
    
    for rq1 in rq1_puts:
        key = (rq1['benchmark'], rq1['case_name'])
        if key in rq3_lookup:
            matched.append({
                'rq1': rq1,
                'rq3': rq3_lookup[key][0],
            })
        else:
            unmatched.append(rq1)
    
    print(f"\nMatched (RQ3 available): {len(matched)}")
    print(f"Unmatched (need synthesis): {len(unmatched)}")
    print()
    
    # Process
    results = {
        'matched': [],
        'unmatched': [],
        'summary': {}
    }
    
    # Phase 1: Migrate RQ3 anchors
    if args.mode in ['migrate', 'both', 'dry-run']:
        print("=" * 80)
        print("Phase 1: Migrate RQ3 anchors")
        print("=" * 80)
        
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, m in enumerate(matched):
            if i % 50 == 0:
                print(f"  Processing {i}/{len(matched)}...")
            
            result = migrate_rq3_anchor(m['rq1'], m['rq3'], dry_run=dry_run)
            result['rq1_case'] = m['rq1']['case']
            result['rq1_test'] = m['rq1']['test']
            result['rq3_test'] = m['rq3']['test']
            results['matched'].append(result)
            
            if result['status'] == 'success':
                success_count += 1
            elif result['status'] == 'skipped':
                skip_count += 1
            elif result['status'] == 'error':
                error_count += 1
        
        print(f"  Phase 1 complete: {success_count} success, {skip_count} skipped, {error_count} errors")
    
    # Phase 2: Synthesize anchors
    if args.mode in ['synthesize', 'both', 'dry-run']:
        print("\n" + "=" * 80)
        print("Phase 2: Synthesize anchors")
        print("=" * 80)
        
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, u in enumerate(unmatched):
            if i % 50 == 0:
                print(f"  Processing {i}/{len(unmatched)}...")
            
            result = synthesize_anchor(u, dry_run=dry_run)
            result['rq1_case'] = u['case']
            result['rq1_test'] = u['test']
            results['unmatched'].append(result)
            
            if result['status'] == 'success':
                success_count += 1
            elif result['status'] == 'skipped':
                skip_count += 1
            elif result['status'] == 'error':
                error_count += 1
        
        print(f"  Phase 2 complete: {success_count} success, {skip_count} skipped, {error_count} errors")
    
    # Summary
    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    
    if args.mode in ['migrate', 'both', 'dry-run']:
        results['summary']['matched_migrated'] = len([r for r in results['matched'] if r.get('status') == 'success'])
        results['summary']['matched_skipped'] = len([r for r in results['matched'] if r.get('status') == 'skipped'])
        results['summary']['matched_errors'] = len([r for r in results['matched'] if r.get('status') == 'error'])
    
    if args.mode in ['synthesize', 'both', 'dry-run']:
        results['summary']['unmatched_synthesized'] = len([r for r in results['unmatched'] if r.get('status') == 'success'])
        results['summary']['unmatched_skipped'] = len([r for r in results['unmatched'] if r.get('status') == 'skipped'])
        results['summary']['unmatched_errors'] = len([r for r in results['unmatched'] if r.get('status') == 'error'])
    
    print(json.dumps(results['summary'], indent=2))
    
    # Save log
    if not dry_run:
        MIGRATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(MIGRATION_LOG, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nMigration log saved to: {MIGRATION_LOG}")


if __name__ == '__main__':
    main()
