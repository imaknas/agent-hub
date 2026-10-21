"""平台設定與專案登錄的唯一入口(只用標準函式庫,系統的 python3 3.9 也能 import)。

新程式一律從這裡拿路徑、名稱、owner,不要再寫死 ~/agent-hub、~/services、專案名稱。
  - hub 根目錄:這個檔案所在的 bin/ 的上一層;環境變數 AGENT_HUB_ROOT 可以覆蓋
  - 設定:<hub>/hub.json
  - 專案登錄:<registry_dir>/<name>.yaml(人改的 YAML),scheduler 每個 tick 用 registry.py
    驗證後寫成 <hub>/.state/registry/index.json——這裡讀的是那份 JSON(不需要 PyYAML);
    還沒產生的話退回舊的 onboarded-projects.tsv(切換期間)
"""
from __future__ import annotations

import json
import os
import pathlib

ROOT = pathlib.Path(os.environ.get("AGENT_HUB_ROOT") or pathlib.Path(__file__).resolve().parents[1])
_cfg: dict | None = None


def config() -> dict:
    global _cfg
    if _cfg is None:
        p = ROOT / "hub.json"
        _cfg = json.loads(p.read_text()) if p.exists() else {}
    return _cfg


def path(value: str | None, base: pathlib.Path | None = None) -> pathlib.Path | None:
    """~ 展開;相對路徑以 base(預設 hub 根目錄)為準。"""
    if not value:
        return None
    p = pathlib.Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base or ROOT) / p


def get(key: str, default=None):
    """點分隔的 key:get("owner.tag")。"""
    cur = config()
    for k in key.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def p(key: str, default: str | None = None) -> pathlib.Path | None:
    return path(get(key, default))


# launchd 底下的 PATH(plist 沒寫 PATH 的時候):指令解析要照這個,不是跑檢查的那個 shell 的 PATH
LAUNCHD_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

STATE = ROOT / ".state"
LOGS = ROOT / ".logs"
REGISTRY_INDEX = STATE / "registry" / "index.json"


def registry_dir() -> pathlib.Path:
    return p("registry_dir", "projects")


def owner_address() -> str:
    return get("owner.address", "owner")


def owner_addresses() -> set[str]:
    """owner 的地址跟別名(舊的信用 knas,兩個都收)。"""
    return {owner_address(), *get("owner.aliases", [])}


def owner_tag() -> str:
    return get("owner.tag", "Owner")


def projects() -> dict[str, dict]:
    """名稱 → 驗證過的登錄(dict)。登錄還沒產生就用 onboarded-projects.tsv 湊最小的欄位。"""
    if REGISTRY_INDEX.exists():
        try:
            return json.loads(REGISTRY_INDEX.read_text())["projects"]
        except (ValueError, KeyError):
            pass
    out = {}
    tsv = ROOT / "onboarded-projects.tsv"
    if tsv.exists():
        for line in tsv.read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                out[cols[0]] = {"name": cols[0], "paths": {"workspace": cols[1],
                                "origin": cols[2] if len(cols) > 2 else None}}
    return out


def reflection_private() -> set[str] | None:
    """privacy.reflection 不是 include 的專案(stats_only / exclude):它們的對話內容(連摘要)不送雲端模型。
    登錄還沒產生、讀不到時回傳 None——呼叫的人要當成「全部都不能送內容」,不是「沒有限制」。"""
    if not REGISTRY_INDEX.exists():
        return None
    try:
        projs = json.loads(REGISTRY_INDEX.read_text())["projects"]
    except (ValueError, KeyError):
        return None
    return {n for n, d in projs.items() if (d.get("privacy") or {}).get("reflection", "include") != "include"}


def workspace(name: str) -> pathlib.Path | None:
    if name == hub_project():
        return ROOT
    info = projects().get(name)
    ws = (info or {}).get("paths", {}).get("workspace")
    return path(ws) if ws else None


def hub_project() -> str:
    """平台自己在登錄裡的名稱。"""
    return "agent-hub"
