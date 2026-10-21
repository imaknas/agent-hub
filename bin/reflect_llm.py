"""weekly-reflect.py 的分析段落:把本週統計交給模型,寫出有結論、有證據、有建議的
每週分析(結構化 JSON,HTML 報告直接用)。每週只呼叫一次(訂閱額度,不是 API 計費)。

**對話原文永遠不送雲端模型**(2026-10-21 Knas 決定,跟 muninn 同一條規則:原文只給 mini
的本機模型讀)。送出去的是統計數字,加上 muninn 本機模型寫的每個 session 摘要
(`muninn digests --week … --json`:固定欄位、沒有原文;工作 domain 跟認不出 domain 的
session muninn 自己就扣掉,只給數量)。EXCLUDED 的專案連摘要也不送,只有統計。muninn 沒有
這個指令、失敗、或這週還沒有摘要時,只送統計。2026-W42 以前的報告是用訊息抽樣寫的,那些
json 照舊保留。欄位定義在 muninn repo;改了要兩邊一起改。
"""
import json
import os
import pathlib
import re
import subprocess

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

MUNINN_CLI = hubconf.p("assistant.cli")
# 專案登錄的 privacy.reflection 不是 include 的(現在是 project-23、project-24):連摘要也不送。
# 登錄讀不到 = None:那就完全不送摘要,只送統計
EXCLUDED = hubconf.reflection_private()

# trajectory.py 還在用(它自己決定送什麼);這裡已經不送任何原文。
# 送給模型前遮掉像憑證的字串:第一次試跑時,模型把對話裡貼過的 Finnhub API key
# 前幾碼引進了報告。已知前綴的 token,跟夠長、字母數字混雜的長字串一律換成 [已遮蔽]。
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{10,}|sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-.]+"
    r"|(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,})")


def redact(text: str) -> str:
    return SECRET.sub("[已遮蔽]", text)


_item = {"type": "object", "additionalProperties": False}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "summary", "insights", "projects", "rhythm", "fluency", "next_week"],
    "properties": {
        "headline": {"type": "string", "description": "一句話講這週最重要的事,≤ 30 字"},
        "summary": {"type": "string", "description": "3–4 句總評:這週的重心、做成了什麼、最大的問題"},
        "insights": {"type": "array", "minItems": 3, "maxItems": 5, "items": {**_item,
            "required": ["title", "analysis", "evidence", "so_what"],
            "properties": {
                "title": {"type": "string", "description": "洞察的結論句,≤ 22 字"},
                "analysis": {"type": "string", "description": "2–3 句分析:看到什麼模式、為什麼會這樣"},
                "evidence": {"type": "string", "description": "具體證據:統計數字或摘要內容"},
                "so_what": {"type": "string", "description": "這代表什麼、該怎麼看待,1 句"}}}},
        "projects": {"type": "array", "items": {**_item,
            "required": ["name", "focus", "assessment", "status", "next"],
            "properties": {
                "name": {"type": "string"},
                "focus": {"type": "string", "description": "這週在做什麼(有摘要就具體到功能/決定,沒有就講投入樣貌)"},
                "assessment": {"type": "string", "description": "進展評估:推進得如何、投入跟產出是否相稱"},
                "status": {"type": "string", "enum": ["推進", "穩定", "卡住", "發散", "收尾"]},
                "next": {"type": "string", "description": "下一步最該做的一件事"}}}},
        "rhythm": {**_item, "required": ["verdict", "analysis", "pattern"],
            "properties": {
                "verdict": {"type": "string", "description": "作息一句話結論"},
                "analysis": {"type": "string", "description": "2–3 句:時段、深夜、連續工作代表什麼,對產出/判斷品質可能的影響"},
                "pattern": {"type": "string", "description": "最明顯的一個節奏模式"}}},
        "fluency": {"type": "array", "minItems": 4, "maxItems": 4, "items": {**_item,
            "required": ["dimension", "level", "observation", "evidence", "suggestion"],
            "properties": {
                "dimension": {"type": "string", "enum": ["交辦", "描述", "判斷", "把關"]},
                "level": {"type": "string", "enum": ["強項", "穩定", "可改進"]},
                "observation": {"type": "string", "description": "2 句觀察"},
                "evidence": {"type": "string", "description": "根據的統計數字或摘要內容;撐不起判斷就直說"},
                "suggestion": {"type": "string", "description": "具體做法,1 句"}}}},
        "next_week": {"type": "array", "minItems": 2, "maxItems": 3, "items": {**_item,
            "required": ["action", "why"],
            "properties": {
                "action": {"type": "string", "description": "可以直接照做的一件事"},
                "why": {"type": "string", "description": "對應哪個觀察、預期效果"}}}},
    },
}

PROMPT = """你是 Knas 的每週工作回顧分析師。以下是他這一週在 Claude Code 裡的活動統計,
{digest_intro}

請寫一份**分析**,不是流水帳:找出模式、解釋原因、指出影響、給出判斷。用繁體中文,直接、
具體、不客套、不誇獎。每個結論都要有證據(統計數字或摘要裡的內容)。只根據這些資料,
不要猜沒寫到的內容;證據撐不起的判斷(尤其是協作方式的 4 個面向)就直說「只看得出 …」。
projects 只列這週有活動的專案。

## 本週統計
{stats}{digests}"""

DIGEST_INTRO = ("以及本機模型替每個 session 寫的摘要(不是原文;一個長 session 可能有好幾列,每列只涵蓋"
                "新的那幾輪)。{withheld}")
NO_DIGEST_INTRO = "只有數字,沒有任何對話內容或摘要。你看不到他做了什麼內容,不要猜具體在做哪個功能或討論什麼。"


def muninn_digests(week: str) -> dict | None:
    """muninn 本機模型寫的 session 摘要;拿不到就回 None(只送統計)。"""
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
    meta = {"model": os.environ.get("INDEX_MODEL", "sonnet"), "source": "stats", "excluded": sorted(EXCLUDED or [])}
    if not stats["totals"]["prompts"]:
        return {"error": "這週沒有活動可分析", "meta": meta}
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
    res = subprocess.run(["claude", "-p", prompt,
                          "--model", meta["model"], "--tools", "", "--output-format", "json",
                          "--json-schema", json.dumps(SCHEMA, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=1200)
    try:
        out = json.loads(res.stdout).get("structured_output")
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return {"error": f"模型呼叫失敗(exit {res.returncode}),統計部分不受影響", "meta": meta}
    out["meta"] = meta
    return out
