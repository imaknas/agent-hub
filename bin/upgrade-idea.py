#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Upgrade an idea sandbox (registry type: idea) into a real project: history, files and memory are all kept
(docs/design/idea-sandboxes.md).

Usage: upgrade-idea.py <sandbox name> [--rename <new name>] [--public] [--apply] [--json]
  Without --apply it only prints what it would do. --json: the last line prints the result (used by lease-runner's
  POST /v1/sandboxes/<name>/upgrade):
    {"result": "upgraded"|"already", "name", "origin"}                                  exit 0
    {"result": "not_found"|"not_idea"|"dirty"|"name_taken"|"failed", "detail"}        exit 1
  One already upgraded (recorded in .state/idea-upgrades.json) called again returns already + the upgraded name, so
  resending is safe.
What it does (in order):
  1. Checks (all before touching anything): the registry says type: idea; the main repo has no uncommitted changes to
     tracked files; the new name isn't taken;
     **the upgraded registry entry is validated first** (validation used to come after creating the GitHub repo, so a
     failure stopped halfway, 10-25 idea-1);
     a repo with the same name already on GitHub: if its content is this sandbox's own history (the remote HEAD is in
     the local history, or it's empty) = the last run stopped halfway, carry on;
     if not = name_taken
  2. --rename: call rename-project.sh <sandbox name> <new name> --apply (folder, memory bare repo/copy and registry
     changed together)
  3. gh repo create <github.owner>/<name> (private by default) + push (if the repo is already ours, only push), origin
     changed to github.remote
  4. Registry: type changed to project, paths.origin filled in; purpose, enforce (with the capabilities and work the
     owner agreed to) and privacy are kept
     (sandboxes created earlier without an explicit enforce.work get work: true on upgrade: idea sandboxes could
     always make changes in a discuss lease)
  5. Add origin to mirror-list.txt; regenerate the index.md roster; commit-hub-files commits only these files
Each step that changes something outside (rename, repo) is recorded as the stage in .state/idea-upgrades.json once
done; on a failure partway, call it again and it carries on from where the record says (under the new name if it was
renamed). It doesn't delete the GitHub repo to "roll back": deleting a repo can't be undone, carrying on is safer.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import registry  # noqa: E402

_ = i18n.domain(__file__)

HUB = hubconf.ROOT
RECORD = hubconf.STATE / "idea-upgrades.json"
JSON = False


def done(result: str, code: int, **extra):
    if JSON:
        print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    elif code:
        print(f"upgrade-idea: {extra.get('detail', result)}", file=sys.stderr)
    sys.exit(code)


def fail(msg: str, result: str = "failed") -> None:
    done(result, 1, detail=msg)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if r.returncode != 0:
        fail(_("{cmd} failed: {err}", cmd=' '.join(cmd[:3]), err=(r.stderr or r.stdout).strip()[-400:]))
    return r


