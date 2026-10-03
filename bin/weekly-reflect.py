#!/usr/bin/env python3
"""每週跨專案活動總覽(類似 claude.ai 的 Reflect,但看的是 Claude Code 這邊)。

claude.ai 的 Reflect 只讀 claude.ai 的聊天紀錄跟 Memory,看不到 Claude Code 的
session;這份報告補上那一塊。統計全部機械計算、零 LLM:
  - 各專案:session 數、真人訊息數、活躍天數、專案 commit、memory 筆記,跟上週比
  - 什麼時段在工作(台北時間的小時/星期分佈)
  - 作息:深夜訊息比例、每天最後一則訊息時間、最長連續工作時段
  - 交辦方式:開了多少 subagent、跨 session 傳訊、用了哪些 skill
  - 變化:這週新冒出來的專案、上週活躍這週停下來的專案
--llm 時另外附一段模型寫的「主題與 AI 協作觀察」(見 reflect_llm 那段)。

資料:mini 上的 transcript 備份(~/transcript-backups,MBP 每小時同步,只有
~/.claude-personal 這個 profile——工作 profile 刻意不備份)、~/git-origin/mirrors
(每天 04:00 更新)、~/projects/<name> 的 memory 副本。

用法:weekly-reflect.py [--week 2026-W40] [--out ~/agent-hub/reflect] [--llm]
  --week 省略 = 上一個完整的週(週一到週日,台北時間)。
"""
import argparse
import collections
import datetime
import pathlib
import subprocess
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hublib  # noqa: E402

TZ = zoneinfo.ZoneInfo("Asia/Taipei")
TRANSCRIPTS = hublib.HOME / "transcript-backups" / "claude-personal-projects"
MIRRORS = hublib.HOME / "git-origin" / "mirrors"
MEMORY_COPIES = hublib.HOME / "projects"
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
    for line in hublib.MANIFEST.read_text().splitlines()[1:]:
        cols = line.split("\t")
        if cols and cols[0] == name and len(cols) > 2 and cols[2].endswith(".git"):
            return MIRRORS / pathlib.Path(cols[2]).name
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
    for name, paths in hublib.project_paths().items():
        for e in hublib.iter_entries(TRANSCRIPTS, paths, prev_start.astimezone(datetime.timezone.utc),
                                     end.astimezone(datetime.timezone.utc)):
            w = weeks["this" if e.ts >= start else "prev"]
            o = e.entry
            if hublib.is_human_prompt(o):
                local = e.ts.astimezone(TZ)
                w.prompts[name] += 1
                w.sessions[name].add(e.session)
                w.days[name].add(local.date())
                w.times.append(local)
            elif o.get("type") == "assistant":
                for c in o.get("message", {}).get("content", []) or []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    tool = c.get("name", "")
                    if tool == "Agent":
                        w.agents += 1
                    elif tool == "Workflow":
                        w.workflows += 1
                    elif tool == "SendMessage" or tool.endswith("send_message"):
                        w.peer_msgs += 1
                    elif tool == "Skill":
                        w.skills[(c.get("input") or {}).get("skill", "?")] += 1
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


def bar(n: int, top: int, width: int = 20) -> str:
    return "█" * round(width * n / top) if top else ""


def delta(a: int, b: int) -> str:
    d = a - b
    return f"{d:+d}" if d else "±0"


