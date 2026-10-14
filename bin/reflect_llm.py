"""weekly-reflect.py 的分析段落:把本週統計跟訊息抽樣一起交給模型,寫出有結論、
有證據、有建議的每週分析(結構化 JSON,HTML 報告直接用)。

每週只呼叫一次(訂閱額度,不是 API 計費)。送進去的訊息每則截短、先遮掉像憑證的
字串;個人生活類專案(EXCLUDED)的原始訊息不送——只送它們的統計數字。
"""
import collections
import datetime
import json
import os
import re
import subprocess
import zoneinfo

import hublib

TZ = zoneinfo.ZoneInfo("Asia/Taipei")
TRANSCRIPTS = hublib.TRANSCRIPT_ROOTS
EXCLUDED = {"project-23", "project-24"}
PER_PROJECT = 40
TOTAL = 240
CHARS = 240

# 送給模型前遮掉像憑證的字串:第一次試跑時,模型把對話裡貼過的 Finnhub API key
# 前幾碼引進了報告。已知前綴的 token,跟夠長、字母數字混雜的長字串一律換成 [已遮蔽]。
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{10,}|sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-.]+"
    r"|(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,})")


def redact(text: str) -> str:
    return SECRET.sub("[已遮蔽]", text)


def sample(start: datetime.datetime, end: datetime.datetime) -> list[tuple[datetime.datetime, str, str]]:
    by_proj: dict[str, list] = collections.defaultdict(list)
    for name, paths in hublib.project_paths().items():
        if name in EXCLUDED:
            continue
        for e in hublib.iter_entries(TRANSCRIPTS, paths, start.astimezone(datetime.timezone.utc),
                                     end.astimezone(datetime.timezone.utc)):
            if hublib.is_human_prompt(e.entry):
                text = redact(" ".join(e.entry["message"]["content"].split()))[:CHARS]
                by_proj[name].append((e.ts.astimezone(TZ), name, text))
    rows = []
    for items in by_proj.values():
        items.sort()
        if len(items) > PER_PROJECT:  # 平均取樣,保留整週的分佈
            step = len(items) / PER_PROJECT
            items = [items[int(i * step)] for i in range(PER_PROJECT)]
        rows += items
    rows.sort()
    if len(rows) > TOTAL:
        step = len(rows) / TOTAL
        rows = [rows[int(i * step)] for i in range(TOTAL)]
    return rows


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
                "evidence": {"type": "string", "description": "具體證據:數字或引用訊息(引號內 ≤ 20 字)"},
                "so_what": {"type": "string", "description": "這代表什麼、該怎麼看待,1 句"}}}},
        "projects": {"type": "array", "items": {**_item,
            "required": ["name", "focus", "assessment", "status", "next"],
            "properties": {
                "name": {"type": "string"},
                "focus": {"type": "string", "description": "這週在做什麼,具體到功能/決定"},
                "assessment": {"type": "string", "description": "進展評估:推進得如何、投入跟產出是否相稱"},
                "status": {"type": "string", "enum": ["推進", "穩定", "卡住", "發散", "收尾"]},
                "next": {"type": "string", "description": "下一步最該做的一件事"}}}},
        "rhythm": {**_item, "required": ["verdict", "analysis", "pattern"],
            "properties": {
                "verdict": {"type": "string", "description": "作息一句話結論"},
                "analysis": {"type": "string", "description": "2–3 句:時段、深夜、連續工作代表什麼,對產出/判斷品質可能的影響"},
                "pattern": {"type": "string", "description": "最明顯的一個節奏模式"}}},
        "fluency": {"type": "array", "minItems": 4, "maxItems": 4, "items": {**_item,
            "required": ["dimension", "level", "observation", "quote", "suggestion"],
            "properties": {
                "dimension": {"type": "string", "enum": ["交辦", "描述", "判斷", "把關"]},
                "level": {"type": "string", "enum": ["強項", "穩定", "可改進"]},
                "observation": {"type": "string", "description": "2 句觀察"},
                "quote": {"type": "string", "description": "一則代表性訊息原文,≤ 25 字"},
                "suggestion": {"type": "string", "description": "具體做法,1 句"}}}},
        "next_week": {"type": "array", "minItems": 2, "maxItems": 3, "items": {**_item,
            "required": ["action", "why"],
            "properties": {
                "action": {"type": "string", "description": "可以直接照做的一件事"},
                "why": {"type": "string", "description": "對應哪個觀察、預期效果"}}}},
    },
}

PROMPT = """你是 Knas 的每週工作回顧分析師。以下是他這一週在 Claude Code 裡的活動統計,
以及他實際送出的訊息抽樣(每則截短,格式:時間 [專案] 內容)。

請寫一份**分析**,不是流水帳:找出模式、解釋原因、指出影響、給出判斷。用繁體中文,直接、
具體、不客套、不誇獎。每個結論都要有證據(統計數字或引用訊息)。只根據這些資料,證據不夠
就直說,不要編造。projects 只列這週有活動的專案;project-23、project-24 只有統計,
沒有訊息內容,評估時只能依數字。

## 本週統計
{stats}

## 訊息抽樣
{rows}"""


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


def analyze(stats: dict, start: datetime.datetime, end: datetime.datetime) -> dict:
    rows = sample(start, end)
    meta = {"model": os.environ.get("INDEX_MODEL", "sonnet"), "sampled": len(rows),
            "excluded": sorted(EXCLUDED)}
    if not rows:
        return {"error": "這週沒有可分析的訊息", "meta": meta}
    text = "\n".join(f"{t:%m-%d %H:%M} [{p}] {m}" for t, p, m in rows)
    res = subprocess.run(["claude", "-p", PROMPT.format(stats=stats_brief(stats), rows=text),
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
