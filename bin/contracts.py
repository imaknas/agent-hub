"""契約的共用工具(標準函式庫):讀 contracts/ 底下的 JSON Schema 跟 markdown 契約,檢查實際資料。

- JSON 契約:`<名稱>.v<N>.schema.json`,用 minischema 驗證
- markdown 契約:`<名稱>.v<N>.md`,裡面 ```regex <名字>``` / ```heading <名字>``` 的區塊是機器讀的規則
tests/test_contracts.py(各 repo 自己的 ② 檢查)跟 bin/contracts-check.py(每晚的 ① 檢查)都用這支。
"""
from __future__ import annotations

import json
import pathlib
import re

import minischema

FENCE = re.compile(r"^```(regex|heading) (\S+)\n(.*?)\n```", re.M | re.S)


def md_rules(path: pathlib.Path) -> dict[str, list[str]]:
    """markdown 契約裡的 regex/heading 區塊:{名字: [每行一條]}。"""
    return {f"{kind}:{name}": body.splitlines() for kind, name, body in FENCE.findall(path.read_text())}


def md_version(text: str) -> tuple[str, int] | None:
    m = re.match(r"<!-- contract: (\S+) v(\d+) -->", text)
    return (m.group(1), int(m.group(2))) if m else None


def check_todo_md(text: str, contract: pathlib.Path) -> list[str]:
    rules = md_rules(contract)
    errs = []
    v = md_version(text)
    if v and v != ("todo-md", 1):
        errs.append(f"版本標記是 {v},不是 todo-md v1")
    headings = rules["heading:owner"]
    found = [l for l in text.splitlines() if l.startswith("## ")]
    owners = [h for h in found if h in headings]
    if owners != headings:
        errs.append(f"負責人標題要依序是 {headings},實際 {owners}")
    item, project = re.compile(rules["regex:item"][0]), re.compile(rules["regex:project"][0])
    section = None
    for i, l in enumerate(text.splitlines(), 1):
        if l.startswith("## "):
            section = l
        elif section in headings and l.startswith("- ") and not item.match(l):
            errs.append(f"第 {i} 行在負責人段落裡,但不是 `- [ ] …`:{l[:60]}")
        elif section in headings and l.startswith("###") and not project.match(l):
            errs.append(f"第 {i} 行的專案小標題格式不對:{l[:60]}")
    return errs


def check_state_todo(text: str, contract: pathlib.Path) -> list[str]:
    rules = md_rules(contract)
    item, group, bare = (re.compile(rules[f"regex:{k}"][0]) for k in ("item", "group", "bare"))
    lines = text.splitlines()
    try:
        start = lines.index("## 待辦") + 1
    except ValueError:
        return ["沒有 `## 待辦` 段落"]
    errs, in_group = [], False
    for l in lines[start:]:
        if l.startswith("## "):
            break
        if not l.strip() or l.strip() == "- (無)":
            continue
        if group.match(l):
            in_group = True
            continue
        if item.match(l) or (in_group and bare.match(l)):
            continue
        errs.append(f"不是待辦格式:{l[:60]}")
    return errs


def check_json(schema_path: pathlib.Path, data) -> list[str]:
    return minischema.errors(json.loads(schema_path.read_text()), data)


def same(a: pathlib.Path, b: pathlib.Path) -> bool:
    """兩份契約內容一樣(JSON 照解析後比,markdown 照文字比)。"""
    if a.suffix == ".json" and b.suffix == ".json":
        return json.loads(a.read_text()) == json.loads(b.read_text())
    return a.read_text() == b.read_text()
