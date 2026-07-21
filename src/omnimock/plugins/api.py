from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    version: str
    service_kinds: tuple[str, ...]
    modes: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    optional_extra: str | None = None


class SimulatorRuntime(Protocol):
    def prepare(self) -> None: ...
    def start(self) -> Sequence[str]: ...
    def drain(self) -> None: ...
    def stop(self) -> None: ...


class SimulatorPlugin(Protocol):
    manifest: PluginManifest

    def create_runtime(self, context: object) -> SimulatorRuntime: ...


BUILTIN_MANIFESTS: tuple[PluginManifest, ...] = (
    PluginManifest("builtin.http", "0.1.0", ("http",), ("native",), ("openapi", "problem_details")),
    PluginManifest("builtin.stream.sse", "0.1.0", ("stream",), ("native",), ("sse", "websocket")),
    PluginManifest("builtin.filesystem", "0.1.0", ("filesystem",), ("native",), ("sandbox", "snapshots")),
    PluginManifest("builtin.mcp", "0.1.0", ("mcp",), ("native",), ("tools", "resources")),
)
