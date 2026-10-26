#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""回頭清理(consolidation):把各專案放太久的 memory 日期檔整理進 reference/,順便修正
reference/ 跟 STATE.md 裡被較新紀錄推翻的敘述。librarian 只往前看(新增的 memory → STATE.md),
這支往回看,不讓 memory 變成流水帳、也不讓過時的敘述一直看起來像定論。

每個專案做的事:
1. 機械挑選(零 LLM):日期檔(memory/YYYY-MM-DD[-主題].md)比 OLDER_THAN_DAYS 舊的才處理,
   一次最多 MAX_FOLD_BYTES(由舊到新);沒有就跳過。同一個專案成功後 MIN_INTERVAL_DAYS 內不再跑。
2. 在暫存目錄 clone 一份乾淨的 memory repo(從 bare repo),模型只在那裡工作——~/.memory-copies
   在 push 成功前完全不動,失敗就整個丟掉,不會留下改一半的東西。
3. 模型(CONSOLIDATE_MODEL,預設 opus,models.env 可改)只准寫 ./memory/reference/** 跟
   ./STATE.md(--permission-mode dontAsk + --allowedTools,prompt 擋不住 haiku 亂改別的檔案);
   沒有 Bash,不能刪檔。它用 structured output 回報每個日期檔放到哪裡、沒保留什麼、修正了哪些敘述
   (每一筆修正都要附較新檔案的逐字引文)。
4. 腳本自己驗證,不信模型的自我回報:
   - git status 有 STATE.md / memory/reference/*.md 以外的變化 → 搬到 .state/stray/,整個專案放棄
   - 「預計/可能…」被改成確定語氣(比對刪掉/整理掉的含保留語氣的句子跟新加的句子),又沒有
     逐字證據撐腰 → 把那個檔案還原;放到那個檔案的日期檔這次不刪
   - 修正附的引文在指定檔案裡找不到 → 還原那個檔案
   - STATE.md 的 `## 待辦` 段落被改壞(check-state-todo.py)→ 還原 STATE.md
   - 只 git rm 模型宣告已整理完、而且目的地都還在(沒被還原)的日期檔
   - 被刪的日期檔裡的網址/`程式碼片段` 整理後找不到 → 不擋,列進 review-log 給人抽查
5. commit → git-safe-push.sh(project session 同時 push 造成分岔就自動 merge 重試;真的衝突就放棄,
   patch 留在 .state/consolidate/failed/)→ with-lock.sh 底下更新 ~/.memory-copies/<專案>。
6. 每一筆變化(理由、修正、diff、機械檢查的警告)寫進 review-log.md,用 commit-hub-files.sh commit。
   librarian 的 checkpoint 剛好停在這次的起點時順便往前推,不讓 librarian 把整理的 diff 再讀一次。

用法:
  consolidate-memory.py                      排程用:最多 MAX_PROJECTS 個到期的專案(最需要的先)
  consolidate-memory.py --list               列出每個專案的狀態(零 LLM)
  consolidate-memory.py --dry-run [--project X] [--keep]
                                             只產生提議的 diff 跟 review-log 條目,不 push、
                                             不寫 review-log/狀態;報告在 .state/consolidate/dry-run/
  consolidate-memory.py --project X          只跑 X(不管 MIN_INTERVAL_DAYS,仍要有夠舊的日期檔)
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

HOME = pathlib.Path.home()
HUB = pathlib.Path(os.environ.get("AGENT_HUB") or hubconf.ROOT)
COPIES = hubconf.p("memory_copies", "~/.memory-copies")
STATE_DIR = HUB / ".state"
WORK_STATE = STATE_DIR / "consolidate" / "state.json"
LOG_DIR = HUB / ".logs"
REVIEW_LOG = HUB / "review-log.md"
BIN = HUB / "bin"
TODAY = datetime.date.today()

OLDER_THAN_DAYS = 14      # 日期檔要多舊才整理:兩週內的還常被翻,留著
MIN_INTERVAL_DAYS = 7     # 同一個專案成功後一週內不再跑
RETRY_AFTER_DAYS = 2      # 失敗後隔多久再試(不要每晚燒同一個失敗)
MAX_PROJECTS = 3          # 一晚最多幾個專案
BUDGET_MINUTES = 30       # 超過就不開新的專案:加上 CALL_TIMEOUT 最晚 02:50 結束,不跟 03:00 的 librarian 重疊
CALL_TIMEOUT = 20 * 60    # 單次模型呼叫上限
MAX_FOLD_BYTES = 60000    # 一次整理的日期檔總量上限(由舊到新取)
MAX_LOG_DIFF = 40000      # review-log 裡 diff 的上限,超過就截斷並附完整 diff 的指令

RAW_RE = re.compile(r"^memory/(\d{4}-\d{2}-\d{2})(-[a-z0-9-]+)?\.md$")
REF_RE = re.compile(r"^memory/reference/[^/]+\.md$")

# 保留語氣:這些字從句子裡消失、句子其他部分沒變,就是「預計 → 確認」那種摘要漂移
HEDGES = [
    "預計", "預期", "預估", "估計", "可能", "也許", "或許", "大概", "大約", "應該", "似乎", "疑似",
    "推測", "猜測", "暫定", "暫時", "待確認", "未確認", "尚未確認", "不確定", "有待", "打算", "計畫",
    "傾向", "考慮", "初步", "假設", "據說", "看起來", "目標是", "希望", "?", "？",
    "maybe", "likely", "probably", "expected", "expect", "estimate", "tentative", "tbd",
    "might", "plan to", "planned", "should be", "assume", "unconfirmed", "not yet",
]

GIT_ENV = {
    **os.environ,
    # memory repo 不跑專案的 git hook(見 known-issues「memory worktree 會繼承專案的 git hook」)
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
    "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""),
}

_log_file = None


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    if _log_file:
        with open(_log_file, "a") as f:
            f.write(line + "\n")
    # 排程器把 stdout 導進同一個 log 檔,只在終端機手動跑時才另外印
    if sys.stdout.isatty() or not _log_file:
        print(line, flush=True)


def git(*args, cwd=None, git_dir=None, check=True) -> str:
    cmd = ["git"] + ([f"--git-dir={git_dir}"] if git_dir else []) + list(args)
    r = subprocess.run(cmd, cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗:{r.stderr.strip()[:500]}")
    return r.stdout


def load_models() -> dict:
    models = {"CONSOLIDATE_MODEL": "opus"}
    env = HUB / "models.env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                models[k.strip()] = v.strip()
    return models


def load_state() -> dict:
    try:
        return json.loads(WORK_STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    WORK_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WORK_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(WORK_STATE)


def days_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (TODAY - datetime.date.fromisoformat(iso)).days


# ---------- 機械挑選 ----------

def project_origin(copy: pathlib.Path) -> str | None:
    r = subprocess.run(["git", "-C", str(copy), "remote", "get-url", "origin"], capture_output=True, text=True)
    url = r.stdout.strip()
    return url if r.returncode == 0 and url else None


def survey(name: str, origin: str) -> dict:
    """直接讀 bare repo 的 agent-memory 分支(最新,而且不用動副本)。"""
    files = git("ls-tree", "-r", "--name-only", "agent-memory", "memory/", git_dir=origin).split()
    cutoff = TODAY - datetime.timedelta(days=OLDER_THAN_DAYS)
    old = []
    for f in sorted(files):
        m = RAW_RE.match(f)
        if m and datetime.date.fromisoformat(m.group(1)) < cutoff:
            old.append(f)
    batch, total = [], 0
    for f in old:
        size = int(git("cat-file", "-s", f"agent-memory:{f}", git_dir=origin).strip())
        if batch and total + size > MAX_FOLD_BYTES:
            break
        batch.append(f)
        total += size
    raw = [f for f in files if RAW_RE.match(f)]
    return {"name": name, "origin": origin, "old": old, "batch": batch, "batch_bytes": total,
            "raw": raw, "oldest": old[0][7:17] if old else None}


def candidates(state: dict, only: str | None, ignore_interval: bool) -> list[dict]:
    out = []
    for copy in sorted(COPIES.iterdir()):
        if not (copy / ".git").exists() or (only and copy.name != only):
            continue
        origin = project_origin(copy)
        if not origin or not pathlib.Path(origin).is_dir():
            log(f"{copy.name}: origin 不是本機的 bare repo({origin}),跳過")
            continue
        try:
            s = survey(copy.name, origin)
        except (RuntimeError, ValueError) as e:
            log(f"{copy.name}: 讀不到 agent-memory 分支,跳過:{e}")
            continue
        st = state.get(copy.name, {})
        s["last_ok"], s["last_try"] = st.get("last_ok"), st.get("last_try")
        if not s["batch"]:
            s["skip"] = f"沒有超過 {OLDER_THAN_DAYS} 天的日期檔"
        elif not ignore_interval and days_since(s["last_ok"]) < MIN_INTERVAL_DAYS:
            s["skip"] = f"{s['last_ok']} 才整理過"
        elif not ignore_interval and s["last_try"] != s["last_ok"] and days_since(s["last_try"]) < RETRY_AFTER_DAYS:
            s["skip"] = f"{s['last_try']} 失敗過,{RETRY_AFTER_DAYS} 天後再試"
        out.append(s)
    # 最需要的先:從沒整理過的、最舊的日期檔越舊越前面
    out.sort(key=lambda s: (s["last_ok"] is not None, s["oldest"] or "9999"))
    return out


# ---------- 模型呼叫 ----------

# 輸出 schema 在 contracts/llm/consolidate.schema.json(判斷點 consolidate,bin/llm.py 套用)


def build_prompt(s: dict, repo: pathlib.Path) -> str:
    batch_text = "\n\n".join(f"===== {f} =====\n{(repo / f).read_text()}" for f in s["batch"])
    newer = [f for f in s["raw"] if f not in s["batch"]]
    refs = []
    for p in sorted((repo / "memory" / "reference").glob("*.md")):
        rel = str(p.relative_to(repo))
        when = git("log", "-1", "--format=%ad", "--date=short", "--", rel, cwd=repo).strip()
        refs.append(f"- {rel}(最後修改 {when or '?'})")
    state_when = git("log", "-1", "--format=%ad", "--date=short", "--", "STATE.md", cwd=repo).strip()
    return f"""你是 agent-hub 的「回頭清理」agent,現在在專案 {s['name']} 的 memory repo 裡工作。
背景先讀 ./AGENT.md(memory 的結構跟規則)。

memory/ 的結構:
- memory/YYYY-MM-DD.md、memory/YYYY-MM-DD-<主題>.md:原始流水紀錄(日期檔),只往後加。
- memory/reference/<主題>.md:整理過的主題文件,README.md 是索引。
- ./STATE.md(專案根目錄):精煉的現況,由每晚的 librarian 維護。

## 任務一:把下面這幾個超過 {OLDER_THAN_DAYS} 天的日期檔整理進 memory/reference/

{batch_text}

做法:
- 先讀 memory/reference/README.md 跟相關的主題文件,把日期檔裡有長期價值的內容整理進對的主題文件:
  決定跟理由、被否決的選項跟原因、踩到的坑跟解法、數字/設定/帳號以外的事實、還沒解決的問題。
  寫成主題文件的樣子(按主題、不按日期);需要時間脈絡就寫「(2026-09-26)」這種標註。
  沿用主題文件原本的語言跟風格。
- 沒有合適的主題文件就開新的 memory/reference/<主題>.md(小寫英文加連字號),並更新 README.md 索引。
  **沒有地方放的事實不能丟**——開新文件或擴充既有文件。
- 可以不保留的只有:(a) 已經在 reference/ 或 STATE.md 寫過的;(b) 被較新紀錄推翻/取代的
  (如果那是個決定,在主題文件裡留一句「原本…,(日期)改成…,因為…」);(c) 沒有長期價值的流程雜訊
  (單純的 commit hash、「已 push」、時間戳記本身)。每個不保留的都要寫進 folded[].dropped。
- 一個日期檔只有在它的內容都放好(或確定可以不保留)之後,才列進 folded;還有疑慮就列進 kept 並說明。
  你不能刪檔案——列進 folded 的,腳本會在檢查過後幫你 git rm。
- ./STATE.md 或 memory/reference/ 裡指向這批日期檔的引用(例如「見 memory/2026-09-25.md」),改成指向接手內容的
  主題文件——檔案刪掉後那些引用就斷了。這是機械性的修正,不用列進 corrections。

## 任務二:修正被推翻的敘述

讀所有現存的日期檔(包括比較新的:{', '.join(newer) or '(無)'}),找出 memory/reference/ 跟 ./STATE.md
裡已經被較新紀錄推翻或取代的敘述,改成現況。主題文件最後修改日期:
{chr(10).join(refs) or '(還沒有主題文件)'}
- ./STATE.md(最後修改 {state_when or '?'})
判斷新舊看日期:日期檔的日期比主題文件最後修改日期新、而且內容衝突,才算推翻;比較舊的日期檔跟主題文件衝突時,
以主題文件為準。每一筆修正都列進 corrections,evidence_quote 要從 evidence_file **逐字複製**(腳本會比對,
對不上的修正會被整個檔案還原)。

## 不能違反的規則

- **保留不確定的語氣**:原文寫「預計/預期/可能/應該/打算/暫定/待確認/?」的,整理後照樣保留那個語氣,
  不能改成確定的說法。只有較新的紀錄明確寫出結果(例如「已上線」「確認是…」)時才能改,而且要列進 corrections
  附逐字引文。腳本會機械比對:保留語氣被拿掉、又沒有對應引文的檔案會被還原。
- 只能建立/修改 ./memory/reference/ 底下的 .md 跟 ./STATE.md。日期檔、AGENT.md、RULES 都不要動;
  memory/reference/ 底下不開子目錄。
- STATE.md 是精煉現況:只修正被推翻/過時的敘述(包括 `## 待辦` 裡已經完成或被推翻的項目——直接移除),
  不要把日期檔的內容搬進 STATE.md,也不要改 `## 待辦` 的格式(`- [ ] 〔{hubconf.owner_tag()}|agent|待定〕…`,沒有就 `- (無)`)。
- 沒有被推翻的東西不要改寫、不要重新措辭——每一個改動都會出現在 diff 給人抽查。
"""


def run_model(prompt: str, repo: pathlib.Path, model: str) -> tuple[dict | None, str]:
    # 判斷點 consolidate(權限 dontAsk、只准 Edit memory/reference/** 跟 STATE.md、schema:contracts/llm-points.yaml)
    cmd = [sys.executable, str(HUB / "bin" / "llm.py"), "run", "consolidate", "--model", model]
    env = dict(GIT_ENV)  # claude 的憑證由 llm.py 決定(預設 headless token)
    try:
        r = subprocess.run(cmd, input=prompt, cwd=repo, env=env, capture_output=True, text=True, timeout=CALL_TIMEOUT + 60)
    except subprocess.TimeoutExpired:
        return None, f"超過 {CALL_TIMEOUT // 60} 分鐘"
    if r.returncode != 0 or not r.stdout.strip():
        return None, f"exit {r.returncode}:{r.stderr.strip()[-500:]}"
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, "輸出不是 JSON:" + r.stdout[:300]


# ---------- 機械檢查 ----------

def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def has_hedge(s: str) -> bool:
    low = s.lower()
    return any(h in low for h in HEDGES)


def strip_hedges(s: str) -> str:
    low = s.lower()
    for h in sorted(HEDGES, key=len, reverse=True):
        low = low.replace(h, "")
    return re.sub(r"[\s\-*#>`_,.;:()\[\]，。、；：（）「」]+", "", low)


def diff_lines(repo: pathlib.Path, base: str, path: str) -> tuple[list[str], list[str]]:
    """base 跟工作目錄之間,這個檔案刪掉/加上的行(新檔案就全部算加上)。"""
    out = git("diff", "--no-color", "-U0", base, "--", path, cwd=repo, check=False)
    if not out and not git("ls-files", "--", path, cwd=repo).strip():
        return [], (repo / path).read_text().splitlines()
    removed = [l[1:] for l in out.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return removed, added


def similar(a: str, b: str, threshold: float = 0.75) -> bool:
    if len(a) < 8 or len(b) < 8:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold


def at_base(repo: pathlib.Path, base: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{base}:{path}"], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def verify_corrections(repo, base, corrections) -> tuple[list[dict], list[dict]]:
    ok, bad = [], []
    for c in corrections:
        src = at_base(repo, base, c.get("evidence_file", "").lstrip("./"))
        quote = norm(c.get("evidence_quote", ""))
        (ok if src is not None and len(quote) >= 4 and quote in norm(src) else bad).append(c)
    return ok, bad


def hedge_upgrades(repo, base, changed, batch, good_corrections) -> dict[str, list[tuple[str, str]]]:
    """回傳 {檔案: [(原本含保留語氣的句子, 新加的確定句子)]},已經有逐字證據的修正不算。"""
    hedged_src = []  # 被刪掉或被整理掉的、含保留語氣的句子
    for f in batch:
        hedged_src += [l for l in (at_base(repo, base, f) or "").splitlines() if has_hedge(l)]
    per_file_added = {}
    for f in changed:
        removed, added = diff_lines(repo, base, f)
        hedged_src += [l for l in removed if has_hedge(l)]
        per_file_added[f] = [l for l in added if not has_hedge(l)]
    src_norm = [(l, strip_hedges(l)) for l in hedged_src]
    afters = [norm(c["after"]) for c in good_corrections]
    found: dict[str, list[tuple[str, str]]] = {}
    for f, added in per_file_added.items():
        for a in added:
            an = strip_hedges(a)
            if any(x and (x in norm(a) or similar(x, norm(a), 0.8)) for x in afters):
                continue
            for orig, on in src_norm:
                if similar(on, an):
                    found.setdefault(f, []).append((orig.strip(), a.strip()))
                    break
    return found


def revert(repo: pathlib.Path, base: str, path: str) -> None:
    if at_base(repo, base, path) is not None:
        git("checkout", base, "--", path, cwd=repo)
    else:
        (repo / path).unlink(missing_ok=True)


def salient_missing(repo: pathlib.Path, base: str, deleted: list[str]) -> dict[str, list[str]]:
    corpus = norm((repo / "STATE.md").read_text() if (repo / "STATE.md").exists() else "")
    for p in (repo / "memory").rglob("*.md"):
        if str(p.relative_to(repo)) not in deleted:
            corpus += norm(p.read_text())
    out = {}
    for f in deleted:
        text = at_base(repo, base, f) or ""
        toks = set(re.findall(r"https?://[^\s)>\]」]+", text)) | set(re.findall(r"`([^`\n]{3,80})`", text))
        miss = sorted(t for t in toks if norm(t) not in corpus)
        if miss:
            out[f] = miss[:15]
    return out


def state_todo_ok(path: pathlib.Path) -> bool:
    # 跟 librarian 一樣用 PATH 上的 python3(GIT_ENV 把 Homebrew 放前面):check-state-todo.py 要 3.10+,
    # 以前排程器的 login shell 給這支的是系統的 python3(3.9);現在一律是平台的 Python(bin/py)
    return subprocess.run(["python3", str(BIN / "check-state-todo.py"), str(path)],
                          env=GIT_ENV, capture_output=True).returncode == 0


def state_todo_broken(repo: pathlib.Path, base: str) -> bool:
    """這次才把 `## 待辦` 改壞(原本就壞的不算,那是 librarian 的事)。"""
    if state_todo_ok(repo / "STATE.md"):
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
        f.write(at_base(repo, base, "STATE.md") or "")
        f.flush()
        return state_todo_ok(pathlib.Path(f.name))


# ---------- 一個專案 ----------

def consolidate(s: dict, model: str, dry_run: bool, keep: bool) -> dict:
    """回傳 {"status": ok|noop|failed, "entry": review-log 條目或 None, ...}"""
    name = s["name"]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"consolidate-{name}-"))
    repo = tmp / "repo"
    result = {"status": "failed", "entry": None, "tmp": tmp}
    try:
        git("clone", "-q", "--branch", "agent-memory", s["origin"], str(repo))
        base = git("rev-parse", "HEAD", cwd=repo).strip()
        log(f"{name}: 整理 {len(s['batch'])} 個日期檔({s['batch_bytes']} bytes,{s['batch'][0]} … {s['batch'][-1]}),"
            f"還有 {len(s['old']) - len(s['batch'])} 個留到下次;模型 {model}")

        out, err = run_model(build_prompt(s, repo), repo, model)
        if out is None:
            log(f"{name}: 模型呼叫失敗({err}),放棄,副本沒動")
            return result
        cost = out.get("total_cost_usd")
        denials = out.get("permission_denials") or []
        log(f"{name}: 模型完成(turns={out.get('num_turns')} cost=${cost}),被權限擋下 {len(denials)} 次")
        so = out.get("structured_output")
        if not so:
            log(f"{name}: 沒有 structured_output(模型最後說:{str(out.get('result', ''))[:300]}),放棄")
            return result

        if git("rev-parse", "HEAD", cwd=repo).strip() != base:
            log(f"{name}: HEAD 被動過(模型不該能 commit),放棄")
            return result
        changed, stray = [], []
        for line in git("status", "--porcelain", "--untracked-files=all", cwd=repo).splitlines():
            path = line[3:].strip().strip('"')
            (changed if path == "STATE.md" or REF_RE.match(path) else stray).append(path)
        if stray:
            qdir = STATE_DIR / "stray" / f"consolidate-{name}-{TODAY}"
            qdir.mkdir(parents=True, exist_ok=True)
            for p in stray:
                if (repo / p).is_file():
                    shutil.copy(repo / p, qdir / p.replace("/", "_"))
            log(f"{name}: 警告——模型動了不准動的檔案,證據在 {qdir}:{' '.join(stray)};整個放棄")
            return result

        warnings: list[str] = []
        reverted: set[str] = set()

        good, bad = verify_corrections(repo, base, so.get("corrections", []))
        for c in bad:
            f = c.get("file", "").lstrip("./")
            if f in changed and f not in reverted:
                revert(repo, base, f)
                reverted.add(f)
            warnings.append(f"還原 `{f}`:修正「{c.get('before', '')[:80]}」→「{c.get('after', '')[:80]}」"
                            f"附的引文在 `{c.get('evidence_file')}` 找不到")
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]

        for f, pairs in hedge_upgrades(repo, base, [c for c in changed if c not in reverted],
                                       s["batch"], good).items():
            revert(repo, base, f)
            reverted.add(f)
            for orig, new in pairs[:3]:
                warnings.append(f"還原 `{f}`:疑似把保留語氣拿掉,沒有逐字證據——「{orig[:100]}」→「{new[:100]}」")

        if "STATE.md" in changed and "STATE.md" not in reverted and state_todo_broken(repo, base):
            revert(repo, base, "STATE.md")
            reverted.add("STATE.md")
            warnings.append("還原 `STATE.md`:`## 待辦` 段落格式被改壞")
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]

        # 要 git rm 哪些:模型宣告整理完 ∩ 這次給的批次 ∩ 目的地都合法、存在、沒被還原
        to_delete, kept = [], list(so.get("kept", []))
        folded = []
        for item in so.get("folded", []):
            f = item.get("file", "").lstrip("./")
            dests = [d.lstrip("./") for d in item.get("destinations", [])]
            if f not in s["batch"]:
                warnings.append(f"模型說整理了 `{f}`,但它不在這次的批次,忽略")
                continue
            bad_dest = [d for d in dests if not (d == "STATE.md" or REF_RE.match(d)) or not (repo / d).exists()]
            why = None
            if reverted & set(dests):
                why = f"目的地 {', '.join(sorted(reverted & set(dests)))} 被還原了"
            elif bad_dest:
                why = f"目的地不合法或不存在:{', '.join(bad_dest)}"
            elif not dests and not item.get("dropped", "").strip():
                why = "沒有目的地也沒說明為什麼不保留"
            if why:
                kept.append({"file": f, "reason": f"(腳本擋下){why}"})
            else:
                to_delete.append(f)
                folded.append({**item, "file": f, "destinations": dests})
        for f in to_delete:
            git("rm", "-q", "--", f, cwd=repo)

        missing = salient_missing(repo, base, to_delete)
        for f, toks in missing.items():
            warnings.append(f"`{f}` 裡有、整理後找不到的字串(可能被改寫或丟了,抽查用):"
                            + "、".join(f"`{t}`" for t in toks))
        curated = {"STATE.md": (repo / "STATE.md").read_text() if (repo / "STATE.md").exists() else ""}
        for p in (repo / "memory" / "reference").glob("*.md"):
            curated[str(p.relative_to(repo))] = p.read_text()
        for f in to_delete:
            where = [k for k, text in curated.items() if pathlib.PurePath(f).name in text]
            if where:
                warnings.append(f"`{', '.join(where)}` 還引用被刪掉的 `{f}`(librarian 的斷連結檢查會接手)")
        refs = sorted(p.name for p in (repo / "memory" / "reference").glob("*.md") if p.name != "README.md")
        readme = repo / "memory" / "reference" / "README.md"
        unindexed = [r for r in refs if r not in (readme.read_text() if readme.exists() else "")]
        if unindexed:
            warnings.append("memory/reference/README.md 沒列到:" + "、".join(unindexed))

        git("add", "-A", "--", "STATE.md", "memory/reference", cwd=repo)
        if not git("status", "--porcelain", cwd=repo).strip():
            log(f"{name}: 檢查後沒有任何變化要提交" + (f"(警告 {len(warnings)} 則)" if warnings else ""))
            for w in warnings:
                log(f"{name}:   {w}")
            result["status"] = "noop"
            return result

        content_diff = git("diff", "--cached", "--no-color", "--", "STATE.md", "memory/reference", cwd=repo)
        stat = git("diff", "--cached", "--stat", cwd=repo)
        msg = f"consolidate: fold {len(to_delete)} raw notes into reference/ ({TODAY})"
        git("commit", "-q", "-m", msg, cwd=repo)
        new_sha = git("rev-parse", "HEAD", cwd=repo).strip()

        entry = review_entry(name, new_sha, model, so.get("summary", ""), folded, kept, good, warnings,
                             content_diff, stat, dry_run)
        result["entry"], result["diff"] = entry, content_diff
        for w in warnings:
            log(f"{name}: ⚠️ {w}")

        if dry_run:
            log(f"{name}: dry-run,不 push;rm {len(to_delete)} 個日期檔,改 {len(changed) - len(reverted)} 個檔案")
            result["status"] = "ok"
            return result

        patch = git("format-patch", "--stdout", f"{base}..HEAD", cwd=repo)
        push = subprocess.run([str(BIN / "git-safe-push.sh")], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
        if push.returncode != 0:
            fdir = STATE_DIR / "consolidate" / "failed"
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / f"{name}-{TODAY}.patch").write_text(patch)
            log(f"{name}: push 失敗(多半是 project session 同時改了同一段),patch 留在 {fdir};"
                f"副本沒動:{push.stderr.strip()[:300]}")
            return result
        pushed = git("rev-parse", "HEAD", cwd=repo).strip()
        log(f"{name}: 已 push {pushed}")

        copy = COPIES / name
        upd = subprocess.run([str(BIN / "with-lock.sh"), "git", "pull", "-q"], cwd=copy, env=GIT_ENV,
                             capture_output=True, text=True)
        if upd.returncode != 0:
            log(f"{name}: 副本 git pull 失敗(librarian 下次會再 pull):{upd.stderr.strip()[:200]}")

        # librarian 的 checkpoint 剛好停在起點、這次是 fast-forward(沒有 merge 別人的 commit),就往前推,
        # 不然 librarian 會把整理的 diff(含被刪的日期檔全文)當成新內容再讀一次
        ckpt = STATE_DIR / f"{name}.sha"
        if pushed == new_sha and ckpt.exists() and ckpt.read_text().strip() == base:
            ckpt.write_text(new_sha + "\n")
            log(f"{name}: librarian checkpoint 推進到 {new_sha[:8]}")
        result["status"] = "ok"
        return result
    except Exception as e:  # noqa: BLE001 — 任何意外都只影響這個專案,副本沒動
        log(f"{name}: 意外錯誤,放棄(副本沒動):{type(e).__name__}: {e}")
        return result
    finally:
        if keep:
            log(f"{name}: 暫存 clone 留在 {repo}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def review_entry(name, sha, model, summary, folded, kept, corrections, warnings, diff, stat, dry_run) -> str:
    lines = [f"## {TODAY} · {name} · 回頭清理" + ("(dry-run,未 push)" if dry_run else ""), "",
             f"**commit**: `{sha}`(模型 {model})", "", f"**摘要**: {summary or '(模型未提供)'}", ""]
    if folded:
        lines += [f"**整理掉的日期檔**(git rm;原文用 `git show {sha[:8]}^:<路徑>` 看):", ""]
        for f in folded:
            lines.append(f"- `{f['file']}` → {', '.join(f'`{d}`' for d in f['destinations']) or '(無)'}"
                         + (f";沒保留:{f['dropped']}" if f.get("dropped", "").strip() else ""))
        lines.append("")
    if kept:
        lines += ["**這次沒刪**:", ""] + [f"- `{k.get('file')}`:{k.get('reason')}" for k in kept] + [""]
    if corrections:
        lines += ["**修正的敘述**(被較新紀錄推翻):", ""]
        for c in corrections:
            lines.append(f"- `{c['file']}`:「{c['before']}」→「{c['after']}」"
                         f"(根據 `{c['evidence_file']}`:「{c['evidence_quote']}」)")
        lines.append("")
    if warnings:
        lines += ["**機械檢查**:", ""] + [f"- ⚠️ {w}" for w in warnings] + [""]
    if len(diff) > MAX_LOG_DIFF:
        diff = diff[:MAX_LOG_DIFF] + (f"\n… (截斷;完整 diff:`git -C ~/.memory-copies/{name} show {sha[:8]}`)")
    lines += ["```", stat.rstrip(), "```", "", "```diff", diff.rstrip(), "```", "", "---", ""]
    return "\n".join(lines) + "\n"


# ---------- main ----------

def main() -> int:
    global _log_file
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留暫存 clone 方便檢查")
    ap.add_argument("--max", type=int, default=MAX_PROJECTS)
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not args.list:
        _log_file = LOG_DIR / f"consolidate-memory-{TODAY}.log"
    state = load_state()
    cands = candidates(state, args.project, ignore_interval=bool(args.project))

    if args.list:
        for s in cands:
            print(f"{s['name']:28} 日期檔 {len(s['raw']):3}  超過{OLDER_THAN_DAYS}天 {len(s['old']):3}  "
                  f"上次成功 {s['last_ok'] or '-':10}  {s.get('skip') or '到期(這次 %d 個)' % len(s['batch'])}")
        return 0

    model = load_models().get("CONSOLIDATE_MODEL", "opus")
    due = [s for s in cands if not s.get("skip")]
    for s in cands:
        if s.get("skip") and args.project:
            log(f"{s['name']}: 跳過——{s['skip']}")
    if args.dry_run and not args.project:
        due = due[:1]
    log(f"=== consolidate start({'dry-run, ' if args.dry_run else ''}到期 {len(due)} 個,這次最多 {args.max} 個)===")

    start, failed, entries = time.monotonic(), 0, []
    for s in due[: args.max]:
        if time.monotonic() - start > BUDGET_MINUTES * 60:
            log(f"超過 {BUDGET_MINUTES} 分鐘,剩下的明天再做")
            break
        r = consolidate(s, model, args.dry_run, args.keep)
        if args.dry_run:
            if r["entry"]:
                ddir = STATE_DIR / "consolidate" / "dry-run"
                ddir.mkdir(parents=True, exist_ok=True)
                out = ddir / f"{s['name']}-{TODAY}.md"
                out.write_text(r["entry"])
                print(r["entry"])
                log(f"{s['name']}: 提議的 review-log 條目在 {out}")
            continue
        st = state.setdefault(s["name"], {})
        st["last_try"] = str(TODAY)
        if r["status"] in ("ok", "noop"):
            st["last_ok"] = str(TODAY)
            if r["entry"]:
                entries.append(r["entry"])
        else:
            failed += 1
        save_state(state)

    if entries:
        subprocess.run(["git", "pull", "-q"], cwd=HUB, env=GIT_ENV, capture_output=True)
        with open(REVIEW_LOG, "a") as f:
            f.writelines(entries)
        c = subprocess.run([str(BIN / "commit-hub-files.sh"), f"consolidate: review-log {TODAY}", "review-log.md"],
                           env=GIT_ENV, capture_output=True, text=True)
        log("review-log.md " + ("已 commit+push" if c.returncode == 0 else f"commit/push 失敗:{c.stdout}{c.stderr}"))
    log(f"=== consolidate end(失敗 {failed} 個)===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
