"""Neutral files may not contain the runtime's (Claude Code's) syntax or paths: translation happens only in
runtime_claude.py (docs/design/capabilities.md).

What is checked is code and strings, not the word "Claude": explanatory text may say where the translation lives, but
may not write out Claude's settings format itself.
New neutral files go into NEUTRAL; add lease-runner.py once m10251446 (contracts independent of the runtime) is done.
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
NEUTRAL = ["policy.py", "seatbelt.py", "landlock.py", "isolation.py", "sandboxed-run", "cap-runner.py", "hub-run",
           "appliers.py", "applier_scheduler.py", "config-check", "hub"]
PATTERNS = [
    (re.compile(r"(?<![\w])\.claude\b"), "Claude's settings/credentials directory"),
    (re.compile(r"\b(Read|Edit|Write|MultiEdit|NotebookEdit|Bash|WebFetch|WebSearch|Glob|Grep)\("),
     "Claude's permission rule syntax"),
    (re.compile(r"settings\.local\.json"), "Claude's settings file"),
    (re.compile(r"\bclaude\s+-p\b|[\"']claude[\"']\s*,"), "calling claude directly"),
    (re.compile(r"--(resume|session-id|permission-prompt-tool|allowedTools|append-system-prompt)\b"), "claude's flags"),
    (re.compile(r"\bCLAUDE_[A-Z_]+"), "Claude's environment variables"),
]


class TestRuntimeBoundary(unittest.TestCase):
    def test_neutral_files_have_no_runtime_syntax(self):
        checked = 0
        for name in NEUTRAL:
            f = BIN / name
            if not f.exists():
                continue
            checked += 1
            for i, line in enumerate(f.read_text().splitlines(), 1):
                for pat, what in PATTERNS:
                    self.assertIsNone(pat.search(line), f"{name}:{i} contains {what}: {line.strip()}")
        self.assertGreater(checked, 0)

    def test_patterns_catch_examples(self):
        for bad in ['deny += ["Read(~/x/**)"]', 'p = home / ".claude"', "settings.local.json",
                    'cmd = ["claude", "-p"]', '"--resume", sid', "os.environ['CLAUDE_CODE_OAUTH_TOKEN']"]:
            self.assertTrue(any(p.search(bad) for p, _ in PATTERNS), bad)
        for ok in ["runtime_claude.settings(pol)", "translated into Claude Code's settings"]:
            self.assertFalse(any(p.search(ok) for p, _ in PATTERNS), ok)



# ---------- ratchet: runtime syntax elsewhere in the platform may only go down (docs/design/llm-providers.md step 1) --
# The goal is "Claude appears only in runtime_claude.py"; there are still many, too many to clear at once, so like
# test_personal_boundary it records a baseline that may only go down.
# One more Claude-specific model name counts too (a judgment point names a tier, not haiku/sonnet/opus).
RATCHET_SCOPE = ("bin", "modules", "agent-jobs", "contracts/llm-points.yaml")
RATCHET_EXEMPT = {"bin/runtime_claude.py",   # the translation lives only here
                  "modules/claude.yaml"}    # Claude Code-only upkeep (login check, update, token):
                  # someone not using Claude leaves this module off
RATCHET = PATTERNS + [(re.compile(r"\b(haiku|sonnet|opus)\b", re.I), "Claude's model names")]
RUNTIME_BASELINE = ROOT / "tests" / "fixtures" / "runtime-baseline.json"


def ratchet_files() -> list[pathlib.Path]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *RATCHET_SCOPE],
                             capture_output=True, text=True, check=True).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    if not paths:  # the test-gate's exported copy is not a git repo
        paths = [p for d in RATCHET_SCOPE for p in ((ROOT / d).rglob("*") if (ROOT / d).is_dir() else [ROOT / d])]
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts
                  and str(p.relative_to(ROOT)) not in RATCHET_EXEMPT)


def ratchet_counts() -> dict[str, int]:
    out = {}
    for f in ratchet_files():
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if n := sum(len(p.findall(text)) for p, _ in RATCHET):
            out[str(f.relative_to(ROOT))] = n
    return out


class TestRuntimeRatchet(unittest.TestCase):
    def test_scans_something(self):
        self.assertGreater(len(ratchet_files()), 50)

    def test_counts_only_go_down(self):
        base = json.loads(RUNTIME_BASELINE.read_text())
        worse = [f"{f}:{n} (baseline {base.get(f, 0)})" for f, n in ratchet_counts().items() if n > base.get(f, 0)]
        self.assertEqual(worse, [], "runtime-specific (Claude) syntax went up: move it into runtime_claude.py, "
                                    "or have the judgment point name a tier; edit "
                                    "tests/fixtures/runtime-baseline.json by hand only for a real reason")


def update() -> int:
    """The baseline only goes down (same as test_personal_boundary)."""
    now = ratchet_counts()
    base = json.loads(RUNTIME_BASELINE.read_text()) if RUNTIME_BASELINE.exists() else dict(now)
    for f in list(base):
        n = now.get(f, 0)
        if n == 0:
            del base[f]
        elif n < base[f]:
            base[f] = n
    RUNTIME_BASELINE.write_text(json.dumps(dict(sorted(base.items())), ensure_ascii=False, indent=1) + "\n")
    print(f"{len(base)} files, {sum(base.values())} occurrences in all")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        sys.exit(update())
    unittest.main()
