#!/usr/bin/env python3
"""排程狀態頁:取代 Claude Desktop「Scheduled」的檢視,所有排程(schedule.yaml)一頁看完。

跑在 mini。scheduler.py 每個 tick 結束、run-agent-job.sh 每次跑完都會重生,零 LLM。
輸出 .state/status/index.html,用 `tailscale serve` 只對 tailnet 開放(MBP、手機打得開,
外網打不開):https://hub-host.<tailnet>.ts.net/

內容:
  - 需要處理:報告第一行 🔔 的 agent job(7 天內)、失敗的 job、排程器本身沒在跑
  - 每個 job:什麼時候跑、上次結果、下次什麼時候
  - agent job(run-agent-job.sh)的最近幾份報告全文
"""
import datetime
import html
import json
import os
import pathlib
import re
import subprocess

import yaml

HUB = pathlib.Path.home() / "agent-hub"
SCHEDULE = HUB / "schedule.yaml"
STATE = HUB / ".state" / "scheduler-last-run.json"
SCHED_LOG = HUB / ".logs" / "scheduler.log"
AGENT_JOBS = HUB / ".state" / "agent-jobs"
OUT = HUB / ".state" / "status" / "index.html"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_ZH = "一二三四五六日"
LOG_LINE = re.compile(r"^\[(\S+ \S+)\] (?:running job '([^']+)'|job '([^']+)' (finished OK|FAILED \(exit (\d+)\)))")


def runs_on(job: dict, d: datetime.date) -> bool:
    """跟 scheduler.py 的 runs_today() 同一組篩選。"""
    if "days_of_week" in job and WEEKDAYS[d.weekday()] not in job["days_of_week"]:
        return False
    if "days_of_month" in job and d.day not in job["days_of_month"]:
        return False
    if "months" in job and d.month not in job["months"]:
        return False
    if "end_date" in job and d > datetime.date.fromisoformat(str(job["end_date"])):
        return False
    return True


def describe_when(job: dict) -> str:
    parts = []
    if "months" in job:
        parts.append(",".join(f"{m}月" for m in job["months"]))
    if "days_of_month" in job:
        parts.append(("" if parts else "每月 ") + ",".join(f"{d}號" for d in job["days_of_month"]))
    if "days_of_week" in job:
        parts.append("每週" + "".join(WEEKDAY_ZH[WEEKDAYS.index(w)] for w in job["days_of_week"]))
    when = " ".join(parts) or "每天"
    if "end_date" in job:
        when += f"(到 {job['end_date']})"
    return f"{when} {job['time']}"


def next_run(job: dict, state: dict, now: datetime.datetime) -> str:
    if not job.get("enabled", True):
        return "暫停中"
    hh, mm = map(int, job["time"].split(":"))
    for i in range(0, 400):
        d = now.date() + datetime.timedelta(days=i)
        if not runs_on(job, d):
            continue
        due = datetime.datetime.combine(d, datetime.time(hh, mm))
        if i == 0 and state.get(job["name"]) == d.isoformat():
            continue  # 今天已經跑完
        if i == 0 and now >= due:
            return "今天(等下一個 tick 或已達重試上限)"
        return fmt_dt(due)
    return "不會再跑"


def fmt_dt(t: datetime.datetime) -> str:
    today = datetime.date.today()
    day = {0: "今天", 1: "明天"}.get((t.date() - today).days, f"{t:%m/%d}(週{WEEKDAY_ZH[t.weekday()]})")
    return f"{day} {t:%H:%M}"


def scheduler_runs() -> tuple[dict, datetime.datetime | None]:
    """job 名稱 → 最後一次 {started, result};以及最後一次 tick 的時間。"""
    runs: dict[str, dict] = {}
    last_tick = None
    if not SCHED_LOG.exists():
        return runs, None
    lines = SCHED_LOG.read_text(errors="replace").splitlines()[-8000:]
    for line in lines:
        m = LOG_LINE.match(line)
        if line.startswith("["):
            try:
                last_tick = datetime.datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        if not m:
            continue
        ts, started, ended, result, code = m.groups()
        if started:
            runs[started] = {"started": ts, "result": "running"}
        else:
            r = runs.setdefault(ended, {"started": ts})
            r["result"] = "ok" if result == "finished OK" else f"failed (exit {code})"
            r["ended"] = ts
    return runs, last_tick


def scheduler_alive() -> bool:
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/local.agent-hub-scheduler"],
                       capture_output=True, text=True)
    return r.returncode == 0


def agent_status(name: str) -> dict | None:
    f = AGENT_JOBS / name / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def agent_reports(name: str, n: int = 5) -> list[pathlib.Path]:
    return sorted((AGENT_JOBS / name).glob("20*.md"), reverse=True)[:n]


def esc(s: str) -> str:
    return html.escape(s or "")


