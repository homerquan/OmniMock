from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from omnimock.cli.main import main
from omnimock.domain.errors import ConfigurationError
from omnimock.infrastructure.config.cli_manifest import load_cli_manifest
from omnimock.infrastructure.config.loader import load_project, load_scenario
from omnimock.simulators.cli import execute_cli

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = REPOSITORY_ROOT / "samples" / "mirror_neuron"


class CliManifestTests(unittest.TestCase):
    def test_mirror_neuron_cli_manifest_declares_desktop_commands(self):
        manifest = load_cli_manifest(SAMPLE_ROOT, "cli-manifest.json")

        self.assertEqual(manifest.id, "mirror-neuron-cli")
        self.assertEqual(len(manifest.commands), 20)
        self.assertTrue(
            {
                "runtime.health.timeout",
                "runtime.start",
                "runtime.stop",
                "blueprint.list",
                "job.status",
                "node.list",
            }.issubset({command.id for command in manifest.commands})
        )

    def test_cli_runner_renders_json_and_captures_without_shell_execution(self):
        project = load_project(SAMPLE_ROOT)
        scenario = load_scenario(project)
        manifest = load_cli_manifest(SAMPLE_ROOT, "cli-manifest.json")

        health = execute_cli(
            manifest,
            ["runtime", "health", "--json", "--timeout", "2"],
            scenario.start,
        )
        injection = execute_cli(
            manifest,
            ["runtime", "start; touch /tmp/should-not-exist"],
            scenario.start,
        )

        self.assertEqual(health.exit_code, 0)
        self.assertEqual(health.command_id, "runtime.health.timeout")
        self.assertEqual(json.loads(health.stdout)["overall"], "passing")
        self.assertEqual(injection.exit_code, 2)
        self.assertIsNone(injection.command_id)

    def test_mock_cli_command_preserves_process_streams_and_exit_code(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "--root",
                    str(SAMPLE_ROOT),
                    "mock-cli",
                    "--",
                    "job",
                    "status",
                    "job-42",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["job_id"], "job-42")
        self.assertEqual(stderr.getvalue(), "")

    def test_manifest_path_must_not_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / "project"
            root.mkdir()
            outside = outer / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            symlink = root / "linked.json"
            symlink.symlink_to(outside)

            for configured in (str(outside), "../outside.json", "linked.json"):
                with self.subTest(configured=configured):
                    with self.assertRaises(ConfigurationError) as caught:
                        load_cli_manifest(root, configured)
                    self.assertTrue(caught.exception.context.code.startswith("OMC-SECURITY-CLI-"))
