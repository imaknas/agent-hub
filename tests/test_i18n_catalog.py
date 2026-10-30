"""E0 of docs/design/i18n.md: the message catalogs match the code.

For every script that does `_ = i18n.domain(__file__)`: each `_("…")` msgid is English, has an entry in each
non-English catalog (`locales/<lang>/<domain>.json`), the entry keeps exactly the msgid's placeholders, the call passes
exactly those placeholders, and the catalog has no entries the code no longer uses.

    bin/py -m unittest tests.test_i18n_catalog
"""
import ast
import json
import pathlib
import re
import string
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402

CJK = re.compile("[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
LANGS = [d.name for d in sorted(i18n.LOCALES.iterdir()) if d.is_dir()] if i18n.LOCALES.is_dir() else []


def fields(text: str) -> set[tuple[str, str, str | None]]:
    """(name, format spec, conversion) of each placeholder: {cost:.6f} and {cost} are not the same placeholder"""
    return {(f, spec or "", conv) for _lit, f, spec, conv in string.Formatter().parse(text) if f is not None}


def placeholders(text: str) -> set[str]:
    return {f for f, _spec, _conv in fields(text)}


def sources():
    for f in sorted((ROOT / "bin").rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts or f.name == "i18n.py":
            continue
        text = f.read_text(errors="ignore")
        if "i18n.domain(" in text and (f.suffix == ".py" or text.startswith("#!/bin/sh -c exec")):
            yield f, text


def messages(tree: ast.AST) -> tuple[set[str], list[tuple[int, str, set[str] | None]]]:
    """names bound to i18n.domain(...), and the (line, msgid, keyword names) of each call through them"""
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "domain"
             for t in n.targets if isinstance(t, ast.Name)}
    # a msgid may also be the module docstring (`_(__doc__)`, usage text) or a module-level string constant
    consts = {t.id: n.value.value for n in getattr(tree, "body", []) if isinstance(n, ast.Assign)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
              for t in n.targets if isinstance(t, ast.Name)}
    consts["__doc__"] = ast.get_docstring(tree, clean=False)
    calls = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names:
            arg = n.args[0] if n.args else None
            msgid = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else \
                consts.get(arg.id) if isinstance(arg, ast.Name) else None
            kws = None if any(k.arg is None for k in n.keywords) else {k.arg for k in n.keywords}
            calls.append((n.lineno, msgid, kws))
    return names, calls


class Catalogs(unittest.TestCase):
    def test_code_and_catalogs_match(self):
        problems, seen = [], {}
        for f, text in sources():
            rel = f.relative_to(ROOT)
            dom = i18n.domain_name(str(f))
            names, calls = messages(ast.parse(text))
            if not names:
                continue
            # rebinding the name (`for _, x in …`, `_ = f()`) breaks every later _() in that scope
            tree = ast.parse(text)
            ok = {id(t) for a in ast.walk(tree) if isinstance(a, ast.Assign) and isinstance(a.value, ast.Call)
                  and getattr(a.value.func, "attr", None) == "domain" for t in a.targets}
            problems += [f"{rel}:{n.lineno}: `{n.id}` is rebound; it is the message function here" for n in ast.walk(tree)
                         if isinstance(n, ast.Name) and n.id in names and isinstance(n.ctx, ast.Store) and id(n) not in ok]
            used = seen.setdefault(dom, set())
            for line, msgid, kws in calls:
                where = f"{rel}:{line}"
                if msgid is None:
                    problems.append(f"{where}: the msgid must be a string literal, __doc__ or a module-level string")
                    continue
                used.add(msgid)
                if CJK.search(msgid):
                    problems.append(f"{where}: msgid is not English: {msgid[:50]}")
                if kws is not None and kws != placeholders(msgid) and (kws or placeholders(msgid)):
                    problems.append(f"{where}: passes {sorted(kws)}, msgid has {sorted(placeholders(msgid))}")
                for lang in LANGS:
                    cat = i18n.catalog(dom, lang)
                    if msgid not in cat:
                        problems.append(f"{where}: no {lang} entry for {msgid[:60]!r}")
                    elif fields(cat[msgid]) != fields(msgid):
                        problems.append(f"{where}: {lang} placeholders differ for {msgid[:60]!r}")
        for lang in LANGS:
            for cat_file in sorted((i18n.LOCALES / lang).glob("*.json")):
                stale = set(json.loads(cat_file.read_text())) - seen.get(cat_file.stem, set())
                problems += [f"{cat_file.relative_to(ROOT)}: unused entry {m[:60]!r}" for m in sorted(stale)]
        self.assertEqual(problems, [])

    def test_lookup(self):
        _ = i18n.domain("/x/bin/demo-tool")
        self.assertEqual(_.domain, "demo-tool")
        self.assertEqual(i18n.translate("no-such-domain", "Hello {name}", {"name": "a"}, "zh-Hant"), "Hello a")
        self.assertEqual(i18n.translate("no-such-domain", "{literal}", {}, "zh-Hant"), "{literal}", "no kw, no format")


if __name__ == "__main__":
    unittest.main()
