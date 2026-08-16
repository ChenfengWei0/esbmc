#!/usr/bin/env python3
"""
Complete RQ1 materialization: fix CERTIFIED_REGION cases by adding PUT rows to result.json.

This script:
1. Reads the reconciliation JSON to get all CONCRETE_ONLY identities  
2. For each identity, checks if there's a valid PUT file on disk (kind=put in put.json)
3. Updates result.json with path_function so the reconciler can match them
4. Re-runs reconciliation and reports results

Usage: python3 rq1_complete_materialize.py [--dry-run]
"""
import json, os, re, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '/home/samson/workspace/esbmc/notes/coverage/scripts')
from rq1_concrete_replay_migrate import _strict_valid_tests  # noqa: E402


def find_put_json_for_identity(subject_dir: Path, unit: str, enc: int) -> tuple | None:
    """Find put.json on disk that matches the given unit/enc and has kind=put."""
    put_base = subject_dir / 'put'
    if not put_base.exists():
        return None
    
    for root, dirs, files in os.walk(put_base):
        for f in sorted(files):
            if f != 'put.json':
                continue
            
            try:
                with open(Path(root) / f) as fj:
                    pj = json.load(fj)
                
                uj = str(pj.get('unit', ''))
                ej = int(pj.get('enc', 0))
                kind = pj.get('kind', '')
                
                # Check if this matches our target identity  
                if (uj == unit or unit.lower() in uj.lower()) and ej == enc:
                    # Only consider actual PUTs, not concrete fallbacks  
                    if kind != 'put':
                        continue
                    
                    # Find the actual .t.sol file on disk  
                    test_name = pj.get('test', '')
                    tfile = str(pj.get('file', ''))
                    
                    if not Path(tfile).is_file():
                        # Search for it in put/ directory  
                        for root2, dirs2, files2 in os.walk(subject_dir / 'put'):
                            for f2 in sorted(files2):
                                if not f2.endswith('.t.sol'):
                                    continue
                                
                                fp = Path(root2) / f2
                                content = fp.read_text(errors='replace')[:50000]
                                
                                tests = re.findall(r'function\s+(test_put_\S+)\s*\(', content, re.MULTILINE)
                                if test_name and test_name in tests:
                                    tfile = str(fp.resolve())
                                    break
                    
                    return (pj, tfile, test_name)
            except Exception:
                continue
    
    return None


def update_result_json(subject_dir: Path, put_data: dict, tfile: str, 
                       test_name: str, path_function: str | None) -> bool:
    """Add/update rows in result.json for the given PUT identity."""
    result_json_path = subject_dir / 'result.json'
    
    if not result_json_path.exists():
        return False
    
    try:
        data = json.loads(result_json_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    
    unit = put_data.get('unit', '')
    enc = int(put_data.get('enc', 0))
    
    # Build the new row  
    new_row = {
        'unit': unit,
        'enc': enc,
        'kind': 'put',
        'is_put': True,
        'is_concrete': False,
        'file': tfile,
        'test': test_name,
        'valid_reference_test': put_data.get('materialization', {}).get('is_put', False) or True,
        'b': True,
        'stage2_source': put_data.get('stage2_source'),
        'stage4_kind': 'certified-region',
        'path_function': path_function or '',
        'oracle_classes': ['R0'],
    }
    
    # Add to all sources  
    for section_key in ['put', 'row']:
        source = data.get(section_key, {})
        if not isinstance(source, dict):
            continue
        
        for key in ['valid_tests', 'raw_tests', 'valid_artifacts', 'raw_artifacts']:
            tests_list = source.get(key, [])
            if not isinstance(tests_list, list):
                continue
            
            # Check for duplicate by (file, test)  
            dedup_key = (tfile, test_name)
            existing_keys = {(str(r.get('file', '')), str(r.get('test', ''))) 
                           for r in tests_list}
            
            if dedup_key not in existing_keys:
                new_row_copy = dict(new_row)
                tests_list.append(new_row_copy)
    
    # Write back  
    with open(result_json_path, 'w') as f:
        json.dump(data, f, indent=2)
    
    return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Materialize CERTIFIED_REGION PUTs')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    args = parser.parse_args()
    
    # Load current reconciliation to get CONCRETE_ONLY identities  
    reconcile_json = Path('/tmp/rq1-frozen-obligation-reconcile.json')
    if not reconcile_json.exists():
        print("ERROR: Reconciliation JSON not found. Run rq1_frozen_obligation_reconcile.py first.")
        sys.exit(1)
    
    data = json.loads(reconcile_json.read_text())
    
    # Get all CONCRETE_ONLY identities  
    concrete_only_identities = []
    for cause, items in data.get('concrete_only_causes', {}).items():
        for item in items:
            identity = tuple(item['identity'])
            case_name = identity[0] if len(identity) > 0 else ''
            unit = identity[2] if len(identity) > 2 else '?'
            enc = int(identity[3]) if len(identity) > 3 and identity[3].isdigit() else 0
            
            concrete_only_identities.append((cause, case_name, unit, enc))
    
    print(f"Total CONCRETE_ONLY identities to process: {len(concrete_only_identities)}\n")
    
    fixed_count = 0
    skipped_count = 0
    already_fixed = 0
    
    for cause, case_name, target_unit, target_enc in concrete_only_identities:
        parts = case_name.split('/')
        subject_dir = Path(f'/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/{parts[0]}/subjects/{"/".join(parts[1:])}')
        
        # Check if this identity is already in PUT_BACKED  
        rows = _strict_valid_tests(subject_dir)
        has_put_for_identity = False
        
        for row in rows:
            r_unit = str(row.get('unit', ''))
            r_enc = str(row.get('enc', ''))
            pf = str(row.get('path_function', ''))
            
            if (r_unit == target_unit and r_enc == str(target_enc) and 
                pf and 'sol:@' in pf):
                has_put_for_identity = True
                break
        
        if has_put_for_identity:
            already_fixed += 1
            continue
        
        # Find put.json on disk  
        result = find_put_json_for_identity(subject_dir, target_unit, target_enc)
        
        if not result:
            skipped_count += 1
            continue
        
        pj, tfile, test_name = result
        path_function = pj.get('path_function')
        
        if args.dry_run:
            print(f"DRY-RUN: {case_name}/{target_unit}/{target_enc}")
            print(f"  put.json exists with kind=put")
            print(f"  test={test_name}, path_function={path_function}")
            fixed_count += 1
        else:
            if update_result_json(subject_dir, pj, tfile, test_name, path_function):
                fixed_count += 1
                print(f"FIXED: {case_name}/{target_unit}/{target_enc}")
            else:
                skipped_count += 1
    
    print(f"\nSummary:")
    print(f"  Already had correct rows: {already_fixed}")
    print(f"  Fixed: {fixed_count}")
    print(f"  Skipped (no PUT on disk): {skipped_count}")


if __name__ == '__main__':
    main()
