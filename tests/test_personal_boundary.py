"""平台程式裡的個人名稱、機器名稱只准變少(docs/design/new-user.md 3.5)。

平台要能給別人用:程式碼裡不該有人(Knas)、助理(Muninn)、機器(project-30、/Users/you)的名字,`bin/` 也不該自己拼
`.secrets` 的路徑(要經過 hubconf)。現在還有很多(多數是註解裡的決策紀錄),一次清不完,所以是 ratchet:
每個檔案的出現次數記在 tests/fixtures/personal-baseline.json,**只准變少**;新檔案一律是 0。

- 變多 → 失敗。要嘛改成從設定讀(hubconf:owner.tag、assistant.project、hosts…)、寫成通稱(owner、助理),
  要嘛真的有理由就手動改基準檔(會出現在 diff 裡,有人看得到)。
- 變少 → 照樣通過;跑 `bin/py tests/test_personal_boundary.py --update` 把基準降下來(--update 只會降、不會升)。

    bin/py -m unittest discover -s tests
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCOPE = ("bin", "templates", "launchd", "tests", "agent-jobs", "modules")
# 公開的說明文件:這個部署特有的事寫在實例目錄的 config/AGENTS.local.md、known-issues.local.md(10-29 開源前清掉;基準 0)。
# docs/design/ 是有日期的決策紀錄,維護者的名字在那裡正常,不掃
PUBLIC_DOCS = ("AGENTS.md", "README.md", "known-issues.md", "docs/mcp.md", "hub.example.json")
BASELINE = ROOT / "tests" / "fixtures" / "personal-baseline.json"
SELF = pathlib.Path(__file__).resolve()
RULES = {
    "personal": re.compile(r"knas|muninn|project-30|imaknas|/Users/you\b|/Users/you", re.I),
    "secrets-path": re.compile(r"[\"'/]\.secrets\b"),   # bin/ 裡自己拼 .secrets 的路徑(要經過 hubconf.secrets_dir())
    # bin/ 裡自己拼 config/data 檔的路徑(要經過 hubconf.config_file()/data_file()、shell 用 hubpaths.sh 的 $HUB_CONFIG/$HUB_DATA)
    # 真的機器名、tailnet 名稱、owner 的帳號、備份 bucket、遙控那台的代稱(knas-tasks.py 這種檔名不算)
    "public-docs": re.compile(r"tail[0-9a-f]+\.ts\.net|/Users/|imaknas|owner|project-30|hub-host|<backup-bucket>"
                              r"|(?<![\w-])knas(?![\w-])|\bMBP\b", re.I),
    "instance-path": re.compile(r"(?:/ |\$(?:HUB|AGENT_HUB)/)[\"']?(?:(?:digest|reflect)(?=[\"'/\s)])|todo\.md|index\.md|review-log\.md"
                                r"|models\.env|mirror-list\.txt|services\.tsv|schedule\.yaml|legacy-paths\.tsv"
                                r"|openclaw-migration-status\.md|hub\.json)\b"),   # digest.html 之類的輸出檔名不算
}
RULE_SCOPE = {"personal": SCOPE, "secrets-path": ("bin",), "instance-path": ("bin",), "public-docs": PUBLIC_DOCS}
# 路徑定義的地方:hubconf(Python)、hubpaths.sh(shell)、看門狗(刻意不 import 平台的模組,自己一份;test_instance_layout 檢查三份一致)、
# bin/hub(init / move-instance 建實例目錄的那段)
DEFINED_IN = {r: {"bin/hubconf.py", "bin/hubpaths.sh", "bin/scheduler-watchdog.py", "bin/agenthub"} for r in ("secrets-path", "instance-path")}


def files() -> list[pathlib.Path]:
    """版控裡的檔案(git ls-files;不在 git 裡跑時退回掃目錄)。"""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *SCOPE, *PUBLIC_DOCS],
                             capture_output=True, text=True, check=True).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    if not paths:  # 不在 git 裡,或 git 往上找到別的 repo、拿到空清單(test-gate 的匯出副本曾經這樣,ratchet 什麼都沒掃就過了)
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
                continue  # 路徑只在這裡定義一次
            if (rel.split("/")[0] in RULE_SCOPE[rule] or rel in RULE_SCOPE[rule]) and (n := len(pat.findall(text))):
                out[rule][rel] = n
    return out


def load_baseline() -> dict[str, dict[str, int]]:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {r: {} for r in RULES}


class PersonalBoundary(unittest.TestCase):
    def test_scans_something(self):
        self.assertGreater(len(files()), 50, "一個檔都沒掃到 = 這個 ratchet 形同虛設")

    def test_counts_only_go_down(self):
        base, now = load_baseline(), counts()
        worse = [f"{rule} {f}:{n}(基準 {base.get(rule, {}).get(f, 0)})"
                 for rule, per in now.items() for f, n in per.items() if n > base.get(rule, {}).get(f, 0)]
        self.assertEqual(worse, [], "個人名稱/機器名稱/.secrets 路徑變多了——改成從設定讀或寫成通稱;"
                                    "真的有理由才手動改 tests/fixtures/personal-baseline.json")

    def test_rules_match_what_they_should(self):
        p, s = RULES["personal"], RULES["secrets-path"]
        for hit in ["〔Knas〕", "muninn digests", "ssh hub-host", "/Users/you/agent-hub", "imaknas/x"]:
            self.assertTrue(p.search(hit), hit)
        for miss in ["owner.tag", "/Users/moeller", "assistant"]:
            self.assertFalse(p.search(miss), miss)
        self.assertTrue(s.search('ROOT / ".secrets" / "x"'))
        self.assertTrue(s.search("~/agent-hub/.secrets/runner-token"))
        self.assertFalse(s.search("hubconf.secret('x')"))
        d = RULES["public-docs"]
        for hit in ["https://box.<tailnet>.ts.net/", "〔Knas〕", "MBP 只當遙控端", "/Users/you/x", "bucket <backup-bucket>"]:
            self.assertTrue(d.search(hit), hit)
        for miss in ["<host>.<tailnet>.ts.net", "bin/knas-tasks.py", "minischema", "〔owner〕"]:
            self.assertFalse(d.search(miss), miss)


def update() -> int:
    """基準只降不升:現在比基準少的檔案降下來、歸零的拿掉;比基準多的不動(那是要修的)。"""
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
