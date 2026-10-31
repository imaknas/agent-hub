"""The analysis section of weekly-reflect.py: hand the week's statistics to the model and get back a weekly analysis
with conclusions, evidence and recommendations (structured JSON, used directly by the HTML report). One call a week
(subscription quota, not API billing).

**Conversation text is never sent to a cloud model** (decided by the owner on 2026-10-21; the same rule as the
assistant's: only the hub machine's local model reads the original text). What is sent is the statistics, plus the
per-session summaries the assistant's local model writes (`<assistant.cli> digests --week … --json`: fixed fields, no
original text; the assistant itself leaves out sessions in the work domain and sessions whose domain it cannot tell,
giving only their count). EXCLUDED projects send not even summaries, only statistics. When the assistant lacks the
command, it fails, or there are no summaries for the week yet, only statistics are sent. Reports before 2026-W42 were
written from sampled messages; those json files are kept as they are. The fields are defined in the Muninn repo;
a change there must be made on both sides.
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import it too

import json
import pathlib
import subprocess

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)   # only what the report page shows (the error, the model); the prompt waits for step 5
MUNINN_CLI = hubconf.p("assistant.cli")
# Projects whose registry privacy.reflection is not include: not even summaries are sent.
# Registry unreadable = None: then no summaries at all, statistics only
EXCLUDED = hubconf.reflection_private()

# Mask credential-like strings before sending to the model: one masking point for the whole platform
# (completion.py; llm.py goes through the same one before sending to any provider).
from completion import SECRET, redact  # noqa: E402,F401


# The output schema is in contracts/llm/reflect-week.schema.json (judgment point reflect-week, applied by bin/llm.py)

PROMPT = """你是 {{owner}} 的每週工作回顧分析師。以下是他這一週在 Claude Code 裡的活動統計,
{digest_intro}

請寫一份**分析**,不是流水帳:找出模式、解釋原因、指出影響、給出判斷。用繁體中文,直接、
具體、不客套、不誇獎。每個結論都要有證據(統計數字或摘要裡的內容)。只根據這些資料,
不要猜沒寫到的內容;證據撐不起的判斷(尤其是協作方式的 4 個面向)就直說「只看得出 …」。
projects 只列這週有活動的專案。

## 本週統計
{stats}{digests}"""
PROMPT = hubconf.fill_names(PROMPT)  # {{owner}} replaced by the configured name (before .format())

DIGEST_INTRO = ("以及本機模型替每個 session 寫的摘要(不是原文;一個長 session 可能有好幾列,每列只涵蓋"
                "新的那幾輪)。{withheld}")
NO_DIGEST_INTRO = "只有數字,沒有任何對話內容或摘要。你看不到他做了什麼內容,不要猜具體在做哪個功能或討論什麼。"


def muninn_digests(week: str) -> dict | None:
    """Session summaries written by the assistant's local model; None when they cannot be had (statistics only)."""
    exe = MUNINN_CLI
    if EXCLUDED is None or not exe or not exe.exists():
        return None
    try:
        r = subprocess.run([str(exe), "digests", "--week", week, "--json"], cwd=exe.parents[2],
                           capture_output=True, text=True, timeout=120)
        d = json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(d, dict) or d.get("week") != week:
        return None
    d["sessions"] = [x for x in d.get("sessions", []) if x.get("project") not in EXCLUDED]
    return d


def digest_rows(d: dict) -> str:
    lines = []
    for x in sorted(d["sessions"], key=lambda x: x.get("last_turn") or ""):
        parts = [f"{x.get('date', '')} [{x.get('project') or x.get('domain') or '?'}] {x.get('summary', '')}"]
        if x.get("themes"):
            parts.append("主題:" + "、".join(x["themes"]))
        if x.get("mood"):
            parts.append(f"情緒:{x['mood']}")
        if x.get("decisions"):
            parts.append("決定:" + ";".join(f"{y.get('what')}({y.get('why')})" if y.get("why") else str(y.get("what"))
                                            for y in x["decisions"]))
        if x.get("commitments"):
            parts.append("承諾:" + ";".join(str(y.get("text")) for y in x["commitments"]))
        if x.get("friction"):
            parts.append("卡住:" + ";".join(x["friction"]))
        parts.append(f"{x.get('user_turns', '?')} 則訊息")
        lines.append("- " + " | ".join(parts))
    return redact("\n".join(lines))


