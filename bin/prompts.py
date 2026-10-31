#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Prompts of the judgment points (docs/design/i18n.md, step 5): English templates in templates/prompts/<name>.md.

    import prompts
    text = prompts.render("librarian-state", project=p, memory_section=m, ...)

A template's placeholders are `{{name}}` (single braces stay text: prompts carry JSON examples). render() fills, besides
the values passed in:
  owner, owner_alias, assistant     the configured names (hubconf.fill_names), or the ones passed in (an eval pins them)
  content_language                  the language the model writes in: hub.json language.content, or lang=
  todo_heading, tag_owner, tag_agent, tag_tbd, todo_none, key_marker
                                    the to-do block's tokens (contracts/state-todo.v1.md): the zh-Hant set until every
                                    reader accepts the English one too (step 6, TOKEN_SET)
A placeholder left unfilled is an error, not text sent to a model. Values are inserted in one pass: a `{{…}}` inside a
value (a memory diff, a letter) stays as it is.
"""
from __future__ import annotations

import pathlib
import re
import sys

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


# Until step 6 (docs/design/i18n.md) every reader of STATE.md's to-do block (check-state-todo, gen-todo, registrar, the
# assistant) knows only the zh-Hant set, so the tokens stay zh-Hant whatever the content language; the prose follows
# content_language. Step 6 makes this TOKENS[lang] once the readers accept both sets.
TOKEN_SET = "zh-Hant"


def language_values(lang: str, owner: str) -> dict[str, str]:
    t = TOKENS[TOKEN_SET]
    return {"content_language": LANGUAGE_NAMES[lang], "todo_heading": t["todo_heading"],
            "tag_owner": t["tag"].format(owner), "tag_agent": t["tag"].format("agent"), "tag_tbd": t["tag"].format(t["tbd"]),
            "todo_none": t["todo_none"], "key_marker": t["key_marker"].format(owner=owner, key="task-…")}


def render(name: str, *, lang: str | None = None, owner: str | None = None, assistant: str | None = None,
           **values: str) -> str:
    owner = owner if owner is not None else hubconf.owner_tag()
    assistant = assistant if assistant is not None else hubconf.assistant_name()
    lang = lang or hubconf.lang_content()
    fill = {"owner": owner, "assistant": assistant, "owner_alias": hubconf.fill_names("{{owner_alias}}"),
            **language_values(lang, owner), **{k: str(v) for k, v in values.items()}}
    text = (DIR / f"{name}.md").read_text().removesuffix("\n")   # the file's last newline is not part of the prompt
    missing = sorted({m for m in PLACEHOLDER.findall(text) if m not in fill})
    if missing:
        raise KeyError(f"prompt {name}: no value for {', '.join(missing)}")
    return PLACEHOLDER.sub(lambda m: fill[m.group(1)], text)
