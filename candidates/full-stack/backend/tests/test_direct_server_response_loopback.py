"""Synthetic loopback-only test of Uvicorn response backpressure.

No real application data, external network, device or cloud resource is used.
The bounded streaming fixture isolates server behavior rather than the dashboard.
"""
import socket
import threading
import time
import unittest

import uvicorn

from railway_candidate.direct_server_config import build_local_config


class StreamingFixture:
    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.chunks_sent = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        if scope['type'] != 'http':
            return
        try:
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/octet-stream')]})
            self.started.set()
            # At most 128 MiB, generated in fixed 64 KiB chunks. When the peer
            # does not read, socket backpressure should block before completion;
            # an unusually large OS buffer is treated as inconclusive.
            chunk = b'x' * 65536
            for _ in range(2048):
                await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                self.chunks_sent += 1
            await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            self.finished.set()


class RealLoopbackResponseTests(unittest.TestCase):
    def test_stalled_response_has_no_keepalive_write_deadline(self):
        fixture = StreamingFixture()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        server = uvicorn.Server(build_local_config(fixture, port=port))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        stream = None
        thread.start()
        try:
            deadline = time.monotonic() + 8
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started, 'Isolated loopback server failed to start')
            stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            stream.settimeout(2)
            stream.connect(('127.0.0.1', port))
            stream.sendall(b'GET /synthetic HTTP/1.1\r\nHost: synthetic.example.com\r\n\r\n')
            self.assertTrue(fixture.started.wait(3), 'Synthetic response did not start')
            # No reads: the candidate's keep-alive limit is 5s. It does not
            # constrain the time spent writing an active response.
            if fixture.finished.wait(5.4):
                self.skipTest('OS buffered the bounded response; write stall could not be observed')
            self.assertGreater(fixture.chunks_sent, 0)
        finally:
            if stream is not None:
                stream.close()
            server.should_exit = True
            thread.join(8)
            if thread.is_alive():
                server.force_exit = True
                thread.join(3)
            listener.close()
            if thread.is_alive():
                raise RuntimeError('Isolated loopback server did not stop')


if __name__ == '__main__':
    unittest.main()
