"""Synthetic checks for the unexposed local Uvicorn profile."""
import os
import unittest
from unittest.mock import patch

from railway_candidate.direct_server_config import build_local_config, candidate_options


async def placeholder(scope, receive, send):
    pass


class LocalServerConfigTests(unittest.TestCase):
    def test_configuration_is_loopback_only_and_does_not_bind(self):
        with patch('socket.socket') as constructor:
            config = build_local_config(placeholder)
        constructor.assert_not_called()
        self.assertEqual(('127.0.0.1', 0), (config.host, config.port))
        self.assertFalse(config.proxy_headers)
        self.assertFalse(config.access_log)
        self.assertEqual(('h11', 'none', 1), (config.http, config.ws, config.workers))

    def test_environment_cannot_override_explicit_worker_or_proxy_settings(self):
        with patch.dict(os.environ, {'WEB_CONCURRENCY': '7', 'FORWARDED_ALLOW_IPS': '*'}):
            config = build_local_config(placeholder, port=8123)
        self.assertEqual(1, config.workers)
        self.assertFalse(config.proxy_headers)
        self.assertEqual(8123, config.port)
        self.assertEqual(64, config.limit_concurrency)
        self.assertEqual(32768, config.h11_max_incomplete_event_size)

    def test_invalid_ports_and_application_are_rejected(self):
        for port in (-1, 65536, True, '8000', 0.5):
            with self.subTest(port=port), self.assertRaises(ValueError):
                build_local_config(placeholder, port=port)
        with self.assertRaises(ValueError):
            build_local_config('module:app')

    def test_exported_options_are_not_mutable_global_configuration(self):
        options = candidate_options()
        options['proxy_headers'] = True
        self.assertFalse(candidate_options()['proxy_headers'])


if __name__ == '__main__':
    unittest.main()
