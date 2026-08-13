from __future__ import annotations

import json
import socket
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.request import Request, urlopen

from omnimock.application.runtime import SimulationRuntime
from omnimock.domain.errors import ConfigurationError
from omnimock.domain.http import match_http_route
from omnimock.infrastructure.config.http_manifest import load_http_manifest
from omnimock.infrastructure.config.loader import load_project, load_scenario
from omnimock.simulators.http import serve

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = REPOSITORY_ROOT / "samples" / "mirror_neuron"


class HttpManifestTests(unittest.TestCase):
    def test_mirror_neuron_manifest_declares_complete_runtime_surface(self):
        manifest = load_http_manifest(SAMPLE_ROOT, "manifest.json")

        self.assertEqual(manifest.id, "mirror-neuron-runtime-api-v2")
        self.assertEqual(manifest.base_paths, ("/api/v1", "/api/v2"))
        self.assertEqual(len(manifest.routes), 140)
        self.assertEqual(len(manifest.websockets), 6)
        realtime = next(item for item in manifest.websockets if item.id == "realtime")
        self.assertFalse(realtime.close_after_messages)
        self.assertEqual([item.id for item in realtime.interactions], ["subscribe", "unsubscribe"])
        route_ids = {route.id for route in manifest.routes}
        self.assertTrue(
            {
                "blueprints.list",
                "jobs.create",
                "jobs.list",
                "runs.monitor",
                "runs.workflow_progress",
                "runtime_runs.logs",
                "runtime_runs.resources",
                "schedules.create",
                "services.resolve",
                "system.runtime_health",
            }.issubset(route_ids)
        )

    def test_literal_route_wins_over_catchall(self):
        manifest = load_http_manifest(SAMPLE_ROOT, "manifest.json")

        catalog = match_http_route(manifest, "GET", "/api/v2/models/catalog")
        artifact = match_http_route(
            manifest,
            "GET",
            "/api/v2/runtime-runs/run-1/artifacts/reports%2Fsummary.md",
        )

        assert catalog is not None
        assert artifact is not None
        self.assertEqual(catalog.route.id, "models.catalog")
        self.assertEqual(artifact.route.id, "runtime_runs.artifact_get")

    def test_manifest_path_must_not_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "project"
            root.mkdir()
            outside = outer / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            symlink = root / "linked.json"
            symlink.symlink_to(outside)

            for configured in (str(outside), "../outside.json", "linked.json"):
                with self.subTest(configured=configured):
                    with self.assertRaises(ConfigurationError) as caught:
                        load_http_manifest(root, configured)
                    self.assertTrue(caught.exception.context.code.startswith("OMC-SECURITY-HTTP-"))

    def test_unknown_manifest_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "kind": "http",
                        "id": "invalid",
                        "base_paths": [""],
                        "routes": [
                            {
                                "id": "health",
                                "method": "GET",
                                "path": "/health",
                                "response": {"status": 200, "body": {}},
                                "surprise": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigurationError) as caught:
                load_http_manifest(root, "manifest.json")

            self.assertEqual(caught.exception.context.code, "OMC-HTTP-MANIFEST-028")


class HttpManifestServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = load_project(SAMPLE_ROOT)
        runtime = SimulationRuntime(project, load_scenario(project))
        manifest = load_http_manifest(SAMPLE_ROOT, "manifest.json")
        cls.server = serve(
            project,
            runtime,
            "mirror-neuron-api",
            "127.0.0.1",
            0,
            SAMPLE_ROOT / ".omnimock" / "mount",
            manifest,
        )
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_serves_v1_and_v2_aliases(self):
        for version in ("v1", "v2"):
            with (
                self.subTest(version=version),
                urlopen(f"{self.base_url}/api/{version}/health", timeout=2) as response,
            ):
                payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["status"], "ok")
                self.assertEqual(response.headers["X-OmniMock-Route"], "system.health")

    def test_renders_path_query_and_body_tokens_without_code_execution(self):
        with urlopen(
            f"{self.base_url}/api/v2/runtime-runs/run%20demo/resources?window=6h&bucket=5m",
            timeout=2,
        ) as response:
            resources = json.load(response)
        request = Request(
            f"{self.base_url}/api/v2/system/cluster/nodes:remove",
            data=json.dumps({"node_name": "mirror_neuron@10.0.0.42"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            removed = json.load(response)

        self.assertEqual(resources["run_id"], "run demo")
        self.assertEqual(resources["window"], "6h")
        self.assertEqual(resources["bucket"], "5m")
        self.assertEqual(removed["node_name"], "mirror_neuron@10.0.0.42")

    def test_rejects_oversized_request_before_reading_it(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.putrequest("POST", "/api/v2/jobs")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "2000001")
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()

        self.assertEqual(response.status, 413)
        self.assertEqual(payload["code"], "OMC-HTTP-413")

    def test_realtime_websocket_supports_bounded_subscribe_and_unsubscribe(self):
        connection = self._open_websocket("/api/v2/realtime")
        try:
            connection.sendall(
                _masked_websocket_frame(
                    1,
                    json.dumps(
                        {
                            "requestId": "request-1",
                            "action": "subscribe",
                            "topic": "launch_progress:mock-progress",
                            "after": 0,
                        }
                    ).encode(),
                )
            )
            subscribed = json.loads(_read_websocket_frame(connection)[1])
            event = json.loads(_read_websocket_frame(connection)[1])
            connection.sendall(
                _masked_websocket_frame(
                    1,
                    json.dumps(
                        {
                            "requestId": "request-2",
                            "action": "unsubscribe",
                            "topic": "launch_progress:mock-progress",
                        }
                    ).encode(),
                )
            )
            unsubscribed = json.loads(_read_websocket_frame(connection)[1])
            connection.sendall(_masked_websocket_frame(8, (1000).to_bytes(2, "big")))
            close_opcode, _ = _read_websocket_frame(connection)
        finally:
            connection.close()

        self.assertEqual(subscribed["action"], "subscribed")
        self.assertEqual(subscribed["requestId"], "request-1")
        self.assertEqual(event["topic"], "launch_progress:mock-progress")
        self.assertEqual(event["patch"]["status"], "completed")
        self.assertEqual(unsubscribed["action"], "unsubscribed")
        self.assertEqual(close_opcode, 8)

    def test_realtime_websocket_rejects_oversized_client_frames(self):
        connection = self._open_websocket("/api/v2/realtime")
        try:
            declared_length = 262145
            connection.sendall(bytes((0x81, 0x80 | 127)) + declared_length.to_bytes(8, "big"))
            opcode, payload = _read_websocket_frame(connection)
        finally:
            connection.close()

        self.assertEqual(opcode, 8)
        self.assertEqual(int.from_bytes(payload[:2], "big"), 1009)

    def _open_websocket(self, path: str) -> socket.socket:
        connection = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1]), timeout=2
        )
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.server.server_address[1]}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        connection.sendall(request.encode())
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(connection.recv(4096))
        self.assertIn(b"HTTP/1.0 101 Switching Protocols", response)
        return connection


def _masked_websocket_frame(opcode: int, payload: bytes) -> bytes:
    mask = b"\x01\x02\x03\x04"
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length <= 65535:
        header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + length.to_bytes(8, "big")
    encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + encoded


def _read_websocket_frame(connection: socket.socket) -> tuple[int, bytes]:
    first, second = _recv_exact(connection, 2)
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_recv_exact(connection, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recv_exact(connection, 8), "big")
    return first & 0x0F, _recv_exact(connection, length)


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    value = bytearray()
    while len(value) < length:
        chunk = connection.recv(length - len(value))
        if not chunk:
            raise OSError("WebSocket disconnected")
        value.extend(chunk)
    return bytes(value)
