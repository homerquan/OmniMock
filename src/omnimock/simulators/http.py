from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import socket
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from omnimock.application.runtime import SimulationRuntime
from omnimock.domain.errors import OmniMockError
from omnimock.domain.http import HttpManifest, match_http_route, match_websocket
from omnimock.domain.values import JsonValue

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class _InvalidBody:
    pass


_INVALID_BODY = _InvalidBody()


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

    @property
    def http_manifest(self) -> HttpManifest | None:
        return self.server.http_manifest  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self.http_manifest is not None:
            self._serve_manifest("GET")
            return
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._serve_websocket()
            return
        route = urlsplit(self.path).path
        if route == "/health":
            self._send_json(200, {"status": "ok", "ready": True})
            return
        if route == "/routes":
            self._send_json(
                200,
                {
                    "routes": [
                        "GET /health",
                        "GET /orders",
                        "POST /orders",
                        "GET /events",
                        "GET /ws",
                        "POST /mcp",
                    ]
                },
            )
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

    def do_POST(self) -> None:
        if self.http_manifest is not None:
            self._serve_manifest("POST")
            return
        if self.path == "/orders":
            payload = self._read_json()
            if isinstance(payload, _InvalidBody):
                return
            self._handle("orders.create", payload, "http")
            return
        if self.path == "/mcp":
            payload = self._read_json()
            if not isinstance(payload, _InvalidBody):
                self._handle_mcp(payload)
            return
        self._send_problem(404, "Operation not declared", "OMC-HTTP-404")

    def do_PUT(self) -> None:
        self._serve_manifest("PUT")

    def do_PATCH(self) -> None:
        self._serve_manifest("PATCH")

    def do_DELETE(self) -> None:
        self._serve_manifest("DELETE")

    def do_HEAD(self) -> None:
        self._serve_manifest("HEAD")

    def do_OPTIONS(self) -> None:
        manifest = self.http_manifest
        if manifest is None:
            self._send_problem(404, "Operation not declared", "OMC-HTTP-404")
            return
        self.send_response(204)
        self._send_cors_headers(manifest)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_manifest(self, method: str) -> None:
        manifest = self.http_manifest
        if manifest is None:
            self._send_problem(404, "Operation not declared", "OMC-HTTP-404")
            return
        parsed = urlsplit(self.path)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._serve_manifest_websocket(
                manifest, parsed.path, parse_qs(parsed.query, keep_blank_values=True)
            )
            return
        matched = match_http_route(manifest, method, parsed.path)
        if matched is None:
            self._send_problem(404, "Operation not declared", "OMC-HTTP-404")
            return
        payload: JsonValue | None = None
        if self.headers.get("Content-Length") not in {None, "0"}:
            decoded = self._read_json(manifest.limits.max_request_body_bytes)
            if isinstance(decoded, _InvalidBody):
                return
            payload = decoded
        query = parse_qs(parsed.query, keep_blank_values=True)
        context: dict[str, JsonValue] = {
            "body": payload if isinstance(payload, dict) else {},
            "path": {key: value for key, value in matched.path_params.items()},
            "query": _query_context(query),
            "request": {"method": method, "path": parsed.path},
            "clock": {"now": self.runtime.scenario.start.isoformat().replace("+00:00", "Z")},
        }
        response = matched.route.response
        rendered = _render_value(response.body, context)
        headers = {
            **dict(response.headers),
            "X-OmniMock-Provenance": "fixture",
            "X-OmniMock-Route": matched.route.id,
        }
        self._send_manifest_body(
            response.status,
            rendered,
            response.content_type,
            headers,
            manifest,
            head=method == "HEAD",
        )

    def _send_manifest_body(
        self,
        status: int,
        value: JsonValue,
        content_type: str,
        headers: dict[str, str],
        manifest: HttpManifest,
        *,
        head: bool,
    ) -> None:
        if content_type == "application/json" or content_type.endswith("+json"):
            body = json.dumps(value, sort_keys=True, default=str).encode()
        elif isinstance(value, str):
            body = value.encode()
        else:
            body = json.dumps(value, sort_keys=True, default=str).encode()
        if len(body) > manifest.limits.max_response_body_bytes:
            self._send_problem(
                500, "Manifest response exceeds configured limit", "OMC-HTTP-RESPONSE-LIMIT"
            )
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers(manifest)
        for key, header_value in headers.items():
            self.send_header(key, header_value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_cors_headers(self, manifest: HttpManifest) -> None:
        origin = self.headers.get("Origin", "")
        allowed = manifest.cors.allow_origins
        if "*" in allowed:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if allowed:
            self.send_header("Access-Control-Allow-Headers", ", ".join(manifest.cors.allow_headers))
            self.send_header("Access-Control-Allow-Methods", ", ".join(manifest.cors.allow_methods))

    def _serve_manifest_websocket(
        self,
        manifest: HttpManifest,
        request_path: str,
        query: dict[str, list[str]],
    ) -> None:
        matched = match_websocket(manifest, request_path)
        if matched is None:
            self._send_problem(404, "WebSocket operation not declared", "OMC-WS-404")
            return
        definition, path_params = matched
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._send_problem(400, "WebSocket key is required", "OMC-WS-400")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        context: dict[str, JsonValue] = {
            "body": {},
            "path": {name: value for name, value in path_params.items()},
            "query": _query_context(query),
            "request": {"method": "WEBSOCKET", "path": request_path},
            "clock": {"now": self.runtime.scenario.start.isoformat().replace("+00:00", "Z")},
        }
        sent = self._send_websocket_messages(
            definition.messages, context, definition.message_interval_ms, manifest, 0
        )
        if definition.close_after_messages:
            self.connection.sendall(
                _websocket_close_frame(definition.close_code, definition.close_reason)
            )
            self.close_connection = True
            return
        self.connection.settimeout(manifest.limits.websocket_idle_timeout_ms / 1_000)
        interactions = 0
        try:
            while (
                interactions < manifest.limits.max_websocket_interactions
                and sent < manifest.limits.max_websocket_messages
            ):
                incoming = _receive_websocket_json(
                    self.connection, manifest.limits.max_websocket_message_bytes
                )
                if incoming is None:
                    break
                interactions += 1
                interaction = next(
                    (
                        item
                        for item in definition.interactions
                        if _message_matches(item.match, incoming)
                    ),
                    None,
                )
                context["message"] = incoming
                context["interaction"] = {
                    "id": interaction.id if interaction is not None else "unmatched"
                }
                messages = (
                    interaction.messages if interaction is not None else definition.on_unmatched
                )
                sent = self._send_websocket_messages(
                    messages, context, definition.message_interval_ms, manifest, sent
                )
        except TimeoutError:
            pass
        except _WebSocketProtocolError as exc:
            self.connection.sendall(_websocket_close_frame(exc.code, exc.reason))
            self.close_connection = True
            return
        except OSError:
            self.close_connection = True
            return
        self.connection.sendall(
            _websocket_close_frame(definition.close_code, definition.close_reason)
        )
        self.close_connection = True

    def _send_websocket_messages(
        self,
        messages: tuple[JsonValue, ...],
        context: Mapping[str, JsonValue],
        interval_ms: int,
        manifest: HttpManifest,
        sent: int,
    ) -> int:
        for message in messages:
            if sent >= manifest.limits.max_websocket_messages:
                break
            rendered = json.dumps(
                _render_value(message, context), sort_keys=True, separators=(",", ":")
            )
            encoded = rendered.encode()
            if len(encoded) > manifest.limits.max_websocket_message_bytes:
                raise _WebSocketProtocolError(1009, "message too large")
            if interval_ms and sent:
                time.sleep(interval_ms / 1_000)
            self.connection.sendall(_websocket_text_frame(rendered))
            sent += 1
        return sent

    def _handle(self, operation: str, payload: Any, protocol: str) -> None:
        try:
            metadata = (
                {"idempotency_key": self.headers.get("Idempotency-Key")}
                if self.headers.get("Idempotency-Key")
                else {}
            )
            result = self.runtime.request(self.service_id, operation, payload, metadata, protocol)
            raw_status = result.metadata.get("status", 200)
            status = int(raw_status) if isinstance(raw_status, (int, str)) else 200
            self._send_json(
                status,
                result.payload,
                {
                    "X-Omnimock-Provenance": result.provenance.source,
                    "X-Omnimock-State-Version": str(result.state_version),
                },
            )
        except OmniMockError as exc:
            self._send_problem(
                422 if exc.category == "validation" else 500,
                exc.context.public_message,
                exc.context.code,
            )

    def _handle_mcp(self, request: Any) -> None:
        if not isinstance(request, dict):
            self._send_problem(400, "MCP request must be an object", "OMC-MCP-001")
            return
        method = request.get("method")
        params = request.get("params", {})
        if method == "tools/list":
            self._send_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {
                        "tools": [
                            {
                                "name": "create_order",
                                "description": "Create an order",
                                "inputSchema": {"type": "object"},
                            },
                            {
                                "name": "find_order",
                                "description": "Find an order",
                                "inputSchema": {"type": "object"},
                            },
                        ]
                    },
                },
            )
            return
        if method == "tools/call" and isinstance(params, dict):
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "create_order":
                try:
                    result = self.runtime.request(
                        self.service_id, "orders.create", arguments, protocol="mcp"
                    )
                    body = {
                        "content": [
                            {"type": "text", "text": json.dumps(result.payload, sort_keys=True)}
                        ],
                        "isError": result.outcome != "success",
                    }
                    self._send_json(
                        200, {"jsonrpc": "2.0", "id": request.get("id"), "result": body}
                    )
                except OmniMockError as exc:
                    self._send_json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": request.get("id"),
                            "error": {"code": -32000, "message": exc.context.public_message},
                        },
                    )
                return
            if name == "find_order" and isinstance(arguments, dict):
                value = self.runtime.store.get_value("orders", str(arguments.get("id", "")))
                if value is None:
                    self._send_json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": request.get("id"),
                            "error": {"code": -32004, "message": "Order not found"},
                        },
                    )
                else:
                    self._send_json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": request.get("id"),
                            "result": {
                                "content": [
                                    {"type": "text", "text": json.dumps(value, sort_keys=True)}
                                ]
                            },
                        },
                    )
                return
        self._send_json(
            200,
            {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32601, "message": "Method not found"},
            },
        )

    def _send_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event in self.runtime.store.events():
            if event.event_type == "filesystem.write":
                continue
            payload = json.dumps(event.payload, separators=(",", ":"))
            self.wfile.write(
                f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n".encode()
            )
        self.wfile.flush()

    def _serve_websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._send_problem(400, "WebSocket key is required", "OMC-WS-400")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        for event in self.runtime.store.events():
            if event.event_type == "filesystem.write":
                continue
            self.connection.sendall(
                _websocket_text_frame(
                    json.dumps(
                        {"id": event.event_id, "type": event.event_type, "data": event.payload},
                        separators=(",", ":"),
                    )
                )
            )
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
        self.send_header(
            "Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, maximum: int = 1_000_000) -> JsonValue | _InvalidBody:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            declared_length = int(raw_length)
        except ValueError:
            self._send_problem(400, "Content-Length must be an integer", "OMC-HTTP-400")
            return _INVALID_BODY
        if declared_length < 0 or declared_length > maximum:
            self._send_problem(413, "Request body exceeds configured limit", "OMC-HTTP-413")
            return _INVALID_BODY
        try:
            return json.loads(self.rfile.read(declared_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_problem(400, "Request body must be valid JSON", "OMC-HTTP-400")
            return _INVALID_BODY

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
        self._send_json(
            status, {"type": "about:blank", "title": title, "status": status, "code": code}
        )

    def log_message(self, format: str, *args: object) -> None:
        # Payloads and authorization headers are deliberately excluded.
        return


class SimulationServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: SimulationRuntime,
        service_id: str,
        sandbox_root: Path,
        http_manifest: HttpManifest | None = None,
    ) -> None:
        super().__init__(address, SimulationRequestHandler)
        self.runtime = runtime
        self.service_id = service_id
        self.sandbox_root = sandbox_root
        self.http_manifest = http_manifest


def serve(
    project: Any,
    runtime: SimulationRuntime,
    service_id: str,
    host: str,
    port: int,
    sandbox_root: Path,
    http_manifest: HttpManifest | None = None,
) -> SimulationServer:
    server = SimulationServer((host, port), runtime, service_id, sandbox_root, http_manifest)
    thread = threading.Thread(
        target=server.serve_forever, name=f"omnimock-{service_id}", daemon=True
    )
    thread.start()
    return server


def _websocket_text_frame(value: str) -> bytes:
    payload = value.encode("utf-8")
    if len(payload) < 126:
        return bytes((0x81, len(payload))) + payload
    if len(payload) <= 65535:
        return bytes((0x81, 126)) + len(payload).to_bytes(2, "big") + payload
    return bytes((0x81, 127)) + len(payload).to_bytes(8, "big") + payload


class _WebSocketProtocolError(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _websocket_close_frame(code: int, reason: str) -> bytes:
    payload = code.to_bytes(2, "big") + reason.encode()
    return bytes((0x88, len(payload))) + payload


def _websocket_control_frame(opcode: int, payload: bytes) -> bytes:
    return bytes((0x80 | opcode, len(payload))) + payload


def _receive_websocket_json(
    connection: socket.socket, maximum_bytes: int
) -> dict[str, JsonValue] | None:
    while True:
        first, second = _recv_exact(connection, 2)
        if first & 0x70 or not first & 0x80:
            raise _WebSocketProtocolError(1002, "fragmented frames are not supported")
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if not masked:
            raise _WebSocketProtocolError(1002, "client frames must be masked")
        if length == 126:
            length = int.from_bytes(_recv_exact(connection, 2), "big")
        elif length == 127:
            length = int.from_bytes(_recv_exact(connection, 8), "big")
        if length > maximum_bytes:
            raise _WebSocketProtocolError(1009, "message too large")
        if opcode >= 8 and length > 125:
            raise _WebSocketProtocolError(1002, "invalid control frame")
        mask = _recv_exact(connection, 4)
        payload = _recv_exact(connection, length)
        decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 8:
            connection.sendall(_websocket_control_frame(8, decoded[:125]))
            return None
        if opcode == 9:
            connection.sendall(_websocket_control_frame(10, decoded))
            continue
        if opcode == 10:
            continue
        if opcode != 1:
            raise _WebSocketProtocolError(1003, "only JSON text messages are supported")
        try:
            value: object = json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _WebSocketProtocolError(1007, "message must be valid JSON") from exc
        if not isinstance(value, dict):
            raise _WebSocketProtocolError(1007, "message must be a JSON object")
        return _json_object(cast(Mapping[object, object], value))


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise OSError("WebSocket peer disconnected")
        data.extend(chunk)
    return bytes(data)


def _json_object(value: Mapping[object, object]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise _WebSocketProtocolError(1007, "message keys must be strings")
        result[key] = _json_runtime_value(item)
    return result


def _json_runtime_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_runtime_value(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return _json_object(cast(Mapping[object, object], value))
    raise _WebSocketProtocolError(1007, "message must contain JSON values")


def _message_matches(expected: Mapping[str, JsonValue], actual: Mapping[str, JsonValue]) -> bool:
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict) or not _message_matches(
                expected_value, actual_value
            ):
                return False
        elif expected_value != actual_value:
            return False
    return True


def _render_value(value: JsonValue, context: Mapping[str, JsonValue]) -> JsonValue:
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, context) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    full = _TOKEN.fullmatch(value)
    if full is not None:
        return _context_value(full.group(1), context)
    return _TOKEN.sub(lambda found: str(_context_value(found.group(1), context) or ""), value)


def _context_value(expression: str, context: Mapping[str, JsonValue]) -> JsonValue:
    value: JsonValue = dict(context)
    for segment in expression.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _query_context(query: Mapping[str, list[str]]) -> dict[str, JsonValue]:
    return {key: values[0] if len(values) == 1 else list(values) for key, values in query.items()}
