#!/bin/sh
# Install agent-hub in one line:
#   curl -fsSL https://raw.githubusercontent.com/imaknas/agent-hub/main/install.sh | sh
# Clones the repo, checks out the newest release, installs uv if it is missing (astral's own installer: user level, no
# sudo), and runs bin/setup (the platform's Python and the `agenthub` command). Anything that needs sudo or a package
# manager (git, jq) is only reported, with the command to run. Then: `agenthub init`.
#   AGENT_HUB_DIR    where to clone (default: agent-hub in your home folder)
#   AGENT_HUB_REPO   what to clone (default: the GitHub repo)
#   AGENT_HUB_REF    a tag or branch instead of the newest release (e.g. main)
set -eu

REPO="${AGENT_HUB_REPO:-https://github.com/imaknas/agent-hub.git}"
DIR="${AGENT_HUB_DIR:-$HOME/agent-hub}"
say() { printf 'agent-hub install: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is missing; install it first (macOS: xcode-select --install; Debian/Ubuntu: sudo apt install git)"

if [ -e "$DIR" ]; then
  if [ -f "$DIR/bin/agenthub" ] && git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
    die "$DIR already holds agent-hub; update it with: agenthub upgrade"
  fi
  die "$DIR already exists and is not agent-hub; set AGENT_HUB_DIR to another folder"
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  command -v curl >/dev/null 2>&1 || die "uv is missing and so is curl to install it; see https://docs.astral.sh/uv/getting-started/installation/"
  say "installing uv (https://astral.sh/uv, into your home folder)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
PATH="$HOME/.local/bin:$PATH"; export PATH

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

"$DIR/bin/setup" || die "bin/setup reported something missing (above); install it, then run: $DIR/bin/setup"

say "done. Next: agenthub init  (then agenthub doctor). If agenthub is not found, open a new shell or add ~/.local/bin to PATH."
