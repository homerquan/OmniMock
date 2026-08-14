from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import unquote

from omnimock.domain.values import JsonValue

_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::(path))?\}")


@dataclass(frozen=True, slots=True)
class SseEventDefinition:
    id: str
    event: str
    data: JsonValue


@dataclass(frozen=True, slots=True)
class SseDefinition:
    events: tuple[SseEventDefinition, ...]
    heartbeat: bool = True
    event_interval_ms: int = 0
    require_numeric_last_event_id: bool = False


@dataclass(frozen=True, slots=True)
class HttpResponseDefinition:
    status: int
    body: JsonValue
    headers: Mapping[str, str] = field(default_factory=lambda: _empty_headers())
    content_type: str = "application/json"
    sse: SseDefinition | None = None


@dataclass(frozen=True, slots=True)
class HttpRequestDefinition:
    if_match: str | None = None
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class HttpRouteDefinition:
    id: str
    method: str
    path: str
    response: HttpResponseDefinition
    request: HttpRequestDefinition = HttpRequestDefinition()


@dataclass(frozen=True, slots=True)
class WebSocketInteractionDefinition:
    id: str
    match: Mapping[str, JsonValue]
    messages: tuple[JsonValue, ...]


@dataclass(frozen=True, slots=True)
class WebSocketDefinition:
    id: str
    path: str
    messages: tuple[JsonValue, ...]
    interactions: tuple[WebSocketInteractionDefinition, ...] = ()
    on_unmatched: tuple[JsonValue, ...] = ()
    close_after_messages: bool = True
    close_code: int = 1000
    close_reason: str = ""
    message_interval_ms: int = 0


@dataclass(frozen=True, slots=True)
class HttpManifestLimits:
    max_request_body_bytes: int = 1_000_000
    max_response_body_bytes: int = 2_000_000
    max_websocket_messages: int = 100
    max_websocket_message_bytes: int = 1_000_000
    max_websocket_interactions: int = 100
    websocket_idle_timeout_ms: int = 5_000
    max_idempotency_records: int = 1_000


@dataclass(frozen=True, slots=True)
class CorsDefinition:
    allow_origins: tuple[str, ...] = ()
    allow_headers: tuple[str, ...] = ("Accept", "Authorization", "Content-Type", "Idempotency-Key")
    allow_methods: tuple[str, ...] = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")


@dataclass(frozen=True, slots=True)
class ProblemDetailsDefinition:
    type_base: str = "about:blank"
    codes: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HttpManifest:
    id: str
    base_paths: tuple[str, ...]
    routes: tuple[HttpRouteDefinition, ...]
    websockets: tuple[WebSocketDefinition, ...] = ()
    limits: HttpManifestLimits = HttpManifestLimits()
    cors: CorsDefinition = CorsDefinition()
    default_headers: Mapping[str, str] = field(default_factory=lambda: _empty_headers())
    problem_details: ProblemDetailsDefinition = ProblemDetailsDefinition()


@dataclass(frozen=True, slots=True)
class HttpRouteMatch:
    route: HttpRouteDefinition
    path_params: Mapping[str, str]


def match_http_route(
    manifest: HttpManifest, method: str, request_path: str
) -> HttpRouteMatch | None:
    normalized_method = method.upper()
    decoded_path = unquote(request_path)
    candidates = sorted(manifest.routes, key=_route_specificity, reverse=True)
    for route in candidates:
        if route.method != normalized_method:
            continue
        for expanded_path in expanded_paths(manifest.base_paths, route.path):
            matched = _match_path(expanded_path, decoded_path)
            if matched is not None:
                return HttpRouteMatch(route, matched)
    return None


def match_websocket(
    manifest: HttpManifest, request_path: str
) -> tuple[WebSocketDefinition, Mapping[str, str]] | None:
    decoded_path = unquote(request_path)
    for websocket in sorted(
        manifest.websockets, key=lambda item: _path_specificity(item.path), reverse=True
    ):
        for expanded_path in expanded_paths(manifest.base_paths, websocket.path):
            matched = _match_path(expanded_path, decoded_path)
            if matched is not None:
                return websocket, matched
    return None


def expanded_paths(base_paths: tuple[str, ...], path: str) -> tuple[str, ...]:
    if not base_paths:
        return (path,)
    return tuple(f"{base.rstrip('/')}{path}" if base else path for base in base_paths)


def _route_specificity(route: HttpRouteDefinition) -> tuple[int, int, int]:
    return _path_specificity(route.path)


def _path_specificity(path: str) -> tuple[int, int, int]:
    segments = [segment for segment in path.split("/") if segment]
    catchall = sum(1 for segment in segments if segment.endswith(":path}"))
    bounded = sum(
        1 for segment in segments if segment.startswith("{") and not segment.endswith(":path}")
    )
    literal = len(segments) - bounded - catchall
    return literal, len(segments), -catchall


def _match_path(template: str, path: str) -> dict[str, str] | None:
    cursor = 0
    pattern: list[str] = ["^"]
    for found in _PARAMETER.finditer(template):
        pattern.append(re.escape(template[cursor : found.start()]))
        name, converter = found.groups()
        pattern.append(f"(?P<{name}>{'.+' if converter == 'path' else '[^/]+'})")
        cursor = found.end()
    pattern.append(re.escape(template[cursor:]))
    pattern.append("$")
    matched = re.match("".join(pattern), path)
    return matched.groupdict() if matched is not None else None


def _empty_headers() -> Mapping[str, str]:
    return {}

