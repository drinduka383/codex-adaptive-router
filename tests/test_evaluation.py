import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_adaptive_router.evaluation import (
    build_evaluation_command,
    run_evaluation,
    validate_evaluation_request,
)
from tests.helpers import model


class EvaluationTests(unittest.TestCase):
    def test_evaluation_requires_explicit_usage_confirmation(self):
        with self.assertRaises(PermissionError):
            validate_evaluation_request(["model-a"], [{"id": "case", "prompt": "Read only"}], confirmed=False)

    def test_evaluation_rejects_empty_or_unbounded_suites(self):
        with self.assertRaises(ValueError):
            validate_evaluation_request([], [{"id": "case", "prompt": "Read only"}], confirmed=True)
        with self.assertRaises(ValueError):
            validate_evaluation_request(["model-a"], [], confirmed=True)
        with self.assertRaises(ValueError):
            validate_evaluation_request(["model-a"], [{"id": str(i), "prompt": "x"} for i in range(20)], confirmed=True)

    def test_evaluation_command_is_ephemeral_read_only_and_single_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            case = {"id": "smoke", "prompt": "Return READY", "expected_regex": "READY"}
            command = build_evaluation_command(model("model-a"), "low", case, Path(temp) / "out.txt")
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("multi_agent", command)
        self.assertEqual(command[-1].splitlines()[-1], case["prompt"])

    def test_orchestrated_evaluation_enables_catalogued_version_and_bounds_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            case = {"id": "split", "prompt": "Inspect two files independently", "expected_regex": "READY"}
            command = build_evaluation_command(
                model("root-model"),
                "medium",
                case,
                Path(temp) / "out.txt",
                mode="orchestrated",
                worker_model_id="worker-model",
                worker_effort="low",
            )
        self.assertIn(("--enable", "multi_agent_v2"), list(zip(command, command[1:])))
        self.assertIn("Use no more than one worker total", command[-1])
        self.assertIn('fork_turns="none"', command[-1])
        self.assertIn("worker-model", command[-1])
        self.assertNotIn("--disable", command)

    def test_fast_evaluation_is_separate_from_effort(self):
        with tempfile.TemporaryDirectory() as temp:
            command = build_evaluation_command(
                model("model-a"),
                "max",
                {"id": "fast", "prompt": "Return READY", "expected_regex": "READY"},
                Path(temp) / "out.txt",
                service_tier="fast",
            )
        self.assertIn("model_reasoning_effort=max", command)
        self.assertIn("service_tier=fast", command)

    def test_process_failure_is_classified_without_persisting_stderr(self):
        completed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "private diagnostic"})()
        with (
            patch("codex_adaptive_router.evaluation.check_chatgpt_auth"),
            patch("codex_adaptive_router.evaluation.subprocess.run", return_value=completed),
        ):
            results = run_evaluation(
                [model("model-a")],
                [{"id": "smoke", "prompt": "read-only", "expected_regex": "READY"}],
            )
        self.assertEqual(results[0]["verification"], "process_exit")
        self.assertNotIn("private diagnostic", str(results))


if __name__ == "__main__":
    unittest.main()
