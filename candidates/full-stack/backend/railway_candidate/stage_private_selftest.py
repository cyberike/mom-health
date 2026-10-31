"""Create a minimal, non-overwriting upload tree and hash manifest; no network."""
from pathlib import Path
import hashlib, json, shutil

backend = Path(__file__).resolve().parents[1]
target = backend.parent / 'build' / 'railway-private-selftest-upload-20261028'
target.mkdir(parents=False, exist_ok=False)
selected = [backend / 'requirements.txt']
for folder, extensions in [('mom_health_backend', {'.py'}), ('migrations', {'.sql'}),
                           ('dashboard', {'.html', '.css', '.js'}),
                           ('railway_candidate', {'.py', '.html', '.js'})]:
    selected.extend(p for p in (backend / folder).rglob('*') if p.is_file() and p.suffix in extensions
                    and '__pycache__' not in p.parts and not p.name.startswith('test_')
                    and not p.name.endswith('.test.js') and p.name != 'stage_private_selftest.py')
manifest = {}
for source in sorted(selected):
    relative = source.relative_to(backend)
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    manifest[relative.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
(target.parent / (target.name + '-manifest.json')).write_bytes(encoded)
print('Tree:', target)
print('Files:', len(manifest))
print('Manifest SHA-256:', hashlib.sha256(encoded).hexdigest())
