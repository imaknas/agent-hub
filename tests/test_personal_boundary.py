"""Personal names and machine names in the platform's code may only go down (docs/design/new-user.md 3.5).

The platform has to be usable by others: the code should not name the person (Knas), the assistant (Muninn) or
accounts (machine names and home-directory paths are on the instance's private list), and `bin/` should not build the
`.secrets` path itself (it goes through hubconf). There are still many (mostly decision records in comments), too many
to clear at once, so this is a ratchet: each file's count is recorded in tests/fixtures/personal-baseline.json and
**may only go down**; a new file is always 0.

- Up → fail. Either read it from settings (hubconf: owner.tag, assistant.project, hosts…) or use the generic term
  (owner, assistant), or, if there really is a reason, edit the baseline by hand (it shows in the diff, someone sees
  it).
- Down → still passes; run `bin/py tests/test_personal_boundary.py --update` to lower the baseline (--update only
  lowers, never raises).

    bin/py -m unittest discover -s tests
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import private_names  # noqa: E402
import todo_tokens  # noqa: E402
SCOPE = ("bin", "templates", "launchd", "tests", "agent-jobs", "modules", "mods")
# Public docs: what is specific to this deployment goes in the instance directory's config/AGENTS.local.md,
# known-issues.local.md (cleared 10-29 before open-sourcing; baseline 0).
# docs/design/ holds dated decision records, where the maintainer's name is normal: not scanned
PUBLIC_DOCS = ("AGENTS.md", "README.md", "README.zh-Hant.md", "known-issues.md", "docs/mcp.md", "hub.example.json")
BASELINE = ROOT / "tests" / "fixtures" / "personal-baseline.json"
# the words for "project" (zh-Hant and English) that a private name which is also a common word is counted next to
PROJECT_WORDS = json.loads((ROOT / "tests" / "fixtures" / "personal-boundary.json").read_text())["project_words"]
SELF = pathlib.Path(__file__).resolve()
RULES = {
    "personal": re.compile(r"knas|muninn|imaknas", re.I),
    # bin/ building the .secrets path itself (goes through hubconf.secrets_dir())
    "secrets-path": re.compile(r"[\"'/]\.secrets\b"),
    # real machine names, tailnet names, the owner's account, the backup bucket, the remote machine's nickname (a file
    # name like knas-tasks.py does not count)
    "public-docs": re.compile(r"tail[0-9a-f]+\.ts\.net|/Users/|imaknas"
                              r"|(?<![\w-])knas(?![\w-])|\bMBP\b", re.I),
    # bin/ building config/data file paths itself (goes through hubconf.config_file()/data_file(); shell uses
    # hubpaths.sh's $HUB_CONFIG/$HUB_DATA); output file names like digest.html do not count
    "instance-path": re.compile(r"(?:/ |\$(?:HUB|AGENT_HUB)/)[\"']?(?:(?:digest|reflect)(?=[\"'/\s)])|todo\.md|index\.md|review-log\.md"
                                r"|models\.env|mirror-list\.txt|services\.tsv|schedule\.yaml|legacy-paths\.tsv"
                                r"|openclaw-migration-status\.md|hub\.json)\b"),
}
RULE_SCOPE = {"personal": SCOPE, "secrets-path": ("bin",), "instance-path": ("bin",), "public-docs": PUBLIC_DOCS}
# Where the paths are defined: hubconf (Python), hubpaths.sh (shell), the watchdog (deliberately imports no platform
# module, keeps its own copy; test_instance_layout checks the three agree), bin/hub (the part of init / move-instance
# that builds the instance directory)
DEFINED_IN = {r: {"bin/hubconf.py", "bin/hubpaths.sh", "bin/scheduler-watchdog.py", "bin/agenthub"} for r in ("secrets-path", "instance-path")}


def files() -> list[pathlib.Path]:
    """Files under version control (git ls-files; falls back to scanning directories when not run inside git)."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *SCOPE, *PUBLIC_DOCS],
                             capture_output=True, text=True, check=True).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    # not in git, or git found another repo further up and returned an empty list (the test runner's exported copy once
    # did this, and the ratchet passed having scanned nothing)
    if not paths:
        paths = [p for d in SCOPE for p in (ROOT / d).rglob("*") if p.is_file()] + [ROOT / f for f in PUBLIC_DOCS]
    return sorted(p for p in paths if p.is_file() and p.resolve() not in (SELF, BASELINE.resolve())
                  and "__pycache__" not in p.parts)


def counts() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {r: {} for r in RULES}
    for f in files():
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(f.relative_to(ROOT))
        for rule, pat in RULES.items():
            if rel in DEFINED_IN.get(rule, ()):
                continue  # the path is defined only here
            if (rel.split("/")[0] in RULE_SCOPE[rule] or rel in RULE_SCOPE[rule]) and (n := len(pat.findall(text))):
                out[rule][rel] = n
    return out


