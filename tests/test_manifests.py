import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_plugin_and_local_marketplace_manifests_resolve_management_and_cx_skills(self):
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "codex-adaptive-router")
        skill = ROOT / plugin["skills"].removeprefix("./") / "codex-adaptive-router/SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertTrue(skill.read_text(encoding="utf-8").startswith("---\nname: codex-adaptive-router\n"))
        cx_skill = ROOT / plugin["skills"].removeprefix("./") / "cx/SKILL.md"
        self.assertTrue(cx_skill.is_file())
        cx_instructions = cx_skill.read_text(encoding="utf-8")
        self.assertTrue(cx_instructions.startswith("---\nname: cx\n"))
        self.assertIn("Never route ordinary prompts implicitly", cx_instructions)
        entries = [entry for entry in marketplace["plugins"] if entry["name"] == plugin["name"]]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"]["source"], "local")
        self.assertEqual(entries[0]["source"]["path"], "./")


if __name__ == "__main__":
    unittest.main()
