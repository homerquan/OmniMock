from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from omnimock.domain.errors import ConfigurationError, ErrorContext
from omnimock.domain.http import (
    CorsDefinition,
    HttpManifest,
    HttpManifestLimits,
    HttpResponseDefinition,
    HttpRouteDefinition,
    WebSocketDefinition,
    WebSocketInteractionDefinition,
)
from omnimock.domain.values import JsonValue
from omnimock.infrastructure.config.yaml_loader import load_document

_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::path)?\}")


def load_http_manifest(project_root: Path, configured_path: str) -> HttpManifest:
    path = _project_file(project_root, configured_path)
    document: object = load_document(path)
    raw = _mapping(document, "OMC-HTTP-MANIFEST-001", "HTTP manifest must be a mapping", path)
    _reject_unknown(
        raw,
        {
            "$schema",
            "schema_version",
            "kind",
            "id",
            "base_paths",
            "limits",
            "cors",
            "routes",
            "websockets",
        },
        "manifest",
        path,
    )
    if str(raw.get("schema_version", "")) != "1":
        raise _error("OMC-HTTP-MANIFEST-002", "Unsupported HTTP manifest schema version", path)
    if raw.get("kind") != "http":
        raise _error("OMC-HTTP-MANIFEST-003", "HTTP manifest kind must be http", path)
    manifest_id = str(raw.get("id", "")).strip()
    if not manifest_id:
        raise _error("OMC-HTTP-MANIFEST-004", "HTTP manifest id is required", path)
    base_paths = _base_paths(raw.get("base_paths", [""]), path)
    limits = _limits(raw.get("limits", {}), path)
    cors = _cors(raw.get("cors", {}), path)
    routes = _routes(raw.get("routes"), path, limits)
    websockets = _websockets(raw.get("websockets", []), path, limits)
    return HttpManifest(manifest_id, base_paths, routes, websockets, limits, cors)


def _project_file(project_root: Path, configured_path: str) -> Path:
    relative = Path(configured_path)
    if relative.is_absolute():
        raise _error("OMC-SECURITY-HTTP-001", "HTTP manifest path must be relative", project_root)
    root = project_root.resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise _error(
            "OMC-SECURITY-HTTP-002", "HTTP manifest path escapes the project root", project_root
        )
    if not path.is_file():
        raise _error("OMC-HTTP-MANIFEST-005", "HTTP manifest was not found", path)
    return path


def _base_paths(value: object, path: Path) -> tuple[str, ...]:
    items = _list(value)
    if items is None or not items:
        raise _error("OMC-HTTP-MANIFEST-006", "base_paths must be a non-empty list", path)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise _error("OMC-HTTP-MANIFEST-006", "base_paths must contain strings", path)
        base = item
        if base and (not base.startswith("/") or base.endswith("/")):
            raise _error(
                "OMC-HTTP-MANIFEST-007", "base paths must start with / and omit a trailing /", path
            )
        if base in result:
            raise _error("OMC-HTTP-MANIFEST-008", f"Duplicate base path: {base}", path)
        result.append(base)
    return tuple(result)


def _limits(value: object, path: Path) -> HttpManifestLimits:
    values = _mapping(value, "OMC-HTTP-MANIFEST-009", "limits must be a mapping", path)
    _reject_unknown(
        values,
        {
            "max_request_body_bytes",
            "max_response_body_bytes",
            "max_websocket_messages",
            "max_websocket_message_bytes",
            "max_websocket_interactions",
            "websocket_idle_timeout_ms",
        },
        "limits",
        path,
    )
    request = _bounded_int(
        values.get("max_request_body_bytes", 1_000_000),
        1,
        10_000_000,
        "max_request_body_bytes",
        path,
    )
    response = _bounded_int(
        values.get("max_response_body_bytes", 2_000_000),
        1,
        10_000_000,
        "max_response_body_bytes",
        path,
    )
    messages = _bounded_int(
        values.get("max_websocket_messages", 100), 0, 1_000, "max_websocket_messages", path
    )
    message_bytes = _bounded_int(
        values.get("max_websocket_message_bytes", 1_000_000),
        1,
        10_000_000,
        "max_websocket_message_bytes",
        path,
    )
    interactions = _bounded_int(
        values.get("max_websocket_interactions", 100),
        0,
        1_000,
        "max_websocket_interactions",
        path,
    )
    idle_timeout = _bounded_int(
        values.get("websocket_idle_timeout_ms", 5_000),
        1,
        60_000,
        "websocket_idle_timeout_ms",
        path,
    )
    return HttpManifestLimits(
        request, response, messages, message_bytes, interactions, idle_timeout
    )


