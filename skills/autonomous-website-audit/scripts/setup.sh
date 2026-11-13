#!/usr/bin/env bash
# One-time tooling check for the audit: Playwright + Chromium for crawl.mjs, Pillow for tile.py.
# Run from the target project's root so its own Playwright is reused when it has one.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

has_project_pw() {
  node -e "for (const m of ['playwright','@playwright/test']) { try { require.resolve(m, {paths:[process.cwd()]}); process.exit(0) } catch {} } process.exit(1)" 2>/dev/null
}

if has_project_pw; then
  echo "playwright: using the project's own copy"
elif [ -d "$HERE/node_modules/playwright" ]; then
  echo "playwright: using the copy in $HERE/node_modules"
else
  echo "playwright: installing next to the scripts ($HERE/node_modules)"
  (cd "$HERE" && npm install --no-audit --no-fund --silent)
fi

if ! node "$HERE/crawl.mjs" selftest; then
  echo "chromium missing for this Playwright version; installing it"
  if has_project_pw; then npx --yes playwright install chromium; else (cd "$HERE" && npx --yes playwright install chromium); fi
  node "$HERE/crawl.mjs" selftest
fi

if python3 -c "import PIL" 2>/dev/null; then
  echo "pillow: ok"
else
  echo "pillow: missing. Install it with: python3 -m pip install --user pillow"
  exit 1
fi
