"""agent-hub runs the conformance data of the Acts Markdown binding (agent-ops-specs) against bin/acts_md.py, the
reader and document steps bin/registrar.py and bin/gen-todo.py use: 0.6.2's (acts-markdown/conformance, source in
contracts/vendor/agent-ops-specs/principal-handoff-0.6.2/SOURCE), 0.5's (principal-handoff-0.5/SOURCE), and 0.4's
(principal-handoff-0.4/acts/SOURCE), whose rules 0.5 and 0.6 keep for every key that is bound (0.5 adds known keys; 0.6
changes no rule of the binding, acts-markdown §6).

Every case runs twice: in the case's own profile alone, built from the spec's profiles.json (what the spec tests), and
in agent-hub's reading, both token sets at once with the content language's set writing (what runs here). For the
second, a zh-hant case's `Owner` is replaced with this instance's owner tag, and an en case runs only where its
principal_tag is the en profile's default `owner` (agent-hub's en set keeps the default, bin/todo_tokens.py).
Acts' records.json and lifecycle.json are the record layer's: tests/test_ph_records.py runs them.

    bin/py -m unittest discover -s tests
"""
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENDOR = ROOT / "contracts" / "vendor" / "agent-ops-specs"
FIXTURES = {"0.6.2": VENDOR / "principal-handoff-0.6.2" / "acts-markdown" / "conformance",
            "0.5": VENDOR / "principal-handoff-0.5" / "acts-markdown" / "conformance",
            "0.4": VENDOR / "principal-handoff-0.4" / "acts"}
sys.path.insert(0, str(ROOT / "bin"))
import acts_md  # noqa: E402
import todo_tokens  # noqa: E402

spec = importlib.util.spec_from_file_location("registrar_conf", ROOT / "bin" / "registrar.py")
registrar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registrar)
TAG = registrar.TAG
SPEC = json.loads((FIXTURES["0.6.2"] / "profiles.json").read_text())["profiles"]
LANG = {"zh-hant": "zh-Hant", "en": "en"}


def cases(file: str) -> list[tuple[str, dict]]:
    """(version, case) of both versions' fixtures"""
    return [(v, c) for v, d in FIXTURES.items() for c in json.loads((d / file).read_text())["cases"]]


