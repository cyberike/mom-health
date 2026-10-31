"""Configuration for an isolated local Uvicorn test of the direct candidate.

This module does not construct an application, read credentials, bind a socket,
or launch a server.  It is not a Railway deployment entry point.  Its options
are candidates for review, not evidence that public ingress is safe.
"""
from types import MappingProxyType
import uvicorn


# Values are deliberately fixed for review.  These controls are process-local;
# none authenticates the upstream edge or prevents distributed abuse.
_OPTIONS = MappingProxyType({
    'loop': 'asyncio',
    'http': 'h11',
    'ws': 'none',
    'lifespan': 'on',
    'workers': 1,
    'reload': False,
    'proxy_headers': False,
    'access_log': False,
    'server_header': False,
    'date_header': False,
    'log_config': None,
    'log_level': 'critical',
    'limit_concurrency': 64,
    'backlog': 64,
    'timeout_keep_alive': 5,
    'timeout_graceful_shutdown': 30,
    'h11_max_incomplete_event_size': 32768,
})


def candidate_options():
    """Return a copy of the reviewed Uvicorn settings; does not open a socket."""
    return dict(_OPTIONS)


def build_local_config(app, *, port=0):
    """Build loopback-only test configuration; a caller must start the server.

    There is intentionally no function in this module that starts a server or
    binds a non-loopback address.  A real deployment needs separate review and
    authorization, including its own validated secret/configuration flow.
    """
    if not callable(app):
        raise ValueError('A constructed ASGI application is required')
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('Port must be an integer in 0..65535')
    return uvicorn.Config(app, host='127.0.0.1', port=port, **candidate_options())
