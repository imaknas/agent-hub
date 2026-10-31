#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""第二階段:每個專案的 sandbox + 權限規則從專案登錄產生,寫進 `.claude/settings.local.json`(managed file)。

要寫什麼:policy.py 算中立的政策、runtime_claude.settings() 翻成 Claude Code 的格式;這支只管寫檔、記錄、偵測偏移。

只對登錄 `enforce: {sandbox: true}` 的專案寫;其他專案只算出「會寫什麼」(--report)。平台自己(agent-hub)不套。
寫的內容:
  sandbox(整個 key 由平台管):enabled、failIfUnavailable、allowUnsandboxedCommands: false、
    filesystem.allowWrite(自己的 runtime、匯流排目錄、工具 cache、登錄的 allow_write)、
    filesystem.denyRead(其他專案的 workspace/runtime、memory 原始筆記、平台的 secrets)、
    network.allowedDomains(平台預設 + 登錄的 network)
  permissions.deny / permissions.allow 裡平台加的那幾條(其他條不動——互動 session 按「一律允許」也寫在這裡):
    deny  Read/Edit 其他專案的 workspace/runtime、Edit 平台/bare repo/memory 副本/LaunchAgents、
          Read memory 副本的原始筆記、Read 平台 secrets
    allow 信箱 CLI、合併閘門(跨界的唯一通道)
  上一次寫了哪些條記在 .state/rendered-settings/<專案>.json,下次先拿掉再加,不會累積。

偏移偵測:實際檔案跟應該的不一樣(session 改了自己的設定、或有人手改)→ 印出來、exit 1(平台 job 失敗 →
狀態頁),而且馬上寫回去。

用法:render-project-settings.py [--report] [--only <專案>]
也是套用者(appliers.py):plan()/apply(),給 `bin/hub config plan/apply` 用。
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402

_ = i18n.domain(__file__)

RENDERED = hubconf.STATE / "rendered-settings"


def desired(name: str, doc: dict, index: dict) -> tuple[dict, list[str], list[str]]:
    """(sandbox, deny 規則, allow 規則):中立政策(policy.py)翻成 Claude Code 的設定(runtime_claude.py)。"""
    return runtime_claude.settings(policy.policy(name, doc, index))


def settings_path(doc: dict) -> pathlib.Path | None:
    ws = registry.dirs(doc)["workspace"]
    return runtime_claude.settings_path(ws) if ws else None


def compose(name: str, doc: dict, index: dict) -> tuple[pathlib.Path, dict, dict, dict, dict] | None:
    """純計算、不寫:(設定檔, 現在的內容, 應該的內容, 上次平台寫的紀錄, 這次要記的紀錄)。沒有 workspace 回 None。"""
    path = settings_path(doc)
    if not path:
        return None
    sandbox, deny, allow = desired(name, doc, index)
    cur = json.loads(path.read_text()) if path.exists() else {}
    prev = json.loads((RENDERED / f"{name}.json").read_text()) if (RENDERED / f"{name}.json").exists() else {}
    new = dict(cur)
    new["sandbox"] = sandbox
    p2 = dict(cur.get("permissions", {}))
    for key, mine in (("deny", deny), ("allow", allow)):
        keep = [r for r in p2.get(key, []) if r not in set(prev.get(key, []))]  # 拿掉上次平台加的,留別人的
        p2[key] = keep + [r for r in mine if r not in keep]
    new["permissions"] = p2
    return path, cur, new, prev, {"sandbox": sandbox, "deny": deny, "allow": allow}


