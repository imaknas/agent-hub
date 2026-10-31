#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The librarian: every night it reads what is new in each project's memory/, updates STATE.md where needed, writes the
project-summary section of today's digest, and asks the model to mark the relations between projects (index.md).
Platform job agent-hub/librarian (runs after consolidate; the mechanical steps after it -- the assistant's task
registry, reconcile-questions, todo, memory-gaps, librarian-publish -- are jobs of their own, not in this script).

10-27: rewritten from librarian.sh in Python (same behaviour; prompt, commit, review-log, digest and checkpoint were
compared byte for byte in an isolated environment with a stand-in llm.py): paths and names all go through hubconf
instead of each being worked out in shell; the LLM's JSON output is read directly, no jq.

Robustness decisions (carried over):
1. The per-project STATE.md judgment uses haiku (high volume, simple task); cross-project relations use sonnet. Model,
   permissions, tools and output schema are declared in contracts/llm-points.yaml (judgment points librarian-state,
   librarian-todo-format, librarian-index); hub.json llm.points can override them.
2. Whether to commit is decided by the actual git state, not the state_updated the model reports; when the two
   disagree a warning is logged.
3. Every real change to STATE.md writes an entry (diff included) to review-log.md for a person to spot-check later;
   no blocking approval.
4. Every push goes through git-safe-push.sh (on divergence it pulls and retries; only a real content conflict fails
   and is left for a person).
5. The model may touch only ./STATE.md (enforced by permission rules); if it touches other files they are moved to
   quarantine and the checkpoint does not advance (second line of defence).

Usage: librarian.py (log written to .logs/librarian-<date>.log; also printed to the screen when run in a terminal)
"""
from __future__ import annotations

import datetime
import difflib
import glob
import json
import pathlib
import re
import shutil
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import edits  # noqa: E402
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
HUB = hubconf.ROOT
STATE_DIR, LOG_DIR = hubconf.STATE, hubconf.LOGS
REVIEW_LOG = hubconf.data_file("review-log.md")
SAFE_PUSH = HUB / "bin" / "git-safe-push.sh"
PY = str(HUB / "bin" / "py")
MAX_DIFF_CHARS = 60000
# Handoffs carrying a task key are not given to the model; the assistant module's task-registry job later applies
# them by key
KEYED_EFFECTS = ("done", "dropped", "already_done", "reopened")

TODAY = datetime.date.today().isoformat()
LOG = LOG_DIR / f"librarian-{TODAY}.log"
OWNER = hubconf.get("owner.tag") or "owner"            # how the prompt names the owner and the assistant
ASSISTANT = hubconf.get("assistant.name") or "個人助理"
# Shadow mode (docs/design/llm-providers.md step 4): the completion version (librarian-state-edits,
# librarian-index-edits) runs every night alongside today's agent version; only the comparison of its proposal with
# the actual result is recorded in .state/librarian-shadow/, no real file is written. Switch over once the comparison
# is good enough, then remove this.
SHADOW = True
SHADOW_DIR = STATE_DIR / "librarian-shadow"
# registrar's key marker (without the end-of-line anchor)
KEY_MARK = re.compile(rf"[(（]等 ?{re.escape(OWNER)}[:：]\s*[^()（）\n]+?\s*[)）]")
total_cost = 0.0
cost_added = False   # the shell version printed $0 when there was no cost at all, %.6f only when there was


def used_model(obj) -> str:
    """The model this call actually used: the llm_model llm.py reports (the model is computed only in llm.model_for; no
    guessing here)."""
    m = (obj or {}).get("llm_model")
    return m if isinstance(m, str) and m else _("tool default")


def log(msg: str) -> None:
    """Write to LOG only; print as well only when run by hand in a terminal. The scheduler sends stdout into the same
    log, so always printing would write every line twice."""
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    if sys.stdout.isatty():
        print(line)


def run(cmd: list, cwd=None, stdin: str | None = None, to_log: bool = False) -> subprocess.CompletedProcess:
    """Run an external command; with to_log=True both stdout and stderr are appended to LOG (like the shell version's
    >>"$LOG" 2>&1)."""
    if to_log:
        with LOG.open("a") as f:
            return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, stdout=f, stderr=f)
    return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, capture_output=True)


def out(cmd: list, cwd=None) -> str:
    """The command's stdout without the trailing newline (like shell's $(…))."""
    return run(cmd, cwd=cwd).stdout.rstrip("\n")


def git(repo, *args) -> str:
    return out(["git", "-C", str(repo), *args])


def llm(point: str, prompt: str, cwd) -> tuple[int, dict | None, str]:
    """Run a judgment point (bin/llm.py run <point>, prompt on stdin); stderr is appended to LOG. Returns
    (exit, parsed JSON, raw text)."""
    with LOG.open("a") as f:
        r = subprocess.run([PY, str(HUB / "bin" / "llm.py"), "run", point], cwd=cwd, input=prompt, text=True,
                           stdout=subprocess.PIPE, stderr=f)
    text = r.stdout.rstrip("\n")
    try:
        obj = json.loads(text) if text else None
    except ValueError:
        obj = None
    return r.returncode, obj if isinstance(obj, dict) else None, text


def add_cost(obj: dict | None) -> str:
    """Add up the cost; returns the string for the log (empty when there is no cost)."""
    global total_cost, cost_added
    c = (obj or {}).get("total_cost_usd")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and c >= 0:
        total_cost += c
        cost_added = True
        return str(c)
    return ""


def jstr(v) -> str:
    """jq's \\(…) string interpolation: null -> "null", strings as they are, anything else as JSON."""
    if v is None:
        return "null"
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def present(v) -> bool:
    """jq truthiness: only null and false are false (an empty string or 0 counts as present)."""
    return v is not None and v is not False


