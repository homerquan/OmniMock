from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from omnimock.domain.errors import BehaviorResolutionError, ErrorContext, ValidationError
from omnimock.domain.scenario import RuleDefinition, ScenarioDefinition
from omnimock.domain.state import InMemoryStateStore, JournalEntry, digest
from omnimock.domain.values import EventEnvelope, JsonValue, Provenance, RequestEnvelope, ResponseEnvelope


_TEMPLATE = re.compile(r"\$\{([^{}]+)\}")


def deterministic_id(seed: int, namespace: str, value: str) -> str:
    raw = f"{seed}:{namespace}:{value}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


class SafeExpression:
    _allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Name, ast.Load,
                ast.Constant, ast.Subscript, ast.Attribute, ast.Gt, ast.GtE, ast.Lt, ast.LtE,
                ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.UnaryOp, ast.Not, ast.USub,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.BinOp, ast.Index)

    @classmethod
    def evaluate(cls, expression: str, variables: Mapping[str, object]) -> bool:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValidationError(ErrorContext("OMC-VALIDATE-001", "Invalid validation expression")) from exc
        for node in ast.walk(tree):
            if not isinstance(node, cls._allowed):
                raise ValidationError(ErrorContext("OMC-VALIDATE-002", "Expression uses a forbidden operation"))
        try:
            return bool(cls._eval(tree.body, variables))
        except (KeyError, IndexError, TypeError, AttributeError, ZeroDivisionError) as exc:
            raise ValidationError(ErrorContext("OMC-VALIDATE-003", "Validation expression could not be evaluated")) from exc

    @classmethod
    def _eval(cls, node: ast.AST, variables: Mapping[str, object]) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return variables[node.id]
        if isinstance(node, ast.Subscript):
            return cls._eval(node.value, variables)[cls._eval(node.slice, variables)]  # type: ignore[index]
        if isinstance(node, ast.Attribute):
            value = cls._eval(node.value, variables)
            if isinstance(value, Mapping):
                return value[node.attr]
            return getattr(value, node.attr)
        if isinstance(node, ast.BoolOp):
            values = [cls._eval(item, variables) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = cls._eval(node.left, variables)
            for operator, comparator in zip(node.ops, node.comparators):
                right = cls._eval(comparator, variables)
                if isinstance(operator, ast.Gt) and not left > right:
                    return False
                if isinstance(operator, ast.GtE) and not left >= right:
                    return False
                if isinstance(operator, ast.Lt) and not left < right:
                    return False
                if isinstance(operator, ast.LtE) and not left <= right:
                    return False
                if isinstance(operator, ast.Eq) and not left == right:
                    return False
                if isinstance(operator, ast.NotEq) and not left != right:
                    return False
                if isinstance(operator, ast.In) and left not in right:
                    return False
                if isinstance(operator, ast.NotIn) and left in right:
                    return False
                left = right
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not cls._eval(node.operand, variables)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -cls._eval(node.operand, variables)  # type: ignore[operator]
        if isinstance(node, ast.BinOp):
            left, right = cls._eval(node.left, variables), cls._eval(node.right, variables)
            if isinstance(node.op, ast.Add): return left + right  # type: ignore[operator]
            if isinstance(node.op, ast.Sub): return left - right  # type: ignore[operator]
            if isinstance(node.op, ast.Mult): return left * right  # type: ignore[operator]
            if isinstance(node.op, ast.Div): return left / right  # type: ignore[operator]
            if isinstance(node.op, ast.Mod): return left % right  # type: ignore[operator]
        raise ValidationError(ErrorContext("OMC-VALIDATE-004", "Unsupported validation expression"))


def _path_get(root: object, path: list[object]) -> object:
    value = root
    for segment in path:
        if isinstance(value, Mapping):
            value = value[segment]  # type: ignore[index]
        else:
            value = value[segment]  # type: ignore[index]
    return value


def _path_set(root: dict[str, Any], path: list[str], value: object) -> None:
    current: dict[str, Any] = root
    for segment in path[:-1]:
        child = current.setdefault(segment, {})
        if not isinstance(child, dict):
            raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-001", "Mutation path crosses a scalar value"))
        current = child
    current[path[-1]] = copy.deepcopy(value)


def _resolve(value: object, context: Mapping[str, object]) -> object:
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {str(key): _resolve(item, context) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    match = _TEMPLATE.fullmatch(value)
    if match:
        expression = match.group(1)
        if expression == "clock.now":
            return context["clock.now"]
        if expression == "mutation.key":
            return context.get("mutation.key")
        if expression == "mutation.value":
            return context.get("mutation.value")
        if expression.startswith("json.encode("):
            inner = expression[len("json.encode("):-1]
            return json.dumps(_resolve("${" + inner + "}", context), separators=(",", ":"))
        if expression.startswith("uuid.deterministic("):
            args = expression[len("uuid.deterministic("):-1].split(",")
            namespace = args[0].strip(" '\"")
            raw_value = args[1].strip()
            value = raw_value.strip(" '\"") if not raw_value.startswith(("input.", "request.")) else str(_expression_value(raw_value, context))
            return deterministic_id(int(context["seed"]), namespace, value)
        return _expression_value(expression, context)
    return _TEMPLATE.sub(lambda item: str(_resolve("${" + item.group(1) + "}", context)), value)


def _expression_value(expression: str, context: Mapping[str, object]) -> object:
    parts = expression.split(".")
    value: object = context
    for part in parts:
        if isinstance(value, Mapping):
            value = value[part]
        else:
            value = getattr(value, part)
    return value


class BehaviorEngine:
    def __init__(self, scenario: ScenarioDefinition, store: InMemoryStateStore) -> None:
        self.scenario = scenario
        self.store = store

    def handle(self, request: RequestEnvelope) -> ResponseEnvelope:
        payload = request.payload if isinstance(request.payload, dict) else {}
        input_digest = digest(payload)
        idempotency_key = request.metadata.get("idempotency_key")
        if isinstance(idempotency_key, str):
            prior = self.store.get_idempotent(idempotency_key, input_digest)
            if isinstance(prior, ResponseEnvelope):
                return prior
        rule = next((candidate for candidate in self.scenario.rules if self._matches(candidate, request)), None)
        if rule is None:
            raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-404", f"No behavior configured for {request.operation_id}"))
        state = self.store.read_state()
        context: dict[str, object] = {
            "input": payload,
            "state": state,
            "request": {"request_id": request.request_id},
            "clock": {"now": request.logical_time.isoformat().replace("+00:00", "Z")},
            "clock.now": request.logical_time.isoformat().replace("+00:00", "Z"),
            "seed": self.scenario.seed,
        }
        for item in rule.validate:
            expression = item.get("expression")
            if not isinstance(expression, str) or not SafeExpression.evaluate(expression, context):
                raise ValidationError(ErrorContext("OMC-VALIDATE-005", str(item.get("message", "Request violates scenario validation"))))
        working = copy.deepcopy(state)
        events: list[EventEnvelope] = []
        for mutation in rule.mutate:
            self._apply_mutation(working, mutation, context)
        response_payload = _resolve(rule.respond.get("body"), {**context, "mutation.value": context.get("mutation.value")})
        for raw_event in rule.emit:
            event = self._event_from_definition(raw_event, context, request)
            if event is not None:
                events.append(event)
        fault = self._fault(request)
        provenance = Provenance("rule", self.scenario.revision, self.scenario.seed, self.store.state_version(), rule.id)
        outcome = "success"
        metadata: dict[str, JsonValue] = {"status": int(rule.respond.get("status", 200))}
        if fault == "timeout":
            outcome, metadata["status"] = "timeout", 504
            response_payload = {"type": "about:blank", "title": "Simulation timeout", "status": 504}
        elif fault == "error":
            outcome, metadata["status"] = "error", self.scenario.faults.error_status
            response_payload = {"type": "about:blank", "title": "Simulated failure", "status": self.scenario.faults.error_status}
        journal = JournalEntry(0, request.request_id, request.run_id, request.operation_id, input_digest,
                               rule.id, self.store.state_version(), tuple(event.event_id for event in events), fault)
        response = ResponseEnvelope(outcome, metadata, response_payload, self.store.state_version(), provenance)
        committed = self.store.commit(expected_version=self.store.state_version(), state=working, events=tuple(events), journal=journal,
                                      idempotency_key=idempotency_key if isinstance(idempotency_key, str) else None,
                                      idempotency_value=response)
        return ResponseEnvelope(outcome, metadata, response_payload, committed.state_version,
                                Provenance("rule", self.scenario.revision, self.scenario.seed, committed.state_version, rule.id))

    def _matches(self, rule: RuleDefinition, request: RequestEnvelope) -> bool:
        operation = rule.when.get("operation")
        return operation is None or operation == request.operation_id

    def _apply_mutation(self, state: dict[str, JsonValue], mutation: Mapping[str, object], context: dict[str, object]) -> None:
        if "decrement" in mutation:
            item = mutation["decrement"]
            if not isinstance(item, Mapping):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-002", "decrement mutation must be a mapping"))
            path_text = str(item.get("path", ""))
            path = [part for part in path_text.replace("${input.sku}", str(_expression_value("input.sku", context))).split(".") if part]
            current = _path_get(state, path)
            amount = _resolve(item.get("by", 1), context)
            if not isinstance(current, (int, float)) or not isinstance(amount, (int, float)):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-003", "decrement requires numeric values"))
            _path_set(state, path, current - amount)
        if "put" in mutation:
            item = mutation["put"]
            if not isinstance(item, Mapping):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-004", "put mutation must be a mapping"))
            collection, key = str(item.get("collection")), str(_resolve(item.get("key"), context))
            value = _resolve(item.get("value"), {**context, "mutation.key": key})
            collections = state.setdefault("collections", {})
            if not isinstance(collections, dict):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-005", "collections state must be a mapping"))
            bucket = collections.setdefault(collection, {})
            if not isinstance(bucket, dict):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-006", "collection must be a mapping"))
            bucket[key] = value  # type: ignore[assignment]
            context["mutation.key"] = key
            context["mutation.value"] = value

    def _event_from_definition(self, raw: Mapping[str, object], context: Mapping[str, object], request: RequestEnvelope) -> EventEnvelope | None:
        filesystem = raw.get("filesystem")
        if isinstance(filesystem, Mapping):
            path = _resolve(filesystem.get("path", ""), context)
            content = _resolve(filesystem.get("content", ""), context)
            if not isinstance(path, str) or not isinstance(content, str):
                raise BehaviorResolutionError(ErrorContext("OMC-BEHAVIOR-007", "Filesystem projection must resolve to text"))
            event_key = f"{request.request_id}:filesystem:{path}:{len(self.store.events())}"
            return EventEnvelope(deterministic_id(self.scenario.seed, "event", event_key), "filesystem.write", request.service_id,
                                 None, request.logical_time, request.logical_time, None, {"path": path}, content)
        event_type = raw.get("type")
        if not isinstance(event_type, str):
            return None
        event_key = f"{request.request_id}:{event_type}:{len(self.store.events())}"
        payload = _resolve(raw.get("payload"), context)
        return EventEnvelope(deterministic_id(self.scenario.seed, "event", event_key), event_type, request.service_id,
                             None, request.logical_time, request.logical_time, None, {}, payload if isinstance(payload, (dict, list, str, int, float, bool, type(None))) else None)

    def _fault(self, request: RequestEnvelope) -> str | None:
        policy = self.scenario.faults
        bucket = int.from_bytes(hashlib.sha256(f"{self.scenario.seed}:{request.run_id}:{request.service_id}:{request.operation_id}:{request.request_id}".encode()).digest()[:8], "big") / 2**64
        if bucket < policy.timeout_probability:
            return "timeout"
        if bucket < policy.timeout_probability + policy.error_probability:
            return "error"
        return None
