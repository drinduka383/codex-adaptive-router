import json
import unittest

from codex_adaptive_router.discovery import (
    approve_model,
    mark_evaluated,
    parse_catalog,
    sync_registry,
)

CATALOG = json.dumps(
    {
        "models": [
            {
                "slug": "model-new",
                "display_name": "Model New",
                "default_reasoning_level": "low",
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                "service_tiers": [{"id": "priority", "name": "Fast"}],
                "multi_agent_version": "v2",
            }
        ]
    }
)


class DiscoveryTests(unittest.TestCase):
    def test_catalog_metadata_is_normalized_without_model_specific_rules(self):
        parsed = parse_catalog(CATALOG)
        capability = parsed["model-new"]
        self.assertEqual(capability.reasoning_efforts, ("low", "high"))
        self.assertEqual(capability.service_tiers, ("default", "fast"))
        self.assertEqual(capability.multi_agent_version, "v2")
        self.assertEqual(capability.lifecycle, "discovered")

    def test_new_model_moves_from_discovered_to_untested_on_reobservation(self):
        catalog = parse_catalog(CATALOG)
        first = sync_registry(catalog, {}, "2026-10-09T10:00:00Z")
        second = sync_registry(catalog, first, "2026-10-10T10:00:00Z")
        self.assertEqual(first["model-new"].lifecycle, "discovered")
        self.assertEqual(second["model-new"].lifecycle, "untested")

    def test_approval_survives_refresh_and_missing_model_is_deprecated(self):
        catalog = parse_catalog(CATALOG)
        existing = {
            "model-new": {
                **catalog["model-new"].to_dict(),
                "lifecycle": "approved",
                "account_availability": "observed_available",
            },
            "retired": {
                **catalog["model-new"].to_dict(),
                "model_id": "retired",
                "lifecycle": "approved",
            },
        }
        updated = sync_registry(catalog, existing, "2026-10-11T10:00:00Z")
        self.assertEqual(updated["model-new"].lifecycle, "approved")
        self.assertEqual(updated["retired"].lifecycle, "deprecated")

    def test_capability_change_resets_approval_for_evaluation(self):
        original = parse_catalog(CATALOG)
        approved = sync_registry(original, {}, "2026-10-09T10:00:00Z")
        approved = sync_registry(original, approved, "2026-10-10T10:00:00Z")
        approved = mark_evaluated(approved, "model-new", success=True, verified_at="2026-10-10T11:00:00Z")
        approved = approve_model(approved, "model-new")
        changed_payload = json.loads(CATALOG)
        changed_payload["models"][0]["supported_reasoning_levels"].append({"effort": "ultra"})
        changed = parse_catalog(json.dumps(changed_payload))
        refreshed = sync_registry(changed, approved, "2026-10-11T10:00:00Z")
        self.assertEqual(refreshed["model-new"].lifecycle, "untested")
        self.assertEqual(refreshed["model-new"].account_availability, "unknown")
        self.assertIn("capabilities changed", " ".join(refreshed["model-new"].evidence))

    def test_only_successful_evaluation_can_be_approved(self):
        registry = sync_registry(parse_catalog(CATALOG), {}, "2026-10-09T10:00:00Z")
        with self.assertRaises(ValueError):
            approve_model(registry, "model-new")
        registry = mark_evaluated(registry, "model-new", success=True, verified_at="2026-10-09T11:00:00Z")
        approved = approve_model(registry, "model-new")
        self.assertEqual(approved["model-new"].lifecycle, "approved")


if __name__ == "__main__":
    unittest.main()
