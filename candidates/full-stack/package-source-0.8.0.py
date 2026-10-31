"""Package reviewed source extensions only; excludes credentials, stores and build inputs."""
from pathlib import Path
import hashlib, json, zipfile
root = Path(__file__).resolve().parent
files = list(root.glob('*.md')) + list(root.glob('*.py')) + [root/'AndroidManifest.xml', root/'backend/deployment/Caddyfile']
allowed = {'.java', '.py', '.sql', '.html', '.css', '.js', '.md', '.txt'}
for folder in ('src', 'tests', 'backend'):
    files += [p for p in (root/folder).rglob('*') if p.is_file() and p.suffix in allowed and '__pycache__' not in p.parts]
target = root/'build/Mom-Health-0.8.0-candidate-source-complete.zip'
manifest = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in manifest: archive.write(root/name, name)
    archive.writestr('SOURCE_SHA256.json', json.dumps(manifest, indent=2))
digest = hashlib.sha256(target.read_bytes()).hexdigest()
(root/'build/source-0.8.0-complete-sha256.txt').write_text(digest+'  '+target.name+'\n')
print(target)
print(digest)
