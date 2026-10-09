import unittest

from codex_adaptive_router.config import default_config
from codex_adaptive_router.policy import route_task
from tests.helpers import model


def registry():
    return {
        "efficient-x": model("efficient-x"),
        "capable-y": model("capable-y"),
        "unproven-z": model("unproven-z", lifecycle="untested"),
    }


def config():
    value = default_config()
    value["models"]["roles"] = {
        "efficient": ["efficient-x"],
        "capable": ["capable-y"],
        "worker": ["efficient-x"],
    }
    return value


class PolicyTests(unittest.TestCase):
    def test_documentation_uses_low_effort_efficient_direct(self):
        decision = route_task("Document the retry option in README.md", config(), registry())
        self.assertEqual((decision.mode, decision.model_id, decision.effort), ("luna", "efficient-x", "low"))

    def test_mechanical_edit_uses_efficient_direct(self):
        decision = route_task("Rename timeout_ms to request_timeout in this function", config(), registry())
        self.assertEqual(decision.mode, "luna")
        self.assertIn(decision.effort, {"medium", "high"})

    def test_race_condition_uses_capable_direct_without_delegation(self):
        decision = route_task("Find the root cause of this authentication race condition", config(), registry())
        self.assertEqual((decision.mode, decision.model_id, decision.effort), ("sol", "capable-y", "high"))
        self.assertFalse(decision.multi_agent)

    def test_independent_modules_use_capable_coordinator_and_efficient_workers(self):
        decision = route_task(
            "Inspect four independent modules and summarize their responsibilities",
            config(),
            registry(),
        )
        self.assertEqual((decision.mode, decision.model_id, decision.effort), ("orchestrated", "capable-y", "medium"))
        self.assertEqual((decision.worker_model_id, decision.worker_effort), ("efficient-x", "low"))
        self.assertLessEqual(decision.max_workers, 2)
        self.assertTrue(decision.multi_agent)

    def test_large_sequential_migration_stays_direct(self):
        decision = route_task(
            "Plan a large database migration with sequential dependent steps; do not edit files",
            config(),
            registry(),
        )
        self.assertEqual(decision.mode, "sol")
        self.assertFalse(decision.multi_agent)

    def test_new_model_is_not_selected_automatically(self):
        value = config()
        value["models"]["roles"]["efficient"] = ["unproven-z"]
        value["models"]["roles"]["capable"] = ["capable-y"]
        decision = route_task("Inspect this file", value, registry())
        self.assertEqual(decision.model_id, "capable-y")
        self.assertNotEqual(registry()[decision.model_id].lifecycle, "untested")

    def test_manual_model_override_can_use_unproven_model_without_approving_it(self):
        decision = route_task(
            "Inspect this file",
            config(),
            registry(),
            model_override="unproven-z",
        )
        self.assertEqual(decision.model_id, "unproven-z")
        self.assertEqual(registry()["unproven-z"].lifecycle, "untested")

    def test_fast_is_independent_from_effort_and_standard_overrides_preference(self):
        fast = route_task("Urgent: inspect this one file quickly", config(), registry(), fast_override=True)
        standard = route_task("Urgent: inspect this one file quickly", config(), registry(), standard_override=True)
        self.assertEqual(fast.service_tier, "fast")
        self.assertEqual(standard.service_tier, "default")
        self.assertEqual(fast.effort, standard.effort)

    def test_direct_override_prevents_automatic_orchestration(self):
        decision = route_task(
            "Inspect four independent modules and summarize their responsibilities",
            config(),
            registry(),
            mode_override="direct",
        )
        self.assertEqual(decision.mode, "sol")
        self.assertFalse(decision.multi_agent)

    def test_explicit_effort_must_be_supported(self):
        with self.assertRaises(ValueError):
            route_task("Inspect this file", config(), registry(), effort_override="ultra")

    def test_luna_max_fast_is_available_as_an_explicit_pair(self):
        decision = route_task(
            "Quick bounded implementation",
            config(),
            registry(),
            mode_override="luna",
            effort_override="max",
            fast_override=True,
        )
        self.assertEqual((decision.mode, decision.effort, decision.service_tier), ("luna", "max", "fast"))

    def test_no_approved_candidate_fails_closed(self):
        value = config()
        only_unproven = {"new-id": model("new-id", lifecycle="untested")}
        value["models"]["roles"]["efficient"] = ["new-id"]
        value["models"]["roles"]["capable"] = ["new-id"]
        value["models"]["fallback_order"] = ["new-id"]
        with self.assertRaisesRegex(ValueError, "no approved model"):
            route_task("Inspect a file", value, only_unproven)


if __name__ == "__main__":
    unittest.main()