def private_names_file() -> pathlib.Path | None:
    """This deployment's list of private project names (bin/private_names.py)."""
    return private_names.names_file()


def private_name_patterns(text: str) -> list[tuple[str, re.Pattern]]:
    """Each line of the list (bin/private_names.py)."""
    return private_names.patterns(text, PROJECT_WORDS)


def private_name_hits(names_text: str) -> list[str]:
    """Places in the platform repo (every versioned file) that mention a private project name: `file:line:name`."""
    pats = private_name_patterns(names_text)
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True).stdout
    paths = [ROOT / q for q in out.split("\0") if q] or [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    hits = []
    for f in sorted(paths):
        try:
            lines = f.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            for name, pat in pats:
                if pat.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}:{name}")
    return hits


def load_baseline() -> dict[str, dict[str, int]]:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {r: {} for r in RULES}


class PersonalBoundary(unittest.TestCase):
    def test_scans_something(self):
        self.assertGreater(len(files()), 50, "not a single file scanned = this ratchet is a dead letter")

    def test_counts_only_go_down(self):
        base, now = load_baseline(), counts()
        worse = [f"{rule} {f}:{n}(baseline {base.get(rule, {}).get(f, 0)})"
                 for rule, per in now.items() for f, n in per.items() if n > base.get(rule, {}).get(f, 0)]
        self.assertEqual(worse, [], "personal names / machine names / .secrets paths went up: read them from settings "
                                    "or use the generic term; edit tests/fixtures/personal-baseline.json by hand "
                                    "only for a real reason")

    def test_no_private_project_names(self):
        """Only agent-hub, Muninn and project names already public may appear in the platform repo; not a single private
        project name (the list is in the instance's config)."""
        f = private_names_file()
        if not f or not f.exists():
            self.skipTest("this machine has no list of private project names (private-names.txt in config)")
        self.assertEqual(private_name_hits(f.read_text()), [],
                         "a private project name appears in the platform repo: replace it with a generic example name")

    def test_private_name_patterns(self):
        pats = dict(private_name_patterns("secret-app\nnotes ~\n"))
        self.assertTrue(pats["secret-app"].search("work in secret-app today"))
        self.assertTrue(pats["secret-app"].search("task-secret-app-funnel"), "next to a - counts too")
        self.assertFalse(pats["secret-app"].search("notsecret-apps"))
        for hit in ("~/workspace/notes", "projects/notes.yaml", *(f"notes {w}" for w in PROJECT_WORDS),
                    *(f"{w} notes" for w in PROJECT_WORDS)):
            self.assertTrue(pats["notes"].search(hit), hit)
        for miss in ("--notes", "notes_args", 'claim(c, rows, "notes")', "def notes(c, r):", "pause/notes"):
            self.assertFalse(pats["notes"].search(miss), miss)

    def test_rules_match_what_they_should(self):
        p, s = RULES["personal"], RULES["secrets-path"]
        for hit in [todo_tokens.bracket("zh-Hant", "Knas"), "muninn digests", "imaknas@mbp", "imaknas/x"]:
            self.assertTrue(p.search(hit), hit)
        for miss in ["owner.tag", "/Users/moeller", "assistant"]:
            self.assertFalse(p.search(miss), miss)
        self.assertTrue(s.search('ROOT / ".secrets" / "x"'))
        self.assertTrue(s.search("~/agent-hub/.secrets/runner-token"))
        self.assertFalse(s.search("hubconf.secret('x')"))
        d = RULES["public-docs"]
        for hit in ["https://box.tail0a1b2c.ts.net/", todo_tokens.bracket("zh-Hant", "Knas"), "the MBP is only a remote",
                    "/Users/someone/x"]:
            self.assertTrue(d.search(hit), hit)
        for miss in ["<host>.<tailnet>.ts.net", "bin/knas-tasks.py", "minischema", todo_tokens.bracket("zh-Hant", "owner")]:
            self.assertFalse(d.search(miss), miss)


def update() -> int:
    """The baseline only goes down: files now below it are lowered, those at zero removed; those above it are left
    alone (those need fixing)."""
    base, now = load_baseline(), counts()
    if not BASELINE.exists():
        base = now
    else:
        for rule in RULES:
            per = base.setdefault(rule, {})
            for f in list(per):
                n = now[rule].get(f, 0)
                if n == 0:
                    del per[f]
                elif n < per[f]:
                    per[f] = n
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps({r: dict(sorted(base.get(r, {}).items())) for r in RULES},
                                   ensure_ascii=False, indent=1) + "\n")
    print({r: sum(v.values()) for r, v in base.items()})
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        sys.exit(update())
    unittest.main()
