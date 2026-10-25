#!/usr/bin/env python3
"""每週跨專案活動分析(類似 claude.ai 的 Reflect,但看的是 Claude Code 這邊)。

claude.ai 的 Reflect 只讀 claude.ai 的聊天紀錄跟 Memory,看不到 Claude Code 的
session;這份報告補上那一塊。產出:
  reflect/YYYY-Www.json   這週的統計 + 分析(--llm 時)
  reflect/index.html      所有週的報告頁(資料直接嵌在頁面裡,templates/reflect.html 套版)

統計全部機械計算、零 LLM:各專案訊息/session/活躍天/commit/memory 筆記(對比上週)、
星期 × 小時的工作分佈(台北時間)、深夜比例、最長連續工作、每天收工時間、交辦方式
(subagent、跨 session 訊息、skill)、新開始/停下來的專案。分析段落見 reflect_llm.py。

資料:mini 上的 transcript 備份(~/transcript-backups,MBP 每小時同步,只有
~/.claude-personal 這個 profile——工作 profile 刻意不備份)、~/git-origin/mirrors
(每天 04:00 更新)、~/.memory-copies/<name> 的 memory 副本。

用法:weekly-reflect.py [--week 2026-W40] [--llm]
  --week 省略 = 上一個完整的週(週一到週日,台北時間)。印出寫入的 json 路徑。
"""
import argparse
import collections
import datetime
import json
import pathlib
import subprocess
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import runtime_ports  # noqa: E402  對話紀錄只透過介面讀(Claude Code 只是一個實作)

TZ = zoneinfo.ZoneInfo("Asia/Taipei")
MIRRORS = hublib.HOME / "git-origin" / "mirrors"
MEMORY_COPIES = hublib.MEMORY_COPIES
OUT = hublib.HUB / "reflect"
TEMPLATE = hublib.HUB / "templates" / "reflect.html"
LATE_NIGHT = range(0, 6)       # 00:00–05:59 台北時間
BREAK_MINUTES = 30             # 兩則訊息間隔超過這個就算休息過
QUIET_THRESHOLD = 10           # 上週至少這麼多則、這週 0 則 → 停下來的專案


def week_bounds(week: str | None) -> tuple[datetime.datetime, datetime.datetime, str]:
    if week:
        y, w = week.split("-W")
        start = datetime.datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=TZ)
    else:
        today = datetime.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - datetime.timedelta(days=today.weekday() + 7)
    end = start + datetime.timedelta(days=7)
    iso = start.isocalendar()
    return start, end, f"{iso.year}-W{iso.week:02d}"


def git_count(repo: pathlib.Path, since: datetime.datetime, until: datetime.datetime,
              *extra: str, work_only: bool = False) -> int | None:
    if not repo.exists():
        return None
    out = subprocess.run(["git", "-C", str(repo), "log", "--all", f"--since={since.isoformat()}",
                          f"--until={until.isoformat()}", "--format=%s", *extra],
                         capture_output=True, text=True).stdout.splitlines()
    return sum(1 for s in out if hublib.is_work_subject(s)) if work_only else len(out)


def mirror_for(name: str) -> pathlib.Path:
    origin = hubconf.roster().get(name, {}).get("origin") or ""
    if origin.endswith(".git"):
        return MIRRORS / pathlib.Path(origin).name
    if name == "agent-hub":
        return hublib.HOME / "git-origin" / "agent-hub.git"
    return MIRRORS / f"{name}.git"


class Week:
    def __init__(self) -> None:
        self.prompts: collections.Counter = collections.Counter()
        self.sessions: dict[str, set] = collections.defaultdict(set)
        self.days: dict[str, set] = collections.defaultdict(set)
        self.times: list[datetime.datetime] = []
        self.agents = self.workflows = self.peer_msgs = 0
        self.skills: collections.Counter = collections.Counter()


def scan(start: datetime.datetime, end: datetime.datetime) -> dict[str, Week]:
    prev_start = start - datetime.timedelta(days=7)
    weeks = {"this": Week(), "prev": Week()}
    rt = runtime_ports.load(ports=("transcripts",))
    for name, paths in hublib.project_paths().items():
        for t in rt.turns(paths, prev_start.astimezone(datetime.timezone.utc), end.astimezone(datetime.timezone.utc)):
            w = weeks["this" if t.ts >= start else "prev"]
            if t.role == "human":
                local = t.ts.astimezone(TZ)
                w.prompts[name] += 1
                w.sessions[name].add(t.session)
                w.days[name].add(local.date())
                w.times.append(local)
            for a in t.actions:  # 中立的動作種類(runtime_ports.ACTIONS),不認得任何 runtime 的工具名稱
                if a.kind == "subagent":
                    w.agents += 1
                elif a.kind == "workflow":
                    w.workflows += 1
                elif a.kind == "peer_message":
                    w.peer_msgs += 1
                elif a.kind == "skill":
                    w.skills[a.detail] += 1
    return weeks


