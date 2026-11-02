"""The roster gen-index-roster.sh generates: it lists every project even with no idea sandboxes (on 2026-10-24 it
once went entirely empty).

    bin/py -m unittest discover -s tests
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def project(name: str, extra: str = "") -> str:
    return (f"v: 1\nname: {name}\npaths:\n  workspace: ~/workspace/{name}\n  memory_bare: ~/git-origin/{name}-memory.git\n"
            f"  origin: (native)\n{extra}")


class IndexRoster(unittest.TestCase):
    def render(self, projects: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            hub = pathlib.Path(d)
            shutil.copy(ROOT / "hub.example.json", hub / "hub.json")
            (hub / "bin").symlink_to(ROOT / "bin")
            (hub / "projects").mkdir()
            for name, text in projects.items():
                (hub / "projects" / f"{name}.yaml").write_text(text)
            (hub / "index.md").write_text("# index\n\n## Other\n")
            subprocess.run([str(ROOT / "bin" / "gen-index-roster.sh")], env={"AGENT_HUB": str(hub), "PATH": "/usr/bin:/bin",
                                **{k: os.environ[k] for k in ("HOME", "AGENT_HUB_PYTHON", "BRUNNA_PYTHON") if k in os.environ}},
                           check=True, capture_output=True)
            text = (hub / "index.md").read_text()
        block = text.split("<!-- roster:begin")[1].split("<!-- roster:end")[0]
        return [l for l in block.splitlines() if l.startswith("| `")]

    def test_no_ideas_lists_every_project(self):
        rows = self.render({"alpha": project("alpha"), "beta": project("beta")})
        self.assertEqual(len(rows), 2)

    def test_ideas_listed_separately(self):
        rows = self.render({"alpha": project("alpha"), "idea-3": project("idea-3", "type: idea\npurpose: give it a try\n")})
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("| `alpha`"))
        self.assertTrue(rows[1].startswith("| `idea-3`"))


if __name__ == "__main__":
    unittest.main()
