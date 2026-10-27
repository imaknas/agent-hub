#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""圖書館員:每晚讀各專案 memory/ 的新增內容,視情況更新 STATE.md,並寫今天 digest 的專案摘要段落、請模型標出
專案之間的關聯(index.md)。平台 job agent-hub/librarian(排在 consolidate 之後;後面的機械步驟——助理的任務登錄、
reconcile-questions、todo、memory-gaps、librarian-publish——是各自的 job,不在這支裡)。

10-27 從 librarian.sh 改寫成 Python(行為一樣,在隔離環境用替身 llm.py 逐位元組比對過 prompt、commit、review-log、
digest、checkpoint):路徑、名字都經過 hubconf,不用在 shell 裡各自推;LLM 輸出的 JSON 直接讀,不用 jq。

robust 化的決定(沿用):
1. 單一專案的 STATE.md 判斷用 haiku(量大、任務單純);跨專案關聯用 sonnet。模型、權限、工具、輸出 schema 宣告在
   contracts/llm-points.yaml(判斷點 librarian-state、librarian-todo-format、librarian-index),models.env 可覆寫。
2. 用「實際 git 狀態」而不是模型自報的 state_updated 決定要不要 commit,兩者不一致時記警告。
3. 每次真的動到 STATE.md,都寫一筆到 review-log.md(連 diff 一起),給人事後抽查,不做阻斷式審批。
4. push 都經過 git-safe-push.sh(遇到分岔自動 pull 重試;真的內容衝突才失敗讓人看)。
5. 模型只准動 ./STATE.md(權限規則機械限制);動了別的檔案就搬到隔離區、checkpoint 不推進(第二道防線)。

用法:librarian.py(log 寫在 .logs/librarian-<日期>.log;在終端機跑時也印到螢幕)
"""
from __future__ import annotations

import datetime
import glob
import json
import pathlib
import re
import shutil
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import hublib  # noqa: E402

HUB = hubconf.ROOT
STATE_DIR, LOG_DIR = hubconf.STATE, hubconf.LOGS
REVIEW_LOG = hubconf.data_file("review-log.md")
SAFE_PUSH = HUB / "bin" / "git-safe-push.sh"
PY = str(HUB / "bin" / "py")
MAX_DIFF_CHARS = 60000
# 帶任務 key 的轉交不給模型,之後的 assistant 模組任務登錄 job 照 key 機械套用
KEYED_EFFECTS = ("done", "dropped", "already_done", "reopened")

TODAY = datetime.date.today().isoformat()
LOG = LOG_DIR / f"librarian-{TODAY}.log"
OWNER = hubconf.get("owner.tag") or "owner"            # prompt 裡對人、對助理的稱呼
ASSISTANT = hubconf.get("assistant.name") or "個人助理"
total_cost = 0.0
cost_added = False   # shell 版沒有任何成本時印 $0,有才印 %.6f


def models() -> tuple[str, str]:
    """(STATE_MODEL, INDEX_MODEL):預設 haiku / sonnet,config 的 models.env 有寫就照它。llm.py 也照同一份 models.env
    選模型(--model > 環境變數 > models.env > 預設),所以這裡寫進 log 的就是實際用的。"""
    vals = {"STATE_MODEL": "haiku", "INDEX_MODEL": "sonnet"}
    env = hubconf.config_file("models.env")
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.fullmatch(r"\s*([A-Z_][A-Z0-9_]*)=(\S+)\s*", line)
            if m and m.group(1) in vals:
                vals[m.group(1)] = m.group(2)
    return vals["STATE_MODEL"], vals["INDEX_MODEL"]


def log(msg: str) -> None:
    """只寫進 LOG;手動在終端機跑時才另外印。排程器把 stdout 導進同一個 log,一律印的話每行會重複兩次。"""
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    if sys.stdout.isatty():
        print(line)


def run(cmd: list, cwd=None, stdin: str | None = None, to_log: bool = False) -> subprocess.CompletedProcess:
    """執行外部指令;to_log=True 時 stdout 跟 stderr 都附加到 LOG(跟 shell 版的 >>"$LOG" 2>&1 一樣)。"""
    if to_log:
        with LOG.open("a") as f:
            return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, stdout=f, stderr=f)
    return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, capture_output=True)


def out(cmd: list, cwd=None) -> str:
    """指令的 stdout,去掉結尾的換行(跟 shell 的 $(…) 一樣)。"""
    return run(cmd, cwd=cwd).stdout.rstrip("\n")


def git(repo, *args) -> str:
    return out(["git", "-C", str(repo), *args])


def llm(point: str, prompt: str, cwd) -> tuple[int, dict | None, str]:
    """跑一個判斷點(bin/llm.py run <point>,prompt 從 stdin);stderr 附加到 LOG。回傳 (exit, 解析過的 JSON, 原文)。"""
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
    """累計成本;回傳給 log 用的字串(沒有成本就是空字串)。"""
    global total_cost, cost_added
    c = (obj or {}).get("total_cost_usd")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and c >= 0:
        total_cost += c
        cost_added = True
        return str(c)
    return ""


def jstr(v) -> str:
    """jq 的 \\(…) 字串插值:null → "null",字串原樣,其他是 JSON。"""
    if v is None:
        return "null"
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def present(v) -> bool:
    """jq 的真假:只有 null 跟 false 是假(空字串、0 都算有)。"""
    return v is not None and v is not False


def jq_bool(v) -> str:
    """jq -r 印一個值:true/false/null,字串原樣。"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return jstr(v)