def write_project(name: str, doc: dict, index: dict, report: bool) -> list[str]:
    """回傳偏移(實際跟應該的不一樣的地方)。report=True 只算、不寫。"""
    c = compose(name, doc, index)
    if not c:
        return []
    path, cur, new, prev, record = c
    sandbox, deny, allow = record["sandbox"], record["deny"], record["allow"]
    perms = cur.get("permissions", {})
    drift = []
    if prev:  # 寫過才談得上偏移
        if cur.get("sandbox") != prev.get("sandbox"):
            drift.append(_("sandbox was changed"))
        for key in ("deny", "allow"):
            missing = set(prev.get(key, [])) - set(perms.get(key, []))
            if missing:
                drift.append(_("permissions.{key} is missing {n} {n:rule|rules} (e.g. {example})", key=key, n=len(missing),
                               example=sorted(missing)[0]))
    if report:
        print(_("{project}: would write sandbox (allowWrite {write}, denyRead {read}, {domains} {domains:domain|domains}), "
                "{deny} deny {deny:rule|rules}, {allow} allow {allow:rule|rules} → {path}", project=name,
                write=len(sandbox["filesystem"]["allowWrite"]), read=len(sandbox["filesystem"]["denyRead"]),
                domains=len(sandbox["network"]["allowedDomains"]), deny=len(deny), allow=len(allow), path=path))
        return drift
    if new != cur:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    RENDERED.mkdir(parents=True, exist_ok=True)
    (RENDERED / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return drift


def unapply(name: str, doc: dict) -> None:
    """登錄關掉 enforce:拿掉平台寫的 sandbox 跟那幾條規則(退回)。"""
    rec = RENDERED / f"{name}.json"
    path = settings_path(doc)
    if not rec.exists() or not path or not path.exists():
        return
    prev = json.loads(rec.read_text())
    cur = json.loads(path.read_text())
    cur.pop("sandbox", None)
    perms = cur.get("permissions", {})
    for key in ("deny", "allow"):
        if key in perms:
            perms[key] = [r for r in perms[key] if r not in set(prev.get(key, []))]
    path.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n")
    rec.unlink()
    print(_("{project}: enforce is off, removed the platform's sandbox/rules", project=name))


GATE_MARK = "# agent-hub:gate (managed; registry gate: strict)"
GATE_HOOK_HEAD = GATE_MARK + """
# No direct commit or merge commit on main/master in the main checkout; merge-branch fast-forwards, which does not run this hook.
b=$(git symbolic-ref --short -q HEAD)
case "$b" in main|master) ;; *) exit 0 ;; esac
[ -n "$MERGE_GATE" ] && exit 0
"""


def gate_hook() -> str:
    """the hook's text; the line it prints when it refuses a commit is a message ($b = the branch, filled in by the shell)"""
    return GATE_HOOK_HEAD + 'echo "' + _(
        "This project has the merge gate on (gate: strict): no direct commits to $b. Work in your own worktree/branch "
        "and finish with agenthub merge-branch.") + '" >&2\nexit 1\n'


def gate(name: str, doc: dict, report: bool, quiet: bool = False) -> list[tuple[str, str]]:
    """登錄 gate: strict → 裝 pre-commit / pre-merge-commit;關掉就拿掉(只動有平台標記的)。
    專案有自己的 core.hooksPath(例:husky)就不裝,只偵測。回傳要做(report)/做了的 (add|update|remove, hook)。"""
    import subprocess
    say = (lambda *_args: None) if quiet else print
    ws = registry.dirs(doc)["workspace"]
    if not ws or not (ws / ".git").is_dir():
        return []
    want = doc.get("gate") == "strict"
    hp = subprocess.run(["git", "-C", str(ws), "config", "core.hooksPath"], capture_output=True, text=True).stdout.strip()
    acts = []
    for hook in ("pre-commit", "pre-merge-commit"):
        f = ws / ".git" / "hooks" / hook
        text = f.read_text(errors="replace") if f.exists() else ""
        mine = GATE_MARK in text
        if want and hp:
            say(_("{project}: gate strict not installed (the project uses its own core.hooksPath={path}); detecting only",
                  project=name, path=hp))
            return acts
        if want and not mine:
            if f.exists():
                say(_("{project}: .git/hooks/{hook} is already the project's own hook; not overwriting it (the gate only "
                      "detects)", project=name, hook=hook))
                continue
            acts.append(("add", hook))
            if not report:
                f.write_text("#!/bin/sh\n" + gate_hook())
                f.chmod(0o755)
                say(_("{project}: installed {hook} (gate strict)", project=name, hook=hook))
        elif want and text != "#!/bin/sh\n" + gate_hook():   # the platform's hook from an older version (or language)
            acts.append(("update", hook))
            if not report:
                f.write_text("#!/bin/sh\n" + gate_hook())
                say(_("{project}: updated {hook} (gate strict)", project=name, hook=hook))
        elif not want and mine:
            acts.append(("remove", hook))
            if not report:
                f.unlink()
                say(_("{project}: gate is off, removed {hook}", project=name, hook=hook))
    return acts


# ---------- 套用者(appliers.py)----------
# Change 只用中立的詞(agent-settings:<專案>、hook:<專案>/<名稱>);執行環境的設定格式留在 runtime_claude。

EXTERNAL = False


def _changes(index: dict) -> list:
    from appliers import Change
    out = []
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project():
            continue
        out += [Change(op, f"hook:{name}/{hook}", "gate strict") for op, hook in gate(name, doc, report=True, quiet=True)]
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        rec = RENDERED / f"{name}.json"
        if not on:
            if rec.exists() and settings_path(doc) and settings_path(doc).exists():
                out.append(Change("remove", f"agent-settings:{name}", _("enforce is off: remove the platform's sandbox/rules")))
            continue
        c = compose(name, doc, index)
        if not c:
            continue
        path, cur, new, prev, record = c
        if new != cur or prev != record:
            what = [w for w, changed in (("sandbox", new.get("sandbox") != cur.get("sandbox")),
                                         (_("permission rules"), new.get("permissions") != cur.get("permissions")),
                                         (_("record"), prev != record)) if changed]
            out.append(Change("add" if not cur else "update", f"agent-settings:{name}", _(", ").join(what)))
    return out


def plan(index: dict | None = None) -> list:
    return _changes(index or registry.refresh())


def apply(index: dict | None = None) -> list:
    index = index or registry.refresh()
    todo = _changes(index)
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project():
            continue
        gate(name, doc, report=False, quiet=True)
        if (doc.get("enforce") or {}).get("sandbox"):
            write_project(name, doc, index, report=False)
        else:
            unapply(name, doc)
    return todo


def main() -> int:
    report = "--report" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    bad = 0
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project() or (only and name != only):
            continue
        gate(name, doc, report)
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        if not on:
            if report:
                write_project(name, doc, index, report=True)
            else:
                unapply(name, doc)
            continue
        drift = write_project(name, doc, index, report=report)
        if drift:
            bad += 1
            print(_("⚠️ {project}'s settings drifted (report mode: not written back): {drift}", project=name,
                    drift="; ".join(drift)) if report else
                  _("⚠️ {project}'s settings drifted (written back): {drift}", project=name, drift="; ".join(drift)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
