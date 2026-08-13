from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from omnimock.domain.values import JsonValue

_ARGUMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)(?::(path))?\}$")


@dataclass(frozen=True, slots=True)
class CliResponseDefinition:
    exit_code: int
    stdout: JsonValue
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class CliCommandDefinition:
    id: str
    args: tuple[str, ...]
    response: CliResponseDefinition
    allow_extra_args: bool = False


@dataclass(frozen=True, slots=True)
class CliManifestLimits:
    max_arg_count: int = 128
    max_arg_length: int = 4_096
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 100_000


@dataclass(frozen=True, slots=True)
class CliManifest:
    id: str
    commands: tuple[CliCommandDefinition, ...]
    limits: CliManifestLimits = CliManifestLimits()


@dataclass(frozen=True, slots=True)
class CliCommandMatch:
    command: CliCommandDefinition
    captures: Mapping[str, JsonValue]
    extra_args: tuple[str, ...]


def match_cli_command(manifest: CliManifest, argv: Sequence[str]) -> CliCommandMatch | None:
    for command in sorted(manifest.commands, key=_specificity, reverse=True):
        matched = _match_args(command, argv)
        if matched is not None:
            return matched
    return None


def _specificity(command: CliCommandDefinition) -> tuple[int, int, int]:
    literal = sum(1 for item in command.args if _ARGUMENT.fullmatch(item) is None)
    catchall = sum(1 for item in command.args if item.endswith(":path}"))
    return literal, len(command.args), -catchall


def _match_args(command: CliCommandDefinition, argv: Sequence[str]) -> CliCommandMatch | None:
    captures: dict[str, JsonValue] = {}
    cursor = 0
    for expected in command.args:
        parameter = _ARGUMENT.fullmatch(expected)
        if parameter is not None and parameter.group(2) == "path":
            captures[parameter.group(1)] = list(argv[cursor:])
            cursor = len(argv)
            break
        if cursor >= len(argv):
            return None
        if parameter is None:
            if argv[cursor] != expected:
                return None
        else:
            captures[parameter.group(1)] = argv[cursor]
        cursor += 1
    extra = tuple(argv[cursor:])
    if extra and not command.allow_extra_args:
        return None
    return CliCommandMatch(command, captures, extra)