def head_spaced(text: str, n: int) -> str:
    """`echo "$x" | head -n | tr '\\n' ' '`:前 n 行,每行後面接一個空白。"""
    return "".join(line + " " for line in text.split("\n")[:n])


# ---------- 轉交 ----------

def load_handoffs() -> list[dict]:
    """助理的轉交(owner 告訴個人助理、跟某個專案有關的事實):hubbus-bridge 每 15 分鐘收進各專案信箱,這裡每晚把還沒
    結案的直接當成這次 STATE.md 呼叫的額外輸入(不寫成流水筆記;改了什麼跟理由留在 review-log.md)。這個專案的結果
    可信(checkpoint 會推進)才 hubbus done,bridge 再回報助理結案;不可信就留到隔晚。"""
    r = run([PY, str(HUB / "bin" / "hubbus"), "open-handoffs"])
    if r.stderr:
        with LOG.open("a") as f:
            f.write(r.stderr)
    try:
        items = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        log("hubbus open-handoffs 讀取失敗,這次不處理轉交")
        return []
    log(f"助理的轉交:{len(items)} 筆待處理")
    for p in sorted({str(h.get("project")) for h in items if isinstance(h, dict)}):
        if not (hubconf.p("memory_copies", "~/.memory-copies") / p / ".git").is_dir():
            log(f"助理的轉交:專案 {p} 沒有 memory 副本,這次不處理(留在信箱)")
    return [h for h in items if isinstance(h, dict)]


def load_records(proj: str) -> list[dict]:
    """v2(Principal Handoff 0.3):principal 端送來、還沒 done 的 record 變成跟轉交一樣的輸入——note、Decisions 的
    settled(答案或「不用回答」)、same_as。Acts 的 settled/reopened 由 registrar 機械套用,這裡不碰。"""
    r = run([PY, str(HUB / "bin" / "hubbus"), "records", "--project", proj])
    try:
        events = json.loads(r.stdout) if r.returncode == 0 else []
    except ValueError:
        events = []
    asked = {}
    for e in events:   # 舊到新:同一個 key 最後的 terms 就是現在問的
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
    """結案記的決定者跟授權種類(規範 core §5),用人話寫給模型看。"""
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


# ---------- 一個專案 ----------

def repair_state_todo(proj: str, d: pathlib.Path) -> None:
    """待辦段落格式不對(haiku 不一定每次照格式寫,gen-todo 就收不到):請模型只修格式、內容不動,修完再驗一次;
    還是不對就記下來,gen-todo 會在 todo.md 檔尾列出。"""
    check = [PY, str(HUB / "bin" / "check-state-todo.py"), str(d / "STATE.md")]
    r = run(check)
    if r.returncode == 0:
        return
    problems = r.stdout.rstrip("\n")
    log(f"{proj}: STATE.md 待辦段落格式不對,請模型只修格式:{head_spaced(problems, 3)}")
    _, obj, _ = llm("librarian-todo-format", todo_fix_prompt(problems), d)
    add_cost(obj)
    r = run(check)
    if r.returncode == 0:
        log(f"{proj}: 待辦段落格式已修正")
    else:
        log(f"{proj}: 待辦段落格式修正後仍有問題(todo.md 檔尾會列出):{head_spaced(r.stdout.rstrip(chr(10)), 2)}")


def quarantine_stray(proj: str, d: pathlib.Path) -> bool:
    """模型只准動 ./STATE.md:多出來的(2026-09-26 有 5 個專案寫成 memory/STATE.md)搬到隔離區並記錄,不讓下一次
    呼叫把它們當成真的內容讀進去。被追蹤的檔案被改或刪:留一份改過的當證據,再還原。"""
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
    log(f"{proj}: 警告——模型動了 STATE.md 以外的檔案,已搬到 {qdir}:{' '.join(stray)}")
    return True


