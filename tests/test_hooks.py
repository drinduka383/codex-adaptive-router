import tempfile
import tomllib
import unittest
from pathlib import Path

from codex_adaptive_router.hooks import (
    build_queue_command,
    consume_handoff,
    handle_prompt_submit,
    install_prompt_hook,
    remove_prompt_hook,
)
from codex_adaptive_router.models import RoutingDecision, TaskSignals


def decision(mode="luna", model_id="model-a", effort="low", service_tier="default"):
    return RoutingDecision(
        mode=mode,
        model_id=model_id,
        effort=effort,
        service_tier=service_tier,
        reason="test route",
        signals=TaskSignals(0.2, 0.2, 0.2, 0.1, 0.9, 0.1, 0.2, 0.1, 0.2, 0.9, True, 1, ("inspection",)),
        confidence=0.9,
        multi_agent=mode == "orchestrated",
        multi_agent_version="v2" if mode == "orchestrated" else None,
        worker_model_id="worker-model" if mode == "orchestrated" else None,
        worker_effort="low" if mode == "orchestrated" else None,
        max_workers=2 if mode == "orchestrated" else 0,
    )


class HookTests(unittest.TestCase):
    def test_queue_command_keeps_prompt_as_one_argument_and_selects_turn_settings(self):
        task = "inspect this; do not run shell commands"
        command = build_queue_command(decision(), task, "thread-123", codex_bin="codex-test")

        self.assertEqual(command[:4], ["codex-test", "queue", "--thread", "thread-123"])
        self.assertEqual(command[command.index("--message") + 1], task)
        self.assertEqual(command[command.index("--model") + 1], "model-a")
        self.assertIn("model_reasoning_effort=low", command)
        self.assertIn("service_tier=default", command)

    def test_orchestrated_queue_enables_native_agents_and_bounds_worker_request(self):
        command = build_queue_command(
            decision("orchestrated", "root-model", "medium", "default"),
            "inspect three independent modules",
            "thread-123",
        )

        self.assertIn("multi_agent", command)
        self.assertIn("multi_agent_v2", command)
        self.assertIn("worker-model", command[command.index("--message") + 1])
        self.assertIn("2 workers", command[command.index("--message") + 1])

    def test_successful_handoff_blocks_original_and_shows_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "inspect module"}
            outcome = handle_prompt_submit(
                event,
                state_dir=Path(directory),
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
                codex_bin="codex-test",
            )

            self.assertEqual(outcome.response["decision"], "block")
            self.assertIn("model-a", outcome.response["systemMessage"])
            self.assertIn("low · Standard", outcome.response["systemMessage"])
            self.assertTrue(outcome.queued)
            self.assertTrue(consume_handoff(Path(directory), "thread-123", "inspect module"))

    def test_queue_failure_fails_open_and_does_not_leave_a_loop_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = handle_prompt_submit(
                {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "inspect module"},
                state_dir=Path(directory),
                route_for_task=lambda task: decision(),
                queue=lambda command: 1,
                codex_bin="codex-test",
            )

            self.assertNotIn("decision", outcome.response)
            self.assertIn("letting the original prompt continue", outcome.response["systemMessage"])
            self.assertFalse(outcome.queued)
            self.assertFalse(consume_handoff(Path(directory), "thread-123", "inspect module"))

    def test_queued_prompt_is_allowed_once_to_prevent_hook_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "inspect module"}
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
                codex_bin="codex-test",
            )
            outcome = handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("a handoff must not be routed recursively"),
                queue=lambda command: self.fail("a handoff must not be queued twice"),
                codex_bin="codex-test",
            )

            self.assertIsNone(outcome.response)
            self.assertEqual(outcome.status, "handoff")

    def test_hook_install_is_idempotent_and_remove_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            original = "[features]\nfast_mode = true\n\n[hooks]\n"
            config.write_text(original, encoding="utf-8")

            installed_now, _ = install_prompt_hook(config, "/tmp/cx hook-submit")
            self.assertTrue(installed_now)
            installed = config.read_text(encoding="utf-8")
            self.assertIn("UserPromptSubmit", installed)
            self.assertIn("/tmp/cx hook-submit", installed)
            parsed = tomllib.loads(installed)
            self.assertEqual(len(parsed["hooks"]["UserPromptSubmit"]), 1)
            installed_now, _ = install_prompt_hook(config, "/tmp/cx hook-submit")
            self.assertFalse(installed_now)
            removed_now, _ = remove_prompt_hook(config)
            self.assertTrue(removed_now)
            self.assertEqual(config.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
