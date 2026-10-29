"""模型回的編輯清單 → 新的文字(docs/design/llm-providers.md「模型回編輯清單,不是整份重寫」)。

completion 形狀的判斷點不碰檔案:模型回 `[{"old": 原文的一段, "new": 換成什麼}, …]`,平台照順序套用,寫哪個檔是平台決定的。
規則(構造上保證「其他行逐字不動」):
  - 每個 old 不能是空的,而且在**套用到這一步時**的文字裡剛好出現一次(出現零次或好幾次 = 這個清單不採用)
  - 套用完,受保護的標記(例:〔owner〕待辦行尾的「(等 owner:task-…)」)一個都不能少、不能多——標記由 registrar 管,
    模型只能整行刪掉(那一項做完了),不能改 key;要刪整行時那個 key 就從「應該在」裡拿掉(呼叫端決定)
標準函式庫。
"""
from __future__ import annotations

import re


class EditError(Exception):
    pass


def apply(text: str, edits: list) -> str:
    """照順序套用;任何一個不合規就丟 EditError(整份不採用,不套一半)。"""
    out = text
    for i, e in enumerate(edits or []):
        if not isinstance(e, dict) or not isinstance(e.get("old"), str) or not isinstance(e.get("new"), str):
            raise EditError(f"第 {i + 1} 個編輯不是 {{old, new}}")
        old, new = e["old"], e["new"]
        if not old:
            raise EditError(f"第 {i + 1} 個編輯的 old 是空的")
        n = out.count(old)
        if n != 1:
            raise EditError(f"第 {i + 1} 個編輯的 old 出現 {n} 次(要剛好一次):{old[:60]!r}")
        out = out.replace(old, new, 1)
    return out


def markers(text: str, pattern: re.Pattern) -> list[str]:
    return [m.group(0) for m in pattern.finditer(text)]


def check_markers(before: str, after: str, pattern: re.Pattern, *, removable: bool = True) -> list[str]:
    """標記不能被改寫或憑空多出來;removable = 整行刪掉(那一項做完)可以。回傳問題(空 = 沒問題)。"""
    b, a = markers(before, pattern), markers(after, pattern)
    probs = [f"多出標記:{m}" for m in a if m not in b]
    if not removable:
        probs += [f"少了標記:{m}" for m in b if m not in a]
    else:
        # 刪掉的標記,它那一整行都要不見了(只刪標記、留下文字 = 改寫,不是做完)
        lines_after = after.splitlines()
        for m in b:
            if m not in a:
                line = next((l for l in before.splitlines() if m in l), "")
                body = line.replace(m, "").strip()
                if body and any(body == l.strip() or body in l for l in lines_after):
                    probs.append(f"標記被拿掉但那一行還在(改寫了 key):{line.strip()[:80]}")
    return probs