def jq_bool(v) -> str:
    """How jq -r prints a value: true/false/null, strings as they are."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return jstr(v)


def head_spaced(text: str, n: int) -> str:
    """`echo "$x" | head -n | tr '\\n' ' '`: the first n lines, each followed by a space."""
    return "".join(line + " " for line in text.split("\n")[:n])


# ---------- handoffs ----------

def load_handoffs() -> list[dict]:
    """The assistant's handoffs (facts about a project that the owner told the personal assistant): hubbus-bridge puts
    them into each project's mailbox every 15 minutes; every night the ones not yet closed are used directly as extra
    input to this STATE.md call (not written as running notes; what changed and why stays in review-log.md). Only when
    this project's result is trustworthy (the checkpoint advances) does it hubbus done them, and the bridge reports the
    closure to the assistant; otherwise they wait for the next night."""
    r = run([PY, str(HUB / "bin" / "hubbus"), "open-handoffs"])
    if r.stderr:
        with LOG.open("a") as f:
            f.write(r.stderr)
    try:
        items = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        log(_("hubbus open-handoffs could not be read; handoffs are not processed this time"))
        return []
    log(_("Assistant handoffs: {n} to process", n=len(items)))
    for p in sorted({str(h.get("project")) for h in items if isinstance(h, dict)}):
        if not (hubconf.p("memory_copies", "~/.memory-copies") / p / ".git").is_dir():
            log(_("Assistant handoffs: project {project} has no memory copy, not processed this time (left in the mailbox)",
                  project=p))
    return [h for h in items if isinstance(h, dict)]


def load_records(proj: str) -> list[dict]:
    """v2 (Principal Handoff 0.3): records sent by the principal side and not yet done become the same kind of input as
    handoffs -- note, Decisions' settled (an answer or "no answer needed"), same_as. Acts' settled/reopened are applied
    mechanically by registrar; not touched here."""
    r = run([PY, str(HUB / "bin" / "hubbus"), "records", "--project", proj])
    try:
        events = json.loads(r.stdout) if r.returncode == 0 else []
    except ValueError:
        events = []
    asked = {}
    for e in events:   # oldest to newest: a key's last terms are what is being asked now
        rec = e.get("record") or {}
        if e.get("direction") == "out" and rec.get("type") in ("raised", "amended"):
            asked[rec.get("key")] = (rec.get("terms") or {}).get("text")
    out = []
    for e in events:
        rec = e.get("record") or {}
        if e.get("direction") != "in" or e.get("done"):
            continue
        kind, t = str(rec.get("ext", "")).split("/")[0], rec.get("type")
        if t == "note":
            effect, fact = "note", rec.get("note")
        elif t == "settled" and kind == "decisions":
            decided = rec.get("outcome") == "decided"
            effect, fact = ("answers", (rec.get("answer") or {}).get("text")) if decided else ("dismissed", rec.get("note"))
        elif t == "related" and (rec.get("relation") or {}).get("kind") == "same_as":
            tg = (rec.get("relation") or {}).get("target") or {}
            effect, fact = "same_as", f"跟 {tg.get('project') or tg.get('uri')} 的 {tg.get('key') or ''} 是同一件"
        else:
            continue
        about = rec.get("about") or {}
        out.append({"id": e.get("id"), "handoff_id": rec.get("id"), "project": proj, "effect": effect, "fact": fact,
                    "quote": None, "item": about.get("quote") or asked.get(about.get("key") or rec.get("key")),
                    "why": authority_text(rec) or "(沒有說明)", "ts": rec.get("ts"), "v2": True})
    return out


def authority_text(rec: dict) -> str | None:
    """Who decided a closure and with what kind of authority (spec core §5), in plain words for the model."""
    a = rec.get("authority") or {}
    basis = (rec.get("evidence") or {}).get("basis")
    return {"principal": f"{OWNER} 本人決定的", "confirmed": f"{OWNER} 確認了{ASSISTANT}的提議",
            "attested": f"{ASSISTANT}依證據認定({basis})" if basis else f"{ASSISTANT}依證據認定",
            "delegated": f"{ASSISTANT}依 {OWNER} 給的授權代為決定"}.get(a.get("kind"), rec.get("context"))


def handoff_text(h: dict) -> str:
    s = f"- [{jstr(h.get('effect'))}] 事實:{jstr(h.get('fact'))}"
    if present(h.get("quote")):
        s += f"\n  {OWNER} 原話:{jstr(h.get('quote'))}"
    item = h.get("item") if present(h.get("item")) else "(沒有指定)"
    ts = h.get("ts")
    return s + f"\n  對應待辦:{jstr(item)}\n  助理的判斷理由:{jstr(h.get('why'))}\n  ({jstr(ts[0:16] if isinstance(ts, str) else ts)})"


# ---------- prompt ----------

def state_prompt(proj: str, memory_section: str, handoff_note: str, dangling_note: str) -> str:
    return f"""你是跨專案整合 agent(圖書館員),現在在專案 {proj} 的目錄裡工作。
{memory_section}
{handoff_note}
{dangling_note}