def render(label: str, start: datetime.datetime, end: datetime.datetime, weeks: dict[str, Week]) -> str:
    this, prev = weeks["this"], weeks["prev"]
    prev_start = start - datetime.timedelta(days=7)
    o = [f"# 每週活動總覽 {label}", "",
         f"> {start:%Y-%m-%d}(一)~ {(end - datetime.timedelta(days=1)):%Y-%m-%d}(日),台北時間。"
         "`bin/weekly-reflect.py` 機械產生。",
         "> 只涵蓋個人 profile(`~/.claude-personal`)的 Claude Code session;工作 profile 刻意不備份,"
         "claude.ai 網頁/App 的對話在 Reflect 裡看。「訊息」只算你實際送出的,不含 session 之間的訊息跟系統通知。", ""]

    total, total_prev = sum(this.prompts.values()), sum(prev.prompts.values())
    all_days = {t.date() for t in this.times}
    o += ["## 總覽", "",
          f"- 訊息 **{total}** 則(上週 {total_prev},{delta(total, total_prev)})",
          f"- session **{sum(len(s) for s in this.sessions.values())}** 個、"
          f"活躍專案 **{len(this.prompts)}** 個、有工作的天數 **{len(all_days)}/7**", ""]

    o += ["## 各專案", "", "| 專案 | 訊息(vs 上週) | session | 活躍天 | 專案 commit | memory 筆記 |",
          "|---|---|---|---|---|---|"]
    names = sorted(set(this.prompts) | set(prev.prompts), key=lambda n: (-this.prompts[n], n))
    for n in names:
        commits = git_count(mirror_for(n), start, end, work_only=True)
        notes = git_count(MEMORY_COPIES / n, start, end, "--invert-grep", "--grep=^init:", "--", "memory/")
        o.append(f"| {n} | {this.prompts[n]}({delta(this.prompts[n], prev.prompts[n])}) | "
                 f"{len(this.sessions[n])} | {len(this.days[n])} | "
                 f"{'—' if commits is None else commits} | {'—' if notes is None else notes} |")
    o.append("")
    new = [n for n in this.prompts if not prev.prompts[n]]
    quiet = [n for n in prev.prompts if prev.prompts[n] >= QUIET_THRESHOLD and not this.prompts[n]]
    if new or quiet:
        o += ["**變化**:" + ";".join(
            ([f"這週開始動:{'、'.join(sorted(new))}"] if new else []) +
            ([f"上週活躍、這週停下來:{'、'.join(sorted(quiet))}"] if quiet else [])), ""]

    hours = collections.Counter(t.hour for t in this.times)
    days = collections.Counter(t.weekday() for t in this.times)
    top_h, top_d = max(hours.values(), default=0), max(days.values(), default=0)
    o += ["## 什麼時候在工作", "", "```"]
    o += [f"{h:02d}:00 {bar(hours[h], top_h)} {hours[h] or ''}" for h in range(24)]
    o += ["", *[f"{'一二三四五六日'[d]}    {bar(days[d], top_d)} {days[d] or ''}" for d in range(7)], "```", ""]

    late = sum(hours[h] for h in LATE_NIGHT)
    late_prev = sum(1 for t in prev.times if t.hour in LATE_NIGHT)
    stretch, stretch_at = longest_stretch(this.times)
    last_per_day: dict[datetime.date, datetime.datetime] = {}
    for t in this.times:
        # 凌晨 6 點前的訊息算前一天的「收工時間」
        day = (t - datetime.timedelta(hours=6)).date()
        last_per_day[day] = max(last_per_day.get(day, t), t)
    o += ["## 作息", "",
          f"- 深夜(00:00–05:59)訊息 **{late}** 則,佔 {late / total:.0%}" if total else "- 這週沒有訊息",
          f"  (上週 {late_prev} 則)" if total else "",
          f"- 最長連續工作 **{stretch:.1f} 小時**(中間沒有超過 {BREAK_MINUTES} 分鐘的空檔)"
          + (f",從 {stretch_at:%m-%d %H:%M} 開始" if stretch_at else ""),
          "- 每天收工時間(最後一則訊息):" + "、".join(
              f"{d:%m/%d} {t:%H:%M}" for d, t in sorted(last_per_day.items())), ""]

    o += ["## 交辦方式", "",
          f"- 開 subagent **{this.agents}** 次(上週 {prev.agents})、workflow {this.workflows} 次",
          f"- session 之間傳訊息 **{this.peer_msgs}** 次(上週 {prev.peer_msgs})"]
    if this.skills:
        o.append("- 用到的 skill:" + "、".join(f"{k}×{v}" for k, v in this.skills.most_common(8)))
    o.append("")
    return "\n".join(l for l in o if l is not None) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--out", default=str(hublib.HUB / "reflect"))
    ap.add_argument("--llm", action="store_true", help="附加模型寫的主題與 AI 協作觀察")
    args = ap.parse_args()
    start, end, label = week_bounds(args.week)
    weeks = scan(start, end)
    text = render(label, start, end, weeks)
    if args.llm:
        import reflect_llm
        text += reflect_llm.section(start, end)
    out = pathlib.Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{label}.md"
    path.write_text(text)
    print(path)


if __name__ == "__main__":
    main()