def doc(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def nonblank(text: str) -> list[str]:
    """acts §9: documents compare as their non-blank lines, trailing whitespace removed."""
    return [l.rstrip() for l in text.split("\n") if l.strip()]


def bound(c: dict) -> set | None:
    return set(c["bound"]) if "bound" in c else None


def known(c: dict) -> set:
    """keys outside the binding whose need records the registrar holds (0.5, step 3)"""
    return set(c.get("known", []))


def own(x, tag: str):
    """the zh-hant data's tag `Owner` -> tag, in labels and markers"""
    if isinstance(x, str):
        zh = todo_tokens.tokens("zh-Hant")
        lead = zh["key_marker"].split("{owner}")[0][1:]
        for a, b in ((todo_tokens.bracket("zh-Hant", "Owner"), todo_tokens.bracket("zh-Hant", tag)),
                     (lead + "Owner", lead + tag), (lead.rstrip() + "Owner", lead.rstrip() + tag)):
            x = x.replace(a, b)
        return x
    if isinstance(x, list):
        return [own(v, tag) for v in x]
    if isinstance(x, dict):
        return {k: own(v, tag) for k, v in x.items()}
    return x


def readings(c: dict):
    """(name, translate, reading profiles, writer) for the case: the spec's profile alone, then agent-hub's"""
    p = acts_md.Profile.from_json(SPEC[c["profile"]], **(c.get("parameters") or {}))
    yield "spec", (lambda x: x), (p,), p
    params = {**SPEC[c["profile"]].get("parameters", {}), **(c.get("parameters") or {})}
    if c["profile"] == "zh-hant":
        if params["principal_tag"] == "Owner":
            yield "agent-hub", (lambda x: own(x, TAG)), acts_md.profiles(TAG), acts_md.profile("zh-Hant", TAG)
    elif params["principal_tag"] == "owner":
        yield "agent-hub", (lambda x: x), acts_md.profiles(TAG), acts_md.profile("en", TAG)


class Profiles(unittest.TestCase):
    def test_the_profiles_did_not_change(self):
        for v in ("0.5", "0.4"):
            self.assertEqual(SPEC, json.loads((FIXTURES[v] / "profiles.json").read_text())["profiles"], v)

    def test_agent_hubs_sets_are_the_spec_profiles(self):
        self.assertEqual(acts_md.profile("zh-Hant", "Owner"), acts_md.Profile.from_json(SPEC["zh-hant"]))
        self.assertEqual(acts_md.profile("zh-Hant", "Alex"),
                         acts_md.Profile.from_json(SPEC["zh-hant"], principal_tag="Alex"))
        self.assertEqual(acts_md.profile("en", "Alex"), acts_md.Profile.from_json(SPEC["en"]),
                         "the en set keeps the default principal_tag, whatever the owner tag")


class Binding(unittest.TestCase):
    def each(self, file: str, check) -> None:
        n = 0
        for version, c in cases(file):
            for name, tr, ps, writer in readings(c):
                n += 1
                with self.subTest(version=version, case=c["id"], reading=name):
                    check(c, tr, ps, writer)
        self.assertTrue(n)

    def test_parse(self):
        def check(c, tr, ps, writer):
            self.assertEqual(acts_md.parse(doc(tr(c["document"])), ps), tr(c["expect"]))
        self.each("parse.json", check)

    def test_keys(self):
        for version, c in cases("keys.json"):
            with self.subTest(version=version, case=c["id"]):
                self.assertEqual(registrar.task_key(c["project"], c["text"]), c["key"])

    def test_register(self):
        def check(c, tr, ps, writer):
            out, raised, reported = acts_md.register(doc(tr(c["document"])), c["project"], ps, writer, bound(c),
                                                     registrar.task_key, known(c))
            self.assertEqual([{"key": r["key"], "text": r["text"]} for r in raised], tr(c["expect"]["raised"]))
            self.assertEqual(reported, c["expect"]["reported"])
            self.assertEqual(nonblank(out), nonblank(doc(tr(c["expect"]["document"]))))
            b = None if bound(c) is None else bound(c) | {r["key"] for r in raised}
            self.assertEqual(acts_md.register(out, c["project"], ps, writer, b, registrar.task_key, known(c))[:2],
                             (out, []), "idempotent")
        self.each("register.json", check)

    def test_relink(self):
        def check(c, tr, ps, writer):
            out, relinked = acts_md.relink(doc(tr(c["document"])), tr(c["active"]), ps, writer, bound(c), known(c))
            self.assertEqual(relinked, c["expect"]["relinked"])
            self.assertEqual(nonblank(out), nonblank(doc(tr(c["expect"]["document"]))))
        self.each("relink.json", check)

    def test_amend(self):
        def check(c, tr, ps, writer):
            self.assertEqual(acts_md.amend(doc(tr(c["document"])), tr(c["latest"]), ps), tr(c["expect"]))
        self.each("amend.json", check)

    def test_apply(self):
        def check(c, tr, ps, writer):
            out, changed = acts_md.apply(doc(tr(c["document"])), tr(c["record"]), ps, writer, tr(c.get("latest")),
                                         bound(c))
            self.assertEqual(changed, c["expect"]["changed"])
            self.assertEqual(nonblank(out), nonblank(doc(tr(c["expect"]["document"]))))
        self.each("apply.json", check)

    def test_reconcile(self):
        def check(c, tr, ps, writer):
            got = acts_md.reconcile(doc(tr(c["document"])), tr(c["open"]), ps, bound(c))
            self.assertEqual([{"key": x["key"], "action": x["action"]} for x in got], c["expect"])
        self.each("reconcile.json", check)


if __name__ == "__main__":
    unittest.main()
