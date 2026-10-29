"""隱私路由:哪些內容能送到哪裡的模型(docs/design/llm-providers.md「隱私」)。規則只寫在這裡;擋在 llm.py(唯一的出口)。

- 判斷點宣告 `material`(contracts/llm-points.yaml):cloud_safe = 哪裡都可以;personal = 看專案;local_only = 一律只准本機。
  sensitive 現在跟 personal 一樣(還沒有點需要更嚴的)。
- 專案登錄 `privacy.memory: cloud_ok | local_only`(預設 cloud_ok;平台管的安全欄位,manifest.update 改不了)。
  local_only = 平台叫的模型不准把這個專案的 memory、STATE.md、程式送到雲端。跟 `privacy.reflection`(對話內容進不進週報)無關。
- 模型在哪:completion 點交給 hub.json llm.providers 時看那家的 `location`(local 只准本機/私有網段,config check 擋);
  其他看執行環境的 `LOCATION`(轉接器宣告;沒宣告 = cloud,平台確認不了的一律當雲端)。
- 要本機卻只有雲端 → 不跑,說原因(llm.py 走 on_failure),**不退回雲端**。有本機的 provider 有那一組模型就自動改用它。
- 跨專案的內容(librarian-index 的 digest)送雲端前拿掉 local_only 專案的段落跟提到它的行(cloud_view)。

注意:librarian 的 agent 點(librarian-state 等)跑在雲端的 coding agent 上,所以 local_only 的專案在換成 completion 點 +
本機 provider 之前,STATE.md 不會被自動更新(會在 log、digest 說明)。
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402


def point_spec(point: str) -> dict:
    """判斷點的宣告(contracts/llm-points.yaml)。"""
    import yaml
    points = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "llm-points.yaml"   # 跟這支程式同一份平台
    return (yaml.safe_load(points.read_text())["points"].get(point)) or {}


def local_only(project: str | None) -> bool:
    """這個專案的 privacy.memory 是 local_only。直接讀登錄檔,不靠快取(快取舊了不能變成「沒有限制」);
    檔案在但讀不懂 → 當成 local_only(寧可不送)。"""
    if not project:
        return False
    f = hubconf.platform_registry() if project == hubconf.hub_project() else hubconf.registry_dir() / f"{project}.yaml"
    try:
        text = f.read_text()
    except OSError:
        return False
    try:
        import yaml
        doc = yaml.safe_load(text) or {}
    except Exception:
        return True
    return isinstance(doc, dict) and (doc.get("privacy") or {}).get("memory") == "local_only"


def local_only_projects() -> set[str]:
    d = hubconf.registry_dir()
    names = {f.stem for f in d.glob("*.yaml")} if d and d.is_dir() else set()
    return {n for n in names if local_only(n)}


def project_for(path: str | pathlib.Path | None) -> str | None:
    """工作目錄 → 專案(登錄的 workspace 或 memory 副本底下);認不出來 = None。"""
    if not path:
        return None
    p = pathlib.Path(path).expanduser().resolve()
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    for name, paths in hubconf.roster().items():
        for root in (hubconf.path(paths.get("workspace")) if paths.get("workspace") else None,
                     copies / name if copies else None):
            if root and (p == root.resolve() or root.resolve() in p.parents):
                return name
    return None


def needs_local(spec: dict, project: str | None) -> str | None:
    """這次呼叫只准本機模型的原因;None = 雲端也可以。"""
    material = spec.get("material") or "personal"
    if material == "cloud_safe":
        return None
    if material == "local_only":
        return "這個判斷點的內容是 local_only"
    if local_only(project):
        return f"{project} 的 privacy.memory 是 local_only"
    return None


def target_location(point: str, spec: dict, require_local: bool = False) -> tuple[str, str]:
    """(local | cloud, 交給誰)。completion 點有 llm.providers → 那家(要本機時改挑本機的那家);其他 → 執行環境。"""
    import completion
    import runtime_ports
    if spec.get("shape") == "completion" and completion.settings():
        try:
            t = completion.resolve(point, spec, require_local=require_local)
            return t["location"], f"llm.providers.{t['provider']}"
        except completion.Refused as e:
            return "cloud", str(e)
    name = spec.get("runtime") or runtime_ports.default_name()
    return runtime_ports.location(name), f"執行環境 {name}"


def refusal(point: str, spec: dict, project: str | None) -> str | None:
    """要本機卻只能送雲端 → 給人看的原因;可以送 → None。"""
    why = needs_local(spec, project)
    if not why:
        return None
    loc, where = target_location(point, spec, require_local=True)
    if loc == "local":
        return None
    return f"隱私:{why},{where} 在雲端,不送(要跑就加一家 location: local 的 provider,或改登錄的 privacy.memory)"


def cloud_view(text: str, hidden: set[str]) -> str:
    """要送雲端的跨專案文字:拿掉 `## <專案>` 整段跟其他提到那些專案的行。"""
    if not hidden:
        return text
    out, skip = [], False
    mention = re.compile("|".join(rf"(?<![\w-]){re.escape(n)}(?![\w-])" for n in sorted(hidden)))
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            skip = line[3:].strip() in hidden
        elif line.startswith("<!--") or line.startswith("# "):
            skip = False
        if skip or (not line.startswith("<!--") and mention.search(line)):
            continue
        out.append(line)
    return "".join(out)
