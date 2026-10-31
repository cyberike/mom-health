"""Offline build using installed Android tools only. No Gradle, downloads, hooks or device access."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile
import uuid

ROOT = Path(__file__).resolve().parent
SDK = Path(os.environ.get('MOM_HEALTH_ANDROID_SDK', 'C:/Users/serge/AppData/Local/Android/Sdk'))
JDK = Path(os.environ.get('MOM_HEALTH_JDK', 'C:/Program Files/Android/Android Studio/jbr'))
TOOLS = SDK / 'build-tools/36.1.0'
ANDROID = SDK / 'platforms/android-36.1/android.jar'
BUILD = ROOT / 'build'
BUILD.mkdir(exist_ok=True)
LOG = []
# Fresh intermediate directories prevent stale classes from entering a later APK.
WORK = BUILD / ('run-' + uuid.uuid4().hex)
WORK.mkdir()

def run(*args):
    command = [str(x) for x in args]
    print('Running:', Path(command[0]).name, flush=True)
    result = subprocess.run(command, cwd=ROOT, text=True, encoding='utf-8', errors='replace', capture_output=True)
    output = result.stdout + result.stderr
    print(output, end='', flush=True)
    LOG.append({'command': command, 'exit_code': result.returncode, 'output': output})
    (BUILD / ('test-0.5.0-log.json' if '--test-only' in sys.argv else 'build-0.5.0-log.json')).write_text(json.dumps(LOG, indent=2), encoding='utf-8')
    if result.returncode:
        raise SystemExit(result.returncode)

for tool in [JDK/'bin/java.exe', JDK/'bin/javac.exe', JDK/'bin/keytool.exe', TOOLS/'aapt2.exe',
             TOOLS/'zipalign.exe', TOOLS/'lib/d8.jar', TOOLS/'lib/apksigner.jar', ANDROID]:
    if not tool.is_file(): raise SystemExit('Missing installed tool: '+str(tool)+'; nothing installed.')
if not (SDK/'licenses/android-sdk-license').is_file():
    raise SystemExit('Existing Android SDK license record absent; no terms accepted by this script.')

java=JDK/'bin/java.exe'
javac=JDK/'bin/javac.exe'
core=[ROOT/'src/org/momhealth/gateway'/n for n in ('DeviceAdapter.java','V1Adapter.java','V8Adapter.java','MeasurementWindow.java','HrSession.java','ReadingDisplay.java','ScreenAwakeState.java','WatchSelection.java')]
test_classes=WORK/'test-classes';test_classes.mkdir()
run(javac,'--release','8','-encoding','UTF-8','-d',test_classes,*core,ROOT/'tests/ProtocolTests.java',ROOT/'tests/V8ProtocolTests.java',ROOT/'tests/HrSessionTests.java',ROOT/'tests/ReadingDisplayTests.java',ROOT/'tests/ScreenAwakeTests.java',ROOT/'tests/WatchSelectionTests.java')
run(java,'-cp',test_classes,'ProtocolTests')
run(java,'-cp',test_classes,'V8ProtocolTests')
run(java,'-cp',test_classes,'HrSessionTests')
run(java,'-cp',test_classes,'ReadingDisplayTests')
run(java,'-cp',test_classes,'ScreenAwakeTests')
run(java,'-cp',test_classes,'WatchSelectionTests')
if '--test-only' in sys.argv: raise SystemExit(0)

# No vendor binaries, resources, signing materials or source enter this input list.
sources=sorted((ROOT/'src').rglob('*.java'))
classes=WORK/'classes';classes.mkdir()
run(javac,'--release','8','-encoding','UTF-8','-classpath',ANDROID,'-d',classes,*sources)
resources=BUILD/'resources.apk'
run(TOOLS/'aapt2.exe','link','-I',ANDROID,'--manifest',ROOT/'AndroidManifest.xml',
    '--min-sdk-version','31','--target-sdk-version','35','--version-code','10','--version-name','0.5.0-local-v1-v8','-o',resources)
dex=WORK/'dex';dex.mkdir()
run(java,'-cp',TOOLS/'lib/d8.jar','com.android.tools.r8.D8','--lib',ANDROID,'--min-api','31',
    '--output',dex,*sorted(classes.rglob('*.class')))
unsigned=BUILD/'unsigned.apk'
with zipfile.ZipFile(resources) as source, zipfile.ZipFile(unsigned,'w') as target:
    for entry in source.infolist(): target.writestr(entry,source.read(entry))
    for path in dex.glob('classes*.dex'): target.write(path,path.name,compress_type=zipfile.ZIP_STORED)
aligned=BUILD/'aligned.apk'
run(TOOLS/'zipalign.exe','-f','-p','4',unsigned,aligned)

# Disposable development certificate only. Not a production/account credential.
# Fixed public Android development password; the keystore remains in ignored local build/.
keystore=BUILD/'local-debug.p12'
if not keystore.exists():
    run(JDK/'bin/keytool.exe','-genkeypair','-keystore',keystore,'-storetype','PKCS12',
        '-storepass','android','-keypass','android','-alias','androiddebugkey','-keyalg','RSA',
        '-keysize','2048','-validity','3650','-dname','CN=Mom Health Local Test,O=Development,C=US','-noprompt')
apk=BUILD/'Mom-Health-0.5.0-V1-V8.apk'
run(java,'-jar',TOOLS/'lib/apksigner.jar','sign','--ks',keystore,'--ks-pass','pass:android',
    '--key-pass','pass:android','--out',apk,aligned)
run(java,'-jar',TOOLS/'lib/apksigner.jar','verify','--verbose',apk)
run(TOOLS/'zipalign.exe','-c','-p','4',apk)
run(TOOLS/'aapt2.exe','dump','badging',apk)
digest=hashlib.sha256(apk.read_bytes()).hexdigest()
(BUILD/'apk-0.5.0-sha256.txt').write_text(digest+'  '+apk.name+'\n',encoding='utf-8')
print('APK:',apk)
print('SHA-256:',digest)
