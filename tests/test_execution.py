import unittest

from codex_adaptive_router.config import default_config
from codex_adaptive_router.execution import build_interactive_command, parse_usage_events, safe_environment
from codex_adaptive_router.policy import route_task
from tests.helpers import model


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.models = {
            "small-model": model("small-model"),
            "strong-model": model("strong-model"),
        }
        self.config = default_config()
        self.config["models"]["roles"] = {
            "efficient": ["small-model"],
            "capable": ["strong-model"],
            "worker": ["small-model"],
        }

    def test_direct_command_disables_delegation_and_uses_argument_array(self):
        task = "Print the literal text $(touch /tmp/should-not-run)"
        decision = route_task(task, self.config, self.models, mode_override="luna")
        command, prompt = build_interactive_command(decision, task, codex_bin="codex-test")
        self.assertIn("--disable", command)
        self.assertIn("multi_agent", command)
        self.assertEqual(command[-1], task)
        self.assertEqual(prompt, task)
        self.assertNotIn("shell", command)

    def test_orchestrated_prompt_keeps_worker_model_request_and_task_bounded(self):
        task = "Inspect three independent directories"
        decision = route_task(task, self.config, self.models, mode_override="orchestrated")
        command, prompt = build_interactive_command(decision, task)
        self.assertIn("--enable", command)
        self.assertIn("multi_agent", command)
        self.assertIn("multi_agent_v2", command)
        self.assertIn('fork_turns="none"', prompt)
        self.assertIn("grandchildren", prompt)
        self.assertIn("small-model", prompt)
        self.assertIn(task, prompt)
        self.assertIn("service tier", prompt.lower())

    def test_v1_catalog_model_does_not_enable_v2(self):
        v1_models = {
            "small-model": model("small-model", multi_agent_version="v1"),
            "strong-model": model("strong-model", multi_agent_version="v1"),
        }
        decision = route_task(
            "Inspect three independent directories", self.config, v1_models, mode_override="orchestrated"
        )
        command, _ = build_interactive_command(decision, "Inspect three independent directories")
        self.assertIn(("--enable", "multi_agent"), list(zip(command, command[1:])))
        self.assertNotIn("multi_agent_v2", command)

    def test_environment_drops_api_routing_overrides_but_preserves_codex_home(self):
        env = safe_environment({
            "OPENAI_API_KEY": "must-not-pass",
            "OPENAI_BASE_URL": "https://custom.invalid",
            "CODEX_HOME": "/tmp/codex-home",
            "PATH": "/bin",
        })
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertEqual(env["CODEX_HOME"], "/tmp/codex-home")

    def test_json_usage_events_are_read_without_logging_their_prompt(self):
        raw = (
            '{"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":3,'
            '"output_tokens":2,"reasoning_tokens":1}}\n'
        )
        self.assertEqual(parse_usage_events(raw), {
            "input_tokens": 7,
            "cached_input_tokens": 3,
            "output_tokens": 2,
            "reasoning_tokens": 1,
        })


if __name__ == "__main__":
    unittest.main()
