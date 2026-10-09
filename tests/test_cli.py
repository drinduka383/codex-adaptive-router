import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from codex_adaptive_router.cli import _catalog_freshness, main, parse_run_args
from codex_adaptive_router.config import default_config
from codex_adaptive_router.models import ModelCapability


class CliTests(unittest.TestCase):
    def test_catalog_staleness_is_reported_from_local_discovery_time(self):
        freshness = _catalog_freshness({"discovered_at": "2000-01-01T00:00:00Z"}, default_config())
        self.assertTrue(freshness.startswith("stale"))

    def test_run_parser_preserves_prompt_as_one_task(self):
        args = parse_run_args(["--mode", "auto", "--dry-run", "implement", "pagination; echo unsafe"])
        self.assertEqual(args.task, "implement pagination; echo unsafe")
        self.assertEqual(args.mode, "auto")
        self.assertTrue(args.dry_run)

    def test_fast_and_standard_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_run_args(["--fast", "--standard", "inspect a file"])

    def test_packaged_evaluation_case_is_available_outside_the_checkout(self):
        cases = json.loads(files("codex_adaptive_router").joinpath("evaluation_cases.json").read_text())
        self.assertEqual(cases[0]["id"], "literal-response-smoke")
        orchestration_cases = json.loads(
            files("codex_adaptive_router").joinpath("orchestration_evaluation_cases.json").read_text()
        )
        self.assertEqual(orchestration_cases[0]["id"], "parallel-arithmetic-smoke")

    def test_dry_run_explains_route_without_auth_or_codex_execution(self):
        model = ModelCapability(
            model_id="test-model",
            display_name="Test Model",
            reasoning_efforts=("low", "medium", "high"),
            service_tiers=("default", "fast"),
            lifecycle="approved",
            account_availability="observed_available",
        )
        output = io.StringIO()
        with (
            patch("codex_adaptive_router.cli._codex_bin", return_value="codex-test"),
            patch(
                "codex_adaptive_router.cli._load_registry",
                return_value=({"test-model": model}, {"catalog_hash": "abc"}),
            ),
            patch("codex_adaptive_router.cli.run_interactive") as run,
            contextlib.redirect_stdout(output),
        ):
            code = main(["--dry-run", "--mode", "luna", "--model", "test-model", "inspect", "a", "file"])
        self.assertEqual(code, 0)
        self.assertIn("Luna Direct", output.getvalue())
        self.assertIn("test-model", output.getvalue())
        run.assert_not_called()

    def test_hooks_install_and_uninstall_preserve_existing_codex_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            original = '[features]\nfast_mode = true\n\n[hooks]\n'
            config.write_text(original, encoding="utf-8")
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}),
                patch("codex_adaptive_router.cli.shutil.which", return_value="/tmp/cx"),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main(["hooks", "install"]), 0)
                installed = config.read_text(encoding="utf-8")
                self.assertEqual(main(["hooks", "install"]), 0)
                self.assertEqual(main(["hooks", "uninstall"]), 0)

            self.assertIn("TUI prompt routing hook installed", output.getvalue())
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertTrue(list(codex_home.glob("config.toml.cx-backup-*")))
            self.assertNotEqual(installed, original)

    def test_hook_submit_queues_once_and_does_not_log_prompt(self):
        task = "inspect a local module without editing it"
        luna = ModelCapability(
            model_id="gpt-6-luna",
            display_name="Luna",
            reasoning_efforts=("low", "medium", "high", "xhigh", "max"),
            service_tiers=("default", "fast"),
            multi_agent_version="v2",
            lifecycle="approved",
            account_availability="observed_available",
        )
        sol = ModelCapability(
            model_id="gpt-6.1-sol",
            display_name="Sol",
            reasoning_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
            service_tiers=("default", "fast"),
            multi_agent_version="v2",
            lifecycle="approved",
            account_availability="observed_available",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdin = io.StringIO(
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "thread-test", "prompt": task})
            )
            stdout = io.StringIO()
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with (
                patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")},
                ),
                patch("codex_adaptive_router.cli._codex_bin", return_value="codex-test"),
                patch("codex_adaptive_router.cli.check_chatgpt_auth"),
                patch(
                    "codex_adaptive_router.cli.read_registry",
                    return_value=({luna.model_id: luna, sol.model_id: sol}, {"catalog_hash": "test-hash"}),
                ),
                patch("codex_adaptive_router.cli.subprocess.run", return_value=completed) as queue,
                patch("sys.stdin", stdin),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(main(["hook-submit"]), 0)

            response = json.loads(stdout.getvalue())
            self.assertEqual(response["decision"], "block")
            self.assertIn("gpt-6-luna", response["systemMessage"])
            queued_command = queue.call_args.args[0]
            self.assertEqual(queued_command[queued_command.index("--thread") + 1], "thread-test")
            self.assertEqual(queued_command[queued_command.index("--message") + 1], task)
            metrics = root / "state" / "codex-adaptive-router" / "runs.jsonl"
            self.assertNotIn(task, metrics.read_text(encoding="utf-8"))

    def test_hook_submit_failure_allows_original_prompt_to_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdin = io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "thread-test",
                        "prompt": "inspect a local module",
                    }
                )
            )
            stdout = io.StringIO()
            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="")
            models = {
                "gpt-6-luna": ModelCapability(
                    "gpt-6-luna",
                    "Luna",
                    ("low", "medium", "high", "xhigh", "max"),
                    ("default", "fast"),
                    "v2",
                    "approved",
                    "observed_available",
                ),
                "gpt-6.1-sol": ModelCapability(
                    "gpt-6.1-sol",
                    "Sol",
                    ("low", "medium", "high", "xhigh", "max", "ultra"),
                    ("default", "fast"),
                    "v2",
                    "approved",
                    "observed_available",
                ),
            }
            with (
                patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")},
                ),
                patch("codex_adaptive_router.cli._codex_bin", return_value="codex-test"),
                patch("codex_adaptive_router.cli.check_chatgpt_auth"),
                patch("codex_adaptive_router.cli.read_registry", return_value=(models, {})),
                patch("codex_adaptive_router.cli.subprocess.run", return_value=failed),
                patch("sys.stdin", stdin),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(main(["hook-submit"]), 0)

            response = json.loads(stdout.getvalue())
            self.assertNotIn("decision", response)
            self.assertIn("letting the original prompt continue", response["systemMessage"].lower())

    def test_hook_submit_without_cached_registry_fails_open_without_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdin = io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "thread-test",
                        "prompt": "inspect a local module",
                    }
                )
            )
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")},
                ),
                patch("codex_adaptive_router.cli._codex_bin", return_value="codex-test"),
                patch("codex_adaptive_router.cli.check_chatgpt_auth"),
                patch("codex_adaptive_router.cli.read_registry", return_value=({}, {})),
                patch("codex_adaptive_router.cli.route_task") as route,
                patch("codex_adaptive_router.cli.subprocess.run") as queue,
                patch("sys.stdin", stdin),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(main(["hook-submit"]), 0)

            response = json.loads(stdout.getvalue())
            self.assertNotIn("decision", response)
            self.assertIn("letting the original prompt continue", response["systemMessage"].lower())
            self.assertIn("cx models refresh", response["systemMessage"])
            queue.assert_not_called()
            route.assert_not_called()


if __name__ == "__main__":
    unittest.main()