def _cors(value: object, path: Path) -> CorsDefinition:
    values = _mapping(value, "OMC-HTTP-MANIFEST-010", "cors must be a mapping", path)
    _reject_unknown(values, {"allow_origins", "allow_headers", "allow_methods"}, "cors", path)
    origins = _string_list(values.get("allow_origins", []), "cors.allow_origins", path)
    headers = _string_list(
        values.get("allow_headers", ["Accept", "Authorization", "Content-Type", "Idempotency-Key"]),
        "cors.allow_headers",
        path,
    )
    methods = tuple(
        item.upper()
        for item in _string_list(
            values.get(
                "allow_methods", ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
            ),
            "cors.allow_methods",
            path,
        )
    )
    if any(method not in _METHODS for method in methods):
        raise _error(
            "OMC-HTTP-MANIFEST-011", "cors.allow_methods contains an unsupported method", path
        )
    return CorsDefinition(origins, headers, methods)


def _routes(
    value: object, path: Path, limits: HttpManifestLimits
) -> tuple[HttpRouteDefinition, ...]:
    items = _list(value)
    if items is None or not items:
        raise _error("OMC-HTTP-MANIFEST-012", "routes must be a non-empty list", path)
    routes: list[HttpRouteDefinition] = []
    identities: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for raw_value in items:
        raw = _mapping(raw_value, "OMC-HTTP-MANIFEST-013", "Every route must be a mapping", path)
        _reject_unknown(raw, {"id", "method", "path", "response"}, "route", path)
        route_id = str(raw.get("id", "")).strip()
        method = str(raw.get("method", "")).upper()
        route_path = str(raw.get("path", ""))
        if not route_id or route_id in ids:
            raise _error("OMC-HTTP-MANIFEST-014", f"Route id must be unique: {route_id}", path)
        if method not in _METHODS:
            raise _error("OMC-HTTP-MANIFEST-015", f"Unsupported route method: {method}", path)
        _validate_route_path(route_path, path)
        identity = (method, route_path)
        if identity in identities:
            raise _error("OMC-HTTP-MANIFEST-016", f"Duplicate route: {method} {route_path}", path)
        response = _response(raw.get("response"), path, limits)
        routes.append(HttpRouteDefinition(route_id, method, route_path, response))
        ids.add(route_id)
        identities.add(identity)
    return tuple(routes)


def _response(value: object, path: Path, limits: HttpManifestLimits) -> HttpResponseDefinition:
    values = _mapping(value, "OMC-HTTP-MANIFEST-017", "route.response must be a mapping", path)
    _reject_unknown(values, {"status", "headers", "content_type", "body"}, "route.response", path)
    status = _bounded_int(values.get("status", 200), 100, 599, "route.response.status", path)
    raw_headers = _mapping(
        values.get("headers", {}),
        "OMC-HTTP-MANIFEST-018",
        "route.response.headers must be a mapping",
        path,
    )
    headers: dict[str, str] = {}
    for key, item in raw_headers.items():
        if not isinstance(item, str) or not _safe_header(key) or not _safe_header(item):
            raise _error(
                "OMC-HTTP-MANIFEST-018", "route.response.headers must contain strings", path
            )
        headers[key] = item
    body = _json_value(values.get("body"), path)
    encoded_size = len(_json_bytes(body))
    if encoded_size > limits.max_response_body_bytes:
        raise _error(
            "OMC-HTTP-MANIFEST-019", "route response exceeds max_response_body_bytes", path
        )
    content_type_value = values.get("content_type", "application/json")
    if not isinstance(content_type_value, str) or not _safe_header(content_type_value):
        raise _error("OMC-HTTP-MANIFEST-029", "route.response.content_type is invalid", path)
    return HttpResponseDefinition(status, body, headers, content_type_value)


