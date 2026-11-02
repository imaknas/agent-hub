#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Owner-facing messages in the instance's UI language (docs/design/i18n.md).

Code holds the English text (the msgid). Each script is its own domain with its own catalog,
`locales/<lang>/<domain>.json` ({msgid: translation}), so two scripts never compete for one msgid.

    import i18n
    _ = i18n.domain(__file__)                        # domain = the script's file name without .py
    print(_("{n} open letters for {project}", n=3, project=p))
    _c = i18n.domain(__file__, content=True)         # content (notes, record texts): in language.content instead
    _.variants("claude not found")                   # the msgid and every catalog's text: to recognise what another
                                                     # process printed in whatever language it ran in

Lookup: the catalog of `hubconf.lang_ui()`; a missing entry (or language en) gives the msgid itself. With keyword
arguments the result is `str.format`ted, so placeholders are `{name}` (literal braces then `{{ }}`); without them the
text is returned as is. Translations keep exactly the msgid's placeholders (tests/test_i18n_catalog.py), except plural
choices, which a language without plural forms (zh-Hant) leaves out, and NAMES: where the English says "the owner" or
"the assistant", a translation may write {owner} / {assistant} instead, filled with the configured names (the
settings' owner.tag, assistant.name), so no catalog carries a person's name. {settings} is the settings file's name as
this instance has it (brunna.json, or {settings} on an install from before the rename), in any message.

Plurals: `{n:problem|problems}` is the first form when n == 1, else the second; it prints no number, so the count is
written next to it: `_("{n} {n:problem|problems} found", n=n)`. A verb that agrees goes into the choice too:
`{n:letter is|letters are}`.

The zh-Hant catalogs were built from the Chinese text the code had before (msgstr = that text, verbatim), which is what
keeps an instance on zh-Hant byte-identical (tests/test_i18n_golden.py).

    i18n.py msg <domain> <msgid> [name=value ...]    for shell scripts: print the message (no newline added)
    i18n.py content <domain> <msgid> [name=value ...]    the same in the content language
"""
from __future__ import annotations

import json
import pathlib
import string
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"
_catalogs: dict[tuple[str, str], dict[str, str]] = {}


def lang() -> str:
    """The UI language. If hub.json itself cannot be read (config-check reporting exactly that), the environment's
    AGENT_HUB_LANG_UI or en: an error message must not fail because the config it is about is broken."""
    if str(ROOT / "bin") not in sys.path:
        sys.path.insert(0, str(ROOT / "bin"))
    try:
        import hubconf
    except (OSError, ValueError):
        import names
        return names.env("LANG_UI") or "en"
    return hubconf.lang_ui()


def catalog(name: str, language: str) -> dict[str, str]:
    key = (language, name)
    if key not in _catalogs:
        f = LOCALES / language / f"{name}.json"
        _catalogs[key] = json.loads(f.read_text()) if language != "en" and f.exists() else {}
    return _catalogs[key]


def domain_name(file: str) -> str:
    p = pathlib.Path(file)
    return p.stem if p.suffix == ".py" else p.name


class _Formatter(string.Formatter):
    def format_field(self, value, spec):
        if "|" in spec:   # a plural choice: one|other
            one, other = spec.split("|", 1)
            try:
                is_one = float(value) == 1   # shell passes "1"
            except (TypeError, ValueError):
                is_one = False
            return one if is_one else other
        return super().format_field(value, spec)


_format = _Formatter().format


# placeholders a translation may add although its msgid doesn't have them: the configured names, and the settings
# file's name (hubconf.config_path: whichever spelling this instance has)
NAMES = ("owner", "assistant", "settings")


def names(text: str, kw: dict) -> dict[str, str]:
    """The NAMES this text uses that the caller didn't pass, with their configured values."""
    want = [n for n in NAMES if n not in kw and "{" + n + "}" in text]
    if not want:
        return {}
    if str(ROOT / "bin") not in sys.path:
        sys.path.insert(0, str(ROOT / "bin"))
    try:
        import hubconf
    except Exception:   # the settings file can't be read (config-check reports exactly that): its name by this release
        import names as table
        return {n: table.current("config_file") if n == "settings" else "" for n in want}
    get = {"owner": hubconf.owner_tag, "assistant": hubconf.assistant_name,
           "settings": lambda: hubconf.config_path().name}
    return {n: get[n]() for n in want}


def translate(name: str, msgid: str, kw: dict, language: str | None = None) -> str:
    text = catalog(name, language or lang()).get(msgid, msgid)
    extra = names(text, kw)
    if kw:
        return _format(text, **extra, **kw)
    # unformatted text keeps its other braces (a usage text's JSON examples): only the names are filled in
    for n, v in extra.items():
        text = text.replace("{" + n + "}", v)
    return text


def languages() -> list[str]:
    """the languages that have catalogs (en has none: its text is the msgid)"""
    return sorted(d.name for d in LOCALES.iterdir() if d.is_dir()) if LOCALES.is_dir() else []


def content_lang() -> str:
    """The content language (hub.json language.content): what models and agents read and write."""
    if str(ROOT / "bin") not in sys.path:
        sys.path.insert(0, str(ROOT / "bin"))
    import hubconf
    return hubconf.lang_content()


def domain(file: str, content: bool = False):
    """content=True: the texts are content, not messages for the owner (a note written into a project's memory, the
    text of a record another agent reads): looked up in the content language, same catalog."""
    name = domain_name(file)

    def _(msgid: str, **kw) -> str:
        return translate(name, msgid, kw, content_lang() if content else None)

    def variants(msgid: str) -> list[str]:
        """the msgid and its text in every catalog, whatever this process's language: for matching text another
        process wrote (a message without placeholders)"""
        return list(dict.fromkeys([msgid, *(translate(name, msgid, {}, lang) for lang in languages())]))
    _.domain = name
    _.variants = variants
    return _


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] in ("msg", "content"):
        kw = dict(a.split("=", 1) for a in argv[3:])
        sys.stdout.write(translate(argv[1], argv[2], kw, content_lang() if argv[0] == "content" else None))
        return 0
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    print("usage: i18n.py msg|content <domain> <msgid> [name=value ...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