def stats_brief(stats: dict) -> str:
    lines = [f"總訊息 {stats['totals']['prompts']}(上週 {stats['totals']['prompts_prev']})、"
             f"session {stats['totals']['sessions']}、活躍專案 {stats['totals']['projects']}、"
             f"工作天數 {stats['totals']['days']}/7"]
    for p in stats["projects"]:
        if p["prompts"] or p["prompts_prev"]:
            lines.append(f"- {p['name']}:訊息 {p['prompts']}(上週 {p['prompts_prev']})、session {p['sessions']}、"
                         f"活躍 {p['days']} 天、commit {p['commits']}、memory 筆記 {p['notes']}")
    r = stats["rhythm"]
    lines.append(f"深夜(00–06)訊息 {r['late']} 則({r['late_share']:.0%},上週 {r['late_prev']});"
                 f"最長連續工作 {r['longest_hours']:.1f} 小時;每天收工時間:"
                 + "、".join(f"{d['day']} {d['time']}" for d in r["last_per_day"]))
    d = stats["delegation"]
    lines.append(f"subagent {d['agents']} 次(上週 {d['agents_prev']})、跨 session 訊息 {d['peer']} 次"
                 f"(上週 {d['peer_prev']})、skill:{', '.join(f'{k}×{v}' for k, v in d['skills'].items()) or '無'}")
    if stats["changes"]["new"] or stats["changes"]["quiet"]:
        lines.append(f"新開始:{'、'.join(stats['changes']['new']) or '無'};停下來:{'、'.join(stats['changes']['quiet']) or '無'}")
    return "\n".join(lines)


def analyze(stats: dict) -> dict:
    import llm
    meta = {"model": llm.model_for("reflect-week") or _("the tool's default"), "source": "stats", "excluded": sorted(EXCLUDED or [])}
    if not stats["totals"]["prompts"]:
        return {"error": _("no activity to analyze this week"), "meta": meta}
    d = muninn_digests(stats["week"])
    if d and d["sessions"]:
        meta.update(source="stats+digests", digests=len(d["sessions"]), withheld=d.get("withheld", 0),
                    complete=d.get("complete"), undigested=d.get("undigested_sessions", 0))
        withheld = f"另有 {d.get('withheld', 0)} 個工作相關的 session 不提供摘要,只算在統計裡。" if d.get("withheld") else ""
        if not d.get("complete"):
            withheld += f"還有 {d.get('undigested_sessions', 0)} 個 session 沒來得及摘要,摘要不完整。"
        prompt = PROMPT.format(digest_intro=DIGEST_INTRO.format(withheld=withheld), stats=stats_brief(stats),
                               digests="\n\n## 每個 session 的摘要\n" + digest_rows(d))
    else:
        prompt = PROMPT.format(digest_intro=NO_DIGEST_INTRO, stats=stats_brief(stats), digests="")
    # judgment point reflect-week (all tools off, schema: contracts/llm-points.yaml)
    res = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parent / "llm.py"),
                          "run", "reflect-week"],
                         input=prompt, capture_output=True, text=True, timeout=1300)
    try:
        got = json.loads(res.stdout)
        out = got.get("structured_output")
        # the one actually used (hub.json llm may hand it to another provider)
        meta["model"] = got.get("llm_model") or meta["model"]
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return {"error": _("the model call failed (exit {code}); the statistics are not affected", code=res.returncode),
                "meta": meta}
    out["meta"] = meta
    return out
