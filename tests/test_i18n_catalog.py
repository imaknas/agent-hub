"""E0 of docs/design/i18n.md: the message catalogs match the code.

For every script that does `_ = i18n.domain(__file__)`: each `_("…")` msgid is English, has an entry in each
non-English catalog (`locales/<lang>/<domain>.json`), the entry keeps exactly the msgid's placeholders, the call passes
exactly those placeholders, and the catalog has no entries the code no longer uses. Shell scripts likewise for each
`hub_msg "…"` / `hub_say "…"` / `hub_content "…"` (bin/hubpaths.sh): the domain is the file's own name. Pages under
templates/ likewise for each `t("…", {name: …})` call in their script and their <title> (the domain is the page's file
name; bin/weekly-reflect.py fills the page's texts from that catalog).

    bin/py -m unittest tests.test_i18n_catalog
"""
import ast
import json
import pathlib
import re
import string
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import i18n  # noqa: E402

CJK = re.compile("[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
LANGS = [d.name for d in sorted(i18n.LOCALES.iterdir()) if d.is_dir()] if i18n.LOCALES.is_dir() else []


def fields(text: str) -> set[tuple[str, str, str | None]]:
    """(name, format spec, conversion) of each placeholder: {cost:.6f} and {cost} are not the same placeholder. Plural
    choices ({n:file|files}) are left out: a language without plural forms has none (the count itself stays a field)."""
    return {(f, spec or "", conv) for _lit, f, spec, conv in string.Formatter().parse(text)
            if f is not None and "|" not in (spec or "")}


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
        # `_.variants("…")`: the msgid in every language, never formatted (so it passes no placeholders)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "variants" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id in names:
            arg = n.args[0] if n.args else None
            msgid = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else \
                consts.get(arg.id) if isinstance(arg, ast.Name) else None
            calls.append((n.lineno, msgid, set()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names:
            arg = n.args[0] if n.args else None
            msgid = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else \
                consts.get(arg.id) if isinstance(arg, ast.Name) else None
            kws = None if any(k.arg is None for k in n.keywords) else {k.arg for k in n.keywords}
            calls.append((n.lineno, msgid, kws))
    return names, calls


SHELL_CALL = re.compile(r'\bhub_(?:msg|say|content)[ \t]+')


def shell_messages(text: str) -> list[tuple[int, str | None, set[str]]]:
    """(line, msgid, names passed as name=value) of each hub_msg / hub_say call in a shell script; msgid None = not a
    plain "…" literal. A call's arguments end at an unquoted ; | & ) > < or the end of the (continued) line."""
    text = text.replace("\\\n", " \r")   # a continued line: \r keeps the line count
    calls = []
    for m in SHELL_CALL.finditer(text):
        if text[max(0, m.start() - 1)] in "_\"'":
            continue
        line = text.count("\n", 0, m.start()) + text.count("\r", 0, m.start()) + 1
        rest = text[m.end():]
        lit = re.match(r'"((?:[^"\\$`]|\\["\\])*)"', rest)
        if not lit:
            calls.append((line, None, set()))
            continue
        msgid = re.sub(r'\\(["\\])', r"\1", lit.group(1))
        args, quote, i = "", None, lit.end()
        while i < len(rest):
            c = rest[i]
            if quote:
                if c == "\\" and quote == '"':
                    args += c + rest[i + 1:i + 2]
                    i += 2
                    continue
                quote = None if c == quote else quote
            elif c in "'\"":
                quote = c
            elif c in ";|&)><\n":
                break
            args += c
            i += 1
        calls.append((line, msgid, set(re.findall(r'(?:^|\s)([A-Za-z_]\w*)=', args))))
    return calls


def shell_sources():
    for f in sorted((ROOT / "bin").iterdir()):
        if not f.is_file() or f.name == "hubpaths.sh" or f.suffix == ".py":
            continue
        text = f.read_text(errors="ignore")
        if not text.startswith("#!/bin/sh -c exec") and SHELL_CALL.search(text):
            yield f, text


PAGE_MSG = re.compile(r'\bt\("')
PAGE_CALL = re.compile(r'\bt\("((?:[^"\\]|\\.)*)"(?:\s*,\s*\{([^{}]*)\})?\s*\)')


def page_messages(text: str) -> list[tuple[int, str | None, set[str]]]:
    """(line, msgid, names passed) of each t("…") call in a page and of its <title>; msgid None = a call this reader
    cannot take apart (keep the second argument a plain {name: value} object, without braces inside)"""
    calls = {m.start(): m for m in PAGE_CALL.finditer(text)}
    out = []
    for m in PAGE_MSG.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        c = calls.get(m.start())
        out.append((line, json.loads(f'"{c.group(1)}"'), set(re.findall(r"(\w+)\s*:", c.group(2) or ""))) if c
                   else (line, None, set()))
    if title := re.search(r"<title>(.*?)</title>", text):
        out.append((text.count("\n", 0, title.start()) + 1, title.group(1), set()))
    return out


def messages_doc(text: str) -> str | None:
    return ast.get_docstring(ast.parse(text), clean=False)


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
                # _(__doc__) without keywords is returned unformatted: braces in a usage text (JSON examples) are text
                if kws is not None and kws != placeholders(msgid) and (kws or placeholders(msgid)) \
                        and not (not kws and msgid == messages_doc(text)):
                    problems.append(f"{where}: passes {sorted(kws)}, msgid has {sorted(placeholders(msgid))}")
                for lang in LANGS:
                    cat = i18n.catalog(dom, lang)
                    if msgid not in cat:
                        problems.append(f"{where}: no {lang} entry for {msgid[:60]!r}")
                    elif fields(cat[msgid]) != fields(msgid):
                        problems.append(f"{where}: {lang} placeholders differ for {msgid[:60]!r}")
        for f, text in shell_sources():
            rel, used = f.relative_to(ROOT), seen.setdefault(f.name, set())
            for line, msgid, names in shell_messages(text):
                where = f"{rel}:{line}"
                if msgid is None:
                    problems.append(f"{where}: the msgid must be a plain \"…\" literal (no $ or backquote)")
                    continue
                used.add(msgid)
                if CJK.search(msgid):
                    problems.append(f"{where}: msgid is not English: {msgid[:50]}")
                if names != placeholders(msgid):
                    problems.append(f"{where}: passes {sorted(names)}, msgid has {sorted(placeholders(msgid))}")
                for lang in LANGS:
                    cat = i18n.catalog(f.name, lang)
                    if msgid not in cat:
                        problems.append(f"{where}: no {lang} entry for {msgid[:60]!r}")
                    elif fields(cat[msgid]) != fields(msgid):
                        problems.append(f"{where}: {lang} placeholders differ for {msgid[:60]!r}")
        for f in sorted((ROOT / "templates").glob("*.html")):
            rel, used = f.relative_to(ROOT), seen.setdefault(f.name, set())
            for line, msgid, names in page_messages(f.read_text()):
                where = f"{rel}:{line}"
                if msgid is None:
                    problems.append(f"{where}: a t() call this test cannot read")
                    continue
                used.add(msgid)
                if CJK.search(msgid):
                    problems.append(f"{where}: msgid is not English: {msgid[:50]}")
                if names != placeholders(msgid):
                    problems.append(f"{where}: passes {sorted(names)}, msgid has {sorted(placeholders(msgid))}")
                for lang in LANGS:
                    cat = i18n.catalog(f.name, lang)
                    if msgid not in cat:
                        problems.append(f"{where}: no {lang} entry for {msgid[:60]!r}")
                    elif fields(cat[msgid]) != fields(msgid):
                        problems.append(f"{where}: {lang} placeholders differ for {msgid[:60]!r}")
        # module descriptions (modules/*.yaml `description:`, shown by `agenthub modules`): domain "modules"
        used = seen.setdefault("modules", set())
        for f in sorted((ROOT / "modules").glob("*.yaml")):
            doc = yaml.safe_load(f.read_text()) or {}
            # what check-secrets shows: the description (`agenthub modules`) and each secret's rotate text
            for what, text in [("description", doc.get("description"))] + \
                    [(f"secret {s.get('name')} rotate", s.get("rotate")) for s in doc.get("secrets") or []]:
                if not text:
                    continue
                used.add(text)
                if CJK.search(text):
                    problems.append(f"modules/{f.name}: {what} is not English: {text[:50]}")
                problems += [f"modules/{f.name}: no {lang} entry for its {what}" for lang in LANGS
                             if text not in i18n.catalog("modules", lang)]
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

    def test_variants(self):
        """the msgid plus every catalog's text, whatever the UI language"""
        _ = i18n.domain(str(ROOT / "bin" / "runtime_claude.py"))
        want = ["claude not found", *(i18n.catalog("runtime_claude", lang)["claude not found"] for lang in LANGS)]
        self.assertEqual(_.variants("claude not found"), want)
        self.assertEqual(i18n.domain("/x/bin/no-such-domain").variants("Hi"), ["Hi"])

    def test_plural_choice(self):
        msg = "{n} {n:letter is|letters are} open"
        self.assertEqual(i18n.translate("no-such-domain", msg, {"n": 1}, "en"), "1 letter is open")
        self.assertEqual(i18n.translate("no-such-domain", msg, {"n": 3}, "en"), "3 letters are open")
        self.assertEqual(i18n.translate("no-such-domain", msg, {"n": "1"}, "en"), "1 letter is open", "from shell")
        self.assertEqual(fields(msg), {("n", "", None)}, "the choice is not a placeholder a translation must keep")
        self.assertEqual(placeholders(msg), {"n"})

    def test_page_extractor(self):
        src = ('<title>A page</title>\n<script>\nconst x = t("Hi {name}", { name: esc(p.name) }) + t("Plain");\n'
               'const y = t("Bad {a}", { a: f({}) });\n</script>\n')
        self.assertEqual(page_messages(src), [(3, "Hi {name}", {"name"}), (3, "Plain", set()), (4, None, set()),
                                              (1, "A page", set())])

    def test_shell_extractor(self):
        src = ('x="$(hub_msg "Hello {name}, \\"q\\"" name="$n")"; echo\n'
               'hub_say "a {x} and {y}" x="$(f)" \\\n  y=\'1;2\' >&2\n'
               'hub_say "bad $v"\n'
               '_hub_msg "$dom" "$id"\n'
               'echo "<!-- $(hub_content "two\nlines") -->"\n')
        self.assertEqual(shell_messages(src), [(1, 'Hello {name}, "q"', {"name"}), (2, "a {x} and {y}", {"x", "y"}),
                                               (4, None, set()), (6, "two\nlines", set())])


if __name__ == "__main__":
    unittest.main()
