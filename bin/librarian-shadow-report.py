#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""librarian 影子模式的比對結果(docs/design/llm-providers.md 步驟 4):completion 版能不能取代 agent 版。

  librarian-shadow-report.py [--days 7]

讀 .state/librarian-shadow/<日期>.jsonl(librarian.py 每晚寫),每個判斷點列:幾次、有沒有結果、編輯清單合不合規、
「要不要改」跟 agent 版一致的比例、跟 agent 版寫出的內容多像、key 標記出問題的次數、成本。
不一致的逐筆列出來(專案、日期、兩邊的判斷、completion 版的理由),提議的全文在 .state/librarian-shadow/<日期>/。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
DIR = hubconf.STATE / "librarian-shadow"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    since = (datetime.date.today() - datetime.timedelta(days=a.days - 1)).isoformat()
    recs = []
    for f in sorted(DIR.glob("*.jsonl")) if DIR.is_dir() else []:
        if f.stem >= since:
            recs += [{**json.loads(x), "_day": f.stem} for x in f.read_text().splitlines() if x.strip()]
    if not recs:
        print(_("no shadow records in the last {days} {days:day|days} ({dir})", days=a.days, dir=DIR))
        return 1
    for point in sorted({r["point"] for r in recs}):
        rs = [r for r in recs if r["point"] == point]
        ok = [r for r in rs if r.get("ok")]
        sims = [r["similarity"] for r in ok if "similarity" in r]
        cost = sum(r.get("cost_usd") or 0 for r in rs)
        pct = lambda n, d: f"{n}/{d}" + (f"({n * 100 // d}%)" if d else "")
        print(_("== {point}: {n} {n:run|runs}, {nights} {nights:night|nights}, cost ${cost:.3f}", point=point, n=len(rs),
                nights=len({r["_day"] for r in rs}), cost=cost))
        print("  " + _("got a result, valid edit list  {valid}; invalid {invalid}, no result {none}",
                       valid=pct(len(ok), len(rs)), invalid=sum(1 for r in rs if "edit_error" in r),
                       none=sum(1 for r in rs if "edit_error" not in r and not r.get("ok"))))
        print("  " + _("same change-or-not as the agent version {same}",
                       same=pct(sum(1 for r in ok if r.get("same_decision")), len(ok))))
        if sims:
            print("  " + _("similarity to the agent version  mean {mean:.2f}, lowest {low:.2f}; identical {n}",
                           mean=sum(sims) / len(sims), low=min(sims), n=sum(1 for r in ok if r.get("identical_to_agent"))))
        marks = [r for r in ok if r.get("marker_problems")]
        if marks:
            print("  ⚠️ " + _("key marker problems {n} {n:time|times}: ", n=len(marks))
                  + ";".join(f"{r.get('project')} {r['_day']}" for r in marks))
        if point.endswith("index-edits"):
            print("  " + _("roster block untouched {n}", n=pct(sum(1 for r in ok if r.get("roster_untouched")), len(ok))))
        for r in rs:
            if r.get("ok") and not r.get("same_decision"):
                print("  - " + _("disagrees {day} {project}: completion version change={shadow}, agent version={agent}. "
                                 "Reason: {reason}", day=r["_day"], project=r.get("project", ""),
                                 shadow=r.get("shadow_changed"), agent=r.get("agent_changed"),
                                 reason=r.get("reason", "")[:160]))
            elif "edit_error" in r:
                print("  - " + _("invalid {day} {project}: {error}", day=r["_day"], project=r.get("project", ""),
                                 error=r["edit_error"][:160]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
