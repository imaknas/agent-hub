#!/usr/bin/env python3
"""把一個已 onboard 專案的開發環境從 MBP 搬到 mini 的 ~/workspace/<name>。

之後用 Claude Desktop 透過 SSH 在 mini 上開 session:session 跑在 mini,MBP 只是遙控端,
MBP 斷線/沒電/闔上不會中斷正在跑的工作(2026-10-14 project-16 試行驗證過)。

跑在 MBP。預設 dry-run:列出檢查結果、要複製的不進版控檔案(含大小)、要在 mini 重建的依賴;
加 --apply 才動手。MBP 上的資料夾原封不動當備份——要不要清掉由 Knas 決定,這支不刪任何東西。

步驟:
  1. 前置檢查(不過就停):manifest 有登記、不是 native、沒有未 commit 的改動、所有本機分支
     都已 push、沒有程序的工作目錄在專案資料夾(先關掉那個專案的 session)、mini 上
     ~/workspace/<name> 還不存在
  2. 補一次 memory 同步 + push,確保 mini 的 bare repo 是最新的
  3. mini 上 recover-project.sh <name> ~/workspace/<name>(clone + 接上既有 agent-memory 分支)
  4. rsync 不進版控、又重建不出來的東西(.env、data/、CLAUDE.local.md、.claude/ 本機設定等);
     依賴、build 產物、cache 不複製
  4b. Claude Code 的 auto memory、專案範圍的 MCP server 設定、.git/info/exclude(都不在 repo 裡)也搬過去
  5. mini 上重跑 sync-local-setup.sh:對齊 managed 區塊跟 hook,保留複製過去的其他內容
  6. MBP 上有 .venv/venv/node_modules 的目錄,在 mini 依 lockfile 重建(失敗只報告,不中斷)

用法:migrate-to-mini.py <name> [--apply] [--allow-local-branches] [--skip <相對路徑>]...
  --skip:這次不搬的不進版控檔案(例:留在 MBP 備份就好的大型實驗資料庫),可以給多次
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hublib  # noqa: E402

MINI = "hub-host"
MINI_PATH_PREFIX = "export PATH=/opt/homebrew/bin:/opt/homebrew/opt/rustup/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH; "
# 依賴、build 產物、cache:不複製,mini 上需要時重建
REBUILDABLE = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "target", "dist", "build", ".next", ".turbo", ".parcel-cache", ".svelte-kit", ".astro",
    ".godot", "coverage", ".DS_Store", ".husky",
}
REBUILDABLE_SUFFIX = (".pyc", ".egg-info", ".egg-info/", ".tsbuildinfo")
# recover-project.sh 自己會建立/對齊的
MANAGED = {".agent-memory"}


def fail(msg: str) -> None:
    sys.exit(f"migrate-to-mini: {msg}")


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(f"{' '.join(cmd)} 失敗:\n{r.stderr.strip()}")
    return r.stdout


def mini(script: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", MINI, MINI_PATH_PREFIX + script],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(f"mini 上執行失敗:{script}\n{r.stdout}{r.stderr}")
    return r


def manifest_row(name: str) -> dict:
    """path 是 MBP 上的位置:manifest 從 2026-10-15 起記的是 mini 的位置,MBP 的舊位置在
    legacy-paths.tsv(第一筆)。"""
    for line in hublib.MANIFEST.read_text().splitlines()[1:]:
        f = line.split("\t")
        if f[0] == name:
            mbp = [p for p in hublib.legacy_paths(name) if p.startswith(hublib.MBP_HOME + "/")]
            if not mbp:
                fail(f"legacy-paths.tsv 裡沒有 '{name}' 在 MBP 上的位置")
            return {"path": mbp[0], "origin": f[2]}
    fail(f"{hublib.MANIFEST} 裡沒有 '{name}'")


def is_rebuildable(rel: str) -> bool:
    # 任何一層是 build 產物/依賴就算(例:build/web/ 的最後一層是 web)
    parts = rel.rstrip("/").split("/")
    return any(part in REBUILDABLE for part in parts) or rel.endswith(REBUILDABLE_SUFFIX)


def ignored_entries(path: str) -> list[str]:
    out = run(["git", "status", "--ignored", "--porcelain", "-z"], cwd=path)
    return [e[3:] for e in out.split("\0") if e.startswith("!! ")]


def du_kb(path: str, rel: str) -> int:
    r = subprocess.run(["du", "-sk", os.path.join(path, rel)], capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.stdout else 0


def human(kb: int) -> str:
    for unit in ("K", "M", "G"):
        if kb < 1024:
            return f"{kb:.0f}{unit}"
        kb /= 1024
    return f"{kb:.1f}T"


def preflight(name: str, path: str, origin: str, allow_local_branches: bool) -> list[str]:
    """回傳警告;致命問題直接 fail。"""
    warnings = []
    if origin == "(native)":
        fail(f"'{name}' 是 native 專案(沒有 GitHub remote),recover-project.sh 沒辦法在 mini 上 clone")
    if not os.path.isdir(path):
        fail(f"{path} 不存在")
    dirty = run(["git", "status", "--porcelain"], cwd=path).strip()
    if dirty:
        fail(f"{path} 有未 commit 的改動——請那個專案的 session 先 commit+push:\n{dirty}")
    branches = run(["git", "for-each-ref", "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
                    "refs/heads"], cwd=path)
    unpushed = []
    for line in branches.splitlines():
        b, up, track = (line.split("\t") + ["", ""])[:3]
        if b == "agent-memory":
            continue
        if not up or "ahead" in track or "gone" in track:
            unpushed.append(f"{b}({'沒有 upstream' if not up else track})")
    if unpushed:
        msg = "本機分支沒 push,mini 上 clone 不到:" + ", ".join(unpushed)
        if not allow_local_branches:
            fail(msg + "\n先 push(或確認不要了),或加 --allow-local-branches 留在 MBP")
        warnings.append(msg + "(--allow-local-branches:留在 MBP)")
    if run(["git", "stash", "list"], cwd=path).strip():
        warnings.append("有 git stash,不會搬過去(留在 MBP)")
    users = run(["lsof", "+d", path], check=False)
    procs = sorted({f"{f[0]}({f[1]})" for f in (l.split() for l in users.splitlines()[1:]) if f})
    if procs:
        fail(f"還有程序的工作目錄在 {path}:{' '.join(procs)}——先關掉那個專案的 Claude session / 終端機分頁")
    if mini(f"test -e ~/workspace/{shlex.quote(name)}", check=False).returncode == 0:
        fail(f"mini 上 ~/workspace/{name} 已經存在——已經搬過了?確認後手動處理")
    return warnings


def dep_dirs(path: str, entries: list[str]) -> list[tuple[str, str]]:
    """MBP 上實際有建過依賴的目錄 → (相對目錄, 類型)。"""
    out = []
    for e in entries:
        rel = e.rstrip("/")
        base = rel.rsplit("/", 1)[-1]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
        if base in (".venv", "venv"):
            out.append((parent, base))
        elif base == "node_modules":
            out.append((parent, "node"))
    return out


def python_version(path: str, parent: str, venv: str) -> str | None:
    cfg = pathlib.Path(path, parent, venv, "pyvenv.cfg")
    if cfg.exists():
        m = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+)", cfg.read_text(), re.M)
        if m:
            return m.group(1)
    return None


def rebuild_cmd(path: str, parent: str, kind: str) -> str | None:
    d = pathlib.Path(path, parent)
    """回傳在專案根目錄執行的指令。"""
    if kind in (".venv", "venv"):
        py = python_version(path, parent, kind)
        pyarg = f" --python {py}" if py else ""
        cd = f"cd {shlex.quote(parent)} && " if parent != "." else ""
        if (d / "uv.lock").exists():
            return f"{cd}uv sync --frozen --all-extras{pyarg}" if kind == ".venv" else None
        if (d / "requirements.txt").exists():
            return f"{cd}uv venv {kind}{pyarg} && VIRTUAL_ENV={kind} uv pip install -r requirements.txt"
        if (d / "pyproject.toml").exists():
            return f"{cd}uv venv {kind}{pyarg} && VIRTUAL_ENV={kind} uv pip install -e ."
        return None
    # npm/pnpm workspaces:lockfile 可能在上層目錄,在那裡裝一次就涵蓋所有 workspace
    root = pathlib.Path(path)
    while True:
        rel = d.relative_to(root).as_posix()
        if (d / "pnpm-lock.yaml").exists():
            return f"cd {shlex.quote(rel)} && pnpm install --frozen-lockfile" if rel != "." else "pnpm install --frozen-lockfile"
        if (d / "package-lock.json").exists():
            return f"cd {shlex.quote(rel)} && npm ci" if rel != "." else "npm ci"
        if d == root:
            return None
        d = d.parent


# 專案裡有這些檔案 → mini 上需要對應的工具鏈(依賴不複製、要在 mini 上重新 build)
TOOLCHAINS = {"Cargo.toml": "cargo", "project.godot": "godot", "go.mod": "go"}


def missing_toolchains(path: str) -> list[str]:
    files = {f.rsplit("/", 1)[-1] for f in run(["git", "ls-files"], cwd=path).splitlines()}
    need = sorted({tool for marker, tool in TOOLCHAINS.items() if marker in files})
    return [t for t in need if mini(f"command -v {t}", check=False).returncode != 0]


def copy_mcp_servers(name: str, path: str) -> None:
    """專案範圍(local scope)的 MCP server 設定存在 MBP 的 ~/.claude-personal/.claude.json、以 MBP
    路徑為 key,不在 repo 裡,clone 不會帶過去(例:project-06 的唯讀資料庫連線、project-13 自己的
    MCP)。轉換路徑後用 `claude mcp add-json` 加到 mini 那個資料夾。設定裡可能有憑證:只經過
    stdin 傳過去,不印出內容。stdio 的指令在 mini 上找不到時,用同名工具在 mini 的位置;
    還是找不到(例:要先 build 的專案自己的執行檔)就提醒。"""
    cfg = hublib.HOME / ".claude-personal" / ".claude.json"
    if not cfg.exists():
        return
    projects = json.loads(cfg.read_text()).get("projects", {})
    old_paths = [path] + [p for p in hublib.legacy_paths(name) if p != path]
    target = f"{hublib.MINI_WORKSPACE}/{name}"
    for old in old_paths:
        for server, conf in (projects.get(old, {}).get("mcpServers") or {}).items():
            text = json.dumps(conf).replace(old, target).replace(f"{hublib.MBP_HOME}/", "/Users/you/")
            conf2 = json.loads(text)
            note = ""
            cmd = conf2.get("command")
            if cmd and mini(f"test -x {shlex.quote(cmd)}", check=False).returncode != 0:
                found = mini(f"command -v {shlex.quote(os.path.basename(cmd))}", check=False).stdout.strip()
                if found:
                    conf2["command"] = found
                else:
                    note = f"(mini 上還沒有 {cmd},先 build/安裝)"
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", MINI, MINI_PATH_PREFIX +
                                f"cd {target} && read -r J && claude mcp add-json {shlex.quote(server)} \"$J\""],
                               input=json.dumps(conf2) + "\n", capture_output=True, text=True)
            ok = r.returncode == 0 or "already exists" in (r.stdout + r.stderr)
            print(f"  MCP {server}:{'已加到 mini' if ok else '失敗 ' + (r.stderr or r.stdout).strip()[:200]}{note}")


def merge_info_exclude(name: str, path: str) -> None:
    """MBP repo 的 .git/info/exclude 裡專案/工具自己加的忽略規則(例:impeccable 的 hook.cache.json)
    不會跟著 clone 過去,缺的話那些檔案在 mini 上會變成未追蹤。把 mini 沒有的行補上。"""
    src = pathlib.Path(run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           cwd=path).strip(), "info", "exclude")
    if not src.exists():
        return
    lines = [l for l in src.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        return
    target = f"~/workspace/{name}"
    have = set(mini(f"cat {target}/.git/info/exclude 2>/dev/null || true").stdout.splitlines())
    missing = [l for l in lines if l not in have]
    if missing:
        body = "\n".join(missing)
        mini(f"mkdir -p {target}/.git/info && printf '%s\\n' {shlex.quote(body)} >> {target}/.git/info/exclude")
        print(f"  info/exclude 補上 {len(missing)} 條忽略規則")


def copy_auto_memory(name: str, path: str) -> None:
    """Claude Code 每個專案的 auto memory(MBP:~/.claude-personal/projects/<slug>/memory/)不在 repo 裡,
    搬到 mini 的 ~/.claude/projects/<mini 路徑的 slug>/memory/(mini 只有個人帳號,所以是 ~/.claude)。
    改過名的專案,舊路徑的 memory 一起帶過去。mini 上已經有的檔案不覆蓋。"""
    old_paths = [path] + [p for p in hublib.legacy_paths(name) if p != path]
    dest = f".claude/projects/{hublib.slug_for_path(f'{hublib.MINI_WORKSPACE}/{name}')}/memory/"
    for old in old_paths:
        src = hublib.HOME / ".claude-personal" / "projects" / hublib.slug_for_path(old) / "memory"
        if not src.is_dir() or not any(src.iterdir()):
            continue
        mini(f"mkdir -p ~/{dest}")
        r = subprocess.run(["rsync", "-a", "--ignore-existing", f"{src}/", f"{MINI}:{dest}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"auto memory rsync 失敗:{r.stderr}")
        print(f"  auto memory:{src.parent.name} → mini ~/{dest}")


def missing_logins(path: str) -> list[str]:
    """mini 上專案會用到、但 SSH session 讀不到的 CLI 登入。憑證要存在檔案裡(SSH 底下讀不到
    keychain),例如 gh 要用 --insecure-storage 登入。只檢查、不代登入。"""
    out = []
    if mini("gh auth status", check=False).returncode != 0:
        out.append("mini 上 gh 沒登入或讀不到 token:在 mini 的終端機跑 "
                   "`gh auth login -h github.com --git-protocol ssh --web --insecure-storage`")
    if os.path.isdir(os.path.join(path, ".vercel")) and \
            mini("vercel whoami", check=False).returncode != 0:
        out.append("這個專案用 Vercel,但 mini 上 vercel 沒登入:在 mini 的終端機跑 `vercel login`")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-local-branches", action="store_true")
    ap.add_argument("--skip", action="append", default=[], help="不搬的不進版控檔案(相對路徑)")
    a = ap.parse_args()

    row = manifest_row(a.name)
    path, target = row["path"], f"~/workspace/{a.name}"
    warnings = preflight(a.name, path, row["origin"], a.allow_local_branches)

    entries = ignored_entries(path)
    skip = {x.rstrip("/") for x in a.skip}
    copy = [e for e in entries if not is_rebuildable(e) and e.rstrip("/") not in MANAGED
            and e.rstrip("/") not in skip]
    left = [e for e in entries if e.rstrip("/") in skip]
    skipped = [e for e in entries if is_rebuildable(e)]
    deps = dep_dirs(path, entries)
    sizes = {e: du_kb(path, e) for e in copy}

    print(f"== {a.name}:{path} → mini {target}")
    for w in warnings:
        print(f"  ! {w}")
    print(f"  複製(不進版控、重建不出來的),共 {human(sum(sizes.values()))}:")
    for e in copy:
        print(f"    {human(sizes[e]):>6}  {e}")
    if skipped:
        print("  不複製(依賴/build/cache):" + " ".join(skipped))
    if left:
        print(f"  不搬(--skip,留在 MBP 備份),共 {human(sum(du_kb(path, e) for e in left))}:" + " ".join(left))
    plans, seen = [], set()
    for p, k in deps:
        cmd = rebuild_cmd(path, p, k)
        if cmd and cmd in seen:
            continue  # 同一個 workspace root 只裝一次
        seen.add(cmd)
        plans.append((p, k, cmd))
    for w in missing_logins(path):
        warnings.append(w)
        print(f"  ! {w}")
    for t in missing_toolchains(path):
        warnings.append(f"mini 上沒有 {t},這個專案需要它才能 build/執行——先在 mini 安裝")
        print(f"  ! mini 上沒有 {t}")
    for p, k, cmd in plans:
        print(f"  mini 上重建 {p}/{'node_modules' if k == 'node' else k}:{cmd or '(找不到 lockfile,手動處理)'}")
    if not a.apply:
        print("\n(dry-run;確認後加 --apply)")
        return

    print("[2/6] 補 memory 同步 + push")
    mem = os.path.join(path, ".agent-memory")
    env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath",
               GIT_CONFIG_VALUE_0="/dev/null")
    for script in ("git-sync-memory.sh", "git-safe-push.sh"):
        r = subprocess.run([str(hublib.HUB / "bin/with-lock.sh"), str(hublib.HUB / "bin" / script)],
                           cwd=mem, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"{script} 失敗:{r.stdout}{r.stderr}")

    print("[3/6] mini:recover-project.sh")
    mini(f"git -C ~/agent-hub pull -q && mkdir -p ~/workspace && "
         f"~/agent-hub/bin/recover-project.sh {shlex.quote(a.name)} {target}")

    if copy:
        print(f"[4/6] rsync {len(copy)} 項({human(sum(sizes.values()))})")
        excludes = [f"--exclude={x}" for x in sorted(REBUILDABLE)] + ["--exclude=*.pyc"]
        r = subprocess.run(["rsync", "-a", "-r", "--from0", "--files-from=-", *excludes,
                            f"{path}/", f"{MINI}:workspace/{a.name}/"],
                           input="\0".join(e.rstrip("/") for e in copy), text=True,
                           capture_output=True)
        if r.returncode != 0:
            fail(f"rsync 失敗:{r.stderr}")

    copy_auto_memory(a.name, path)
    merge_info_exclude(a.name, path)
    copy_mcp_servers(a.name, path)

    print("[5/6] mini:sync-local-setup.sh")
    out = mini(f"cd {target} && ~/agent-hub/bin/sync-local-setup.sh").stdout
    for l in out.splitlines():
        if l.startswith("⚠️"):  # 已 commit 的檔案提到這套機制等,要那個專案的 session 處理
            print(f"  ! {l}")

    print("[6/6] mini:重建依賴")
    problems = []
    for p, k, cmd in plans:
        if not cmd:
            problems.append(f"{p}:{k} 找不到 lockfile,沒重建")
            continue
        r = mini(f"cd {target} && {cmd}", check=False)
        status = "ok" if r.returncode == 0 else "失敗"
        print(f"  {p} ({cmd}):{status}")
        if r.returncode != 0:
            problems.append(f"{p}:{cmd} 失敗\n{(r.stdout + r.stderr)[-800:]}")
    dirty = mini(f"cd {target} && git status --porcelain").stdout.strip()
    if dirty:
        problems.append(f"mini 上的工作副本有非預期的改動(重建依賴改到 lockfile?):\n{dirty}")

    # MBP 舊副本留標記(放在 .git 裡,不會被 commit、git status 也看不到):之後在這裡開
    # session,sync-local-setup.sh 會在 SessionStart 提醒改去 mini 開
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path).strip()
    pathlib.Path(common, "agent-hub-migrated-to-mini").write_text(
        f"mini:{target}\t{datetime.date.today().isoformat()}\n")

    print(f"\n完成:{a.name} 在 mini 的 {target}。在 Claude Desktop 用 SSH(hub-host)開這個資料夾。")
    print(f"MBP 上的 {path} 沒動,當備份(.git 裡留了標記:在那裡開 session 會被提醒改去 mini)。")
    for p in problems:
        print(f"  ! {p}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
