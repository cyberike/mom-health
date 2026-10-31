"""Local synthetic checks and APK inspection only; no app/service/device activation."""
from pathlib import Path
import json, subprocess, uuid

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
OUT = BUILD / ('verification-' + uuid.uuid4().hex)
OUT.mkdir()
JDK = Path('C:/Program Files/Android/Android Studio/jbr/bin')
SDK = Path('C:/Users/serge/AppData/Local/Android/Sdk')
ANDROID = SDK / 'platforms/android-36.1/android.jar'
TOOLS = SDK / 'build-tools/36.1.0'
results = []
def run(args):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    results.append(dict(command=[str(a) for a in args], exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
    (OUT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(result.stdout, result.stderr, flush=True)
    if result.returncode: raise SystemExit(result.returncode)

# Recompile the original portable integration suite into a fresh directory.
prior = json.loads((BUILD / 'integration-synthetic-dea14b7d01ee4b579a2b0c2e151c56d1/results.json').read_text())
old_dir = str(BUILD / 'integration-synthetic-dea14b7d01ee4b579a2b0c2e151c56d1')
for entry in prior:
    args = entry['command'][:]
    # Preserve source paths (including its small V4 runner); replace only output/classpath.
    args = [str(OUT) if arg == old_dir else arg for arg in args]
    run(args)

# Android classes are compiled, but only portable configuration methods are executed.
classes = OUT / 'android-classes'
classes.mkdir()
run([JDK/'javac.exe', '--release', '8', '-encoding', 'UTF-8', '-cp', ANDROID, '-d', classes,
     *sorted((ROOT/'src').rglob('*.java')), ROOT/'tests/service/CaregiverSettingsChecks.java'])
run([JDK/'java.exe', '-cp', str(classes)+';'+str(ANDROID), 'org.momhealth.gateway.CaregiverSettingsChecks'])
apk = BUILD/'Mom-Health-0.8.0-candidate-V1-V8.apk'
run([JDK/'java.exe', '-jar', TOOLS/'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', apk])
run([TOOLS/'zipalign.exe', '-c', '-p', '4', apk])
run([TOOLS/'aapt2.exe', 'dump', 'badging', apk])
run([TOOLS/'aapt2.exe', 'dump', 'permissions', apk])
print('Verification record:', OUT/'results.json')
