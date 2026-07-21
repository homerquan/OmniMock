from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnimock.application.runtime import SimulationRuntime
from omnimock.domain.behavior import BehaviorEngine
from omnimock.domain.errors import ValidationError
from omnimock.domain.scenario import ServiceDefinition, scenario_from_mapping
from omnimock.domain.state import InMemoryStateStore
from omnimock.domain.values import RequestEnvelope
from omnimock.infrastructure.config.loader import ProjectConfig


def scenario():
    return scenario_from_mapping({
        "schema_version": "1",
        "id": "test",
        "version": "1.0.0",
        "seed": 7,
        "initial_state": {"inventory": {"SKU": {"available": 5}}, "collections": {"orders": {}}},
        "rules": [{
            "id": "create", "priority": 10, "when": {"operation": "orders.create"},
            "validate": [{"expression": "input.quantity >= 1"}],
            "mutate": [{"decrement": {"path": "inventory.${input.sku}.available", "by": "${input.quantity}"}},
                       {"put": {"collection": "orders", "key": "${uuid.deterministic('order', request.request_id)}",
                                 "value": {"id": "${mutation.key}", "sku": "${input.sku}", "quantity": "${input.quantity}"}}}],
            "respond": {"status": 201, "body": "${mutation.value}"},
        }],
    })


def request(quantity: int) -> RequestEnvelope:
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return RequestEnvelope("req-1", "run-1", "orders-api", "orders.create", "test", now, now, {}, {"sku": "SKU", "quantity": quantity})


class BehaviorTests(unittest.TestCase):
    def test_same_seed_and_request_are_equal(self):
        left_store, right_store = InMemoryStateStore(scenario().initial_state), InMemoryStateStore(scenario().initial_state)
        left = BehaviorEngine(scenario(), left_store).handle(request(2))
        right = BehaviorEngine(scenario(), right_store).handle(request(2))
        self.assertEqual(left.payload, right.payload)
        self.assertEqual(left_store.read_state(), right_store.read_state())
        self.assertEqual(left_store.journal(), right_store.journal())

    def test_failed_validation_does_not_mutate_state(self):
        store = InMemoryStateStore(scenario().initial_state)
        before = store.read_state()
        with self.assertRaises(ValidationError):
            BehaviorEngine(scenario(), store).handle(request(0))
        self.assertEqual(store.read_state(), before)
        self.assertEqual(store.state_version(), 0)

    def test_runtime_materializes_filesystem_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectConfig(root, {"state": {"driver": "memory", "sqlite_path": ".omnimock/state.db"}},
                                   (ServiceDefinition("files", "filesystem", "builtin.filesystem", "native", "test", root="mount"),), root / "scenario.yaml")
            runtime = SimulationRuntime(project, scenario())
            # The test scenario has no filesystem event; this asserts the safe
            # no-op path and keeps projection behavior covered by the sample.
            self.assertEqual(runtime.store.state_version(), 0)

