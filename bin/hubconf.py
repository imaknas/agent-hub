"""平台設定與專案登錄的唯一入口(只用標準函式庫,系統的 python3 3.9 也能 import)。

新程式一律從這裡拿路徑、名稱、owner,不要再寫死 ~/agent-hub、~/services、專案名稱。
  - hub 根目錄:這個檔案所在的 bin/ 的上一層;環境變數 AGENT_HUB_ROOT 可以覆蓋
  - 設定:<hub>/hub.json
  - 專案登錄:<registry_dir>/<name>.yaml(人改的 YAML),scheduler 每個 tick 用 registry.py
    驗證後寫成 <hub>/.state/registry/index.json——這裡讀的是那份 JSON(不需要 PyYAML);
    還沒產生的話(新機器、MBP)退回 roster():直接從 YAML 檔讀名稱跟 paths

給 shell 腳本用(任何 python3 都能跑):
  hubconf.py roster    每個專案一行 name<TAB>workspace<TAB>origin<TAB>memory_bare,沒有標題列;
                       workspace 展開 ~、沒有 origin 印 (native)、memory_bare 前面加 hub.json 的 memory_host:
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

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


def nvm_bin() -> pathlib.Path | None:
    """nvm 預設版本(~/.nvm/alias/default,例:24、lts/*、node、v24.19.0)對到的已安裝版本裡最新的 bin/。

    node(跟裝在它底下的 claude)只由 nvm 管(2026-10-24 Knas 決定;Homebrew 的 node 被 autoremove 刪掉、
    LINE bot 停了一天半之後)。背景服務跟排程沒有 shell 的 nvm 設定,靠這裡換算成完整路徑;nvm 升級後
    下一個 render-services 會跟著換、重啟服務。"""
    nvm = pathlib.Path(os.environ.get("NVM_DIR") or pathlib.Path.home() / ".nvm")
    versions = nvm / "versions" / "node"
    if not versions.is_dir():
        return None
    spec = "default"
    for _ in range(5):  # alias 可以指到另一個 alias(lts/* → lts/jod → v22…)
        f = nvm / "alias" / spec
        if not f.is_file():
            break
        spec = f.read_text().strip()

    def ver(p: pathlib.Path) -> tuple:
        return tuple(int(x) for x in p.name.lstrip("v").split(".") if x.isdigit())
    installed = sorted((p for p in versions.iterdir() if (p / "bin" / "node").exists()), key=ver)
    if spec not in ("node", "stable"):
        want = tuple(int(x) for x in spec.lstrip("v").split(".") if x.isdigit())
        installed = [p for p in installed if want and ver(p)[:len(want)] == want]
    return installed[-1] / "bin" if installed else None


# launchd 底下的 PATH(plist 沒寫 PATH 的時候,render-services 也把這個寫進 plist):指令解析要照這個,
# 不是跑檢查的那個 shell 的 PATH。nvm 預設版本的 bin 放最前面(node、claude)。
LAUNCHD_PATH = ":".join([*([str(nvm_bin())] if nvm_bin() else []), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])


def expand_env(value: str) -> str:
    """登錄 service 的 env 值裡的 $PLATFORM_PATH 換成 LAUNCHD_PATH(要 Homebrew 又要 nvm 的 node/claude 時用)。"""
    return value.replace("$PLATFORM_PATH", LAUNCHD_PATH)


def tool(name: str) -> str:
    """無人值守的程式要跑的指令(claude 等)的完整路徑:先照目前的 PATH,再照 LAUNCHD_PATH;找不到就原樣回傳。"""
    import shutil
    return shutil.which(name, path=f"{os.environ.get('PATH', '')}:{LAUNCHD_PATH}") or name

STATE = ROOT / ".state"
LOGS = ROOT / ".logs"
REGISTRY_INDEX = STATE / "registry" / "index.json"


# 事件匯流排有自己的目錄(hub.json bus_dir):第二階段的 sandbox 只對這個目錄開寫入(session 寄信、hook 收信都要寫)。
# 不放在 hub 目錄底下:專案的權限規則 deny 寫 ~/agent-hub/**,而 deny 會蓋過 sandbox 的 allowWrite。
BUS_DIR = p("bus_dir", ".state/bus")
BUS_LOG = BUS_DIR / "hubbus.log"
_OLD_BUS = [STATE / "bus" / "bus.db", STATE / "bus.db"]  # 以前放過的位置(依序找)


def bus_db() -> pathlib.Path:
    """匯流排的 SQLite 檔。新位置還沒有、舊位置有 → 搬過來:一個連線拿 RESERVED 鎖(BEGIN IMMEDIATE)
    擋住其他寫入,另一個連線用 backup API 讀出來(同一個連線鎖著自己再 backup 會卡死),改名之後才放鎖。"""
    new = BUS_DIR / "bus.db"
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    old = next((o for o in _OLD_BUS if o.exists() and o != new), None)
    if new.exists() or old is None:
        return new
    import sqlite3
    lock = sqlite3.connect(old, timeout=60, isolation_level=None)
    try:
        lock.execute("BEGIN IMMEDIATE")
        if not new.exists():  # 另一個程序可能剛搬完
            tmp = BUS_DIR / "bus.db.migrating"
            src, dst = sqlite3.connect(old, timeout=60), sqlite3.connect(tmp)
            src.backup(dst)
            dst.close()
            src.close()
            tmp.replace(new)
            for suffix in ("", "-wal", "-shm"):
                f = pathlib.Path(str(old) + suffix)
                if f.exists():
                    f.rename(pathlib.Path(str(old.parent / "bus.db.moved") + suffix))
        lock.execute("ROLLBACK")
    finally:
        lock.close()
    oldlog = old.parent / "hubbus.log" if old.parent.name == "bus" else LOGS / "hubbus.log"
    if oldlog.exists() and not oldlog.is_symlink() and not BUS_LOG.exists():
        oldlog.rename(BUS_LOG)
    link = LOGS / "hubbus.log"  # 還在讀 .logs/hubbus.log 的(其他 session 的監看)照樣讀得到
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        link.symlink_to(BUS_LOG)
    return new


def registry_dir() -> pathlib.Path:
    return p("registry_dir", "projects")


def owner_address() -> str:
    return get("owner.address", "owner")


def owner_addresses() -> set[str]:
    """owner 的地址跟別名(舊的信用 knas,兩個都收)。"""
    return {owner_address(), *get("owner.aliases", [])}


def owner_tag() -> str:
    return get("owner.tag", "Owner")


_PATHS_LINE = re.compile(r"^\s+(\w+):\s*(.*?)\s*(?:#.*)?$")


def roster() -> dict[str, dict]:
    """登錄裡有哪些專案(平台自己除外)→ 它的 paths(原始字串,~ 沒展開)。

    直接讀 <registry_dir>/*.yaml 的檔名跟 `paths:` 區塊:不驗證、不需要 PyYAML、不靠 .state 的快取,
    所以新機器(recover)、MBP、系統的 python3 都能用。要驗證過的完整登錄用 projects()。"""
    out = {}
    d = registry_dir()
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        if f.stem == hub_project():
            continue
        paths, inside = {}, False
        for line in f.read_text().splitlines():
            if line.split("#")[0].rstrip() == "paths:":
                inside = True
            elif inside and (m := _PATHS_LINE.match(line)):
                paths[m.group(1)] = m.group(2).strip("'\"") or None
            elif inside and line.strip() and not line.lstrip().startswith("#"):
                break
        out[f.stem] = paths
    return out


def projects() -> dict[str, dict]:
    """名稱 → 驗證過的登錄(dict)。快取還沒產生(新機器、MBP)就用 roster() 湊最小的欄位。"""
    if REGISTRY_INDEX.exists():
        try:
            return json.loads(REGISTRY_INDEX.read_text())["projects"]
        except (ValueError, KeyError):
            pass
    return {name: {"name": name, "paths": paths} for name, paths in roster().items()}


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


def main(argv: list[str]) -> int:
    if argv == ["roster"]:
        host = get("memory_host")
        for name, paths in roster().items():
            bare = paths.get("memory_bare") or f"~/git-origin/{name}-memory.git"
            print("\t".join([name, str(path(paths.get("workspace")) or ""), paths.get("origin") or "(native)",
                             f"{host}:{bare}" if host else bare]))
        return 0
    print("用法:hubconf.py roster", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
