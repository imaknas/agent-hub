#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Owner-facing messages in the instance's UI language (docs/design/i18n.md).

Code holds the English text (the msgid). Each script is its own domain with its own catalog,
`locales/<lang>/<domain>.json` ({msgid: translation}), so two scripts never compete for one msgid.

    import i18n
    _ = i18n.domain(__file__)                        # domain = the script's file name without .py
    print(_("{n} open letters for {project}", n=3, project=p))

Lookup: the catalog of `hubconf.lang_ui()`; a missing entry (or language en) gives the msgid itself. With keyword
arguments the result is `str.format`ted, so placeholders are `{name}` (literal braces then `{{ }}`); without them the
text is returned as is. Translations keep exactly the msgid's placeholders (tests/test_i18n_catalog.py).

The zh-Hant catalogs were built from the Chinese text the code had before (msgstr = that text, verbatim), which is what
keeps an instance on zh-Hant byte-identical (tests/test_i18n_golden.py).

    i18n.py msg <domain> <msgid> [name=value ...]    for shell scripts: print the message (no newline added)
"""
from __future__ import annotations

import json
import os
import pathlib
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
        return os.environ.get("AGENT_HUB_LANG_UI") or "en"
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


def translate(name: str, msgid: str, kw: dict, language: str | None = None) -> str:
    text = catalog(name, language or lang()).get(msgid, msgid)
    return text.format(**kw) if kw else text


def domain(file: str):
    name = domain_name(file)

    def _(msgid: str, **kw) -> str:
        return translate(name, msgid, kw)
    _.domain = name
    return _


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "msg":
        kw = dict(a.split("=", 1) for a in argv[3:])
        sys.stdout.write(translate(argv[1], argv[2], kw))
        return 0
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    print("usage: i18n.py msg <domain> <msgid> [name=value ...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
