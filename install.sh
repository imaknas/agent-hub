#!/bin/sh
# Install agent-hub in one line:
#   curl -fsSL https://raw.githubusercontent.com/imaknas/agent-hub/main/install.sh | sh
# Clones the repo, checks out the newest release, installs uv if it is missing (astral's own installer: user level, no
# sudo), runs bin/setup (the platform's Python and the `agenthub` command), and then goes straight on into
# `agenthub init` when there is a terminal to answer its questions in (docs/design/newcomer-path.md B1, B2).
#
# git is the one thing it may have to install. It asks first (on macOS, Apple's command line tools; on Linux, the
# package manager with your own sudo password) and installs nothing else that needs sudo; bin/setup names anything else
# that is missing, with the command to run.
#
#   sh -s -- --yes       (after the pipe) answer yes to installing git, e.g. on a machine set up unattended
#   sh -s -- --no-init   stop after the install; run agenthub init yourself
#   AGENT_HUB_DIR    where to clone (default: agent-hub in your home folder)
#   AGENT_HUB_REPO   what to clone (default: the GitHub repo)
#   AGENT_HUB_REF    a tag or branch instead of the newest release (e.g. main)
#   AGENT_HUB_YES=1, AGENT_HUB_NO_INIT=1   the same as the flags
#
# Pasting the line again after an install that stopped halfway (no instance yet) carries on from where it stopped.
# The whole script is one function called on its last line, so a download cut off midway runs nothing.
set -eu

REPO="${AGENT_HUB_REPO:-https://github.com/imaknas/agent-hub.git}"
DIR="${AGENT_HUB_DIR:-$HOME/agent-hub}"

say() { printf 'agent-hub install: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

# a terminal the questions can be asked in: stdin is the script itself when piped, so it has to be /dev/tty
have_tty() { ( : </dev/tty ) 2>/dev/null; }

# confirm "<question>": yes with --yes; otherwise asked on the terminal (Enter = yes); no terminal = no
confirm() {
  [ -n "$YES" ] && return 0
  have_tty || return 1
  printf '%s [Y/n] ' "$1" >/dev/tty
  read -r ans </dev/tty || return 1
  case "$ans" in ""|y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}

need_git_macos() {
  xcode-select -p >/dev/null 2>&1 && return 0   # /usr/bin/git is a stub that is always on PATH: ask xcode-select
  if [ -n "${SSH_CONNECTION:-}" ]; then
    die "git needs Apple's command line tools, and their installer opens a window this SSH session can't show: run xcode-select --install on the machine's own screen (or install them from https://developer.apple.com/download/all/), then paste the line again"
  fi
  confirm "git comes with Apple's command line tools. Install them now?" \
    || die "git is needed: run xcode-select --install, then paste the line again"
  xcode-select --install >/dev/null 2>&1 || true
  say "Apple's installer is running in its own window; waiting for it to finish (this can take several minutes)"
  i=0
  until xcode-select -p >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -le 360 ] || die "the command line tools are still not installed after an hour; finish the installer, then paste the line again"
    sleep 10
  done
  say "Apple's command line tools are installed"
}

need_git_linux() {
  command -v git >/dev/null 2>&1 && return 0
  if command -v apt-get >/dev/null 2>&1; then cmd="sudo apt-get install -y git"
  elif command -v dnf >/dev/null 2>&1; then cmd="sudo dnf install -y git"
  elif command -v pacman >/dev/null 2>&1; then cmd="sudo pacman -S --noconfirm git"
  else die "git is missing; install it with your package manager, then paste the line again"
  fi
  confirm "git is missing. Install it now with: $cmd ?" || die "git is needed: $cmd, then paste the line again"
  if have_tty; then sh -c "$cmd" </dev/tty; else sh -c "$cmd"; fi || die "installing git failed: $cmd"
}

instance_exists() {
  [ -f "$DIR/.hub-home" ] || [ -f "$DIR/hub.json" ]
}

main() {
  YES="${AGENT_HUB_YES:-}"
  NO_INIT="${AGENT_HUB_NO_INIT:-}"
  for a in "$@"; do
    case "$a" in
      --yes) YES=1 ;;
      --no-init) NO_INIT=1 ;;
      *) die "unknown option: $a (see the top of install.sh)" ;;
    esac
  done

  if [ "$(uname -s)" = Darwin ]; then need_git_macos; else need_git_linux; fi

  resume=""
  if [ -e "$DIR" ]; then
    if [ -f "$DIR/bin/agenthub" ] && git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
      instance_exists && die "$DIR already holds a set-up agent-hub; update it with: agenthub upgrade"
      say "$DIR holds agent-hub but it isn't set up yet; carrying on from there"
      resume=1
    else
      die "$DIR already exists and is not agent-hub; set AGENT_HUB_DIR to another folder"
    fi
  fi

  if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    command -v curl >/dev/null 2>&1 || die "uv is missing and so is curl to install it; see https://docs.astral.sh/uv/getting-started/installation/"
    say "installing uv (https://astral.sh/uv, into your home folder)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  PATH="$HOME/.local/bin:$PATH"; export PATH

  if [ -z "$resume" ]; then
    say "cloning $REPO into $DIR"
    git clone -q "$REPO" "$DIR"
    if [ -n "${AGENT_HUB_REF:-}" ]; then
      ref="$AGENT_HUB_REF"
    else
      ref="$(git -C "$DIR" tag -l 'v[0-9]*' --sort=-v:refname | head -n 1)"
    fi
    if [ -n "$ref" ]; then
      git -C "$DIR" -c advice.detachedHead=false checkout -q "$ref"
      say "at $ref"
    else
      say "no release yet; staying on the default branch"
    fi
  fi

  "$DIR/bin/setup" || die "bin/setup reported something missing (above); install it, then paste the line again"

  if [ -n "$NO_INIT" ]; then
    say "done. Next: agenthub init  (then agenthub doctor)"
  elif have_tty; then
    say "installed. Now a few questions to set it up (agenthub init):"
    # by full path: a fresh shell may not have ~/.local/bin on PATH yet, and install-cli may have failed
    if "$DIR/bin/py" "$DIR/bin/agenthub" init </dev/tty; then
      say "done. agenthub doctor shows anything still missing. Open a new terminal if 'agenthub' is not found."
    else
      say "init stopped before it finished; run it again with: agenthub init  (it carries on where it stopped)"
    fi
  else
    say "done. No terminal here to answer questions in; next: agenthub init  (then agenthub doctor). If agenthub is not found, open a new shell or add ~/.local/bin to PATH."
  fi
}

main "$@"
