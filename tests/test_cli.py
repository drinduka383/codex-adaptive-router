import contextlib
import io
import json
import tempfile
import unittest
from importlib.resources import files
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
        with tempfile.TemporaryDirectory() as temp:
            env = {"XDG_CONFIG_HOME": temp, "XDG_STATE_HOME": temp}
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


if __name__ == "__main__":
    unittest.main()