背景請讀 ./AGENT.md 跟 ./STATE.md。**STATE.md 在專案根目錄(./STATE.md),不在 memory/ 底下**;
memory/ 底下沒有、也不能有 STATE.md。

memory/ 的結構:
- memory/YYYY-MM-DD.md、memory/YYYY-MM-DD-<主題>.md:原始流水紀錄,只會往後加。
- memory/reference/<主題>.md(有 README.md 當索引):整理過的主題文件,會被改寫,
  內容是目前最準的版本;跟舊的流水紀錄衝突時以 reference/ 為準。

規則:
- 只有在有『實質變化』時才編輯 STATE.md(精煉現況,不是流水帳);沒有實質變化就完全不要動 STATE.md。
  STATE.md 還是 onboard 時的佔位內容、而 memory/ 已經有實質內容的話,這本身就算實質變化。
- STATE.md 裡提到已被搬走的檔案、或已經被新內容回答/推翻的待決事項,要一併修正。
- STATE.md 一定要有一個 `## 待辦` 段落,格式固定,一行一項:
  `- [ ] 〔{OWNER}〕…`(只有 {OWNER} 本人能做:帳號、密碼、付款、要他拍板的決定)
  `- [ ] 〔agent〕…`(agent 可以直接做的)
  `- [ ] 〔待定〕…`(還要先做決定才能動的)
  〔{OWNER}〕行尾的「(等 {OWNER}:task-…)」是 {ASSISTANT} 任務登錄的 key,**原樣保留,不要改那一行的文字**
  ({OWNER} 在 {ASSISTANT} 結案時會照 key 自動移掉;文字一改 key 就對不上)。
  沒有待辦就寫 `- (無)`。只列還沒完成、具體可行動的事;完成、被推翻或過時的直接移除,
  不要留著打勾。待辦只放在這個段落,不要在 STATE.md 別的地方另開「下一步」「未完成」之類的清單。
- 除了 ./STATE.md,不要建立、修改或刪除任何檔案。"""


def handoff_note(items: list[dict]) -> str:
    return f"""
{OWNER} 透過他的個人助理({ASSISTANT})告訴我們這些跟這個專案有關的事(比舊的 memory 紀錄新,也比較可信):
{chr(10).join(handoff_text(h) for h in items)}

處理方式(這些轉交本身就算實質變化):
- [resolves]:{OWNER} 已經處理或回答了對應待辦。從 `## 待辦` 移除那一條(用意思比對,文字不一定一樣),
  並把事實寫進 STATE.md 合適的段落。
- [changes]:對應待辦還在,但前提或日期變了。依事實改寫那一條,必要時一併更新相關段落。
- [relevant]:只供參考,影響到現況才更新 STATE.md。
- [answers]:{OWNER} 回答了這個專案問過他的問題(對應待辦 = 當初的問題,事實 = 回答)。把決定寫進相關段落;
  有在等這個回答的待辦就依回答移除或改寫。
- 對應待辦在 STATE.md 找不到時不要硬套到別條,只把事實記進相關段落(有關的話)。
- 「事實」是助理轉述的,可能比 {OWNER} 原話講得更肯定。有原話以原話為準;寫進 STATE.md 時保留
  不確定的語氣(預計、預期、可能、打算),不要寫成已確定。"""+(V2_NOTE if any(h.get("v2") for h in items) else "")


V2_NOTE = f"""
- [note]:{ASSISTANT}帶來的資訊(前提或日期變了、跟哪件待辦有關)。對應待辦是專案原話,用意思比對;影響到那件待辦
  就依事實改寫,否則記進相關段落。note 不代表做完了,不要因為 note 就刪掉待辦。
- [dismissed]:{OWNER} 決定這個問題不用回答。移除在等這個回答的待辦;需要的話記一筆為什麼。
- [same_as]:{ASSISTANT}把這一件跟別的專案的同一件合併了,只供參考,不用改待辦。
- 「助理的判斷理由」寫的是誰決定的、根據什麼:{OWNER} 本人決定的就照著寫;{ASSISTANT}依證據認定的,寫進 STATE.md 時
  標明是推斷,不要寫成 {OWNER} 說的。"""


def todo_fix_prompt(problems: str) -> str:
    return f"""只修 ./STATE.md 的 `## 待辦` 段落格式,其他地方、待辦的內容都不要改,也不要動任何其他檔案。

正確格式(一行一項;沒有待辦就寫 `- (無)`;STATE.md 一定要有這個段落):
  `- [ ] 〔{OWNER}〕…`(只有 {OWNER} 本人能做:帳號、密碼、付款、要他拍板的決定、他自己要買/做的事)
  `- [ ] 〔agent〕…`(agent 可以直接做的)
  `- [ ] 〔待定〕…`(還要先做決定才能動的)

如果 STATE.md 別的地方有待辦性質的清單(例如「待決事項」「需要 {OWNER} 拍板」「可由 agent 執行」
「下一步」「未完成」),把那些項目**搬進** `## 待辦`(標上負責人)並刪掉原本那段;
只有整份 STATE.md 真的沒有任何待辦時才寫 `- (無)`。排程、已完成的事實不算待辦,留在原處。

目前的問題:
{problems}"""


def index_prompt() -> str:
    return f"""更新 index.md:今天({TODAY})各專案的摘要在 digest/{TODAY}.md,讀進來後,
