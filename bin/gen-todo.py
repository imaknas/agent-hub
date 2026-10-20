#!/usr/bin/env python3
"""從各專案 STATE.md 的 `## 待辦` 段落,機械產生跨專案 todo.md。不用 LLM。

來源格式(librarian 維護 STATE.md 時照這個寫,agent-hub 自己的待辦寫在
openclaw-migration-status.md):

    ## 待辦
    - [ ] 〔Knas〕只有 Knas 能做的事(帳號、密碼、付款、決定)
    - [ ] 〔agent〕agent 可以做的事
    - [ ] 〔待定〕還要先做決定的事
    - (無)                       ← 沒有待辦時

也接受 `### 〔Knas〕` 小標題底下放 `- [ ] …` 的分組寫法。段落裡的說明文字、參考連結略過;
只有「看起來是待辦卻沒標負責人」或「已打勾還留著」的行算格式問題。

輸入一樣、輸出就一樣:todo.md 只在某個專案的待辦真的變了才會變。格式不對的
專案(有實質內容卻沒有 `## 待辦`、或行不合格式)列在檔尾「格式問題」,
不會被默默漏掉。

跑在 mini(librarian 呼叫,讀 ~/.memory-copies/*/STATE.md)。
用法:gen-todo.py <輸出 todo.md 路徑>
結束碼:0 正常;格式問題不算失敗(已寫進檔案跟 stdout)。
"""
import pathlib
import re
import sys

HOME = pathlib.Path.home()
HUB = HOME / "agent-hub"
MANIFEST = HUB / "onboarded-projects.tsv"
MIGRATION = HUB / "openclaw-migration-status.md"
PROJECTS = HOME / ".memory-copies"

OWNERS = ("Knas", "待定", "agent")
ITEM = re.compile(r"^- \[ \] 〔(Knas|agent|待定)〕\s*(.+)$")
GROUP = re.compile(r"^#{3,4}\s*〔(Knas|agent|待定)〕\s*$")
BARE = re.compile(r"^- \[ \] (?!〔)(.+)$")
PLACEHOLDER_MAX_BYTES = 200  # onboard 時的佔位 STATE.md,本來就沒有待辦


def todo_section(text: str) -> list[str] | None:
    """回傳 `## 待辦` 段落的非空行;沒有這個段落就回傳 None。"""
    m = re.search(r"^## 待辦[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return None
    return [l.rstrip() for l in m.group(1).splitlines()
            if l.strip() and not l.lstrip().startswith(">")]


def parse(source: str, text: str, items: dict, problems: list) -> None:
    lines = todo_section(text)
    if lines is None:
        problems.append(f"{source}:沒有 `## 待辦` 段落")
        return
    group = None  # 另一種也接受的寫法:`### 〔Knas〕` 小標題底下放不帶負責人的 `- [ ] …`
    for line in lines:
        if line.strip() == "- (無)":
            continue
        h = GROUP.match(line)
        if h:
            group = h.group(1)
            continue
        m = ITEM.match(line)
        if m:
            items[m.group(1)].append((source, m.group(2).strip()))
            continue
        bare = BARE.match(line)
        if bare and group:
            items[group].append((source, bare.group(1).strip()))
        elif bare or line.startswith("- [x]") or line.startswith("- [X]"):
            # 看起來是待辦,卻沒有負責人(或已打勾還留著):一定要讓人看到,不能默默漏掉
            problems.append(f"{source}:待辦沒標負責人或已完成未移除 `{line[:60]}`")
        # 其他行(說明文字、「詳見 reference」連結、續行)不是待辦,略過


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    items: dict[str, list] = {o: [] for o in OWNERS}
    problems: list[str] = []

    names = [l.split("\t")[0] for l in MANIFEST.read_text().splitlines()[1:] if l.strip()]
    for name in names:
        state = PROJECTS / name / "STATE.md"
        if not state.exists():
            problems.append(f"{name}:mini 上沒有 STATE.md")
            continue
        if state.stat().st_size <= PLACEHOLDER_MAX_BYTES:
            continue
        parse(name, state.read_text(), items, problems)
    if MIGRATION.exists():
        parse("agent-hub", MIGRATION.read_text(), items, problems)

    total = sum(len(v) for v in items.values())
    o = [
        "# 跨專案待辦", "",
        # 標題不放日期:內容沒變時檔案就不該變(要知道哪天產生的,看 git log)
        f"> 由 `bin/gen-todo.py` 從各專案 STATE.md 的 `## 待辦` 段落機械產生(共 {total} 項),",
        "> 不要手動改——要改內容,改來源(各專案的 memory,librarian 會更新 STATE.md;",
        "> agent-hub 自己的待辦在 `openclaw-migration-status.md`)。", "",
    ]
    # muninn 用這三個標題判斷待辦是誰的(對照表在它的 data/config.yaml external_todo_owners);改標題要一起改那邊
    titles = {"Knas": "只有 Knas 能做", "待定": "待定(需要先做決定)", "agent": "agent 可以做"}
    for owner in OWNERS:
        o += [f"## {titles[owner]}", ""]
        rows = items[owner]
        if not rows:
            o += ["(無)", ""]
            continue
        by_proj: dict[str, list[str]] = {}
        for proj, text in rows:
            by_proj.setdefault(proj, []).append(text)
        for proj in sorted(by_proj):
            o.append(f"### {proj}")
            o += [f"- [ ] {t}" for t in by_proj[proj]]
            o.append("")
    if problems:
        o += ["## 格式問題(這些專案的待辦沒被收進來)", ""]
        o += [f"- {p}" for p in problems]
        o.append("")
    out.write_text("\n".join(o).rstrip() + "\n")
    print(f"todo.md: {total} 項" + (f",{len(problems)} 個格式問題" if problems else ""))
    for p in problems:
        print(f"  格式問題:{p}")


if __name__ == "__main__":
    main()
