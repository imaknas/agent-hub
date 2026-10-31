#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The platform's templates for models and agents, filled in one place (docs/design/i18n.md, step 5): the judgment
points' prompts (templates/prompts/<name>.md), the agent rules every project's rules file carries
(templates/agent-instructions.md + templates/rules/<name>.md), the MCP server's instructions and the platform's other
prompt texts. The templates name no one.

    import prompts
    text = prompts.render("librarian-state", project=p, memory_section=m, ...)   # templates/prompts/<name>.md
    text = prompts.fill(template_text, project=p, ...)                           # any template text
    text, missing = prompts.agent_rules(["engineering"])                         # the rules file's managed block

A template's placeholders are `{{name}}` (single braces stay text: prompts carry JSON examples). fill() fills, besides
the values passed in:
  owner, owner_alias, assistant     the configured names: the owner's tag, the address agents mail the owner at
                                    (hubconf.owner_alias), the assistant's name; or the ones passed in (an eval pins
                                    them)
  content_language                  the language the model or agent writes in: hub.json language.content, or lang=
  todo_heading, tag_owner, tag_agent, tag_tbd, todo_none, key_marker, key_pattern
                                    the to-do block's tokens (contracts/state-todo.v1.md): the zh-Hant set until every
                                    reader accepts the English one too (step 6, TOKEN_SET). key_marker is a line's
                                    marker with an example key, key_pattern the same with `<key>` in its place
A placeholder left unfilled is an error, not text sent to a model or an agent. Values are inserted in one pass: a
`{{…}}` inside a value (a memory diff, a letter) stays as it is. The language values are looked up only when the text
uses them.
"""
from __future__ import annotations

import pathlib
import re
import sys
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

DIR = hubconf.ROOT / "templates" / "prompts"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
LANGUAGE_NAMES = {"zh-Hant": "Traditional Chinese (zh-Hant)", "en": "English"}
# the to-do block's token set per content language; key_marker's {owner} and {key} are filled per use
TOKENS = {
    "zh-Hant": {"todo_heading": "## 待辦", "tag": "〔{}〕", "tbd": "待定", "todo_none": "- (無)",
                "key_marker": "(等 {owner}:{key})"},
    "en": {"todo_heading": "## To-do", "tag": "[{}]", "tbd": "tbd", "todo_none": "- (none)",
           "key_marker": "(waiting on {owner}: {key})"},
}
LANGUAGE_KEYS = {"content_language", "todo_heading", "tag_owner", "tag_agent", "tag_tbd", "todo_none", "key_marker",
                 "key_pattern"}


# Until step 6 (docs/design/i18n.md) every reader of STATE.md's to-do block (check-state-todo, gen-todo, registrar, the
# assistant) knows only the zh-Hant set, so the tokens stay zh-Hant whatever the content language; the prose follows
# content_language. Step 6 makes this TOKENS[lang] once the readers accept both sets.
TOKEN_SET = "zh-Hant"


def language_values(lang: str, owner: str) -> dict[str, str]:
    t = TOKENS[TOKEN_SET]
    return {"content_language": LANGUAGE_NAMES[lang], "todo_heading": t["todo_heading"],
            "tag_owner": t["tag"].format(owner), "tag_agent": t["tag"].format("agent"), "tag_tbd": t["tag"].format(t["tbd"]),
            "todo_none": t["todo_none"], "key_marker": t["key_marker"].format(owner=owner, key="task-…"),
            "key_pattern": t["key_marker"].format(owner=owner, key="<key>")}


def fill(text: str, *, lang: str | None = None, owner: str | None = None, assistant: str | None = None,
         **values) -> str:
    used = set(PLACEHOLDER.findall(text))
    if not used:
        return text
    owner = owner if owner is not None else hubconf.owner_tag()
    known = {"owner": owner, "assistant": assistant if assistant is not None else hubconf.assistant_name(),
             "owner_alias": hubconf.owner_alias()}
    if used & LANGUAGE_KEYS:
        known |= language_values(lang or hubconf.lang_content(), owner)
    known |= {k: str(v) for k, v in values.items()}
    missing = sorted(used - known.keys())
    if missing:
        raise KeyError(f"no value for {', '.join(missing)}")
    return PLACEHOLDER.sub(lambda m: known[m.group(1)], text)


def render(name: str, **kw) -> str:
    """templates/prompts/<name>.md filled (the file's last newline is not part of the prompt)"""
    try:
        return fill((DIR / f"{name}.md").read_text().removesuffix("\n"), **kw)
    except KeyError as e:
        raise KeyError(f"prompt {name}: {e.args[0]}") from None


def agent_rules(rules: list[str], *, read: Callable[[str], str | None] | None = None,
                lang: str | None = None) -> tuple[str, list[str]]:
    """The platform's rules for a project's agents, what sync-local-setup.sh puts between the rules file's
    agent-hub:begin/end markers: templates/agent-instructions.md without its title (the first two lines), then each
    templates/rules/<name>.md listed, filled. Returns (text, the listed names that have no file).
    read(<path under the hub root>) -> its text, or None when there is no such file (default: this checkout; an eval
    passes an older version's)."""
    def here(rel: str) -> str | None:
        p = hubconf.ROOT / rel
        return p.read_text() if p.exists() else None
    read = read or here
    body = "\n".join(read("templates/agent-instructions.md").splitlines()[2:]).strip("\n")
    missing = []
    for name in rules:
        text = read(f"templates/rules/{name}.md")
        if text is None:
            missing.append(name)
        else:
            body += "\n\n" + text.strip("\n")
    return fill(body, lang=lang), missing