標出專案之間看得出來的關聯——沒有明顯關聯就不要硬掰,保持精簡。
直接編輯 index.md,不要重寫整份格式風格。如果沒有值得改的地方,可以不動這個檔案。
<!-- roster:begin --> 到 <!-- roster:end --> 之間的專案名冊是 gen-index-roster.sh 機械產生的,
不要編輯那個區塊(改了也會被覆蓋);你的註記寫在區塊外。"""


# ---------- shadow mode: the completion version ----------

def _state_rules(proj: str) -> str:
    """The verbatim 「規則:」 (rules) section of the agent version's prompt (the same rules, not a second copy), without
    the last rule, which only means something to an agent with tools."""
    rules = state_prompt(proj, "", "", "").split("\n規則:\n", 1)[1]
    return rules.rsplit("\n- 除了 ./STATE.md", 1)[0]


def _reference(d: pathlib.Path, limit: int = 12000) -> str:
    ref = d / "memory" / "reference"
    if not ref.is_dir():
        return "(沒有 memory/reference/)"
    names = sorted(p.name for p in ref.glob("*.md"))
    readme = (ref / "README.md").read_text(errors="replace")[:limit] if (ref / "README.md").exists() else "(沒有 README.md)"
    return "檔案:" + "、".join(names) + "\n\nREADME.md:\n" + readme


def state_edits_prompt(proj: str, d: pathlib.Path, before: str, memory_section: str, h_note: str, d_note: str) -> str:
    agent_md = (d / "AGENT.md").read_text(errors="replace") if (d / "AGENT.md").exists() else "(沒有 AGENT.md)"
    return f"""你是跨專案整合 agent(圖書館員),負責專案 {proj} 的 STATE.md。你沒有任何工具:需要的東西都在下面,
回答只能是一個 JSON(格式由系統指定)。你不直接改檔案,而是回「編輯清單」,平台照順序套用到 STATE.md。
{memory_section}
{h_note}
{d_note}

## AGENT.md(這個專案的 agent 規則,背景)
{agent_md}

## memory/reference/(整理過的主題文件,跟舊的流水紀錄衝突時以這裡為準)
{_reference(d)}

## 現在的 STATE.md(完整內容)
<<<STATE
{before}
STATE>>>

規則:
{_state_rules(proj)}

編輯清單的格式:
- edits 是照順序套用的 {{"old", "new"}}:old 一定要是**現在 STATE.md 裡剛好出現一次**的一段原文(逐字、含換行;
  太短會出現好幾次,就多帶幾個字),new 是換成的文字(空字串 = 刪掉那一段)。不要回整份 STATE.md。
- 不用改就 state_updated: false、edits: []。
- 〔{OWNER}〕行尾的「(等 {OWNER}:…)」標記不能改;那一項做完了就把整行刪掉。"""


def _shadow_write(rec: dict, name: str | None = None, text: str | None = None) -> None:
    day = SHADOW_DIR / TODAY
    day.mkdir(parents=True, exist_ok=True)
    if name and text is not None:
        (day / name).write_text(text)
    with (SHADOW_DIR / f"{TODAY}.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _shadow_call(point: str, prompt: str) -> tuple[dict | None, dict]:
    rc, obj, _text = llm(point, prompt, None)
    so = (obj or {}).get("structured_output") if rc == 0 else None
    meta = {"cost_usd": (obj or {}).get("total_cost_usd"), "model": (obj or {}).get("model"),
            "provider": (obj or {}).get("provider")}
    if not isinstance(so, dict):
        meta["error"] = (obj or {}).get("llm_error") or f"exit {rc}"
        return None, meta
    return so, meta


def shadow_state(proj: str, d: pathlib.Path, before: str, after: str, agent_changed: bool,
                 memory_section: str, h_note: str, d_note: str) -> None:
    """Compare the completion version's proposal for the same input with the STATE.md the agent version actually wrote.
    Records only; writes no real file."""
    if not SHADOW:
        return
    try:
        so, meta = _shadow_call("librarian-state-edits", state_edits_prompt(proj, d, before, memory_section, h_note, d_note))
        rec = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": "librarian-state-edits",
               "project": proj, "agent_changed": agent_changed, **meta}
        if so is None:
            rec["ok"] = False
            _shadow_write(rec)
            log(_("{project}: shadow (completion version) has no result: {error}", project=proj, error=meta.get("error")))
            return
        proposed = None
        try:
            proposed = edits.apply(before, so.get("edits") or [])
        except edits.EditError as e:
            rec["edit_error"] = str(e)
        shadow_changed = bool(so.get("state_updated")) and proposed is not None and proposed != before
        rec.update(ok=proposed is not None, shadow_changed=shadow_changed, same_decision=shadow_changed == agent_changed,
                   edits=len(so.get("edits") or []), reason=str(so.get("reason") or "")[:400])
        if proposed is not None:
            rec["marker_problems"] = edits.check_markers(before, proposed, KEY_MARK)
            rec["identical_to_agent"] = proposed == after
            rec["similarity"] = round(difflib.SequenceMatcher(None, proposed, after).ratio(), 3)
            tmp = SHADOW_DIR / TODAY / f"{proj}.STATE.md"
            _shadow_write(rec, tmp.name, proposed)
            rec_todo = run([PY, str(HUB / "bin" / "check-state-todo.py"), str(tmp)])
            if rec_todo.returncode != 0:
                log(_("{project}: the shadow's proposed to-do section is malformed: {problems}", project=proj,
                      problems=head_spaced(rec_todo.stdout.rstrip(chr(10)), 2)))
        else:
            _shadow_write(rec)
        log(shadow_line(proj, shadow_changed, agent_changed, rec)
            + (_(", marker problems: {n}", n=len(rec["marker_problems"])) if rec.get("marker_problems") else ""))
    except Exception as e:   # a broken shadow does not affect the real run
        log(_("{project}: shadow mode failed: {error_type}: {error}", project=proj, error_type=type(e).__name__, error=e))


def shadow_line(what: str, shadow_changed: bool, agent_changed: bool, rec: dict) -> str:
    """the log line comparing the shadow's decision with the agent's (a project, or index.md)"""
    return (_("{project}: shadow (completion version): change={shadow} (agent version={agent})", project=what,
              shadow=shadow_changed, agent=agent_changed)
            + (_(", similarity {similarity}", similarity=rec["similarity"]) if "similarity" in rec else "")
            + (_(", edit list invalid: {error}", error=rec["edit_error"]) if "edit_error" in rec else ""))


def index_edits_prompt(before: str, digest_text: str) -> str:
    return f"""更新 index.md:下面是今天({TODAY})各專案的摘要(digest)跟現在的 index.md。標出專案之間看得出來的關聯——
