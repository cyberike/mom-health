"""Verify the separate candidate with installed tools and synthetic tests only."""
from pathlib import Path
import json
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
OUT = BUILD / '0.8.3-candidate' / ('verification-' + uuid.uuid4().hex)
OUT.mkdir(parents=False, exist_ok=False)
JDK = Path('C:/Program Files/Android/Android Studio/jbr/bin')
SDK = Path('C:/Users/serge/AppData/Local/Android/Sdk')
TOOLS = SDK / 'build-tools/36.1.0'
JSON_JAR = Path('C:/Program Files/Android/Android Studio/plugins/grazie/lib/org.json-json.jar')
APK = BUILD / '0.8.3-candidate/Mom-Health-0.8.3-candidate-V1-V8.apk'
results = []

def run(args):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    results.append(dict(command=[str(a) for a in args], exit_code=result.returncode,
                        stdout=result.stdout, stderr=result.stderr))
    (OUT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(result.stdout, result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)

# Recompile and rerun the prior portable suite into a fresh directory.
prior_dir = BUILD / '0.8.1-candidate/portable-tests-460dbfd099864a4480e9b8d50a50f2ba'
prior = json.loads((prior_dir / 'results.json').read_text(encoding='utf-8'))
for entry in prior:
    args = [str(OUT) if arg == str(prior_dir) else arg for arg in entry['command']]
    run(args)

# The ACK and status checks use only the installed, non-stub org.json jar.
ack = OUT / 'ack'
ack.mkdir()
sources = [ROOT / 'src/org/momhealth/gateway/data' / (name + '.java')
           for name in ('HealthSample', 'UploadBatch', 'BatchAck', 'UploadOutcome')]
run([JDK / 'javac.exe', '--release', '8', '-encoding', 'UTF-8', '-cp', JSON_JAR,
     '-d', ack, *sources, ROOT / 'tests/data/BatchAcknowledgmentTests.java'])
run([JDK / 'java.exe', '-cp', str(ack) + ';' + str(JSON_JAR),
     'org.momhealth.gateway.data.BatchAcknowledgmentTests'])
run([JDK / 'java.exe', '-jar', TOOLS / 'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', APK])
run([TOOLS / 'zipalign.exe', '-c', '-p', '4', APK])
run([TOOLS / 'aapt2.exe', 'dump', 'badging', APK])
print('Verification record:', OUT / 'results.json')