def longest_stretch(times: list[datetime.datetime]) -> tuple[float, datetime.datetime | None]:
    if not times:
        return 0.0, None
    ts = sorted(times)
    best, best_start, run_start = 0.0, ts[0], ts[0]
    for a, b in zip(ts, ts[1:]):
        if (b - a).total_seconds() > BREAK_MINUTES * 60:
            run_start = b
        dur = (b - run_start).total_seconds() / 3600
        if dur > best:
            best, best_start = dur, run_start
    return best, best_start


def build_stats(label: str, start: datetime.datetime, end: datetime.datetime, weeks: dict[str, Week]) -> dict:
    this, prev = weeks["this"], weeks["prev"]
    names = sorted(set(this.prompts) | set(prev.prompts), key=lambda n: (-this.prompts[n], n))
    projects = []
    for n in names:
        projects.append({
            "name": n, "prompts": this.prompts[n], "prompts_prev": prev.prompts[n],
            "sessions": len(this.sessions[n]), "days": len(this.days[n]),
            "commits": git_count(mirror_for(n), start, end, work_only=True),
            "notes": git_count(MEMORY_COPIES / n, start, end, "--invert-grep", "--grep=^init:", "--", "memory/"),
        })
    matrix = [[0] * 24 for _ in range(7)]
    for t in this.times:
        matrix[t.weekday()][t.hour] += 1
    total = sum(this.prompts.values())
    late = sum(1 for t in this.times if t.hour in LATE_NIGHT)
    stretch, stretch_at = longest_stretch(this.times)
    last_per_day: dict[datetime.date, datetime.datetime] = {}
    for t in this.times:
        day = (t - datetime.timedelta(hours=6)).date()  # 凌晨 6 點前算前一天的收工
        last_per_day[day] = max(last_per_day.get(day, t), t)
    return {
        "v": 1,  # 契約 contracts/reflect-week.v1.schema.json
        "week": label,
        "start": start.date().isoformat(), "end": (end - datetime.timedelta(days=1)).date().isoformat(),
        "generated": datetime.datetime.now(TZ).isoformat(timespec="minutes"),
        "totals": {"prompts": total, "prompts_prev": sum(prev.prompts.values()),
                   "sessions": sum(len(s) for s in this.sessions.values()),
                   "projects": len(this.prompts), "days": len({t.date() for t in this.times})},
        "projects": projects,
        "matrix": matrix,
        "rhythm": {"late": late, "late_share": late / total if total else 0,
                   "late_prev": sum(1 for t in prev.times if t.hour in LATE_NIGHT),
                   "longest_hours": round(stretch, 1),
                   "longest_start": stretch_at.isoformat(timespec="minutes") if stretch_at else None,
                   "last_per_day": [{"day": f"{d:%m/%d}", "time": f"{t:%H:%M}",
                                     "late": t.hour in LATE_NIGHT}
                                    for d, t in sorted(last_per_day.items())]},
        "delegation": {"agents": this.agents, "agents_prev": prev.agents, "workflows": this.workflows,
                       "peer": this.peer_msgs, "peer_prev": prev.peer_msgs,
                       "skills": dict(this.skills.most_common(8))},
        "changes": {"new": sorted(n for n in this.prompts if not prev.prompts[n]),
                    "quiet": sorted(n for n in prev.prompts
                                    if prev.prompts[n] >= QUIET_THRESHOLD and not this.prompts[n])},
    }


def build_html() -> pathlib.Path:
    """把 reflect/*.json 全部嵌進 templates/reflect.html,寫成 reflect/index.html。"""
    weeks = [json.loads(p.read_text()) for p in sorted(OUT.glob("*-W*.json"), reverse=True)]
    data = json.dumps(weeks, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__REFLECT_DATA__*/[]", data)
    path = OUT / "index.html"
    path.write_text(html)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--llm", action="store_true", help="附加模型寫的分析")
    ap.add_argument("--html-only", action="store_true", help="只用現有 json 重建 index.html")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.html_only:
        start, end, label = week_bounds(args.week)
        stats = build_stats(label, start, end, scan(start, end))
        if args.llm:
            import reflect_llm
            stats["analysis"] = reflect_llm.analyze(stats)
        path = OUT / f"{label}.json"
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=1))
        print(path)
    print(build_html())


if __name__ == "__main__":
    main()