沒有明顯關聯就不要硬掰,保持精簡,不要重寫整份格式風格;沒有值得改的地方就 index_updated: false、edits: []。
你沒有任何工具,回答只能是一個 JSON:edits 是照順序套用的 {{"old", "new"}},old 一定要是現在 index.md 裡剛好出現一次的原文。
<!-- roster:begin --> 到 <!-- roster:end --> 之間的專案名冊是機械產生的,不要編輯那個區塊;你的註記寫在區塊外。

## 今天的 digest
{digest_text}

## 現在的 index.md
<<<INDEX
{before}
INDEX>>>"""


ROSTER = re.compile(r"<!-- roster:begin -->.*?<!-- roster:end -->", re.S)


def shadow_index(before: str, after: str, digest_text: str) -> None:
    if not SHADOW:
        return
    try:
        so, meta = _shadow_call("librarian-index-edits", index_edits_prompt(before, digest_text))
        rec = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": "librarian-index-edits",
               "agent_changed": before != after, **meta}
        if so is None:
            rec["ok"] = False
            _shadow_write(rec)
            log(_("{project}: shadow (completion version) has no result: {error}", project="index.md",
                  error=meta.get("error")))
            return
        proposed = None
        try:
            proposed = edits.apply(before, so.get("edits") or [])
        except edits.EditError as e:
            rec["edit_error"] = str(e)
        if proposed is not None:
            rec["roster_untouched"] = ROSTER.findall(before) == ROSTER.findall(proposed)
            rec["similarity"] = round(difflib.SequenceMatcher(None, proposed, after).ratio(), 3)
        shadow_changed = proposed is not None and proposed != before
        rec.update(ok=proposed is not None, shadow_changed=shadow_changed, same_decision=shadow_changed == (before != after),
                   relations=str(so.get("relations_found") or "")[:300])
        _shadow_write(rec, "index.md" if proposed is not None else None, proposed)
        log(shadow_line("index.md", shadow_changed, before != after, rec))
    except Exception as e:
        log(_("{project}: shadow mode failed: {error_type}: {error}", project="index.md", error_type=type(e).__name__,
              error=e))


# ---------- eval cases (docs/design/i18n.md E2) ----------
# Each night's real inputs to the librarian's judgment points, saved so prompt changes can be compared on the same
# inputs: the raw builder arguments (to re-render with new code), the prompts as rendered tonight (the "old" arm), a copy
# of what the model could read, and tonight's outcome. Platform eval dir only (0700, not in git, not backed up).
# Recording must never change or break the run.
CASE_KEEP_DAYS = 60


def _case_dir(point: str, name: str) -> pathlib.Path | None:
    base = hubconf.evals_dir()
    if base is None:
        return None
    root = base / "cases" / point
    d = root / TODAY / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    cutoff = datetime.date.today() - datetime.timedelta(days=CASE_KEEP_DAYS)
    for old in root.iterdir():
        if old.is_dir() and old.name < cutoff.isoformat():
            shutil.rmtree(old, ignore_errors=True)
    return d


def _case_meta() -> dict:
    return {"v": 1, "date": TODAY, "owner": OWNER, "assistant": ASSISTANT,
            "code": out(["git", "-C", str(HUB), "rev-parse", "HEAD"]).strip()}


def record_state_case(proj: str, d: pathlib.Path, sha: str, before: str, raw: dict, rendered: dict,
                      name: str | None = None) -> pathlib.Path | None:
    try:
        c = _case_dir("librarian-state", name or proj)
        if c is None:
            return None
        snap = c / "snapshot"
        snap.mkdir()
        tar = subprocess.run(["git", "-C", str(d), "archive", "--format=tar", sha], capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(snap)], input=tar.stdout, check=True)
        (c / "args.json").write_text(json.dumps({**_case_meta(), "project": proj, "sha": sha, **raw, **rendered},
                                                ensure_ascii=False, indent=1))
        (c / "prompt.librarian-state.txt").write_text(
            state_prompt(proj, rendered["memory_section"], rendered["h_note"], rendered["d_note"]))
        (c / "prompt.librarian-state-edits.txt").write_text(state_edits_prompt(
            proj, snap, before, rendered["memory_section"], rendered["h_note"], rendered["d_note"]))
        return c
    except Exception as e:
        log(f"{proj}: eval case not recorded: {type(e).__name__}: {e}")
        return None


def record_index_case(before: str, digest_text: str) -> pathlib.Path | None:
    try:
        c = _case_dir("librarian-index", "index")
        if c is None:
            return None
        (c / "args.json").write_text(json.dumps({**_case_meta(), "index_before": before, "digest": digest_text},
                                                ensure_ascii=False, indent=1))
        (c / "prompt.librarian-index.txt").write_text(index_prompt())
        (c / "prompt.librarian-index-edits.txt").write_text(index_edits_prompt(before, digest_text))
        return c
    except Exception as e:
        log(f"index.md: eval case not recorded: {type(e).__name__}: {e}")
        return None


def record_outcome(case: pathlib.Path | None, outcome: dict, files: dict[str, str]) -> None:
    if case is None:
        return
    try:
        (case / "outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=1))
        for name, text in files.items():
            (case / f"after.{name}").write_text(text)
    except Exception as e:
        log(f"eval outcome not recorded ({case.name}): {type(e).__name__}: {e}")


# ---------- one project ----------

def repair_state_todo(proj: str, d: pathlib.Path) -> None:
    """The to-do section is malformed (haiku does not always follow the format, and then gen-todo cannot collect it):
    ask the model to fix only the format, content unchanged, and check again afterwards; if it is still wrong, log it
    and gen-todo lists it at the end of todo.md."""
    check = [PY, str(HUB / "bin" / "check-state-todo.py"), str(d / "STATE.md")]
    r = run(check)
    if r.returncode == 0:
        return
    problems = r.stdout.rstrip("\n")
    log(_("{project}: the STATE.md to-do section is malformed, asking the model to fix only the format: {problems}",
          project=proj, problems=head_spaced(problems, 3)))
    _rc, obj, _text = llm("librarian-todo-format", todo_fix_prompt(problems), d)
    add_cost(obj)
    r = run(check)
    if r.returncode == 0:
        log(_("{project}: to-do section format fixed", project=proj))
    else:
        log(_("{project}: the to-do section still has problems after the fix (listed at the end of todo.md): {problems}",
              project=proj, problems=head_spaced(r.stdout.rstrip(chr(10)), 2)))


def quarantine_stray(proj: str, d: pathlib.Path) -> bool:
    """The model may touch only ./STATE.md: extra files (on 2026-09-26 five projects wrote memory/STATE.md) are moved
    to quarantine and logged, so the next call does not read them as real content. A tracked file that was changed or
    deleted: keep a copy of the changed one as evidence, then restore it."""
    stray = []
    for line in git(d, "status", "--porcelain", "--untracked-files=all").split("\n"):
        parts = line.split()
        if len(parts) > 1 and parts[1] != "STATE.md":
            stray.append(parts[1])
    if not stray:
        return False
    qdir = STATE_DIR / "stray" / f"{proj}-{TODAY}"
    qdir.mkdir(parents=True, exist_ok=True)
    for f in stray:
        if run(["git", "-C", str(d), "ls-files", "--error-unmatch", "--", f]).returncode == 0:
            if (d / f).exists():
                shutil.copy(d / f, qdir / f.replace("/", "_"))
            run(["git", "-C", str(d), "checkout", "-q", "--", f])
        else:
            shutil.move(str(d / f), str(qdir / f.replace("/", "_")))
    log(_("{project}: warning: the model touched files other than STATE.md, moved to {dir}: {files}", project=proj,
          dir=qdir, files=" ".join(stray)))
    return True


def dangling_refs(d: pathlib.Path) -> str:
    """Whether the memory/ paths STATE.md mentions (may contain *) still exist; returns the shell version's format
    (each preceded by a space)."""
    st = d / "STATE.md"
    refs = sorted({r for r in re.findall(r"memory/[A-Za-z0-9._*/-]+\.md", st.read_text(errors="replace"))
                   if "YYYY" not in r}) if st.exists() else []
    return "".join(f" {r}" for r in refs if not glob.glob(r, root_dir=d))


def memory_input(d: pathlib.Path, rng: str, changed: str) -> tuple[str, str, bool]:
    """The prompt's memory section for commits `rng` (changed = the memory files they touch): (section, diff, truncated)."""
    if not changed:
        return memory_text("", "", False), "", False
    # The script supplies the diff: the model has no Bash, cannot run git log itself and cannot read files already
    # git rm'd. Too large: truncate it, and the model fills in with Read
    diff = git(d, "log", "-p", "--reverse", "--format==== commit %h %ad %s", "--date=short", rng, "--", "memory/")
    truncated = len(diff) > MAX_DIFF_CHARS
    return memory_text(changed, diff[:MAX_DIFF_CHARS], truncated), diff[:MAX_DIFF_CHARS], truncated


