#!/usr/bin/env bash
# Bootstrap the development environment on macOS / Linux / Git Bash.
#
#   bash scripts/bootstrap.sh
#
# Checks prerequisites, creates .env from the example, installs both stacks,
# migrates the database and seeds the demo world. Safe to re-run.

set -euo pipefail

cd "$(dirname "$0")/.."

red=$'\033[31m'; green=$'\033[32m'; yellow=$'\033[33m'; cyan=$'\033[36m'; reset=$'\033[0m'

fail() { printf '  %sx %s%s\n' "$red" "$1" "$reset" >&2; exit 1; }
ok()   { printf '  %s+ %s%s\n' "$green" "$1" "$reset"; }
warn() { printf '  %s! %s%s\n' "$yellow" "$1" "$reset"; }
step() { printf '\n%s%s%s\n' "$cyan" "$1" "$reset"; }

step "Checking prerequisites"

# --- Node ---------------------------------------------------------------------
command -v node >/dev/null 2>&1 || fail "node not found. Install Node 24 LTS (nvm: 'nvm install 24')."
node_version=$(node --version); node_version=${node_version#v}
node_major=${node_version%%.*}
if [ "$node_major" -lt 20 ]; then
  fail "node $node_version is too old. Vite 8 needs Node 20.19+; 24 LTS is what CI uses."
elif [ "$node_major" -lt 24 ]; then
  warn "node $node_version works, but CI runs Node 24"
else
  ok "node $node_version"
fi

# --- pnpm ---------------------------------------------------------------------
command -v pnpm >/dev/null 2>&1 || fail "pnpm not found. Install it with 'npm install -g pnpm@9'."
ok "pnpm $(pnpm --version)"

# --- Python -------------------------------------------------------------------
python_bin=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then python_bin=$candidate; break; fi
done
[ -n "$python_bin" ] || fail "python not found. Install Python 3.13 or newer."
python_version=$("$python_bin" --version | cut -d' ' -f2)
py_major=${python_version%%.*}
py_rest=${python_version#*.}
py_minor=${py_rest%%.*}
if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 13 ]; }; then
  fail "python $python_version is too old. The backend requires 3.13+."
fi
ok "python $python_version"

# --- uv -----------------------------------------------------------------------
# scripts/uv.mjs falls back to 'python -m uv', so a pip-installed uv that is not
# on PATH is fine. Only a completely missing uv is fatal.
if ! command -v uv >/dev/null 2>&1 && ! "$python_bin" -m uv --version >/dev/null 2>&1; then
  warn "uv not found - installing with 'pip install --user uv'"
  "$python_bin" -m pip install --user uv || fail "uv installation failed. See https://docs.astral.sh/uv/"
fi
ok "uv available"

# --- .env ---------------------------------------------------------------------
step "Configuring"
if [ -f .env ]; then
  ok ".env already exists (left untouched)"
else
  cp .env.example .env
  ok ".env created from .env.example"
fi

# --- Install ------------------------------------------------------------------
step "Installing and preparing the database"
# Not `pnpm setup` -- that is pnpm's own built-in command (it configures PNPM_HOME
# and rewrites your PATH), and a package script named `setup` would never run.
pnpm bootstrap

step "Ready. Start everything with:"
printf '  pnpm dev\n\n'
