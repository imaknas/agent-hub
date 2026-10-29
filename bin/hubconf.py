"""平台設定與專案登錄的唯一入口(只用標準函式庫;平台的 Python 是 bin/py,最低 3.13)。

新程式一律從這裡拿路徑、名稱、owner,不要再寫死 ~/agent-hub、~/services、專案名稱。
  - hub 根目錄:這個檔案所在的 bin/ 的上一層;環境變數 AGENT_HUB_ROOT 可以覆蓋
  - 設定:<config>/hub.json(實例目錄,見下)
  - 實例目錄(docs/design/new-user.md 3.1):使用者自己的東西分三塊——config(hub.json、登錄、models.env…;
    config repo)、data(digest、todo、review-log、reflect…;不進 git、靠備份)、secrets。位置由**一個指標檔**決定:
    <hub>/.hub-home 寫著實例目錄的路徑 → <實例>/config、<實例>/data、<實例>/secrets;沒有這個檔 = 舊的擺法
    (三塊都是 hub 根目錄、secrets 是 <hub>/.secrets),也就是搬家之前的樣子。搬家時最後才寫指標檔,刪掉就退回。
    個別覆蓋:AGENT_HUB_HOME(= 指標檔的內容)、AGENT_HUB_CONFIG、AGENT_HUB_DATA、AGENT_HUB_SECRETS(例:config repo
    的 pre-commit 把暫存區匯出到暫存目錄,只有 config 指過去)。
    .state、.logs 不在實例裡:跟匯流排(bus_dir)、平台的 Python 一樣是每台機器一份的執行狀態,留在 hub 根目錄
    (.logs 的路徑寫在每個服務的 plist 裡,搬它等於重啟所有服務)。
  - 平台自己的登錄(<hub>/projects/agent-hub.yaml)是平台的一部分:registry 不管 registry_dir 在哪都會讀它
  - 專案登錄:<registry_dir>/<name>.yaml(人改的 YAML),scheduler 每個 tick 用 registry.py
    驗證後寫成 <hub>/.state/registry/index.json——這裡讀的是那份 JSON(不需要 PyYAML);
    還沒產生的話(新機器、MBP)退回 roster():直接從 YAML 檔讀名稱跟 paths

給 shell 腳本用("$HUB/bin/py" hubconf.py …):
  hubconf.py ideas     想法沙盒(登錄 type: idea)的名稱,一行一個
  hubconf.py get <key> hub.json 的單一值(點分隔,例:github.owner),給 shell 腳本用
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
POINTER = ROOT / ".hub-home"
_cfg: dict | None = None


def instance_home() -> pathlib.Path | None:
    """實例目錄;None = 舊的擺法(全部在 hub 根目錄)。"""
    v = os.environ.get("AGENT_HUB_HOME")
    if not v and POINTER.is_file():
        v = POINTER.read_text().strip()
    return pathlib.Path(os.path.expanduser(v)) if v else None


def _part(env: str, sub: str, legacy: pathlib.Path) -> pathlib.Path:
    if os.environ.get(env):
        return pathlib.Path(os.path.expanduser(os.environ[env]))
    home = instance_home()
    return home / sub if home else legacy


def config_dir() -> pathlib.Path:
    """config(hub.json、登錄、models.env、mirror-list.txt、services.tsv、schedule.yaml)所在。"""
    return _part("AGENT_HUB_CONFIG", "config", ROOT)


def data_dir() -> pathlib.Path:
    """data(digest/、reflect/、todo.md、index.md、review-log.md、openclaw-migration-status.md、legacy-paths.tsv)所在。"""
    return _part("AGENT_HUB_DATA", "data", ROOT)


def config_file(name: str) -> pathlib.Path:
    return config_dir() / name


def data_file(name: str) -> pathlib.Path:
    return data_dir() / name


def config() -> dict:
    global _cfg
    if _cfg is None:
        p = config_file("hub.json")
        _cfg = json.loads(p.read_text()) if p.exists() else {}
    return _cfg


def path(value: str | None, base: pathlib.Path | None = None) -> pathlib.Path | None:
    """~ 展開;相對路徑以 base(預設 hub.json 所在的 config 目錄;舊擺法就是 hub 根目錄)為準。
    `.secrets/<名稱>` 一律指平台的憑證目錄(secrets_dir()),不管 hub.json 放在哪。"""
    if not value:
        return None
    if base is None and value.startswith(".secrets/"):
        return secrets_dir() / value[len(".secrets/"):]
    p = pathlib.Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base or config_dir()) / p


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


def secrets_dir() -> pathlib.Path:
    """平台的憑證目錄(不進版控、每晚加密備份)。程式都從這裡拿,不自己拼路徑。"""
    return _part("AGENT_HUB_SECRETS", "secrets", legacy_secrets_dir())


def legacy_secrets_dir() -> pathlib.Path:
    """搬家前的憑證位置(<hub>/.secrets)。搬家只複製、確認沒問題才刪,這段期間它還有真的憑證——
    sandbox 的讀取封鎖要連它一起擋(policy.secret_dirs())。"""
    return ROOT / ".secrets"


def host(role: str) -> str:
    """機器角色 → ssh 名稱;"" = 就是這台(一台機器全包)。目前只有一個角色 server:跑 hub 的那台(memory bare repo、
    排程器、workspace 都在它上面)。另一台(遙控用的筆電)上的腳本要 ssh 過去,在 server 本機的就直接做。
    hosts.server 沒寫時沿用舊的 memory_host。"""
    if role != "server":
        raise KeyError(role)
    return get("hosts.server") or get("memory_host") or ""


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

STATE = pathlib.Path(os.environ["AGENT_HUB_STATE"]) if os.environ.get("AGENT_HUB_STATE") else ROOT / ".state"   # 測試用另一個
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


def platform_registry() -> pathlib.Path:
    """平台自己的登錄(核心的 job、服務、憑證宣告、契約):是平台的一部分,在平台 repo 裡,不在使用者的 registry_dir。"""
    return ROOT / "projects" / f"{hub_project()}.yaml"


def owner_address() -> str:
    return get("owner.address", "owner")


def owner_addresses() -> set[str]:
    """owner 的地址跟別名(舊的信用 knas,兩個都收)。"""
    return {owner_address(), *get("owner.aliases", [])}


def owner_tag() -> str:
    return get("owner.tag", "Owner")


def owner_side() -> str:
    """Principal Handoff 的 principal 端是誰(hub.json owner_side):assistant = hub.json assistant 設定的助理(bridge 跟它的 API 交換);
    builtin = 平台內建的最小 owner 端(bin/owner.py)。沒寫:有 assistant.api 就是 assistant,否則 builtin。"""
    v = get("owner_side")
    if v in ("assistant", "builtin"):
        return v
    return "assistant" if get("assistant.api") else "builtin"


def party(role: str, project: str | None = None) -> str:
    """Principal Handoff 的參與者識別字(規範 core §1、R6;記錄的 by、authority.decider 都用這個)。
    principal / principal_side 從 hub.json 讀(owner.principal_id、assistant.party_id);requester 跟 agent 是
    project:<專案>(要給 project);registrar 是 <平台>:registrar(一個 registrar 服務所有專案)。"""
    if role == "principal":
        return get("owner.principal_id") or f"person:{owner_address()}"
    if role == "principal_side":
        if owner_side() == "builtin":
            return f"{hub_project()}:owner-side"
        return get("assistant.party_id") or get("assistant.project") or "assistant"
    if role in ("requester", "agent"):
        if not project:
            raise ValueError(f"party({role!r}) 要給 project")
        return f"project:{project}"
    if role == "registrar":
        return f"{hub_project()}:registrar"
    raise ValueError(f"不認得的角色 {role!r}")


def former_names(project: str) -> list[str]:
    """這個專案改名前用過的名稱(照 .state/project-renames.json,只算改完的 stage done)。匯流排上舊的信、助理寄來的
    record 可能還用舊名稱(例:idea-1 升級改名成 project-33)。"""
    try:
        renames = json.loads((STATE / "project-renames.json").read_text())
    except (OSError, ValueError):
        return []

    def now(name: str) -> str:
        seen = set()
        while name in renames and name not in seen and (renames[name] or {}).get("stage") == "done":
            seen.add(name)
            name = renames[name]["new"]
        return name
    return sorted(old for old in renames if old != project and now(old) == project)


def principal_is_legal_person() -> bool:
    return bool(get("owner.legal_person", False))


def assistant_name() -> str:
    """個人助理給人看的名字(hub.json assistant.name);沒設定就是通稱。"""
    return get("assistant.name") or "個人助理"


def fill_names(text: str) -> str:
    """樣板裡的 {{owner}}(待辦標籤、對人的稱呼)、{{owner_alias}}(hubbus send --to 的地址)、{{assistant}} 換成設定的值。
    templates/ 底下給 agent 看的說明都經過這裡,樣板本身不寫人名。"""
    aliases = get("owner.aliases") or []
    return (text.replace("{{owner}}", owner_tag())
                .replace("{{owner_alias}}", aliases[0] if aliases else owner_address())
                .replace("{{assistant}}", assistant_name()))


_PATHS_LINE = re.compile(r"^\s+(\w+):\s*(.*?)\s*(?:#.*)?$")


def roster() -> dict[str, dict]:
    """登錄裡有哪些專案(平台自己除外)→ 它的 paths(原始字串,~ 沒展開)。

    直接讀 <registry_dir>/*.yaml 的檔名跟 `paths:` 區塊:不驗證、不需要 PyYAML、不靠 .state 的快取,
    所以新機器(recover)、MBP 都能用。要驗證過的完整登錄用 projects()。"""
    out = {}
    d = registry_dir()
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        if f.stem != hub_project():
            out[f.stem] = roster_paths(f.read_text())
    return out


def roster_paths(text: str) -> dict:
    """一個登錄檔的 `paths:` 區塊(逐行解析,只認 block 格式;registry.py 驗證時會比對跟 YAML 讀到的一樣)。"""
    paths, inside = {}, False
    for line in text.splitlines():
        if line.split("#")[0].rstrip() == "paths:":
            inside = True
        elif inside and (m := _PATHS_LINE.match(line)):
            paths[m.group(1)] = m.group(2).strip("'\"") or None
        elif inside and line.strip() and not line.lstrip().startswith("#"):
            break
    return paths


_TYPE_LINE = re.compile(r"^type:\s*['\"]?(\w+)['\"]?\s*(?:#.*)?$")


def ideas() -> list[str]:
    """登錄 `type: idea` 的專案(想法沙盒)。跟 roster() 一樣逐行讀頂層的 `type:`,不需要 PyYAML。"""
    d = registry_dir()
    out = []
    for f in sorted(d.glob("*.yaml")) if d and d.is_dir() else []:
        for line in f.read_text().splitlines():
            if (m := _TYPE_LINE.match(line)):
                if m.group(1) == "idea":
                    out.append(f.stem)
                break
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


def capabilities() -> dict[str, dict]:
    """平台有的能力(docs/design/capabilities.md):hub.json 的 capabilities,而且 capabilities/<名稱>.sb 存在。"""
    cat = {k: v for k, v in (get("capabilities") or {}).items() if not k.startswith("_") and isinstance(v, dict)}
    return {k: v for k, v in cat.items() if (ROOT / "capabilities" / f"{k}.sb").is_file()}


def code_stamp() -> float:
    """bin/ 底下的程式最後一次被改的時間。常駐服務啟動時記下,之後變了就找空檔自己結束、讓 launchd 重啟——
    不然它載入的舊模組會跟磁碟上的新模組混用(2026-10-25 lease-runner、cap-runner 都因此壞過一次)。"""
    stamps = [f.stat().st_mtime for f in (ROOT / "bin").iterdir() if f.is_file()]
    return max(stamps) if stamps else 0.0


def hub_project() -> str:
    """平台自己在登錄裡的名稱。"""
    return "agent-hub"


def main(argv: list[str]) -> int:
    if argv == ["roster"]:
        server = host("server")
        for name, paths in roster().items():
            bare = paths.get("memory_bare") or f"~/git-origin/{name}-memory.git"
            print("\t".join([name, str(path(paths.get("workspace")) or ""), paths.get("origin") or "(native)",
                             f"{server}:{bare}" if server else bare]))
        return 0
    if len(argv) == 2 and argv[0] == "get":
        v = get(argv[1])
        if v is None or isinstance(v, (dict, list)):
            print(f"hubconf.py get:{argv[1]} 沒有設定或不是單一值", file=sys.stderr)
            return 1
        print(v)
        return 0
    if len(argv) == 2 and argv[0] == "dir" and argv[1] in ("config", "data", "secrets", "state", "logs"):
        print({"config": config_dir, "data": data_dir, "secrets": secrets_dir}.get(argv[1], lambda: None)()
              or (STATE if argv[1] == "state" else LOGS))
        return 0
    if len(argv) == 2 and argv[0] == "host":  # 印 ssh 名稱;一台機器全包時印空行
        print(host(argv[1]))
        return 0
    if len(argv) == 2 and argv[0] == "path":  # shell 腳本拿展開過的路徑(例:memory_copies),不要自己寫死
        v = p(argv[1])
        if v is None:
            print(f"hubconf.py path:{argv[1]} 沒有設定", file=sys.stderr)
            return 1
        print(v)
        return 0
    if argv == ["ideas"]:
        for name in ideas():
            print(name)
        return 0
    print("用法:hubconf.py roster | ideas | get <key> | path <key> | host server | dir config|data|secrets|state|logs", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