def memory_text(changed: str, diff: str, truncated: bool) -> str:
    if not changed:
        return f"這次 memory/ 沒有新變動,只有下面 {OWNER} 的轉交要處理。"
    note = f"(diff 太長,只附前 {MAX_DIFF_CHARS} 字元;其餘請用 Read 讀上面列出、目前還存在的檔案)" if truncated else ""
    return f"這個專案自上次檢查後,以下 memory 檔案有變動:\n{changed}\n\n實際變動內容(git log -p,由舊到新){note}:\n{diff}"


def dangling_note(dangling: str) -> str:
    return ("\nSTATE.md 目前引用了這些已經不存在的檔案(被搬走或刪除),這次一定要修正引用,\n"
            f"指向現在實際存放內容的地方(通常是 memory/reference/ 底下的主題文件):{dangling}") if dangling else ""


def process(proj: str, d: pathlib.Path, handoffs: list[dict], digest: list[str]) -> None:
    log(f"--- {proj} ---")
    if run(["git", "pull", "-q"], cwd=d, to_log=True).returncode != 0:
        log(f"{proj}: git pull failed, skip")
        return
    head = git(d, "rev-parse", "HEAD")
    ckpt = STATE_DIR / f"{proj}.sha"
    last = ckpt.read_text().strip() if ckpt.exists() else ""
    mine = [h for h in handoffs if h.get("project") == proj and h.get("effect") not in KEYED_EFFECTS] + load_records(proj)
    ids = [jstr(h.get("id")) for h in mine]
    nums = " ".join(f"#{jstr(h.get('handoff_id'))}" for h in mine)
    if head == last and not ids:
        log(f"{proj}: no new commits, skip")
        return

    changed, rng = "", ""
    if head != last:
        rng = f"{last}..{head}" if last and run(["git", "-C", str(d), "cat-file", "-e", last]).returncode == 0 else head
        names = git(d, "log", "--name-only", "--pretty=format:", rng, "--", "memory/")
        changed = "\n".join(sorted({n for n in names.split("\n") if n}))
    if not changed and not ids:
        log(f"{proj}: no memory/ changes in range {rng}")
        ckpt.write_text(head + "\n")
        return

    if changed:
        log(f"{proj}: changed memory files: {changed.replace(chr(10), ' ')} ")
    memory_section, diff, truncated = memory_input(d, rng, changed)

    h_note = ""
    if ids:
        log(_("{project}: assistant handoffs {nums}", project=proj, nums=nums))
        h_note = handoff_note(mine)

    dangling = dangling_refs(d)
    if dangling:
        log(_("{project}: STATE.md refers to files that do not exist:{files}", project=proj, files=dangling))
    d_note = dangling_note(dangling)

    pre = git(d, "rev-parse", "HEAD")
    before = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""   # input for shadow mode
    # Only ./STATE.md may be written: saying so in the prompt does not stop it (a small model changed memory/reference/
    # anyway), so the librarian-state judgment point enforces it with permission rules (dontAsk, only Edit(./STATE.md));
    # the quarantine check below is the second line of defence
    case = record_state_case(proj, d, pre, before, {"changed": changed, "diff": diff, "truncated": truncated,
                                                     "handoffs": mine, "dangling": dangling},
                             {"memory_section": memory_section, "h_note": h_note, "d_note": d_note})
    rc, obj, text = llm("librarian-state", state_prompt(proj, memory_section, h_note, d_note), d)
    if rc != 0 or not text:
        # The call itself failed: it may have stopped halfway, so nothing is kept. Restore the copy to a clean state
        # rather than leave it for the next night
        run(["git", "-C", str(d), "checkout", "-q", "--", "."])
        run(["git", "-C", str(d), "clean", "-fdq"])
        log(_("{project}: claude invocation failed (exit {rc}); the copy is restored and the checkpoint not advanced",
              project=proj, rc=rc))
        return
    if (cost := add_cost(obj)):
        log(f"{proj}: call cost ${cost} (model: {used_model(obj)})")

    so = (obj or {}).get("structured_output")
    # No structured_output (happens every few nights): use git as usual to decide whether anything actually changed,
    # and commit if it did; there is just no summary for the digest, and the checkpoint does not advance (a fixed
    # STATE.md used to be left until the next night)
    no_output = so is None
    if no_output:
        o = obj or {}
        # shell: jq's newline became a trailing space through tr
        said = (jstr(o.get("result")) if present(o.get("result")) else "") + "\n"
        log(_("{project}: no structured_output (subtype={subtype}, turns={turns}; the model last said: {said}); "
              "going by the actual file changes", project=proj,
              subtype=jstr(o.get("subtype")) if present(o.get("subtype")) else "?",
              turns=jstr(o.get("num_turns")) if present(o.get("num_turns")) else "?",
              said=said.replace(chr(10), " ")[:300]))
    so = so if isinstance(so, dict) else {}
    summary = jstr(so["summary"]) if present(so.get("summary")) else ""
    reason = jstr(so["reason"]) if present(so.get("reason")) else ""
    reported = "unknown" if no_output or obj is None else jq_bool(so.get("state_updated"))
    worth = jq_bool(so.get("worth_digesting"))

    stray = quarantine_stray(proj, d)
    repair_state_todo(proj, d)

    # Go by the actual git state. Trap: the project's own SessionEnd hook may already have committed and pushed
    # STATE.md while the call was still running (a headless call is a full session too) -- so first compare whether
    # HEAD moved; that is the truth the hook cannot overwrite
    post = git(d, "rev-parse", "HEAD")
    changed_state, state_diff, new_sha = False, "", post
    if post != pre:
        changed_state, state_diff = True, git(d, "diff", pre, post, "--", "STATE.md")
    elif run(["git", "-C", str(d), "diff", "--quiet", "--", "STATE.md"]).returncode != 0:
        changed_state, state_diff = True, git(d, "diff", "--", "STATE.md")
    actually = "true" if changed_state else "false"
    if reported != "unknown" and reported != actually:
        log(_("{project}: warning: the agent reported state_updated={reported}, but the actual file change={actually} "
              "(going by the actual files)", project=proj, reported=reported, actually=actually))
    after = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""
    shadow_state(proj, d, before, after, changed_state, memory_section, h_note, d_note)
    record_outcome(case, {"rc": rc, "structured_output": so or None, "changed": changed_state, "stray": bool(stray),
                          "model": used_model(obj)}, {"STATE.md": after})

    if changed_state:
        # the hook did not take over (the project did not go through its hook this time, or the hook is not set
        # up): commit and push ourselves
        if post == pre:
            if run(["git", "add", "STATE.md"], cwd=d).returncode == 0 and \
                    run(["git", "commit", "-m", "librarian: update STATE.md", "-q"], cwd=d).returncode == 0:
                run([str(SAFE_PUSH)], cwd=d)
            new_sha = git(d, "rev-parse", "HEAD")
        log(f"{proj}: STATE.md updated & pushed ({new_sha})")
        # `**理由**` is a label registrar.py reads back (removal_reason): a token, not a message
        block = (f"## {TODAY} · {proj}\n\n**commit**: `{new_sha}`\n\n**理由**: {reason or _('(the agent gave no reason)')}\n\n"
                 + (_("**Handoffs from {assistant}**: {nums}", assistant=hubconf.assistant_name(), nums=nums) + "\n\n" if ids else "")
                 + f"```diff\n{state_diff}\n```\n\n---\n\n")
        with REVIEW_LOG.open("a") as f:
            f.write(block)
        log(_("{project}: written to review-log.md for later spot checks", project=proj))
    else:
        # Keep the reason even when nothing is updated: when STATE.md stays placeholder content, that is how to tell
        # whether the model is too conservative or the prompt is wrong
        log(_("{project}: STATE.md not updated; reason: {reason}", project=proj,
              reason=reason or _("(the agent gave no reason)")))

    # The model said it updated but nothing changed, or it touched files it should not have: treat this run as not done,
    # do not advance the checkpoint, and run the same range next time
    if (reported == "true" and actually != "true") or stray or no_output:
        log(_("{project}: this result is not trustworthy; the checkpoint is not advanced, retrying next time", project=proj))
        return
    ckpt.write_text(new_sha + "\n")

    if ids:
        r = run([PY, str(HUB / "bin" / "hubbus"), "done", *ids, "--note",
                 _("librarian applied this to STATE.md ({date}, see review-log)", date=TODAY)], to_log=True)
        if r.returncode == 0:
            log(_("{project}: assistant handoffs {nums} closed (the bridge reports it to the assistant)", project=proj,
                  nums=nums))
        else:
            log(_("{project}: closing the assistant handoffs failed; they are processed again tomorrow night (STATE.md "
                  "was already changed; processing them again should change nothing)", project=proj))

    if worth == "true" and summary:
        digest.append(f"## {proj}\n\n{summary}\n\n")


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    log("=== librarian run start ===")
    # quarantine only keeps evidence; cleared automatically after 14 days
    cutoff = datetime.datetime.now().timestamp() - 14 * 86400
    for q in (STATE_DIR / "stray").glob("*") if (STATE_DIR / "stray").is_dir() else []:
        if q.stat().st_mtime < cutoff:
            shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink()

    handoffs = load_handoffs()
    digest: list[str] = []
    for d in sorted(p for p in copies.glob("*/") if (p / ".git").is_dir()):
        process(d.name, d, handoffs, digest)

    # Today's digest: the project-summary section, then ask the model to mark the relations between projects
    # (index.md). The digest is written in sections (hublib.digest_append): each job writes its own, and a rerun
    # replaces only its own
    run(["git", "pull", "-q"], cwd=HUB, to_log=True)
    if digest:
        try:
            hublib.digest_append("projects", "".join(digest))
        except OSError:
            log(_("digest {date}: write failed", date=TODAY))
        index_file = hubconf.data_file("index.md")
        index_before = index_file.read_text(errors="replace") if index_file.exists() else ""
        digest_now = hublib.digest_path()
        icase = record_index_case(index_before, digest_now.read_text(errors="replace") if digest_now.exists() else "")
        rc, obj, text = llm("librarian-index", index_prompt(), hubconf.data_dir())
        if rc == 0 and text:
            if (cost := add_cost(obj)):
                log(f"index.md: call cost ${cost} (model: {used_model(obj)})")
            so = (obj or {}).get("structured_output")
            rel = so.get("relations_found") if isinstance(so, dict) else None
            log(f"index.md: {jstr(rel) if present(rel) else ''}")
        else:
            log(f"index.md update failed (exit {rc})")
        digest_file = hublib.digest_path()
        so = (obj or {}).get("structured_output") if rc == 0 else None
        record_outcome(icase, {"rc": rc, "structured_output": so if isinstance(so, dict) else None, "model": used_model(obj)},
                       {"index.md": index_file.read_text(errors="replace") if index_file.exists() else ""})
        shadow_index(index_before, index_file.read_text(errors="replace") if index_file.exists() else "",
                     digest_file.read_text(errors="replace") if digest_file.exists() else "".join(digest))
    else:
        # no project summaries today: drop this section (in case an earlier run the same day wrote one)
        hublib.digest_append("projects", "")
        log("nothing to digest today")
    log(f"=== librarian run end (total cost today: ${f'{total_cost:.6f}' if cost_added else '0'}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
