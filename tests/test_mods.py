"""mods/: clients for other apps (AGENTS.md, docs/design/herdr.md). Each folder is laid out as its app requires, and a
client reads only the platform's neutral output: here, herdr's brunna mod imports only herdr's own modules, runs no
program but brunna (and sh, for its fallback to ~/.local/bin), and its recipe has the sections herdr's guide asks for.
The repo carries herdr's topic herdr-mod, so it may never have a layouts/ folder.

    bin/py -m unittest tests.test_mods
"""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOD = ROOT / "mods" / "brunna"
HERDR_MODULES = {"gpui-kit", "gpui-base", "herdr"}
RECIPE_SECTIONS = ["## What you see", "## How it behaves", "## Built from", "## Watch out", "## Done when"]


class HerdrMod(unittest.TestCase):
    def test_layout(self):
        self.assertTrue((MOD / "main.js").is_file())
        mod = json.loads((MOD / "mod.json").read_text())
        self.assertIn(mod.get("place"), {"right", "left", "top", "bottom", "none"})
        self.assertFalse((ROOT / "layouts").exists(), "herdr lists a herdr-mod repo only without a layouts/ folder")

    def test_reads_only_neutral_output(self):
        js = (MOD / "main.js").read_text()
        self.assertEqual(set(re.findall(r'from\s+"([^"]+)"', js)) - HERDR_MODULES, set(), "imports beyond herdr's own")
        programs = set(re.findall(r'\[\s*"([^"]+)"\s*,\s*\[', js)) | set(re.findall(r'exec\(\s*"([^"]+)"', js))
        self.assertLessEqual(programs, {"brunna", "sh"}, programs)
        self.assertIn('"glance", "--json"', js, "it reads brunna glance --json")
        for banned in ("setTimeout", "setInterval", "bus.db", "hub.json", "brunna.json", "fetch("):
            self.assertNotIn(banned, js)

    def test_recipe(self):
        text = (MOD / "RECIPE.md").read_text()
        self.assertTrue(text.startswith("# brunna\n"))
        self.assertIn("![brunna](preview.png)", text)
        positions = [text.find(s) for s in RECIPE_SECTIONS]
        self.assertTrue(all(p > 0 for p in positions) and positions == sorted(positions), positions)

    @unittest.skipUnless(shutil.which("node"), "no node to check the syntax with")
    def test_main_js_parses(self):
        r = subprocess.run(["node", "--check", "--input-type=module"], input=(MOD / "main.js").read_text(),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