def dangling_refs(d: pathlib.Path) -> str:
    """STATE.md 裡提到的 memory/ 路徑(可含 *)現在還存不存在;回傳 shell 版的格式(每個前面一個空白)。"""
    st = d / "STATE.md"
    refs = sorted({r for r in re.findall(r"memory/[A-Za-z0-9._*/-]+\.md", st.read_text(errors="replace"))
                   if "YYYY" not in r}) if st.exists() else []
    return "".join(f" {r}" for r in refs if not glob.glob(r, root_dir=d))


def process(proj: str, d: pathlib.Path, handoffs: list[dict], state_model: str, digest: list[str]) -> None:
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

    memory_section = f"這次 memory/ 沒有新變動,只有下面 {OWNER} 的轉交要處理。"
    if changed:
        log(f"{proj}: changed memory files: {changed.replace(chr(10), ' ')} ")
        # diff 由腳本直接給:模型沒有 Bash,自己跑不了 git log,也讀不到已經被 git rm 的檔案。太大就截斷,模型再用 Read 補
        diff = git(d, "log", "-p", "--reverse", "--format==== commit %h %ad %s", "--date=short", rng, "--", "memory/")
        note = ""
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS]
            note = f"(diff 太長,只附前 {MAX_DIFF_CHARS} 字元;其餘請用 Read 讀上面列出、目前還存在的檔案)"
        memory_section = f"這個專案自上次檢查後,以下 memory 檔案有變動:\n{changed}\n\n實際變動內容(git log -p,由舊到新){note}:\n{diff}"

    h_note = ""
    if ids:
        log(f"{proj}: 助理的轉交 {nums}")
        h_note = handoff_note(mine)

    dangling = dangling_refs(d)
    d_note = ""
    if dangling:
        log(f"{proj}: STATE.md 引用了不存在的檔案:{dangling}")
        d_note = ("\nSTATE.md 目前引用了這些已經不存在的檔案(被搬走或刪除),這次一定要修正引用,\n"
                  f"指向現在實際存放內容的地方(通常是 memory/reference/ 底下的主題文件):{dangling}")

    pre = git(d, "rev-parse", "HEAD")
    # 只准寫 ./STATE.md:prompt 講了也擋不住(2026-10-15 project-06 的 haiku 還是改了 memory/reference/),判斷點
    # librarian-state 用權限規則機械限制(dontAsk、只准 Edit(./STATE.md));下面的隔離檢查是第二道防線
    rc, obj, text = llm("librarian-state", state_prompt(proj, memory_section, h_note, d_note), d)
    if rc != 0 or not text:
        # 呼叫本身失敗:可能改到一半,不採用。副本還原成乾淨狀態,不留給下一晚
        run(["git", "-C", str(d), "checkout", "-q", "--", "."])
        run(["git", "-C", str(d), "clean", "-fdq"])
        log(f"{proj}: claude invocation failed (exit {rc}), 已還原副本,checkpoint 不推進")
        return
    if (cost := add_cost(obj)):
        log(f"{proj}: call cost ${cost} (model: {state_model})")

    so = (obj or {}).get("structured_output")
    # 拿不到 structured_output(每幾晚會發生一次):照常用 git 判斷實際有沒有改、有改就 commit,只是沒有摘要可放
    # digest、checkpoint 也不推進(2026-09-28 project-36、09-29 the-crucible 改好的 STATE.md 曾經被留到隔晚)
    no_output = so is None
    if no_output:
        o = obj or {}
        said = (jstr(o.get("result")) if present(o.get("result")) else "") + "\n"   # shell:jq 的換行經 tr 變成結尾的空白
        log(f"{proj}: 沒有拿到 structured_output(subtype={jstr(o.get('subtype')) if present(o.get('subtype')) else '?'} "
            f"turns={jstr(o.get('num_turns')) if present(o.get('num_turns')) else '?'} "
            f"模型最後說:{said.replace(chr(10), ' ')[:300]}),仍以實際檔案變化為準")
    so = so if isinstance(so, dict) else {}
    summary = jstr(so["summary"]) if present(so.get("summary")) else ""
    reason = jstr(so["reason"]) if present(so.get("reason")) else ""
    reported = "unknown" if no_output or obj is None else jq_bool(so.get("state_updated"))
    worth = jq_bool(so.get("worth_digesting"))

    stray = quarantine_stray(proj, d)
    repair_state_todo(proj, d)

    # 以實際 git 狀態為準。陷阱:這個專案自己的 SessionEnd hook 在呼叫「進行中」就可能已經 commit+push 過 STATE.md
    # (headless 呼叫也是一個完整的 session)——先比 HEAD 有沒有動,那才是不會被 hook 蓋掉的真相
    post = git(d, "rev-parse", "HEAD")
    changed_state, state_diff, new_sha = False, "", post
    if post != pre:
        changed_state, state_diff = True, git(d, "diff", pre, post, "--", "STATE.md")
    elif run(["git", "-C", str(d), "diff", "--quiet", "--", "STATE.md"]).returncode != 0:
        changed_state, state_diff = True, git(d, "diff", "--", "STATE.md")
    actually = "true" if changed_state else "false"
    if reported != "unknown" and reported != actually:
        log(f"{proj}: 警告——agent 回報 state_updated={reported},但實際檔案變化={actually}(以實際檔案為準)")

    if changed_state:
        if post == pre:  # hook 沒接手(這個專案這次沒有走 hook,或 hook 沒設好),自己補 commit+push
            if run(["git", "add", "STATE.md"], cwd=d).returncode == 0 and \
                    run(["git", "commit", "-m", "librarian: update STATE.md", "-q"], cwd=d).returncode == 0:
                run([str(SAFE_PUSH)], cwd=d)
            new_sha = git(d, "rev-parse", "HEAD")
        log(f"{proj}: STATE.md updated & pushed ({new_sha})")
        block = (f"## {TODAY} · {proj}\n\n**commit**: `{new_sha}`\n\n**理由**: {reason or '(agent 未提供理由)'}\n\n"
                 + (f"**{ASSISTANT} 轉交**: {nums}\n\n" if ids else "")
                 + f"```diff\n{state_diff}\n```\n\n---\n\n")
        with REVIEW_LOG.open("a") as f:
            f.write(block)
        log(f"{proj}: 已寫入 review-log.md,供事後抽查")
    else:
        # 不更新也留理由:STATE.md 一直是佔位內容時,才分得出是模型太保守還是 prompt 有問題
        log(f"{proj}: STATE.md 未更新,理由: {reason or '(agent 未提供理由)'}")

    # 模型說有更新、實際沒改,或動了不該動的檔案:這次當作沒處理完,不推進 checkpoint,下次再跑同一個範圍
    if (reported == "true" and actually != "true") or stray or no_output:
        log(f"{proj}: 這次結果不可信,checkpoint 不推進,下次重試")
        return
    ckpt.write_text(new_sha + "\n")

    if ids:
        r = run([PY, str(HUB / "bin" / "hubbus"), "done", *ids, "--note", f"librarian 套用進 STATE.md({TODAY},見 review-log)"],
                to_log=True)
        if r.returncode == 0:
            log(f"{proj}: 助理的轉交 {nums} 已結案(bridge 會回報助理)")
        else:
            log(f"{proj}: 助理的轉交結案失敗,隔晚會再處理一次(STATE.md 已經改過,重複處理應該不會再改)")

    if worth == "true" and summary:
        digest.append(f"## {proj}\n\n{summary}\n\n")


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    state_model, index_model = models()
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    log("=== librarian run start ===")
    # 隔離區只是留證據,14 天後自動清掉
    cutoff = datetime.datetime.now().timestamp() - 14 * 86400
    for q in (STATE_DIR / "stray").glob("*") if (STATE_DIR / "stray").is_dir() else []:
        if q.stat().st_mtime < cutoff:
            shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink()

    handoffs = load_handoffs()
    digest: list[str] = []
    for d in sorted(p for p in copies.glob("*/") if (p / ".git").is_dir()):
        process(d.name, d, handoffs, state_model, digest)

    # 今天的 digest:專案摘要這一段,再請模型標出專案之間的關聯(index.md)。digest 是分段寫的(hublib.digest_append):
    # 各 job 寫自己那段,重跑只換自己的
    run(["git", "pull", "-q"], cwd=HUB, to_log=True)
    if digest:
        try:
            hublib.digest_append("projects", "".join(digest))
        except OSError:
            log(f"digest {TODAY} 寫入失敗")
        rc, obj, text = llm("librarian-index", index_prompt(), hubconf.data_dir())
        if rc == 0 and text:
            if (cost := add_cost(obj)):
                log(f"index.md: call cost ${cost} (model: {index_model})")
            so = (obj or {}).get("structured_output")
            rel = so.get("relations_found") if isinstance(so, dict) else None
            log(f"index.md: {jstr(rel) if present(rel) else ''}")
        else:
            log(f"index.md update failed (exit {rc})")
    else:
        hublib.digest_append("projects", "")   # 今天沒有專案摘要:拿掉這一段(同一天重跑、上一次有寫過的話)
        log("nothing to digest today")
    log(f"=== librarian run end (total cost today: ${f'{total_cost:.6f}' if cost_added else '0'}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
