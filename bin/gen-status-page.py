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
LOG_LINE = re.compile(r"^\[(\S+ \S+)\] (?:running job '([^']+)'|job '([^']+)' (finished OK|FAILED \(exit (-?\d+)\)))")


def prune_line() -> str:
    """每晚 worktree 清理(prune-worktrees --auto)的結果,一行;沒跑過就不顯示。"""
    import json, pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import hubconf
    f = hubconf.STATE / "worktree-prune.json"
    try:
        r = json.loads(f.read_text())
    except (OSError, ValueError):
        return ""
    parts = [f"刪依賴 {len(r.get('deps', []))} 個", f"移除 worktree {len(r.get('removed', []))} 個",
             f"釋放 {r.get('freed_mb', 0)} MB"]
    if r.get("skipped"):
        parts.append(f"跳過 {len(r['skipped'])} 個(有不能確定可重建的檔案)")
    return (f"<div class=sub style='margin-top:8px'>worktree 自動清理({esc(str(r.get('at', ''))[:16])}):"
            + "、".join(parts) + "</div>")


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
    if "every_minutes" in job:
        return f"{when} 每 {job['every_minutes']} 分鐘" + (f"({job['between']})" if "between" in job else "")
    return f"{when} {job['time']}"


def next_run(job: dict, state: dict, now: datetime.datetime) -> str:
    if not job.get("enabled", True):
        return "暫停中"
    if "every_minutes" in job:
        last = state.get(f"{job['name']}.last_ok")
        if not last:
            return "下一個 tick"
        nxt = datetime.datetime.fromisoformat(last) + datetime.timedelta(minutes=job["every_minutes"])
        return fmt_dt(nxt) + (f"(只在 {job['between']})" if "between" in job else "")
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
    import scheduler  # schedule.yaml + 專案登錄,跟排程器用同一份清單
    jobs, reg_info = scheduler.all_jobs()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    runs, last_tick = scheduler_runs()
    alive = scheduler_alive()

    alerts, rows, report_blocks = [], [], []
    if not alive:
        alerts.append(("bad", "排程器(local.agent-hub-scheduler)沒有載入,所有排程都停了。在 mini 上跑 schedulectl start"))
    else:
        # scheduler.py 只在有 job 要跑時才寫 log,但每個 tick 都會存狀態檔——用它的修改時間判斷
        # 排程器還有沒有在跑(用 log 判斷,半夜沒 job 的時段會誤報)
        tick = datetime.datetime.fromtimestamp(STATE.stat().st_mtime) if STATE.exists() else None
        if tick is None or now - tick > datetime.timedelta(minutes=40):
            alerts.append(("bad", f"排程器最後一次 tick 是 {tick:%m/%d %H:%M},超過 40 分鐘沒動靜" if tick
                           else "找不到排程器的狀態檔"))

    # 排程器的看門狗(scheduler-watchdog.py,獨立的 launchd job):這頁是排程器自己產生的,
    # 排程器停了這頁也不會更新——真正會叫的是看門狗,所以它沒在顧要在這裡講
    if subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/local.agent-hub-scheduler-watchdog"],
                      capture_output=True).returncode != 0:
        alerts.append(("bad", "排程器看門狗(local.agent-hub-scheduler-watchdog)沒有載入:排程器停了不會有人通知。"
                              "在 mini 上跑 bin/recover-service.sh local.agent-hub-scheduler-watchdog"))
    elif not all((HUB / ".secrets" / f).exists() for f in ("discord-token", "discord-alert-channel")):
        alerts.append(("bad", "排程器看門狗沒設定 Discord(.secrets/discord-token、.secrets/discord-alert-channel),"
                              "排程器停了發不出警報"))

    # 警報發不出去(bin/alert.py 記的;Discord token 失效、網路斷了…):不能默默算了
    try:
        import alert
        for key, f in sorted(alert.failures().items()):
            alerts.append(("bad", f"Discord 警報發不出去({key},{f.get('at', '')[:16]}):{f.get('text', '')}"
                                  "——確認 .secrets/discord-token 還能用;下次會再試"))
    except Exception as e:  # 狀態頁不能因為這個掛掉
        alerts.append(("bad", f"讀不到警報狀態:{e}"))

    # 第二階段:無人值守的 agent job 要在登錄宣告 allow:(dontAsk);還沒宣告的列出來(暫時是 auto)
    legacy_auto = [j["name"] for j in jobs if "run-agent-job.sh" in j["command"] and "AGENT_JOB_ALLOW=" not in j["command"]
                   and j.get("enabled", True)]
    if legacy_auto:
        alerts.append(("notify", f"這些 Claude job 還沒在登錄宣告 allow:(無人值守時用 auto,不是 dontAsk):{', '.join(legacy_auto)}"))

    for proj, errs in sorted(reg_info["errors"].items()):
        alerts.append(("bad", f"專案登錄 {proj} 不合格(沿用上一份合格的,沒有就不排它的 job):" + ";".join(errs[:4])
                              + (f"……共 {len(errs)} 項" if len(errs) > 4 else "") + "。`bin/registry.py check " + proj + "`"))

    for job in jobs:
        name = job["name"]
        is_agent = "run-agent-job.sh" in job["command"]
        aname = job.get("agent_name", name)  # 登錄的 agent job 狀態目錄用舊名稱或 <專案>-<job>
        run = runs.get(name, {})
        result, detail = run.get("result", ""), ""
        last = run.get("started", "")[5:16]
        if is_agent and (st := agent_status(aname)):
            result = st.get("state", result)
            last = (st.get("started_at") or "")[5:16].replace("T", " ")
            detail = st.get("summary") or st.get("error") or ""
            if st.get("state") == "failed":
                alerts.append(("bad", f"{name} 失敗:{st.get('error') or st.get('stderr') or ''}"))
            fin = st.get("finished_at") or ""
            if st.get("notify") == "yes" and fin and \
                    now - datetime.datetime.fromisoformat(fin).replace(tzinfo=None) < datetime.timedelta(days=7):
                alerts.append(("notify", f"{name}:{st.get('summary', '')}"))
            if (AGENT_JOBS / aname / "STOP").exists():
                detail = "已自行停止:" + (AGENT_JOBS / aname / "STOP").read_text().strip()[:120]
        elif result.startswith("failed"):
            # 下一個 tick 成功就不算;只在最後一次是失敗時提醒
            alerts.append(("bad", f"{name} 最後一次執行失敗({result}),log:.logs/{name.replace('/', '__')}-<日期>.log"))
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
            reps = agent_reports(aname)
            if reps:
                items = "".join(
                    f"<details{' open' if i == 0 else ''}><summary>{esc(r.stem)}</summary>"
                    f"<pre>{esc(r.read_text(errors='replace'))}</pre></details>"
                    for i, r in enumerate(reps))
                report_blocks.append(f"<section><h3>{esc(name)}</h3>{items}</section>")

    # 加密雲端備份(bin/backup.py):最後一次成功太久、還原測試太久都標紅
    bdir = HUB / ".state" / "backup"
    for fname, limit_h, what in (("last.json", 36, "備份"), ("last-verify.json", 40 * 24, "還原測試")):
        f = bdir / fname
        try:
            at = datetime.datetime.fromisoformat(json.loads(f.read_text())["at"]).replace(tzinfo=None)
            if now - at > datetime.timedelta(hours=limit_h):
                alerts.append(("bad", f"最後一次成功的{what}是 {at:%m/%d %H:%M},太久了(看 .logs/agent-hub__backup*)"))
        except (OSError, ValueError, KeyError):
            alerts.append(("bad", f"還沒有成功的{what}紀錄(.state/backup/{fname})"))

    # 專案信箱/事件匯流排(bin/hubbus):卡住的信、送不進 Muninn 的、壞掉被移開的 bus.db
    mail_html = ""
    r = subprocess.run(["/usr/bin/python3", str(HUB / "bin" / "hubbus"), "status", "--json"], capture_output=True, text=True)
    try:
        bus = json.loads(r.stdout)
    except ValueError:
        bus = None
        alerts.append(("bad", f"讀不到專案信箱狀態(hubbus status exit {r.returncode}):{r.stderr.strip()[-160:]}"))
    if bus:
        for st in bus["stuck"]:
            alerts.append(("bad", f"信 {st['id']} 寄給 {st['to']} 超過 12 小時沒有 session 收(會交給 headless agent mailbox-{st['to']})"))
        tm = bus["to_muninn"]
        if tm["n"] and now - datetime.datetime.fromisoformat(tm["oldest"]).replace(tzinfo=None) > datetime.timedelta(hours=1):
            alerts.append(("bad", f"{tm['n']} 則要送進 Muninn 的(給 knas 的信、轉交結案)卡住了,最舊 {tm['oldest'][5:16]};"
                                  "看 .state/bus/hubbus.log"))
        for f in bus["job_failed_24h"]:
            if f.get("job") == "hubbus-bridge":
                alerts.append(("bad", f"hubbus:{f.get('detail', '')}"))
        if bus["corrupt"]:
            alerts.append(("bad", f"bus.db 壞過,舊檔移到 {', '.join(bus['corrupt'])}(裡面還沒收的信不見了)"))
        mail_rows = "".join(f"<tr><td>{esc(p)}</td><td>{v['open']}</td><td>{v['undelivered']}</td>"
                            f"<td>{esc(v['oldest'][5:16].replace('T', ' '))}</td></tr>" for p, v in bus["projects"].items())
        mail_html = ("<h2 style=\"font-size:18px;margin-top:28px\">專案信箱</h2>"
                     + (f"<table><tr><th>收件</th><th>沒結案</th><th>還沒送進 session</th><th>最舊</th></tr>{mail_rows}</table>"
                        if mail_rows else "<div class=sub>沒有沒結案的信</div>"))

    # 平行 session 的 worktree / 合併閘門(bin/worktrees.py、bin/merge-branch)
    wt_html = ""
    try:
        import worktrees
        ov = worktrees.overview()
        wt_rows = []
        for proj, info in ov.items():
            for w in info["worktrees"]:
                state_txt = "已合併" + (",可以清(bin/prune-worktrees)" if w["clean"] else ",有沒 commit 的改動") if w["merged"] \
                    else f"進行中,改了 {len(w['files'])} 個檔"
                hot = any(f in info["overlaps"] for f in w["files"]) and not w["merged"]
                wt_rows.append(f"<tr><td>{esc(proj)}</td><td>{esc(w['branch'])}</td><td>{w['age_days']} 天前</td>"
                               f"<td{' class=\'st bad\'' if hot else ''}>{esc(state_txt)}{'(跟別的 branch 改同一個檔)' if hot else ''}</td></tr>")
            for f, bs in info["overlaps"].items():
                alerts.append(("bad", f"{proj}:{len(bs)} 個還沒合併的 branch 都改了 {f}({', '.join(bs)}),後合併的那個 rebase 會撞"))
        ungated = {p: i["ungated_main_7d"] for p, i in ov.items() if i["ungated_main_7d"]}
        wt_html = ("<h2 style=\"font-size:18px;margin-top:28px\">平行 session(worktree)</h2>"
                   + (f"<table><tr><th>專案</th><th>branch</th><th>最後 commit</th><th>狀態</th></tr>{''.join(wt_rows)}</table>"
                      if wt_rows else "<div class=sub>沒有進行中的 worktree</div>")
                   + (f"<div class=sub style='margin-top:8px'>最近 7 天直接 commit 到 main(沒經過 merge-branch):"
                      + "、".join(f"{esc(p)} {n}" for p, n in sorted(ungated.items())) + "</div>" if ungated else "")
                   + prune_line())
        import hubconf
        bus_db = hubconf.bus_db()
        if bus_db.exists():
            import sqlite3
            for (pl,) in sqlite3.connect(bus_db, timeout=10).execute(
                    "SELECT payload FROM events WHERE type='merge.failed' AND ts > ? ORDER BY seq DESC LIMIT 5",
                    ((now - datetime.timedelta(days=1)).isoformat(),)):
                f = json.loads(pl)
                alerts.append(("bad", f"merge-branch 失敗:{f.get('project')} {f.get('branch')}({f.get('stage')}):{(f.get('detail') or '')[:160]}"))
    except Exception as e:  # 一覽壞掉不影響狀態頁其他部分
        wt_html = f"<div class=sub>worktree 一覽失敗:{esc(repr(e))}</div>"

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
{mail_html}
{wt_html}
<h2 style="font-size:18px;margin-top:28px">Claude job 報告</h2>
{''.join(report_blocks) or '<div class=sub>還沒有報告</div>'}
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(OUT)


if __name__ == "__main__":
    main()
