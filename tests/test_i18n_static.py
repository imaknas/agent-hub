"""E0 of docs/design/i18n.md: Chinese text in tracked files may only go down, until only the catalogs and fixtures have it.

The baseline (tests/fixtures/cjk-baseline.json) is lines containing CJK characters per file, taken before translation
started. A file may drop below its baseline, never rise above it; a file not in the baseline may have none. Allowed to
keep Chinese: `locales/` (the catalogs) and `tests/fixtures/` (goldens and deliberately Chinese test data).

    bin/py -m unittest tests.test_i18n_static
    bin/py tests/test_i18n_static.py --update     # lower the baseline after translating (never raises it)
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "cjk-baseline.json"
CJK = re.compile("[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
ALLOWED = ("locales/", "tests/fixtures/")


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        return [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):   # exported tree without git (the commit gate's copy)
        return [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
                and ".state" not in p.parts]


def counts() -> dict[str, int]:
    out = {}
    for rel in tracked():
        if rel.startswith(ALLOWED):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = sum(1 for line in text.splitlines() if CJK.search(line))
        if n:
            out[rel] = n
    return out


def load_baseline() -> dict[str, int]:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


class CJKRatchet(unittest.TestCase):
    def test_only_goes_down(self):
        base, now = load_baseline(), counts()
        worse = [f"{f}: {n} lines with Chinese (baseline {base.get(f, 0)})" for f, n in sorted(now.items()) if n > base.get(f, 0)]
        self.assertEqual(worse, [], "Chinese text went up. New text is English; owner-facing Chinese goes in locales/ "
                                    "(docs/design/i18n.md). Lower the baseline with --update after translating.")

    def test_pattern(self):
        self.assertTrue(CJK.search("\u5f85\u8fa6"))
        self.assertTrue(CJK.search("\u3014agent\u3015"))
        self.assertFalse(CJK.search("To-do — [agent] “quoted” café"))


def update() -> int:
    """Lower entries that went down, drop ones that reached zero; never raise one (that is what the test is for)."""
    base, now = load_baseline(), counts()
    if not BASELINE.exists():
        base = now
    else:
        base = {f: min(n, now[f]) for f, n in base.items() if now.get(f)}
    BASELINE.write_text(json.dumps(dict(sorted(base.items())), ensure_ascii=False, indent=1) + "\n")
    print(f"cjk baseline: {len(base)} files, {sum(base.values())} lines")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        sys.exit(update())
    unittest.main()
