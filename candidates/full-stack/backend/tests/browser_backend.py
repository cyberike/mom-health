"""Loopback-only synthetic backend for the real-browser dashboard checks.

Started by ``dashboard/browser.test.js``; not a deployment entry point. It binds
127.0.0.1 only and uses a disposable SQLite file inside ``build/browser-fixtures``.

``--mode local`` (default) serves the unmodified ``create_app`` with a fictional
bearer token. ``--mode gateway`` and ``--mode direct`` serve the unmodified
Railway ``gateway.py`` or the inactive ``direct_candidate.py`` for the fictional
host ``synthetic.example.com``; the browser maps that name to loopback itself.
Those two require a mounted volume, which is simulated for this one fresh
owner-only fixture directory exactly as the existing suites do; nothing here
establishes real mount, TLS, edge identity or Railway behavior.

A restart check may pass ``--db`` to reopen a local-mode database this launcher
created earlier; any other path is refused so the launcher can never open
retained or real data. Prints one JSON line ``{"port": N, "db": "..."}`` once
Uvicorn is serving, then runs until stdin closes or SIGTERM/SIGINT arrives.
Fixtures are retained for inspection (no recursive cleanup).
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn  # noqa: E402

from mom_health_backend.app import Settings, create_app  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / 'build' / 'browser-fixtures'
# Test constants only; never deployed or stored as operational credentials.
TOKEN = 'synthetic-browser-token-not-for-deployment-0001'
HOST = 'synthetic.example.com'
CAREGIVER = 'fictional-caregiver'
PASSWORD = 'fictional-caregiver-password-not-for-use-1234'


def hosted_app(mode):
    folder = (FIXTURES / str(uuid4())).resolve()
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    database = folder / 'mom-health-synthetic.sqlite3'
    real_ismount = os.path.ismount
    simulated = patch('railway_candidate.gateway.os.path.ismount', side_effect=lambda p: Path(p) == folder or real_ismount(p))
    with simulated:
        if mode == 'gateway':
            from railway_candidate.gateway import create_gateway
            app = create_gateway(HOST, CAREGIVER, PASSWORD, TOKEN, str(database), str(folder), '127.0.0.1/32')
        else:
            from railway_candidate.direct_candidate import create_direct_candidate
            app = create_direct_candidate(HOST, CAREGIVER, PASSWORD, TOKEN, str(database), str(folder))
    return app, database


def fixture_database(requested):
    if requested is None:
        folder = FIXTURES / str(uuid4())
        folder.mkdir(parents=True)
        return folder / 'synthetic.sqlite3'
    path = Path(requested).resolve()
    if path.name != 'synthetic.sqlite3' or path.parent.parent != FIXTURES.resolve() or not path.is_file():
        raise SystemExit('Refusing a database outside the browser fixture directory')
    return path


def loopback_listener(port):
    """The socket Uvicorn serves on: 127.0.0.1 only, and IPPROTO_TCP, as the documented server's listener is
    (uvicorn.run with a host and port lets asyncio bind it; Uvicorn's own Config.bind_socket(), used for --reload and
    --workers, is not). asyncio sets TCP_NODELAY only on such sockets. Without it, every response after a connection's
    first trails its headers by about 40 ms (tests/test_browser_backend.py)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    return listener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--db')
    parser.add_argument('--mode', choices=('local', 'gateway', 'direct'), default='local')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        raise SystemExit('Invalid port')
    if args.mode == 'local':
        database = fixture_database(args.db)
        app = create_app(Settings(sqlite_path=str(database), access_token=TOKEN))
    elif args.db is not None:
        raise SystemExit('Hosted modes always use a fresh fixture')
    else:
        app, database = hosted_app(args.mode)
    listener = loopback_listener(args.port)
    config = uvicorn.Config(app, log_level='warning', access_log=False, lifespan='on',
                            proxy_headers=False, server_header=False, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if not worker.is_alive() or time.monotonic() > deadline:
            raise SystemExit('Synthetic backend did not start')
        time.sleep(0.02)
    print(json.dumps({'port': listener.getsockname()[1], 'db': str(database)}), flush=True)

    def stop(*_):
        server.should_exit = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=lambda: (sys.stdin.read(), stop()), daemon=True).start()
    while worker.is_alive():
        worker.join(0.1)


if __name__ == '__main__':
    main()
