"""Prepare an immutable minimal upload tree for the approved read-only probe."""
from pathlib import Path
import hashlib, json, shutil

backend = Path(__file__).resolve().parents[1]
dest = backend.parent / 'build' / 'railway-volume-inspection-20261028'
dest.mkdir(parents=False, exist_ok=False)
selected = [backend/'requirements.txt']
selected.extend(sorted((backend/'mom_health_backend').glob('*.py')))
selected.extend(backend/'railway_candidate'/name for name in ('gateway.py', 'inspect_volume.py', 'inspect_supervisor.py'))
manifest = {}
for source in selected:
    rel = source.relative_to(backend)
    target = dest/rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    manifest[rel.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
data = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
(dest.parent/(dest.name+'-manifest.json')).write_bytes(data)
print('Upload files:', len(manifest))
print('Source manifest SHA-256:', hashlib.sha256(data).hexdigest())
