"""E1 golden (docs/design/i18n.md) for the small adapters, on zh-Hant: runtime_command's run/complete/health errors
(no template, command not found, timeout, no output), runtime_fake's login/resume/credential texts, provider_ollama's
missing-model health note, provider_openai's no-choices error, and systemd_units' timer description.

In-process; the command runtime runs real tiny commands (true, sleep), the providers' HTTP call is replaced.

    bin/py -m unittest tests.test_i18n_golden_adapters
    I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_adapters    # regenerate (only before a conversion)
"""
import os
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import hubconf  # noqa: E402
import provider_ollama  # noqa: E402
import provider_openai  # noqa: E402
import runtime_command  # noqa: E402
import runtime_fake  # noqa: E402
import systemd_units  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "i18n-golden"
UPDATE = os.environ.get("I18N_GOLDEN_UPDATE") == "1"
ZH = {"AGENT_HUB_LANG_UI": "zh-Hant"}


def task(**kw):
    t = dict(prompt="p", output_schema=None, model=None, cwd=None, env=None, timeout_s=10)
    t.update(kw)
    return types.SimpleNamespace(**t)


def req(**kw):
    r = dict(prompt="p", schema=None, model="m", system="", max_tokens=10, timeout_s=10, point="x")
    r.update(kw)
    return types.SimpleNamespace(**r)


class Golden(unittest.TestCase):
    maxDiff = None

    def check(self, name, out):
        text = "".join(f"=== {k}\n{v}\n" for k, v in out)
        f = GOLDEN / f"{name}.txt"
        if UPDATE or not f.exists():
            f.write_text(text)
            if not UPDATE:
                self.fail(f"new golden written: {f.relative_to(ROOT)} (review it, then rerun)")
            return
        self.assertEqual(text, f.read_text())

    def test_runtime_command(self):
        out = []

        def conf(c):
            return mock.patch.object(hubconf, "get", lambda key, default=None: c if key == "runtime.command" else default)

        with mock.patch.dict(os.environ, ZH):
            with conf({}):
                out.append(("health: no template", runtime_command.health({})))
                try:
                    runtime_command.run(task())
                except RuntimeError as e:
                    out.append(("run: no template", str(e)))
            with conf({"run": ["/nonexistent/agent-cli"]}):
                out.append(("health: not found", runtime_command.health({})))
                out.append(("run: not found", runtime_command.run(task()).error))
                out.append(("complete: not found", runtime_command.complete(req(), {}).error))
            with conf({"run": ["sleep", "5"], "stdin": False}):
                out.append(("run: timeout", runtime_command.run(task(timeout_s=1)).error))
            with conf({"run": ["true"]}):
                out.append(("run: no output, empty stderr", runtime_command.run(task()).error))
                out.append(("complete: no output", runtime_command.complete(req(), {}).error))
            with conf({"run": ["sh", "-c", "echo oops >&2"], "stdin": False}):
                out.append(("run: no output, stderr", runtime_command.run(task()).error))
                out.append(("complete: no output, stderr", runtime_command.complete(req(), {}).error))
        self.check("runtime_command", out)

    def test_runtime_fake(self):
        out = []
        runtime_fake.reset()
        self.addCleanup(runtime_fake.reset)
        with mock.patch.dict(os.environ, ZH):
            out.append(("classify_exit login", runtime_fake.classify_exit(1, "login needed").data))
            out.append(("classify_exit crash", runtime_fake.classify_exit(2, "boom").data))
            out.append(("resume_command", runtime_fake.resume_command("s1")))
            runtime_fake.set_credential("token", False)
            out.append(("auth_env missing", runtime_fake.auth_env("token", {})))
            out.append(("credentials", [(c.name, c.label) for c in runtime_fake.credentials()]))
        self.check("runtime_fake", out)

    def test_providers_and_units(self):
        out = []
        tags = {"models": [{"name": "have:latest"}]}
        with mock.patch.dict(os.environ, ZH):
            with mock.patch.object(provider_ollama, "call", lambda *a: (200, tags, {}, "")):
                out.append(("ollama health: missing models",
                            provider_ollama.health({"models": {"a": "have", "b": "qwen3:8b", "c": {"name": "x:1b"}}})))
            with mock.patch.object(provider_openai, "call", lambda *a: (200, {"choices": []}, {}, "")):
                out.append(("openai: no choices", provider_openai.complete(req(), {}).error))
            plist = {"Label": "local.x.tick", "ProgramArguments": ["/bin/true"], "StartInterval": 900, "RunAtLoad": True}
            out.append(("systemd timer", systemd_units.files(plist)["local.x.tick.timer"]))
        self.check("adapters", out)


if __name__ == "__main__":
    sys.exit(unittest.main())