def main() -> None:
    now = datetime.datetime.now()
    jobs = (yaml.safe_load(SCHEDULE.read_text()) or {}).get("jobs", [])
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    runs, last_tick = scheduler_runs()
    alive = scheduler_alive()

    alerts, rows, report_blocks = [], [], []
    if not alive:
        alerts.append(("bad", "排程器(local.agent-hub-scheduler)沒有載入,所有排程都停了。在 mini 上跑 schedulectl start"))
    elif last_tick and now - last_tick > datetime.timedelta(minutes=40):
        alerts.append(("bad", f"排程器最後一次紀錄是 {last_tick:%m/%d %H:%M},超過 40 分鐘沒動靜"))

    for job in jobs:
        name = job["name"]
        is_agent = "run-agent-job.sh" in job["command"]
        run = runs.get(name, {})
        result, detail = run.get("result", ""), ""
        last = run.get("started", "")[5:16]
        if is_agent and (st := agent_status(name)):
            result = st.get("state", result)
            last = (st.get("started_at") or "")[5:16].replace("T", " ")
            detail = st.get("summary") or st.get("error") or ""
            if st.get("state") == "failed":
                alerts.append(("bad", f"{name} 失敗:{st.get('error') or st.get('stderr') or ''}"))
            fin = st.get("finished_at") or ""
            if st.get("notify") == "yes" and fin and \
                    now - datetime.datetime.fromisoformat(fin).replace(tzinfo=None) < datetime.timedelta(days=7):
                alerts.append(("notify", f"{name}:{st.get('summary', '')}"))
            if (AGENT_JOBS / name / "STOP").exists():
                detail = "已自行停止:" + (AGENT_JOBS / name / "STOP").read_text().strip()[:120]
        elif result.startswith("failed"):
            # 下一個 tick 成功就不算;只在最後一次是失敗時提醒
            alerts.append(("bad", f"{name} 最後一次執行失敗({result}),log:.logs/{name}-<日期>.log"))
        cls = {"ok": "ok", "running": "run"}.get(result, "bad" if result.startswith("failed") else "")
        label = {"ok": "成功", "running": "執行中", "failed": "失敗"}.get(result.split(" ")[0], result or "—")
        rows.append(
            f"<tr class='{'off' if not job.get('enabled', True) else ''}'>"
            f"<td><b>{esc(name)}</b>{' <span class=tag>Claude</span>' if is_agent else ''}"
            f"{'<div class=sub>' + esc(detail) + '</div>' if detail else ''}</td>"
            f"<td>{esc(describe_when(job))}</td>"
            f"<td><span class='st {cls}'>{esc(label)}</span> <span class=sub>{esc(last)}</span></td>"
            f"<td>{esc(next_run(job, state, now))}</td></tr>")
        if is_agent:
            reps = agent_reports(name)
            if reps:
                items = "".join(
                    f"<details{' open' if i == 0 else ''}><summary>{esc(r.stem)}</summary>"
                    f"<pre>{esc(r.read_text(errors='replace'))}</pre></details>"
                    for i, r in enumerate(reps))
                report_blocks.append(f"<section><h3>{esc(name)}</h3>{items}</section>")

    alert_html = "".join(f"<div class='alert {c}'>{'🔔' if c == 'notify' else '⚠️'} {esc(t)}</div>"
                         for c, t in alerts) or "<div class='alert good'>✅ 沒有需要處理的事</div>"
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>排程狀態</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff;--ok:#15803d;--bad:#b91c1c;--run:#1d4ed8;--warn:#fef3c7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524;--ok:#4ade80;--bad:#f87171;--run:#93c5fd;--warn:#422006}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"PingFang TC",sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px}}
.alert{{padding:10px 14px;border-radius:8px;margin:8px 0;background:var(--card);border:1px solid var(--line)}}
.alert.bad{{border-color:var(--bad)}} .alert.notify{{background:var(--warn)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden;margin-top:16px}}
td,th{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:13px;color:var(--mut);font-weight:500}} tr.off{{opacity:.45}}
.st.ok{{color:var(--ok)}} .st.bad{{color:var(--bad);font-weight:600}} .st.run{{color:var(--run)}}
.tag{{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 4px;color:var(--mut)}}
section{{margin-top:24px}} h3{{font-size:16px;margin:0 0 6px}}
details{{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0;padding:6px 12px}}
summary{{cursor:pointer;color:var(--mut)}} pre{{white-space:pre-wrap;word-break:break-word;font:14px/1.55 -apple-system,"PingFang TC",sans-serif}}
@media (max-width:640px){{td:nth-child(2),th:nth-child(2){{display:none}}}}
</style></head><body><main>
<h1>排程狀態</h1><div class=sub>mini · 產生於 {now:%Y-%m-%d %H:%M} · 每 5 分鐘自動重新整理 · 排程定義在 ~/agent-hub/schedule.yaml</div>
{alert_html}
<table><tr><th>Job</th><th>時間</th><th>上次</th><th>下次</th></tr>{''.join(rows)}</table>
<h2 style="font-size:18px;margin-top:28px">Claude job 報告</h2>
{''.join(report_blocks) or '<div class=sub>還沒有報告</div>'}
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(OUT)


if __name__ == "__main__":
    main()
