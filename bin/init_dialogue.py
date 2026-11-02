"""How init asks its questions (docs/design/newcomer-path.md B3): one dialogue, several transports.

init's steps ask through the current transport (`ui()`), never through input()/getpass/isatty themselves, so a
question exists in every transport or in none:
  Terminal   the owner at a terminal: input(), getpass; with --yes or no terminal, every answer is its default and
             nothing is consented to
  Recorder   init --questions: walks the questions, answering each with what the agent's answers file says so far
             or its default, and acts on nothing; the list is printed as JSON for the agent to ask in its chat
  Agent      init --answers <file>: the chat answers arrive as init's own options; a consent or a secret is asked of
             the owner directly, never taken from the file, and when that can't be done here it is reported back as
             needs_user (what the owner has to do)
Each question has a stable id. Three kinds:
  ask/menu   an ordinary answer (`via: chat` when an agent relays it)
  secret     a key or token (`via: page`): never passes through an agent
  consent    a yes to something done for the user, such as installing software or a user-level setting
             (`via: dialog`): only the owner's own yes counts, never an agent's
"""
from __future__ import annotations

import getpass
import html
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import i18n

_ = i18n.domain(__file__)


class Terminal:
    # whether the owner sees init's output and the commands it runs (a login, a token printed by a tool)
    terminal = True
    # whether init carries out what it is told (Recorder: only lists the questions)
    acts = True

    def __init__(self, yes: bool = False):
        self.yes = yes

    @property
    def can_ask(self) -> bool:
        """Whether a question gets a real answer here; otherwise each takes its default."""
        return not self.yes and sys.stdin.isatty()

    def say(self, text: str) -> None:
        print(text)

    def ask(self, qid: str, question: str, default: str) -> str:
        if not self.can_ask:
            return default
        a = input(_("{question} [{default}]: ", question=question, default=default)).strip()
        return a or default

    def menu(self, qid: str, question: str, options: list[tuple[str, str]], default: str) -> str:
        """A numbered list; Enter takes the default, anything else asks again."""
        if not self.can_ask:
            return default
        values = [v for v, _label in options]
        print(question)
        for i, (_v, label) in enumerate(options, 1):
            print(f"  {i}. {label}")
        while True:
            a = input(f"[{values.index(default) + 1}]: ").strip()
            if not a:
                return default
            if a.isdigit() and 1 <= int(a) <= len(values):
                return values[int(a) - 1]

    def consent(self, qid: str, question: str) -> bool:
        """A yes asked on the terminal (Enter = yes); no terminal = no, and --yes = no as well: init never installs or
        changes a user-level setting on its own."""
        if not self.can_ask:
            return False
        return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")

    def secret(self, qid: str, prompt: str) -> str | None:
        """A value typed without being shown; None when nothing can be typed here."""
        if not self.can_ask:
            return None
        return getpass.getpass(prompt).strip() or None

    def needs_user(self, qid: str, what: str) -> None:
        """Something only the owner can do, outside init (a login in a terminal)."""
        print(what)


class Recorder(Terminal):
    terminal, acts = False, False

    def __init__(self, answers: dict):
        super().__init__()
        self.answers, self.questions = answers, []

    @property
    def can_ask(self) -> bool:
        return True

    def say(self, text: str) -> None:
        pass

    def _add(self, qid: str, via: str, text: str, default=None, choices=None):
        if any(q["id"] == qid for q in self.questions):
            return
        q = {"id": qid, "via": via, "text": text.strip()}
        if via == "chat":
            q["default"] = default
        if choices:
            q["choices"] = choices
        self.questions.append(q)

    def ask(self, qid: str, question: str, default: str) -> str:
        self._add(qid, "chat", question, default)
        return self.answers.get(qid, default)

    def menu(self, qid: str, question: str, options: list[tuple[str, str]], default: str) -> str:
        self._add(qid, "chat", question, default, [{"value": v, "label": label} for v, label in options])
        return self.answers.get(qid, default)

    def consent(self, qid: str, question: str) -> bool:
        self._add(qid, "dialog", question)
        return False

    def secret(self, qid: str, prompt: str) -> str | None:
        self._add(qid, "page", prompt)
        return None

    def needs_user(self, qid: str, what: str) -> None:
        self._add(qid, "user", what)


WAIT = 600   # how long a dialog or a page waits for the owner (seconds)


