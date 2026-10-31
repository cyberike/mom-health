import subprocess,sys,json
from pathlib import Path
root=Path(__file__).resolve().parent
results=[]
for suite in ['ContractTests','BufferTests','BatchTests','ReviewTests','IntegrationTests']:
    p=subprocess.run([sys.executable,'-m','unittest','-v','test_chunks.'+suite],cwd=root,capture_output=True,text=True)
    results.append(dict(suite=suite,exit_code=p.returncode,output=p.stdout+p.stderr))
    print(suite+': '+('PASS' if p.returncode==0 else 'FAIL'))
    print(p.stdout+p.stderr)
    if p.returncode:break
(root/'verification.json').write_text(json.dumps(results,indent=2))
raise SystemExit(any(r['exit_code'] for r in results))
