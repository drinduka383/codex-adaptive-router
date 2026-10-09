import json
import tempfile
import unittest
from pathlib import Path

from codex_adaptive_router.config import (
    ConfigStore,
    default_config,
    load_config,
    set_config_value,
    validate_config,
)


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid_and_contains_no_core_model_assumptions(self):
        value = default_config()
        validate_config(value)
        self.assertEqual(value["models"]["roles"]["efficient"], ["gpt-6-luna"])
        self.assertEqual(value["models"]["roles"]["capable"], ["gpt-6.1-sol"])

    def test_set_accepts_known_dotted_key_and_rejects_unknown_or_invalid_values(self):
        value = default_config()
        changed = set_config_value(value, "routing.max_workers", "3")
        self.assertEqual(changed["routing"]["max_workers"], 3)
        with self.assertRaises(KeyError):
            set_config_value(changed, "routing.shell_command", '"unsafe"')
        with self.assertRaises(ValueError):
            set_config_value(changed, "routing.max_workers", "99")

    def test_evaluation_and_discovery_limits_remain_bounded(self):
        for section, key, value in (
            ("evaluation", "max_models", 5),
            ("evaluation", "max_cases", 6),
            ("evaluation", "timeout_seconds", 301),
            ("discovery", "refresh_hours", 0),
            ("routing", "max_task_chars", 20001),
        ):
            config = default_config()
            config[section][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_config(config)

    def test_loaded_config_rejects_misspelled_keys_but_accepts_custom_presets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            value = default_config()
            value["presets"]["careful"] = {"routing": {"strong_model_difficulty": 0.4}}
            path.write_text(json.dumps(value))
            self.assertEqual(load_config(path)["presets"]["careful"]["routing"]["strong_model_difficulty"], 0.4)
            value["routing"]["max_workerss"] = 3
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "unknown config key"):
                load_config(path)

    def test_config_history_can_restore_previous_valid_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            history = Path(temp) / "history"
            store = ConfigStore(path, history)
            initial = default_config()
            store.save(initial)
            store.save(set_config_value(initial, "routing.max_workers", "3"))
            restored = store.rollback()
            self.assertEqual(restored["routing"]["max_workers"], initial["routing"]["max_workers"])


if __name__ == "__main__":
    unittest.main()
