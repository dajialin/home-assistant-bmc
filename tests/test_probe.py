"""Offline tooling tests, not E5 protocol or HA integration acceptance."""
import json
import unittest
from unittest.mock import patch

import websocket

from tools.ha_bluetooth_probe import capture, endpoint


class FakeSocket:
    def __init__(self, auth="auth_ok"):
        self.frames = iter([{"type": "auth_required"}, {"type": auth},
                            {"id": 1, "type": "result", "success": True}])
        self.sent = []
        self.closed = False

    def recv(self):
        try:
            return json.dumps(next(self.frames))
        except StopIteration:
            raise websocket.WebSocketTimeoutException()

    def send(self, value):
        self.sent.append(json.loads(value))

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True


class ProbeTests(unittest.TestCase):
    def test_endpoint(self):
        self.assertEqual(endpoint("https://example.org/ha/"), "wss://example.org/ha/api/websocket")
        for url in ("http://example.org", "https://user:pass@example.org", "https://example.org/?token=x", "https://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                endpoint(url)

    def test_subscription_only_and_no_token_in_capture(self):
        socket = FakeSocket()
        with patch("tools.ha_bluetooth_probe.websocket.create_connection", return_value=socket):
            data = capture("https://example.org", "SECRET_FOR_TEST_ONLY", 1)
        self.assertTrue(socket.closed)
        self.assertNotIn("SECRET_FOR_TEST_ONLY", json.dumps(data))
        self.assertEqual([m["type"] for m in socket.sent], [
            "auth", "bluetooth/subscribe_scanner_details", "bluetooth/subscribe_advertisements",
            "bluetooth/subscribe_connection_allocations"])
        self.assertEqual(len(data["messages"]), 1)

    def test_auth_failure_closes_without_subscribing(self):
        socket = FakeSocket("auth_invalid")
        with patch("tools.ha_bluetooth_probe.websocket.create_connection", return_value=socket):
            with self.assertRaisesRegex(RuntimeError, "authentication failed"):
                capture("https://example.org", "SECRET_FOR_TEST_ONLY", 1)
        self.assertTrue(socket.closed)
        self.assertEqual(len(socket.sent), 1)


if __name__ == "__main__":
    unittest.main()
