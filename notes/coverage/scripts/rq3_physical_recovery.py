#!/usr/bin/env python3
"""Copy exact RQ3 emitted Solidity sources into RQ1 mechanical closures."""
from __future__ import annotations
import argparse, hashlib, json, shutil
from pathlib import Path

def sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('binding',type=Path)
    ap.add_argument('--rq3-root',type=Path,required=True); ap.add_argument('--rq1-root',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True); ap.add_argument('--apply',action='store_true'); a=ap.parse_args()
    binding=json.loads(a.binding.read_text()); rows=[]; added=0; missing=0
    for item in binding['rows']:
        ident=[str(x) for x in item['frozen_identity']]; case,pf,unit,enc,piece=ident
        bench,subject=case.split('/',1); sd=a.rq3_root/bench/'subjects'/subject
        target=a.rq1_root/bench/'subjects'/subject; result=target/'result.json'
        doc=json.loads(result.read_text()) if result.is_file() else {}
        old=next((x for x in doc.get('rq3_mechanical_closure',[]) if x.get('frozen_identity')==ident),None)
        # Never overwrite an already materialized closure.
        if old and old.get('source') and Path(old['source']).is_file():
            continue
        pn=pf.rsplit('#',1)[-1] if '#' in pf else ''
        candidates=[]
        if sd.is_dir():
            marker=f'__{unit}__pf{pn}'
            encmarker=f'__{enc}__'
            candidates=[p for p in sd.rglob('*.t.sol') if marker in str(p) and encmarker in str(p)]
            # An RQ3 replay can be emitted once and indexed under another
            # encoder.  Keep this explicitly source-only and preserve that
            # distinction in the closure tier; never manufacture a PUT.
            if not candidates:
                candidates=[p for p in sd.rglob('*.t.sol') if marker in str(p)]
        candidates.sort(key=lambda p:(0 if '/test/' in str(p) else 1,0 if '/emit/' in str(p) else 1,str(p)))
        row={'frozen_identity':ident,'status':'missing'}
        if not candidates:
            missing+=1; rows.append(row); continue
        source=candidates[0]; key=hashlib.sha256('\t'.join(ident).encode()).hexdigest()[:20]
        dest=target/'put'/'rq3-mechanical'/'unindexed-exact'/key
        copied=dest/'test'/source.name
        row.update(status='source-backed-exact-scan',source=str(source),destination=str(copied),source_sha256=sha(source))
        if a.apply:
            dest.mkdir(parents=True,exist_ok=True); copied.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,copied)
            project=next((p for p in (source.parent,*source.parents) if (p/'foundry.toml').is_file()),None)
            flat=project/'src'/'flat.sol' if project else None; flatcopy=None
            if flat and flat.is_file(): flatcopy=dest/'src'/'flat.sol'; flatcopy.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(flat,flatcopy)
            closures=[x for x in doc.get('rq3_mechanical_closure',[]) if x.get('frozen_identity')!=ident]
            tier='exact-identity-source-scan' if encmarker in str(source) else 'same-path-function-cross-enc-source-scan'
            closures.append({'schema':'veriput-rq3-mechanical-source-only/v1','frozen_identity':ident,'rq3_identity':ident,'match_tier':tier,'source':str(copied),'source_sha256':sha(copied),'test':source.stem,'put_json':None,'forge_run':False,'put_credit':False,'source_only':True,'flat_source':str(flatcopy) if flatcopy else None})
            doc['rq3_mechanical_closure']=closures; result.write_text(json.dumps(doc,indent=2,sort_keys=True)+'\n'); row['result']=str(result); added+=1
        rows.append(row)
    summary={'rows_examined':len(rows),'added':added,'missing':missing,'apply':a.apply}
    out={'schema':'veriput-rq3-mechanical-physical-recovery/v1','summary':summary,'rows':rows,'policy':{'esbmc_run':False,'forge_run':False,'put_credit':False}}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(summary,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
