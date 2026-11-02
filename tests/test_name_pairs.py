"""The rename to brunna (docs/design/rename-brunna.md): the name table's rules, and a ratchet on spellings written
outside it.

The ratchet counts, per file under bin/, the literals of each class that code should take from bin/names.py instead
(environment variables in either old spelling, unit labels, rules-file and hook markers). The counts are recorded in
tests/fixtures/name-pairs-baseline.json and may only go down: moving a read onto names.env() lowers its file's count;
`bin/py tests/test_name_pairs.py --update` lowers the baseline (it never raises it). A new literal fails the test.

    bin/py -m unittest tests.test_name_pairs
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import names  # noqa: E402

BASELINE = ROOT / "tests" / "fixtures" / "name-pairs-baseline.json"
CLASSES = {
    "env": re.compile(r"\b(?:AGENT_HUB|AGENTHUB|BRUNNA)_[A-Z][A-Z_]*"),
    "unit": re.compile(r"local\.(?:agent-hub|brunna)\b"),
    "marker": re.compile(r"(?:agent-hub|brunna):(?:begin|end|gate)\b"),
}
# the table itself; the inline copies are compared with it below instead
EXEMPT = {"bin/names.py"}


def files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "bin"], capture_output=True, check=True)
    return sorted(f for f in out.stdout.decode().split("\0") if f and f not in EXEMPT and (ROOT / f).is_file())


def counts() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {cls: {} for cls in CLASSES}
    for f in files():
        try:
            text = (ROOT / f).read_text()
        except UnicodeDecodeError:
            continue
        for cls, rx in CLASSES.items():
            if (n := len(rx.findall(text))):
                out[cls][f] = n
    return out


class Table(unittest.TestCase):
    def test_spellings(self):
        self.assertFalse(names.SWITCHED, "v0.6 reads both and writes the old spellings")
        for cls in names.PAIRS:
            with self.subTest(cls=cls):
                self.assertEqual(set(names.all(cls)), set(names.PAIRS[cls]))
                self.assertEqual(names.all(cls)[0], names.current(cls))

    def test_resolve(self):
        base = pathlib.Path(tempfile.mkdtemp())
        self.assertEqual(names.resolve(base, "repo_dir").name, "brunna", "neither exists: the new one")
        (base / "agent-hub").mkdir()
        self.assertEqual(names.resolve(base, "repo_dir").name, "agent-hub", "an existing one is used as it is")
        (base / "brunna").mkdir()
        self.assertEqual(len(names.resolve_both(base, "repo_dir")), 2)

    def test_env(self):
        self.assertEqual(names.env_names("ROOT"), ("AGENT_HUB_ROOT", "BRUNNA_ROOT"))
        self.assertEqual(names.env_names("PROJECT"), ("AGENTHUB_PROJECT", "BRUNNA_PROJECT"))
        self.assertEqual(names.env("ROOT", environ={"BRUNNA_ROOT": "n"}), "n")
        self.assertEqual(names.env("ROOT", environ={"BRUNNA_ROOT": "n", "AGENT_HUB_ROOT": "o"}), "o",
                         "an old spelling is an explicit override")
        e = {"BRUNNA_STATE": "stale"}
        names.env_set(e, "STATE", "x")
        self.assertEqual(e, {"AGENT_HUB_STATE": "x"}, "v0.6 writes the old spelling and removes the other")
        e = {}
        names.env_set(e, "JOB_CHAIN", "c")
        self.assertEqual(e, {"AGENT_HUB_JOB_CHAIN": "c", "BRUNNA_JOB_CHAIN": "c"}, "documented ones in both")
        names.env_unset(e, "JOB_CHAIN")
        self.assertEqual(e, {})
        self.assertEqual(names.env_strip({"AGENT_HUB_X": 1, "AGENTHUB_Y": 2, "BRUNNA_Z": 3, "HOME": 4}), {"HOME": 4})


class Ratchet(unittest.TestCase):
    def test_literals_only_go_down(self):
        base = json.loads(BASELINE.read_text())
        problems = [f"{cls} {f}: {n} (baseline {base.get(cls, {}).get(f, 0)})"
                    for cls, per in counts().items() for f, n in per.items() if n > base.get(cls, {}).get(f, 0)]
        self.assertEqual(problems, [], "a name written as a literal outside bin/names.py: use names.current/all/env")


def update() -> None:
    """Lower the baseline to today's counts (a file whose count rose stays at the old count, so the test still fails)."""
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    now = counts()
    out = {cls: {f: min(n, base.get(cls, {}).get(f, n)) for f, n in sorted(now[cls].items())} for cls in CLASSES}
    BASELINE.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        update()
    else:
        unittest.main()
