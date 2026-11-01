"""E0 of docs/design/i18n.md: the repo is English. A tracked file may contain CJK characters only where Chinese is
data, not untranslated text:

- `locales/`: the catalogs (zh-Hant text of every message), per-language data (consolidate's hedge words, eval inputs,
  the Simplified-character list)
- `tests/fixtures/`, `contracts/fixtures/`: goldens of what a zh-Hant instance outputs, and zh-Hant input data
- `contracts/vendor/`: other projects' contracts and conformance vectors, copied verbatim
- SPEC: the files that define the zh-Hant tokens of a machine format

Everything else has none (step 7 brought it from 8,400 lines to zero; the per-file baseline it ratcheted down is gone).

    bin/py -m unittest tests.test_i18n_static
"""
import pathlib
import re
import subprocess
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
CJK = re.compile("[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
ALLOWED = ("locales/", "tests/fixtures/", "contracts/fixtures/",
           # verbatim copies of other projects' contracts and conformance vectors (their zh-hant profiles are Chinese by
           # definition); contracts-check compares them with the originals, so they are never edited here
           "contracts/vendor/")
# Chinese there is the spec, not untranslated text: each file defines or documents zh-Hant tokens a reader must accept
SPEC = {
    # the to-do formats' zh-Hant set (heading, tags, key marker, empty marker, todo.md headings): the v1 contracts,
    # their v2 successors (v1 + the English set), and the one table readers and writers build from
    "contracts/state-todo.v1.md", "contracts/state-todo.v2.md", "contracts/todo-md.v1.md", "contracts/todo-md.v2.md",
    "bin/todo_tokens.py",
    # reflect-week v1 stored the analysis's classifications as zh-Hant words; v2 maps each to its value
    # (x-v1-values), which is how readers take the weeks stored before v2
    "contracts/reflect-week.v2.schema.json",
    # the i18n design: both token sets side by side and the Simplified-only characters the evals score
    "docs/design/i18n.md",
    # the README for zh-Hant readers: the prompt they paste into their AI app is Chinese by definition
    "README.zh-Hant.md",
}


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        return [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):   # exported tree without git (the commit gate's copy)
        # relative parts: the commit gate's copy itself sits under <hub>/.state/, so absolute parts would skip everything
        rels = (p.relative_to(ROOT) for p in ROOT.rglob("*") if p.is_file())
        return [str(r) for r in rels if not {".git", ".state", ".logs", "__pycache__"} & set(r.parts)]


def with_cjk() -> dict[str, list[int]]:
    """file -> the line numbers with CJK characters, for every tracked file outside the allowed places"""
    out = {}
    for rel in tracked():
        if rel.startswith(ALLOWED) or rel in SPEC:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = [i for i, line in enumerate(text.splitlines(), 1) if CJK.search(line)]
        if lines:
            out[rel] = lines
    return out


class NoCJK(unittest.TestCase):
    def test_none_outside_the_allowed_places(self):
        found = [f"{f}:{','.join(map(str, ls[:10]))}" for f, ls in sorted(with_cjk().items())]
        self.assertEqual(found, [], "Chinese text outside locales/, fixtures, contracts/vendor/ and the spec files. "
                                    "New text is English; owner-facing text goes through the catalog (_(), hub_msg, "
                                    "the page's t()), zh-Hant tokens come from their definition (bin/todo_tokens.py), "
                                    "zh-Hant test data goes in tests/fixtures/ (docs/design/i18n.md)")

    def test_spec_and_allowed_exist(self):
        """a renamed or deleted spec file must leave the list, not keep an exemption nobody uses"""
        files = tracked()
        self.assertEqual(sorted(s for s in SPEC if s not in files), [])
        self.assertEqual([a for a in ALLOWED if not any(f.startswith(a) for f in files)], [])

    def test_sees_files_without_git(self):
        """the fallback (no git, as in the commit gate) must still find the tracked files"""
        with mock.patch.object(subprocess, "run", side_effect=OSError):
            files = tracked()
        self.assertIn("bin/librarian.py", files)
        self.assertNotIn(".git/HEAD", files)

    def test_pattern(self):
        self.assertTrue(CJK.search("\u5f85\u8fa6"))
        self.assertTrue(CJK.search("\u3014agent\u3015"))
        self.assertFalse(CJK.search("To-do — [agent] “quoted” café"))


if __name__ == "__main__":
    unittest.main()