def has_screen() -> bool:
    """Whether someone may be at this machine's screen: a logged-in desktop session (macOS Aqua; DISPLAY or
    WAYLAND_DISPLAY on Linux). Over SSH, or on a headless machine, a dialog or a page would open where nobody is."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.stdout.strip() == "Aqua"
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def native_yes(question: str, wait: int = WAIT) -> bool | None:
    """Ask yes/no in the desktop's own dialog: True, False, or None when no dialog could be shown or nobody answered
    in time. "No" is the default button. The question goes in as an argument, never into the script's text."""
    if sys.platform == "darwin":
        script = ["on run argv", "activate", 'display dialog (item 1 of argv) with title "brunna" buttons {"No", '
                  f'"Yes"}} default button "No" cancel button "No" giving up after {wait}', "end run"]
        argv = ["osascript", *[x for line in script for x in ("-e", line)], "--", question]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=wait + 30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return False if "-128" in r.stderr else None   # -128: the No (cancel) button
        return None if "gave up:true" in r.stdout else "button returned:Yes" in r.stdout
    if shutil.which("zenity"):
        argv = ["zenity", "--question", "--title=brunna", f"--text={question}", f"--timeout={wait}",
                "--default-cancel", "--no-markup"]
    elif shutil.which("kdialog"):
        argv = ["kdialog", "--title", "brunna", "--yesno", question]
    else:
        return None
    try:
        r = subprocess.run(argv, capture_output=True, timeout=wait + 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(r.returncode)   # zenity 5: timed out


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>brunna</title><style>body{{font:16px system-ui;max-width:36em;margin:3em auto;padding:0 1em}}
input{{font:inherit;width:100%;padding:.4em;margin:.6em 0}}button{{font:inherit;padding:.4em 1.2em}}</style>
<h1>brunna</h1><form method="post" action="{action}"><label>{prompt}<input type="password" name="value"
autocomplete="off" autofocus required></label><button>{save}</button></form><p>{note}</p>"""


def secret_page(prompt: str, wait: int = WAIT) -> str | None:
    """One value from the owner through a one-time page on 127.0.0.1, opened in their browser by this process (the
    path is random and never printed). One submission, or `wait` seconds, then it closes. None when no browser could be
    opened or nothing came."""
    path = "/" + secrets.token_urlsafe(24)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # the request line holds the path: never written anywhere
            pass

        def ours(self) -> bool:
            host_ok = self.headers.get("Host") == f"127.0.0.1:{port}"   # not a rebound name
            origin = self.headers.get("Origin")
            return host_ok and self.path == path and origin in (None, f"http://127.0.0.1:{port}")

        def reply(self, code: int, body: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.ours() or got:
                return self.reply(404, "")
            self.reply(200, PAGE.format(action=path, prompt=html.escape(prompt), save=html.escape(_("Save")),
                                        note=html.escape(_("It goes straight to brunna on this machine, not to "
                                                           "your AI chat. This page works once."))))

        def do_POST(self):
            if not self.ours() or got:
                return self.reply(404, "")
            n = min(int(self.headers.get("Content-Length") or 0), 65536)
            value = (urllib.parse.parse_qs(self.rfile.read(n).decode(errors="replace")).get("value") or [""])[0]
            if value.strip():
                got["value"] = value.strip()
            self.reply(200, "<!doctype html><meta charset=utf-8><p>" + html.escape(
                _("Saved. You can close this page and go back to your chat.") if got else _("Nothing was entered.")))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    server.timeout = 1
    opener = ["open"] if sys.platform == "darwin" else ["xdg-open"]
    try:
        if not shutil.which(opener[0]) or subprocess.run([*opener, f"http://127.0.0.1:{port}{path}"],
                                                         capture_output=True, timeout=30).returncode != 0:
            return None
        deadline = time.monotonic() + wait
        while "value" not in got and time.monotonic() < deadline:
            server.handle_request()
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        server.server_close()
    return got.get("value")


class Agent(Terminal):
    terminal = False

    def __init__(self):
        super().__init__()
        self.needs: list[dict] = []

    @property
    def can_ask(self) -> bool:
        return False   # the chat answers came in as init's options; anything else takes its default

    def needs_user(self, qid: str, what: str) -> None:
        print(what)
        self.needs.append({"id": qid, "needs_user": what})

    def consent(self, qid: str, question: str) -> bool:
        """The owner's own yes, in the desktop's dialog: never the answers file's, never the agent's."""
        if has_screen():
            print(_("Asking in a dialog on this machine's screen: {question}", question=question), flush=True)
            answer = native_yes(question)
            if answer is not None:
                print(_("Answered: {answer}", answer=_("yes") if answer else _("no")))
                return answer
        self.needs_user(qid, _("Run brunna init in a terminal on this machine to answer: {question}",
                               question=question))
        return False

    def secret(self, qid: str, prompt: str) -> str | None:
        """A key or token typed by the owner into a page agent-hub opens in their browser, never into the chat."""
        if has_screen():
            print(_("A page from brunna opened in your browser for: {prompt}", prompt=prompt), flush=True)
            value = secret_page(prompt)
            if value:
                print(_("Received (not shown)."))
                return value
        self.needs_user(qid, _("Run brunna init in a terminal on this machine to enter it: {prompt}", prompt=prompt))
        return None


_current: Terminal = Terminal()


def ui() -> Terminal:
    return _current


def use(transport) -> None:
    global _current
    _current = transport
