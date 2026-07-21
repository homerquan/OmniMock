from __future__ import annotations

import json
import mimetypes
import threading
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omnimock.application.runtime import SimulationRuntime
from omnimock.domain.errors import OmniMockError


class SimulationRequestHandler(BaseHTTPRequestHandler):
    server_version = "OmniMock/0.1"

    @property
    def runtime(self) -> SimulationRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    @property
    def service_id(self) -> str:
        return self.server.service_id  # type: ignore[attr-defined]

    @property
    def sandbox_root(self) -> Path:
        return self.server.sandbox_root  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._serve_websocket()
            return
        route = urlsplit(self.path).path
        if route == "/health":
            self._send_json(200, {"status": "ok", "ready": True})
            return
        if route == "/routes":
            self._send_json(200, {"routes": ["GET /health", "GET /orders", "POST /orders", "GET /events", "GET /ws", "POST /mcp"]})
            return
        if route == "/orders":
            self._send_json(200, self.runtime.store.query("orders"))
            return
        if route == "/events":
            self._send_events()
            return
        if route.startswith("/files/"):
            self._send_file(route.removeprefix("/files/"))
            return
        if route.startswith("/orders/"):
            order_id = route.removeprefix("/orders/")
            value = self.runtime.store.get_value("orders", order_id)
            if value is None:
                self._send_problem(404, "Order not found", "OMC-HTTP-404")
            else:
                self._send_json(200, value)
            return
        self._send_problem(404, "Operation not declared", "OMC-HTTP-404")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/orders":
            payload = self._read_json()
            self._handle("orders.create", payload, "http")
            return
        if self.path == "/mcp":
            self._handle_mcp(self._read_json())
            return
        self._send_problem(404, "Operation not declared", "OMC-HTTP-404")

    def _handle(self, operation: str, payload: Any, protocol: str) -> None:
        try:
            metadata = {"idempotency_key": self.headers.get("Idempotency-Key")} if self.headers.get("Idempotency-Key") else {}
            result = self.runtime.request(self.service_id, operation, payload, metadata, protocol)
            status = int(result.metadata.get("status", 200))
            self._send_json(status, result.payload, {"X-Omnimock-Provenance": result.provenance.source,
                                                      "X-Omnimock-State-Version": str(result.state_version)})
        except OmniMockError as exc:
            self._send_problem(422 if exc.category == "validation" else 500, exc.context.public_message, exc.context.code)

    def _handle_mcp(self, request: Any) -> None:
        if not isinstance(request, dict):
            self._send_problem(400, "MCP request must be an object", "OMC-MCP-001")
            return
        method = request.get("method")
        params = request.get("params", {})
        if method == "tools/list":
            self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": {"tools": [
                {"name": "create_order", "description": "Create an order", "inputSchema": {"type": "object"}},
                {"name": "find_order", "description": "Find an order", "inputSchema": {"type": "object"}},
            ]}})
            return
        if method == "tools/call" and isinstance(params, dict):
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "create_order":
                try:
                    result = self.runtime.request(self.service_id, "orders.create", arguments, protocol="mcp")
                    body = {"content": [{"type": "text", "text": json.dumps(result.payload, sort_keys=True)}], "isError": result.outcome != "success"}
                    self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": body})
                except OmniMockError as exc:
                    self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": exc.context.public_message}})
                return
            if name == "find_order" and isinstance(arguments, dict):
                value = self.runtime.store.get_value("orders", str(arguments.get("id", "")))
                if value is None:
                    self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32004, "message": "Order not found"}})
                else:
                    self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}]}})
                return
        self._send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": "Method not found"}})

    def _send_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event in self.runtime.store.events():
            if event.event_type == "filesystem.write":
                continue
            payload = json.dumps(event.payload, separators=(",", ":"))
            self.wfile.write(f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    def _serve_websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._send_problem(400, "WebSocket key is required", "OMC-WS-400")
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        for event in self.runtime.store.events():
            if event.event_type == "filesystem.write":
                continue
            self.connection.sendall(_websocket_text_frame(json.dumps({"id": event.event_id, "type": event.event_type, "data": event.payload}, separators=(",", ":"))))
        self.connection.sendall(bytes((0x88, 0)))
        self.close_connection = True

    def _send_file(self, relative: str) -> None:
        try:
            candidate = (self.sandbox_root / relative).resolve()
            root = self.sandbox_root.resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ValueError
            body = candidate.read_bytes()
        except (OSError, ValueError):
            self._send_problem(404, "File not found", "OMC-FS-404")
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = min(int(raw_length), 1_000_000)
        except ValueError:
            length = 0
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_problem(400, "Request body must be valid JSON", "OMC-HTTP-400")
            return {}

    def _send_json(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(value, sort_keys=True, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, header_value in (headers or {}).items():
            self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(body)

    def _send_problem(self, status: int, title: str, code: str) -> None:
        self._send_json(status, {"type": "about:blank", "title": title, "status": status, "code": code})

    def log_message(self, format: str, *args: object) -> None:
        # Payloads and authorization headers are deliberately excluded.
        return


class SimulationServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runtime: SimulationRuntime, service_id: str, sandbox_root: Path) -> None:
        super().__init__(address, SimulationRequestHandler)
        self.runtime = runtime
        self.service_id = service_id
        self.sandbox_root = sandbox_root


def serve(project: Any, runtime: SimulationRuntime, service_id: str, host: str, port: int, sandbox_root: Path) -> SimulationServer:
    server = SimulationServer((host, port), runtime, service_id, sandbox_root)
    thread = threading.Thread(target=server.serve_forever, name=f"omnimock-{service_id}", daemon=True)
    thread.start()
    return server


def _websocket_text_frame(value: str) -> bytes:
    payload = value.encode("utf-8")
    if len(payload) < 126:
        return bytes((0x81, len(payload))) + payload
    if len(payload) <= 65535:
        return bytes((0x81, 126)) + len(payload).to_bytes(2, "big") + payload
    return bytes((0x81, 127)) + len(payload).to_bytes(8, "big") + payload
