"""What run-agent-job.sh hands to llm.py (characterization): the judgment point, the prompt file, the extra runtime
arguments, and the environment that decides the login and the allow list.

Pinned before the connectors jobs' MCP deny (docs/design/evaluators.md step 0) so that change cannot move anything
here: a job that is not a connectors job (no LLM_AUTH=subscription) keeps the sealing flag after `--`, exactly as
before; a connectors job gets no extra arguments. Runs the real script against a temp hub whose llm.py only records
what it was given, with /bin/bash: the background run always re-executes itself with it (macOS: bash 3.2).

    bin/py -m unittest tests.test_run_agent_job_args
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
PY = os.environ.get("AGENT_HUB_PYTHON") or sys.executable
BASH = "/bin/bash"
_v = subprocess.run([BASH, "-c", "echo ${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"], capture_output=True, text=True).stdout
OLD_BASH = tuple(int(x) for x in _v.strip().split(".")) < (4, 4)
# Found while pinning this: on bash < 4.4 an empty array under `set -u` is an unbound variable, so a connectors job
# (no sealing argument) dies at the llm.py line and its status stays "running"
crashes_on_old_bash = unittest.expectedFailure if OLD_BASH else (lambda f: f)

RECORDER = """import json, os, sys
rec = {"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in
       ("LLM_AUTH", "AGENT_JOB_ALLOW", "AGENT_JOB_POINT", "CLAUDE_CODE_OAUTH_TOKEN")}, "cwd": os.getcwd()}
pathlib = __import__("pathlib")
pathlib.Path(os.environ["RECORD_TO"]).write_text(json.dumps(rec))
print(json.dumps({"result": "fine", "session_id": "s-1", "num_turns": 1, "duration_ms": 1000}))
"""
# event jobs: the prompt is rebuilt with the events; the fake only copies it
EVENTS = """import shutil, sys
_, src, dst = sys.argv[1:4]
shutil.copy(src, dst)
"""


class RunAgentJobArgs(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.hub = self.tmp / "hub"
        (self.hub / "bin").mkdir(parents=True)
        for f in ("py", "hubpaths.sh", "i18n.py", "hubconf.py", "run-agent-job.sh"):
            (self.hub / "bin" / f).symlink_to(BIN / f)
        for f, body in (("llm.py", RECORDER), ("gen-status-page.py", "pass\n"), ("event_jobs.py", EVENTS)):
            (self.hub / "bin" / f).write_text(body)
        (self.hub / "agent-jobs").mkdir()
        self.prompt = self.hub / "agent-jobs" / "job-a.md"
        self.prompt.write_text("do it\n")
        for d in ("home", "config", "data", "secrets", "state", "ws"):
            (self.tmp / d).mkdir()
        (self.tmp / "config" / "hub.json").write_text(json.dumps({"owner": {"name": "alex", "tag": "Alex"}}))
        self.record = self.tmp / "record.json"
        self.env = {**{k: v for k, v in os.environ.items()
                       if not k.startswith(("AGENT_HUB_", "LLM_AUTH", "AGENT_JOB_", "CLAUDE_CODE_OAUTH"))},
                    "AGENT_HUB_PYTHON": PY, "AGENT_HUB_ROOT": str(self.hub), "HOME": str(self.tmp / "home"),
                    "AGENT_HUB_CONFIG": str(self.tmp / "config"), "AGENT_HUB_DATA": str(self.tmp / "data"),
                    "AGENT_HUB_SECRETS": str(self.tmp / "secrets"), "AGENT_HUB_STATE": str(self.tmp / "state"),
                    "RUN_AGENT_JOB_FOREGROUND": "1", "RECORD_TO": str(self.record),
                    "CLAUDE_CODE_OAUTH_TOKEN": "inherited-not-a-token"}

    def run_job(self, *args, **env) -> dict:
        r = subprocess.run([BASH, str(self.hub / "bin" / "run-agent-job.sh"), *args],
                           env={**self.env, **env}, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(self.record.read_text())

    def test_default_job_is_sealed_from_ambient_mcp(self):
        rec = self.run_job("job-a", str(self.tmp / "ws"))
        self.assertEqual(rec["argv"], ["run", "agent-job", "--prompt-file", str(self.prompt), "--",
                                       "--strict-mcp-config"])
        self.assertEqual(pathlib.Path(rec["cwd"]).resolve(), (self.tmp / "ws").resolve())
        self.assertIsNone(rec["env"]["LLM_AUTH"])

    def test_token_login_is_sealed_too(self):
        rec = self.run_job("job-a", str(self.tmp / "ws"), LLM_AUTH="token")
        self.assertEqual(rec["argv"][-2:], ["--", "--strict-mcp-config"])
        self.assertEqual(rec["env"]["LLM_AUTH"], "token")

    @crashes_on_old_bash
    def test_connectors_job_gets_no_extra_arguments(self):
        rec = self.run_job("job-a", str(self.tmp / "ws"), LLM_AUTH="subscription")
        self.assertEqual(rec["argv"], ["run", "agent-job", "--prompt-file", str(self.prompt)])
        self.assertEqual(rec["env"]["LLM_AUTH"], "subscription")

    def test_allow_list_and_point_pass_through_the_environment(self):
        allow = json.dumps(["Read(./x/**)", "ToolSearch"])
        rec = self.run_job("job-a", str(self.tmp / "ws"), AGENT_JOB_ALLOW=allow, AGENT_JOB_POINT="mailbox-escalation")
        self.assertEqual(rec["argv"][:2], ["run", "mailbox-escalation"])
        self.assertEqual(rec["argv"][-2:], ["--", "--strict-mcp-config"])
        self.assertEqual(rec["env"]["AGENT_JOB_ALLOW"], allow)

    @crashes_on_old_bash
    def test_the_runtime_credential_variable_is_not_inherited(self):
        rec = self.run_job("job-a", str(self.tmp / "ws"), LLM_AUTH="subscription")
        self.assertIsNone(rec["env"]["CLAUDE_CODE_OAUTH_TOKEN"])

    def test_registry_prompt_is_a_full_path(self):
        full = self.tmp / "config" / "projects" / "beta" / "jobs" / "notify.md"
        full.parent.mkdir(parents=True)
        full.write_text("notify\n")
        rec = self.run_job("beta-notify", str(self.tmp / "ws"), str(full))
        self.assertEqual(rec["argv"], ["run", "agent-job", "--prompt-file", str(full), "--", "--strict-mcp-config"])

    def test_event_run_gets_its_own_prompt_file(self):
        rec = self.run_job("job-a", str(self.tmp / "ws"), AGENT_HUB_EVENTS="[]", AGENT_JOB_POINT="agent-job-event")
        prompt = rec["argv"][rec["argv"].index("--prompt-file") + 1]
        self.assertNotEqual(prompt, str(self.prompt))
        self.assertTrue(prompt.endswith(".prompt.md"), prompt)
        self.assertEqual(rec["argv"][:2], ["run", "agent-job-event"])
        self.assertEqual(rec["argv"][-2:], ["--", "--strict-mcp-config"])


if __name__ == "__main__":
    unittest.main()