def _websockets(
    value: object, path: Path, limits: HttpManifestLimits
) -> tuple[WebSocketDefinition, ...]:
    items = _list(value)
    if items is None:
        raise _error("OMC-HTTP-MANIFEST-020", "websockets must be a list", path)
    result: list[WebSocketDefinition] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for raw_value in items:
        raw = _mapping(
            raw_value, "OMC-HTTP-MANIFEST-021", "Every websocket must be a mapping", path
        )
        _reject_unknown(
            raw,
            {
                "id",
                "path",
                "messages",
                "interactions",
                "on_unmatched",
                "close_after_messages",
                "close_code",
                "close_reason",
                "message_interval_ms",
            },
            "websocket",
            path,
        )
        websocket_id = str(raw.get("id", "")).strip()
        websocket_path = str(raw.get("path", ""))
        if not websocket_id or websocket_id in ids or websocket_path in paths:
            raise _error("OMC-HTTP-MANIFEST-022", "WebSocket ids and paths must be unique", path)
        _validate_route_path(websocket_path, path)
        messages = _websocket_messages(raw.get("messages", []), limits, path)
        interactions = _websocket_interactions(raw.get("interactions", []), limits, path)
        on_unmatched = _websocket_messages(raw.get("on_unmatched", []), limits, path)
        declared_messages = (
            len(messages)
            + len(on_unmatched)
            + sum(len(interaction.messages) for interaction in interactions)
        )
        if declared_messages > limits.max_websocket_messages:
            raise _error(
                "OMC-HTTP-MANIFEST-023", "WebSocket messages exceed the configured bound", path
            )
        close_after_messages = raw.get("close_after_messages", True)
        if not isinstance(close_after_messages, bool):
            raise _error("OMC-HTTP-MANIFEST-033", "close_after_messages must be a boolean", path)
        close_code = _bounded_int(
            raw.get("close_code", 1000), 1000, 4999, "websocket.close_code", path
        )
        if close_code in {1004, 1005, 1006, 1015}:
            raise _error("OMC-HTTP-MANIFEST-034", "WebSocket close code is reserved", path)
        close_reason = raw.get("close_reason", "")
        if not isinstance(close_reason, str) or len(close_reason.encode()) > 123:
            raise _error("OMC-HTTP-MANIFEST-035", "WebSocket close reason is invalid", path)
        interval = _bounded_int(
            raw.get("message_interval_ms", 0),
            0,
            10_000,
            "websocket.message_interval_ms",
            path,
        )
        result.append(
            WebSocketDefinition(
                websocket_id,
                websocket_path,
                messages,
                interactions,
                on_unmatched,
                close_after_messages,
                close_code,
                close_reason,
                interval,
            )
        )
        ids.add(websocket_id)
        paths.add(websocket_path)
    return tuple(result)


def _websocket_messages(
    value: object, limits: HttpManifestLimits, path: Path
) -> tuple[JsonValue, ...]:
    items = _list(value)
    if items is None or len(items) > limits.max_websocket_messages:
        raise _error(
            "OMC-HTTP-MANIFEST-023", "WebSocket messages exceed the configured bound", path
        )
    messages = tuple(_json_value(message, path) for message in items)
    if any(len(_json_bytes(message)) > limits.max_websocket_message_bytes for message in messages):
        raise _error(
            "OMC-HTTP-MANIFEST-036", "WebSocket message exceeds max_websocket_message_bytes", path
        )
    return messages


