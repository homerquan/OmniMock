from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from omnimock.domain.cli import CliManifest, match_cli_command
from omnimock.domain.values import JsonValue

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class CliExecutionResult:
    command_id: str | None
    exit_code: int
    stdout: str
    stderr: str


def execute_cli(manifest: CliManifest, argv: Sequence[str], clock: datetime) -> CliExecutionResult:
    if len(argv) > manifest.limits.max_arg_count or any(
        len(item) > manifest.limits.max_arg_length or "\x00" in item for item in argv
    ):
        return CliExecutionResult(None, 2, "", "mn: arguments exceed configured limits\n")
    matched = match_cli_command(manifest, argv)
    if matched is None:
        rendered = " ".join(argv)
        return CliExecutionResult(None, 2, "", f"mn: unsupported mock command: {rendered}\n")
    context: dict[str, JsonValue] = {
        "arg": dict(matched.captures),
        "argv": list(argv),
        "extra": list(matched.extra_args),
        "clock": {"now": clock.isoformat().replace("+00:00", "Z")},
    }
    response = matched.command.response
    stdout_value = _render_value(response.stdout, context)
    stdout = (
        stdout_value
        if isinstance(stdout_value, str)
        else json.dumps(stdout_value, sort_keys=True, separators=(",", ":")) + "\n"
    )
    stderr_value = _render_value(response.stderr, context)
    stderr = stderr_value if isinstance(stderr_value, str) else str(stderr_value)
    if len(stdout.encode()) > manifest.limits.max_stdout_bytes:
        return CliExecutionResult(matched.command.id, 2, "", "mn: stdout limit exceeded\n")
    if len(stderr.encode()) > manifest.limits.max_stderr_bytes:
        return CliExecutionResult(matched.command.id, 2, "", "mn: stderr limit exceeded\n")
    return CliExecutionResult(matched.command.id, response.exit_code, stdout, stderr)


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
