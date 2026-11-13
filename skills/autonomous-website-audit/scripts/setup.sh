#!/usr/bin/env bash
# One-time tooling check for the audit: Playwright + Chromium and axe-core for crawl.mjs,
# Pillow for tile.py and summarize.py.
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

# axe-core powers the accessibility scan. Prefer the project's copy; never add it to the project.
if node -e "require.resolve('axe-core/axe.min.js', {paths:[process.cwd()]})" 2>/dev/null; then
  echo "axe-core: using the project's own copy"
elif [ -f "$HERE/node_modules/axe-core/axe.min.js" ]; then
  echo "axe-core: ok"
else
  echo "axe-core: installing next to the scripts"
  (cd "$HERE" && npm install --no-audit --no-fund --silent --no-save axe-core@^4.10.0) \
    || echo "axe-core: install failed (offline?). Captures still run but report a11y as SKIPPED."
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
