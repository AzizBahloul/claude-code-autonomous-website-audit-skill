#!/usr/bin/env bash
# Install one skill from Anthropic's official skills repository into ~/.claude/skills.
# The source is fixed to github.com/anthropics/skills: this script cannot install from anywhere else.
#
#   bash install_skill.sh frontend-design            # -> ~/.claude/skills/frontend-design
#   bash install_skill.sh webapp-testing --dest DIR  # install somewhere else
#
# Prints the source URL and commit so the agent can tell the user exactly what was installed.
set -euo pipefail

REPO="https://github.com/anthropics/skills"
name="${1:-}"
dest_root="$HOME/.claude/skills"
if [ "${2:-}" = "--dest" ] && [ -n "${3:-}" ]; then dest_root="$3"; fi

if [ -z "$name" ] || ! [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "usage: install_skill.sh <skill-name> [--dest DIR]   (lowercase letters, digits, hyphens)" >&2
  exit 2
fi

dest="$dest_root/$name"
if [ -e "$dest" ]; then
  echo "already installed: $dest (left unchanged)"
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# GitHub connections do reset mid-clone; retry, and fall back to a plain shallow clone,
# because the blob-filtered clone needs extra round trips at checkout time.
fetched=""
for attempt in 1 2 3; do
  rm -rf "$work/repo"
  if [ "$attempt" -lt 3 ]; then
    git clone --quiet --depth 1 --filter=blob:none --sparse "$REPO" "$work/repo" 2>/dev/null \
      && git -C "$work/repo" sparse-checkout set "skills/$name" >/dev/null 2>&1 \
      && fetched=1 && break
  else
    git clone --quiet --depth 1 "$REPO" "$work/repo" 2>/dev/null && fetched=1 && break
  fi
  echo "clone attempt $attempt failed; retrying" >&2
  sleep $((attempt * 2))
done
if [ -z "$fetched" ]; then
  echo "could not fetch $REPO after 3 attempts (network); nothing was installed" >&2
  exit 1
fi
src="$work/repo/skills/$name"
if [ ! -f "$src/SKILL.md" ]; then
  echo "no skill named '$name' in $REPO. Available:" >&2
  git -C "$work/repo" ls-tree --name-only HEAD skills/ | sed 's#^skills/#  #' >&2
  exit 1
fi

sha="$(git -C "$work/repo" rev-parse HEAD)"
mkdir -p "$dest_root"
cp -R "$src" "$dest"
printf 'source: %s/tree/%s/skills/%s\ninstalled: %s\n' "$REPO" "$sha" "$name" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$dest/.installed-from"
echo "installed $name -> $dest"
echo "source: $REPO/tree/$sha/skills/$name"
echo "note: new sessions list it automatically; in this session, Read $dest/SKILL.md directly."
