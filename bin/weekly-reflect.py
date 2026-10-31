#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Weekly cross-project activity analysis (like claude.ai's Reflect, but looking at the Claude Code side).

claude.ai's Reflect reads only claude.ai's chat history and Memory and can't see Claude Code
sessions; this report fills that gap. Output:
  reflect/YYYY-Www.json   this week's statistics + analysis (with --llm)
  reflect/index.html      the report page for all weeks (data embedded in the page, templates/reflect.html as layout)

Statistics are all computed mechanically, zero LLM: per-project messages/sessions/active days/commits/memory notes
(compared with last week), the work distribution by weekday × hour (Taipei time), the late-night share, the longest
continuous stretch, the daily stopping time, how work was delegated (subagents, cross-session messages, skills),
projects started/stopped. The analysis section: see reflect_llm.py.

Data: the runtime's transcript_roots (sessions local to the hub machine, plus those from the remote machine backed up
by transcript-backup.sh, personal profile only: the work profile is deliberately not read), ~/git-origin/mirrors
(updated daily at 04:00), the memory copies in ~/.memory-copies/<name>.

Usage: weekly-reflect.py [--week 2026-W40] [--llm]
  --week omitted = the last full week (Monday to Sunday, Taipei time). Prints the path of the json written.
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import this too

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
import i18n  # noqa: E402
import runtime_ports  # noqa: E402  conversation records only through the interface (Claude Code is one implementation)

_ = i18n.domain(__file__)
TZ = zoneinfo.ZoneInfo("Asia/Taipei")
MIRRORS = hubconf.p("git_origin", "~/git-origin") / "mirrors"
MEMORY_COPIES = hublib.MEMORY_COPIES
OUT = hubconf.data_file("reflect")
TEMPLATE = hublib.HUB / "templates" / "reflect.html"
LATE_NIGHT = range(0, 6)       # 00:00–05:59 Taipei time
BREAK_MINUTES = 30             # a gap between two messages longer than this counts as a break
QUIET_THRESHOLD = 10           # at least this many last week and 0 this week → a stopped project


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
        return hubconf.p("git_origin", "~/git-origin") / "agent-hub.git"
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
            for a in t.actions:  # neutral collaboration kinds (runtime_ports.COLLABS); knows no runtime's tool names
                if a.collab == "subagent":
                    w.agents += 1
                elif a.collab == "workflow":
                    w.workflows += 1
                elif a.collab == "peer_message":
                    w.peer_msgs += 1
                elif a.collab == "skill":
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
    matrix = [[0] * 24 for _day in range(7)]
    for t in this.times:
        matrix[t.weekday()][t.hour] += 1
    total = sum(this.prompts.values())
    late = sum(1 for t in this.times if t.hour in LATE_NIGHT)
    stretch, stretch_at = longest_stretch(this.times)
    last_per_day: dict[datetime.date, datetime.datetime] = {}
    for t in this.times:
        day = (t - datetime.timedelta(hours=6)).date()  # before 6 a.m. counts as the previous day's stopping time
        last_per_day[day] = max(last_per_day.get(day, t), t)
    return {
        "v": 1,  # contract contracts/reflect-week.v1.schema.json
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
    """Embed all of reflect/*.json into templates/reflect.html and write reflect/index.html."""
    weeks = [json.loads(p.read_text()) for p in sorted(OUT.glob("*-W*.json"), reverse=True)]
    data = json.dumps(weeks, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__REFLECT_DATA__*/[]", data)
    path = OUT / "index.html"
    path.write_text(html)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--llm", action="store_true", help=_("add the analysis the model writes"))
    ap.add_argument("--html-only", action="store_true", help=_("only rebuild index.html from the existing json"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.html_only:
        start, end, label = week_bounds(args.week)
        try:
            runtime_ports.load(ports=("transcripts",))
            unsupported = None
        # no conversation records: say so explicitly rather than send the model empty statistics as "nothing done this week"
        except runtime_ports.Unsupported as e:
            unsupported = str(e)
        stats = build_stats(label, start, end, scan(start, end) if not unsupported else {"this": Week(), "prev": Week()})
        if unsupported:
            stats["unsupported"] = unsupported
            print(_("no conversation statistics in the weekly report: {reason}", reason=unsupported))
        elif args.llm:
            import reflect_llm
            stats["analysis"] = reflect_llm.analyze(stats)
        path = OUT / f"{label}.json"
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=1))
        print(path)
    print(build_html())


if __name__ == "__main__":
    main()
