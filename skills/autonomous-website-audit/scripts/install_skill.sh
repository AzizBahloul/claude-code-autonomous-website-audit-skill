#!/usr/bin/env bash
# Install one skill into ~/.claude/skills, from a FIXED set of sources:
#   - Anthropic's official repository github.com/anthropics/skills (any skill in skills/<name>);
#   - the vetted third-party entries in trusted_skills.json next to this script.
# It cannot install from an arbitrary URL. To add a source, a human edits trusted_skills.json.
#
#   bash install_skill.sh frontend-design            # Anthropic official
#   bash install_skill.sh <name-in-trusted_skills.json>
#   bash install_skill.sh --list                     # show the vetted third-party entries
#   bash install_skill.sh NAME --dest DIR            # install somewhere else
#
# Prints the source URL and the exact commit so the agent can tell the user what was installed,
# and records both in <dest>/.installed-from.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
TRUSTED="$HERE/trusted_skills.json"

if [ "${1:-}" = "--list" ]; then
  python3 - "$TRUSTED" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
for name, e in d["skills"].items():
    print(f"{name:<28} {e['repo']}  [{', '.join(e.get('concerns', []))}]  {e.get('license', '?')}")
    print(f"{'':<28} {e.get('why', '')}")
EOF
  exit 0
fi

name="${1:-}"
dest_root="$HOME/.claude/skills"
if [ "${2:-}" = "--dest" ] && [ -n "${3:-}" ]; then dest_root="$3"; fi

if [ -z "$name" ] || ! [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "usage: install_skill.sh <skill-name> [--dest DIR] | --list   (lowercase letters, digits, hyphens)" >&2
  exit 2
fi

# Resolve the source: a trusted third-party entry, or Anthropic's repository.
read -r REPO SUBPATH REF <<<"$(python3 - "$TRUSTED" "$name" <<'EOF'
import json, sys
try:
    e = json.load(open(sys.argv[1]))["skills"].get(sys.argv[2])
except (OSError, ValueError):
    e = None
if e:
    print(e["repo"], e["path"], e.get("ref", "HEAD"))
else:
    print("https://github.com/anthropics/skills", f"skills/{sys.argv[2]}", "HEAD")
EOF
)"
if [ "$REF" != "HEAD" ] && ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "refusing: trusted entry for $name must pin a full 40-character commit SHA" >&2; exit 2
fi
case "$REPO" in
  https://github.com/*) ;;
  *) echo "refusing: source $REPO is not a GitHub repository" >&2; exit 2 ;;
esac

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
  if [ "$REF" != "HEAD" ]; then
    # Pinned third-party entry: fetch exactly that commit, nothing newer.
    git init --quiet "$work/repo" \
      && git -C "$work/repo" remote add origin "$REPO" \
      && git -C "$work/repo" fetch --quiet --depth 1 origin "$REF" 2>/dev/null \
      && git -C "$work/repo" checkout --quiet FETCH_HEAD 2>/dev/null \
      && fetched=1 && break
  elif [ "$attempt" -lt 3 ]; then
    git clone --quiet --depth 1 --filter=blob:none --sparse "$REPO" "$work/repo" 2>/dev/null \
      && { [ "$SUBPATH" = "." ] || git -C "$work/repo" sparse-checkout set "$SUBPATH" >/dev/null 2>&1; } \
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
if [ "$SUBPATH" = "." ]; then git -C "$work/repo" sparse-checkout disable >/dev/null 2>&1 || true; fi
src="$work/repo/$SUBPATH"
if [ ! -f "$src/SKILL.md" ]; then
  echo "no SKILL.md at $SUBPATH in $REPO." >&2
  if [ "$REPO" = "https://github.com/anthropics/skills" ]; then
    echo "Anthropic skills available:" >&2
    git -C "$work/repo" ls-tree --name-only HEAD skills/ | sed 's#^skills/#  #' >&2
    echo "Vetted third-party skills: bash install_skill.sh --list" >&2
  fi
  exit 1
fi

# Never carry executable hooks or dotfiles from a third-party skill into ~/.claude silently.
if find "$src" -name 'hooks.json' -o -name '.mcp.json' -o -name 'settings*.json' | grep -q .; then
  echo "refusing: $SUBPATH ships hooks, MCP or settings files; review it with the user first" >&2
  exit 3
fi

sha="$(git -C "$work/repo" rev-parse HEAD)"
mkdir -p "$dest_root"
cp -R "$src" "$dest"
rm -rf "$dest/.git"
printf 'source: %s/tree/%s/%s\ninstalled: %s\n' "$REPO" "$sha" "$SUBPATH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$dest/.installed-from"
echo "installed $name -> $dest"
echo "source: $REPO/tree/$sha/$SUBPATH"
echo "note: new sessions list it automatically; in this session, Read $dest/SKILL.md directly."