def _websocket_interactions(
    value: object, limits: HttpManifestLimits, path: Path
) -> tuple[WebSocketInteractionDefinition, ...]:
    items = _list(value)
    if items is None or len(items) > limits.max_websocket_interactions:
        raise _error(
            "OMC-HTTP-MANIFEST-037", "WebSocket interactions exceed the configured bound", path
        )
    interactions: list[WebSocketInteractionDefinition] = []
    ids: set[str] = set()
    for item in items:
        raw = _mapping(
            item, "OMC-HTTP-MANIFEST-038", "Every WebSocket interaction must be a mapping", path
        )
        _reject_unknown(raw, {"id", "match", "messages"}, "websocket interaction", path)
        interaction_id = str(raw.get("id", "")).strip()
        if not interaction_id or interaction_id in ids:
            raise _error("OMC-HTTP-MANIFEST-039", "WebSocket interaction ids must be unique", path)
        matched = _mapping(
            raw.get("match"),
            "OMC-HTTP-MANIFEST-040",
            "WebSocket interaction match must be a non-empty mapping",
            path,
        )
        if not matched:
            raise _error(
                "OMC-HTTP-MANIFEST-040",
                "WebSocket interaction match must be a non-empty mapping",
                path,
            )
        match_value = {key: _json_value(item_value, path) for key, item_value in matched.items()}
        messages = _websocket_messages(raw.get("messages", []), limits, path)
        interactions.append(WebSocketInteractionDefinition(interaction_id, match_value, messages))
        ids.add(interaction_id)
    return tuple(interactions)


def _validate_route_path(value: str, path: Path) -> None:
    if not value.startswith("/") or (len(value) > 1 and value.endswith("/")) or len(value) > 512:
        raise _error(
            "OMC-HTTP-MANIFEST-024",
            "Route paths must be bounded absolute URL paths without a trailing /",
            path,
        )
    without_parameters = _PATH_PARAMETER.sub("", value)
    if "{" in without_parameters or "}" in without_parameters:
        raise _error("OMC-HTTP-MANIFEST-030", "Route path contains an invalid parameter", path)
    names = [matched.group(1) for matched in _PATH_PARAMETER.finditer(value)]
    if len(names) != len(set(names)):
        raise _error("OMC-HTTP-MANIFEST-031", "Route path parameter names must be unique", path)


def _string_list(value: object, label: str, path: Path) -> tuple[str, ...]:
    items = _list(value)
    if items is None:
        raise _error("OMC-HTTP-MANIFEST-025", f"{label} must be a list of non-empty strings", path)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or not _safe_header(item):
            raise _error(
                "OMC-HTTP-MANIFEST-025", f"{label} must be a list of non-empty strings", path
            )
        result.append(item)
    return tuple(result)


def _bounded_int(value: object, minimum: int, maximum: int, label: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _error("OMC-HTTP-MANIFEST-026", f"{label} must be an integer", path)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _error("OMC-HTTP-MANIFEST-026", f"{label} must be an integer", path) from exc
    if not minimum <= parsed <= maximum:
        raise _error(
            "OMC-HTTP-MANIFEST-027", f"{label} must be between {minimum} and {maximum}", path
        )
    return parsed


def _reject_unknown(value: Mapping[str, object], allowed: set[str], label: str, path: Path) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise _error(
            "OMC-HTTP-MANIFEST-028", f"Unknown {label} field(s): {', '.join(unknown)}", path
        )


def _json_bytes(value: JsonValue) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _mapping(value: object, code: str, message: str, path: Path) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _error(code, message, path)
    source = cast(Mapping[object, object], value)
    result: dict[str, object] = {}
    for key, item in source.items():
        if not isinstance(key, str):
            raise _error(code, message, path)
        result[key] = item
    return result


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None


def _json_value(value: object, path: Path) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    items = _list(value)
    if items is not None:
        return [_json_value(item, path) for item in items]
    if isinstance(value, Mapping):
        mapping = _mapping(
            cast(object, value),
            "OMC-HTTP-MANIFEST-032",
            "Manifest response data must be valid JSON",
            path,
        )
        return {key: _json_value(item, path) for key, item in mapping.items()}
    raise _error("OMC-HTTP-MANIFEST-032", "Manifest response data must be valid JSON", path)


def _safe_header(value: str) -> bool:
    return bool(value) and "\r" not in value and "\n" not in value and len(value) <= 8192


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(ErrorContext(code, message, source=str(path)))
