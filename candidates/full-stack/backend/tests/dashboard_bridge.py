"""Synthetic in-process API bridge for the actual dashboard JS. No listening socket."""
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from mom_health_backend.app import Settings, create_app

folder = Path(__file__).resolve().parents[2] / 'build' / 'integration' / str(uuid4())
folder.mkdir(parents=True)
# Test constant only; never deployed or stored as an operational credential.
TOKEN = 'synthetic-integration-token-not-for-deployment'
app = create_app(Settings(sqlite_path=str(folder / 'synthetic.sqlite3'), access_token=TOKEN))
with TestClient(app, client=('127.0.0.1', 51000)) as client:
    for line in sys.stdin:
        request = json.loads(line)
        if request.get('close'):
            break
        response = client.request(request.get('method', 'GET'), request['path'],
                                  headers=request.get('headers', {}), json=request.get('body'))
        print(json.dumps({'status': response.status_code, 'headers': dict(response.headers),
                          'body': response.text}), flush=True)
