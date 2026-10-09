import json
import tempfile
import unittest
from pathlib import Path

from codex_adaptive_router.hooks import (
    build_queue_command,
    consume_handoff,
    handle_prompt_submit,
    install_prompt_hook,
    prompt_hook_installed,
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

        self.assertEqual(command[:2], ["codex-test", "queue"])
        self.assertEqual(command[command.index("--thread") + 1], "thread-123")
        self.assertEqual(command[command.index("--remote") + 1], "unix://")
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

    def test_successful_handoff_says_model_is_requested_until_codex_confirms_it(self):
        with tempfile.TemporaryDirectory() as directory:
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"}
            outcome = handle_prompt_submit(
                event,
                state_dir=Path(directory),
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
                codex_bin="codex-test",
            )

            self.assertEqual(outcome.response["decision"], "block")
            self.assertIn("model-a", outcome.response["systemMessage"])
            self.assertIn("requested", outcome.response["systemMessage"])
            self.assertNotIn("confirmed", outcome.response["systemMessage"])
            self.assertIn("requested effort low · requested tier Standard", outcome.response["systemMessage"])
            self.assertTrue(outcome.queued)
            self.assertTrue(consume_handoff(Path(directory), "thread-123", "inspect module"))

    def test_second_hook_pass_confirms_codex_active_model(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"}
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
            )

            confirmation = handle_prompt_submit(
                {**event, "prompt": "inspect module", "model": "model-a"},
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("a handed-off prompt must not be routed again"),
                queue=lambda command: self.fail("a handed-off prompt must not be queued again"),
            )

            self.assertEqual(confirmation.status, "handoff")
            self.assertIn("active model model-a", confirmation.response["systemMessage"])
            self.assertIn("requested effort low · requested tier Standard", confirmation.response["systemMessage"])
            self.assertNotIn("decision", confirmation.response)

    def test_second_hook_pass_reports_model_mismatch_and_does_not_requeue(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"}
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
            )

            confirmation = handle_prompt_submit(
                {**event, "prompt": "inspect module", "model": "model-b"},
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("a handed-off prompt must not be routed again"),
                queue=lambda command: self.fail("a handed-off prompt must not be queued again"),
            )

            self.assertIn("requested model model-a", confirmation.response["systemMessage"])
            self.assertIn("Codex reports active model model-b", confirmation.response["systemMessage"])
            self.assertEqual(confirmation.response["decision"], "block")

    def test_second_hook_pass_marks_missing_model_as_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"}
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
            )

            confirmation = handle_prompt_submit(
                {**event, "prompt": "inspect module"},
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("a handed-off prompt must not be routed again"),
                queue=lambda command: self.fail("a handed-off prompt must not be queued again"),
            )

            self.assertIn("active model was not reported", confirmation.response["systemMessage"])
            self.assertEqual(confirmation.response["decision"], "block")

    def test_orchestrated_handoff_confirms_wrapped_prompt_without_rerouting(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-123",
                "prompt": "$cx inspect three independent modules",
            }
            queued_command = []
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision("orchestrated", "root-model", "medium"),
                queue=lambda command: queued_command.extend(command) or 0,
            )
            queued_prompt = queued_command[queued_command.index("--message") + 1]

            confirmation = handle_prompt_submit(
                {**event, "prompt": queued_prompt, "model": "root-model"},
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("an orchestrated handoff must not be routed again"),
                queue=lambda command: self.fail("an orchestrated handoff must not be queued again"),
            )

            self.assertIn("active model root-model", confirmation.response["systemMessage"])

    def test_queue_failure_blocks_explicit_cx_task_and_does_not_leave_a_loop_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = handle_prompt_submit(
                {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"},
                state_dir=Path(directory),
                route_for_task=lambda task: decision(),
                queue=lambda command: 1,
                codex_bin="codex-test",
            )

            self.assertEqual(outcome.response["decision"], "block")
            self.assertIn("explicit request is blocked", outcome.response["systemMessage"])
            self.assertFalse(outcome.queued)
            self.assertFalse(consume_handoff(Path(directory), "thread-123", "inspect module"))

    def test_routing_failure_blocks_explicit_cx_task(self):
        def fail_route(task):
            raise RuntimeError(f"could not route {task}")

        with tempfile.TemporaryDirectory() as directory:
            outcome = handle_prompt_submit(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "thread-123",
                    "prompt": "$cx investigate this issue",
                },
                state_dir=Path(directory),
                route_for_task=fail_route,
                queue=lambda command: self.fail("routing failure must happen before queueing"),
            )

            self.assertEqual(outcome.response["decision"], "block")
            self.assertIn("explicit request is blocked", outcome.response["systemMessage"])

    def test_queued_prompt_is_allowed_once_to_prevent_hook_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "thread-123", "prompt": "$cx inspect module"}
            handle_prompt_submit(
                event,
                state_dir=state_dir,
                route_for_task=lambda task: decision(),
                queue=lambda command: 0,
                codex_bin="codex-test",
            )
            outcome = handle_prompt_submit(
                {**event, "prompt": "inspect module"},
                state_dir=state_dir,
                route_for_task=lambda task: self.fail("a handoff must not be routed recursively"),
                queue=lambda command: self.fail("a handoff must not be queued twice"),
                codex_bin="codex-test",
            )

            self.assertIn("active model was not reported", outcome.response["systemMessage"])
            self.assertEqual(outcome.response["decision"], "block")
            self.assertEqual(outcome.status, "handoff")

    def test_ordinary_prompt_passes_through_without_routing(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = handle_prompt_submit(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "thread-123",
                    "prompt": "implement pagination for this API",
                },
                state_dir=Path(directory),
                route_for_task=lambda task: self.fail("ordinary prompts must not be routed"),
                queue=lambda command: self.fail("ordinary prompts must not be queued"),
            )

            self.assertIsNone(outcome.response)
            self.assertEqual(outcome.status, "not_routed")

    def test_cx_prefix_is_removed_before_classification_and_queueing(self):
        with tempfile.TemporaryDirectory() as directory:
            routed_tasks = []
            command = []
            outcome = handle_prompt_submit(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "thread-123",
                    "prompt": "$cx  inspect module  ",
                },
                state_dir=Path(directory),
                route_for_task=lambda task: routed_tasks.append(task) or decision(),
                queue=lambda args: command.extend(args) or 0,
            )

            self.assertEqual(routed_tasks, ["inspect module"])
            self.assertEqual(command[command.index("--message") + 1], "inspect module")
            self.assertEqual(outcome.status, "queued")

    def test_empty_cx_invocation_is_blocked_with_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = handle_prompt_submit(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "thread-123",
                    "prompt": "$cx   ",
                },
                state_dir=Path(directory),
                route_for_task=lambda task: self.fail("empty task must not be classified"),
                queue=lambda command: self.fail("empty task must not be queued"),
            )

            self.assertEqual(outcome.status, "empty_cx")
            self.assertEqual(outcome.response["decision"], "block")
            self.assertIn("Usage: $cx <task>", outcome.response["systemMessage"])

    def test_hook_json_install_is_idempotent_and_preserves_existing_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "hooks.json"
            original = {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/tmp/stop-hook"}]}],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/tmp/cx hook-submit",
                                    "statusMessage": "another prompt hook",
                                }
                            ]
                        }
                    ],
                }
            }
            config.write_text(json.dumps(original), encoding="utf-8")

            installed_now, _ = install_prompt_hook(config, "/tmp/cx hook-submit")
            self.assertTrue(installed_now)
            installed = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(installed["hooks"]["Stop"], original["hooks"]["Stop"])
            self.assertEqual(len(installed["hooks"]["UserPromptSubmit"]), 2)
            self.assertTrue(prompt_hook_installed(config))
            installed_now, _ = install_prompt_hook(config, "/tmp/cx hook-submit")
            self.assertFalse(installed_now)
            removed_now, _ = remove_prompt_hook(config)
            self.assertTrue(removed_now)
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)

    def test_hook_json_install_refuses_to_overwrite_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            hooks_file = Path(directory) / "hooks.json"
            hooks_file.write_text("{invalid", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "cannot safely edit Codex hooks file"):
                install_prompt_hook(hooks_file, "/tmp/cx hook-submit")

            self.assertEqual(hooks_file.read_text(encoding="utf-8"), "{invalid")

    def test_install_upgrades_legacy_router_hook_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "hooks.json"
            original = {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/tmp/stop-hook"}]}],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/tmp/cx hook-submit",
                                    "timeout": 60,
                                    "statusMessage": "cx is selecting a model for this turn",
                                }
                            ]
                        }
                    ],
                }
            }
            config.write_text(json.dumps(original), encoding="utf-8")

            changed, backup = install_prompt_hook(config, "/tmp/new/bin/cx hook-submit")

            self.assertTrue(changed)
            self.assertTrue(backup and backup.is_file())
            installed = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(installed["hooks"]["Stop"], original["hooks"]["Stop"])
            submit_hooks = installed["hooks"]["UserPromptSubmit"][0]["hooks"]
            self.assertEqual(len(submit_hooks), 1)
            self.assertEqual(submit_hooks[0]["command"], "/tmp/new/bin/cx hook-submit")
            self.assertEqual(
                submit_hooks[0]["statusMessage"], "cx is checking for an explicit $cx request"
            )
            self.assertTrue(prompt_hook_installed(config))


if __name__ == "__main__":
    unittest.main()
