"""How init asks its questions (docs/design/newcomer-path.md B3): one dialogue, several transports.

init's steps ask through the current transport (`ui()`), never through input()/getpass/isatty themselves, so a
question exists in every transport or in none:
  Terminal   the owner at a terminal: input(), getpass; with --yes or no terminal, every answer is its default and
             nothing is consented to
Each question has a stable id. Three kinds:
  ask/menu   an ordinary answer (`via: chat` when an agent relays it)
  secret     a key or token (`via: page`): never passes through an agent
  consent    a yes to something done for the user, such as installing software or a user-level setting
             (`via: dialog`): only the owner's own yes counts, never an agent's
"""
from __future__ import annotations

import getpass
import sys

import i18n

_ = i18n.domain(__file__)


class Terminal:
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


_current: Terminal = Terminal()


def ui() -> Terminal:
    return _current


def use(transport) -> None:
    global _current
    _current = transport
