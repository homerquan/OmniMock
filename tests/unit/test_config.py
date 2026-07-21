from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnimock.domain.errors import ConfigurationError
from omnimock.infrastructure.config.loader import load_project
from omnimock.infrastructure.config.yaml_loader import load_document


class ConfigTests(unittest.TestCase):
    def test_yaml_nested_list_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.yaml"
            path.write_text("services:\n  - id: api\n    mode: native\n    listen:\n      port: 8080\n", encoding="utf-8")
            self.assertEqual(load_document(path)["services"][0]["listen"]["port"], 8080)

    def test_unknown_root_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "omnimock.yaml").write_text("schema_version: '1'\nunknown: true\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError) as error:
                load_project(root)
            self.assertEqual(error.exception.context.code, "OMC-CONFIG-017")