def records() -> dict:
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def record(idea: str, **fields) -> None:
    rec = records()
    rec[idea] = {**rec.get(idea, {}), **fields,
                 "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=1))


def unfinished_rename(name: str) -> str | None:
    """A rename the rename endpoint (project-rename.py) left halfway: if the same project (old or new name) isn't done
    yet, return an explanation. Each side records its own progress, and interleaving them would leave a retry unable
    to find the registry entry (e.g. an upgrade's rename stopped halfway, then it was renamed in between), so neither
    starts while the other is unfinished."""
    try:
        recs = json.loads((hubconf.STATE / "project-renames.json").read_text())
    except (OSError, ValueError):
        return None
    for old, r in recs.items():
        if isinstance(r, dict) and r.get("stage") != "done" and name in (old, r.get("new")):
            return _("the rename {old} → {new} stopped halfway (stage {stage}): first retry the rename until it "
                     "completes",
                     old=old, new=r.get('new'), stage=r.get('stage'))
    return None


def remote_url(owner: str, name: str) -> str:
    return (hubconf.get("github.remote") or "git@github.com:{owner}/{name}.git").format(owner=owner, name=name)


def remote_is_ours(ws: pathlib.Path, url: str) -> bool:
    """The remote was pushed by this sandbox itself (the last run stopped halfway): no branches, or every branch tip is
    in the local history. Every branch, not just HEAD: a repo whose HEAD names a branch nobody pushed (the default
    branch name differs between git setups) would otherwise look empty while it holds someone else's history."""
    r = subprocess.run(["git", "-C", str(ws), "ls-remote", "--heads", url], text=True, capture_output=True)
    if r.returncode != 0:
        return False
    tips = [line.split()[0] for line in r.stdout.splitlines() if line.strip()]
    return all(subprocess.run(["git", "-C", str(ws), "merge-base", "--is-ancestor", sha, "HEAD"],
                              capture_output=True).returncode == 0 for sha in tips)


def upgraded_doc(doc: dict, final: str, url: str) -> dict:
    """What the registry entry looks like after the upgrade (with a rename, the name and folder change too): it's
    validated before anything is done."""
    import copy
    out = copy.deepcopy(doc)
    enf = out.get("enforce")
    if doc.get("type") == "idea" and isinstance(enf, dict) and enf.get("sandbox") and "work" not in enf:
        # idea sandboxes may make changes in a discuss lease by default (10-25, the owner); older ones didn't say so
        # explicitly, so the upgrade carries it over explicitly
        enf["work"] = True
    out["type"] = "project"
    paths = out.setdefault("paths", {})
    if final != doc.get("name"):
        out["name"] = final
        for key, old in (("workspace", doc.get("name")), ("memory_bare", f"{doc.get('name')}-memory.git")):
            if isinstance(paths.get(key), str) and paths[key].endswith("/" + old):
                paths[key] = paths[key][: -len(old)] + (final if key == "workspace" else f"{final}-memory.git")
    paths["origin"] = url
    return out


def main(argv: list[str]) -> int:
    global JSON
    args, apply, public, new = [], False, False, None
    it = iter(argv)
    for a in it:
        if a == "--json":
            JSON = True
        elif a == "--apply":
            apply = True
        elif a == "--public":
            public = True
        elif a == "--rename":
            new = next(it, None) or fail(_("--rename needs a new name"))
        else:
            args.append(a)
    if len(args) != 1:
        fail(_("usage: upgrade-idea.py <sandbox name> [--rename <new name>] [--public] [--apply]"))
    name = args[0]
    if (why := unfinished_rename(name)):
        fail(why)
    prev = records().get(name)
    if prev and prev.get("stage", "done") == "done" and (hubconf.registry_dir() / f"{prev['name']}.yaml").exists():
        done("already", 0, name=prev["name"], origin=prev["origin"])  # already upgraded: resending is safe
    renamed = bool(prev and prev.get("stage") in ("renamed", "repo") and prev["name"] != name)
    if renamed:  # the last run stopped after the rename: the registry entry and folder already have the new name
        if new and new != prev["name"]:
            fail(_("the last upgrade of {name} stopped halfway after renaming to {new}; run it again with the name "
                   "{new}",
                   name=name, new=prev['name']), "name_taken")
        new = None
    final = prev["name"] if renamed else (new or name)
    reg = hubconf.registry_dir() / f"{final if renamed else name}.yaml"
    if not reg.exists():
        fail(_("{name} is not in the registry", name=name), "not_found")
    import yaml
    doc = yaml.safe_load(reg.read_text())
    if doc.get("type") != "idea":
        fail(_("{name} is not an idea sandbox (type: {type})", name=name, type=doc.get('type', 'project')), "not_idea")
    ws = registry.dirs(doc)["workspace"]
    if run(["git", "-C", str(ws), "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        fail(_("{path} has uncommitted changes to tracked files; deal with them first", path=ws), "dirty")
    owner = hubconf.get("github.owner") or fail(_("hub.json has no github.owner"))
    url = remote_url(owner, final)
    errs = registry.validate(upgraded_doc(doc, final, url), final)
    if errs:
        fail(_("the upgraded registry entry would not be valid; nothing was changed: {errors}", errors='; '.join(errs)))
    if new:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", new):
            fail(_("the new name may only use lowercase letters, digits and -: {name}", name=new), "name_taken")
        taken = [w for w, hit in ((f"projects/{new}.yaml", (hubconf.registry_dir() / f"{new}.yaml").exists()),
                                  (f"~/workspace/{new}", (hubconf.p("workspace_root", "~/workspace") / new).exists()))
                 if hit]
        if taken:
            fail(_("{name} is already taken: {paths}", name=new, paths=', '.join(taken)), "name_taken")
    repo_exists = subprocess.run(["gh", "repo", "view", f"{owner}/{final}"], capture_output=True).returncode == 0
    if repo_exists and not remote_is_ours(ws, url):
        fail(_("GitHub already has {owner}/{name} (not this sandbox's history)", owner=owner, name=final), "name_taken")

    plan = [_("rename {old} → {new} (rename-project.sh)", old=name, new=new) if new
            else (_("already renamed to {name}", name=final) if renamed else _("no rename")),
            (_("{owner}/{name} on GitHub was created by the previous run: push only", owner=owner, name=final)
             if repo_exists else
             f"gh repo create {owner}/{final} --{'public' if public else 'private'} + push") + _(", origin = {url}", url=url),
            _("registry projects/{name}.yaml: type: project, paths.origin: {url} (purpose/enforce/privacy kept)",
              name=final, url=url),
            _("add origin to mirror-list.txt, regenerate index.md, commit agent-hub")]
    if not JSON:
        print("\n".join(f"  {i}. {p}" for i, p in enumerate(plan, 1)))
    if not apply:
        print(_("(dry-run; add --apply to run it)"))
        return 0

    if new:
        r = subprocess.run([str(HUB / "bin" / "rename-project.sh"), name, new, "--apply"], text=True,
                           capture_output=JSON)
        if r.returncode != 0:
            fail(_("rename-project.sh failed; stopping here (the GitHub repo is not created yet)"))
        record(name, name=final, origin=url, stage="renamed")
        reg = hubconf.registry_dir() / f"{final}.yaml"
        doc = yaml.safe_load(reg.read_text())
        ws = registry.dirs(doc)["workspace"]

    if not repo_exists:
        run(["gh", "repo", "create", f"{owner}/{final}", "--public" if public else "--private",
             f"--source={ws}", "--remote=origin", "--push"])
    has_origin = subprocess.run(["git", "-C", str(ws), "remote", "get-url", "origin"], capture_output=True).returncode == 0
    run(["git", "-C", str(ws), "remote", "set-url" if has_origin else "add", "origin", url])
    if repo_exists:
        run(["git", "-C", str(ws), "push", "-u", "origin", "HEAD"])
    record(name, name=final, origin=url, stage="repo")

    text = reg.read_text()
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    doc = upgraded_doc(doc, final, url)
    errs = registry.validate(doc, final)
    # validated before starting; still invalid here means the registry entry was changed midway. The repo stays; fix
    # the registry entry and call again to carry on
    if errs:
        fail(_("the registry entry is not valid: {errors} (the GitHub repo is already created; fix the registry entry "
               "and run this again to continue)", errors='; '.join(errs)))
    reg.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))

    # config (the instance directory; before the move, the one in the hub)
    mirror = hubconf.config_file("mirror-list.txt")
    lines = mirror.read_text().splitlines() if mirror.exists() else []
    if url not in lines:
        mirror.write_text("\n".join(lines + [url]) + "\n")
    subprocess.run([str(HUB / "bin" / "gen-index-roster.sh")], check=False)
    subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), f"upgrade idea {name} → project {final} (upgrade-idea.py)",
                    f"projects/{final}.yaml", "mirror-list.txt", "index.md"], check=False)
    record(name, name=final, origin=url, stage="done")
    if not JSON:
        print(_("Done: {name} is now the project {new} ({url})", name=name, new=final, url=url))
    done("upgraded", 0, name=final, origin=url)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
