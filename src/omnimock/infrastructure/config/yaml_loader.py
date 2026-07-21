from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from omnimock.domain.errors import ConfigurationError, ErrorContext


def load_document(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(ErrorContext("OMC-CONFIG-001", f"Cannot read {path}", str(exc), str(path))) from exc
    if not text.strip():
        raise ConfigurationError(ErrorContext("OMC-CONFIG-002", "Configuration file is empty", source=str(path)))
    try:
        if text.lstrip().startswith(("{", "[")):
            return json.loads(text)
        return _parse_yaml(text)
    except (ValueError, TypeError, KeyError) as exc:
        raise ConfigurationError(ErrorContext("OMC-CONFIG-003", f"Invalid YAML in {path}", str(exc), str(path))) from exc


def _strip_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in ("{}", "[]"):
        return {} if value == "{}" else []
    if value[0:1] == value[-1:] and value[0:1] in ("'", '"'):
        if value[0] == '"':
            return json.loads(value)
        return value[1:-1].replace("''", "'")
    if value.startswith("[") and value.endswith("]"):
        return [_parse_scalar(item) for item in _split_inline(value[1:-1])] if value[1:-1].strip() else []
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, Any] = {}
        for item in _split_inline(value[1:-1]):
            key, separator, raw = item.partition(":")
            if not separator:
                raise ValueError("inline mapping item lacks ':'")
            result[str(_parse_scalar(key))] = _parse_scalar(raw)
        return result
    lowered = value.lower()
    if lowered in ("null", "~"):
        return None
    if lowered in ("true", "false"):
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def _split_inline(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    for char in value:
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        elif quote is None and char in "[{":
            depth += 1
        elif quote is None and char in "]}":
            depth -= 1
        if char == "," and quote is None and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _parse_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw:
            raise ValueError("tabs are not supported for indentation")
        content = _strip_comment(raw).strip(" ")
        if not content or content == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, content))
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError("unexpected indentation")
    return value


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    is_list = lines[index][1].startswith("-")
    result: list[Any] | dict[str, Any] = [] if is_list else {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError("inconsistent indentation")
        if is_list:
            if not content.startswith("-"):
                raise ValueError("mixed list and mapping")
            item = content[1:].strip()
            if not item:
                if index + 1 >= len(lines) or lines[index + 1][0] <= indent:
                    result.append(None)
                    index += 1
                else:
                    child, index = _parse_block(lines, index + 1, lines[index + 1][0])
                    result.append(child)
                continue
            if ":" in item and not item.startswith(("'", '"')):
                key, _, raw = item.partition(":")
                mapping: dict[str, Any] = {str(_parse_scalar(key)): _parse_scalar(raw)} if raw.strip() else {str(_parse_scalar(key)): None}
                index += 1
                if index < len(lines) and lines[index][0] > indent:
                    child, index = _parse_block(lines, index, lines[index][0])
                    if isinstance(child, dict):
                        if raw.strip():
                            mapping.update(child)
                        else:
                            mapping[str(_parse_scalar(key))] = child
                    else:
                        raise ValueError("mapping continuation must be a mapping")
                result.append(mapping)
                continue
            result.append(_parse_scalar(item))
            index += 1
        else:
            if content.startswith("-"):
                raise ValueError("mixed mapping and list")
            key, separator, raw = content.partition(":")
            if not separator:
                raise ValueError("mapping item lacks ':'")
            key = str(_parse_scalar(key))
            index += 1
            if raw.strip():
                result[key] = _parse_scalar(raw)
            elif index < len(lines) and lines[index][0] > indent:
                child, index = _parse_block(lines, index, lines[index][0])
                result[key] = child
            else:
                result[key] = None
    return result, index
