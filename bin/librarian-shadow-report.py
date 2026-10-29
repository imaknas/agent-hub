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
        print(f"最近 {a.days} 天沒有影子紀錄({DIR})")
        return 1
    for point in sorted({r["point"] for r in recs}):
        rs = [r for r in recs if r["point"] == point]
        ok = [r for r in rs if r.get("ok")]
        sims = [r["similarity"] for r in ok if "similarity" in r]
        cost = sum(r.get("cost_usd") or 0 for r in rs)
        pct = lambda n, d: f"{n}/{d}" + (f"({n * 100 // d}%)" if d else "")
        print(f"== {point}:{len(rs)} 次,{len({r['_day'] for r in rs})} 晚,成本 ${cost:.3f}")
        print(f"  有結果、編輯清單合規  {pct(len(ok), len(rs))};不合規 {sum(1 for r in rs if 'edit_error' in r)}、"
              f"沒結果 {sum(1 for r in rs if 'edit_error' not in r and not r.get('ok'))}")
        print(f"  要不要改跟 agent 版一致 {pct(sum(1 for r in ok if r.get('same_decision')), len(ok))}")
        if sims:
            print(f"  跟 agent 版的相似度  平均 {sum(sims) / len(sims):.2f},最低 {min(sims):.2f};"
                  f"逐字一樣 {sum(1 for r in ok if r.get('identical_to_agent'))}")
        marks = [r for r in ok if r.get("marker_problems")]
        if marks:
            print(f"  ⚠️ key 標記有問題 {len(marks)} 次:" + ";".join(f"{r.get('project')} {r['_day']}" for r in marks))
        if point.endswith("index-edits"):
            print(f"  名冊區塊沒被動 {pct(sum(1 for r in ok if r.get('roster_untouched')), len(ok))}")
        for r in rs:
            if r.get("ok") and not r.get("same_decision"):
                print(f"  - 不一致 {r['_day']} {r.get('project', '')}:completion 版要改={r.get('shadow_changed')},"
                      f"agent 版={r.get('agent_changed')}。理由:{r.get('reason', '')[:160]}")
            elif "edit_error" in r:
                print(f"  - 不合規 {r['_day']} {r.get('project', '')}:{r['edit_error'][:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
