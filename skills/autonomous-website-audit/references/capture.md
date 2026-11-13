# Layers: baseline and verify

> **Baseline answers:** what does every role see, at every width and theme, before any change?
> **Verify answers:** did this page's changes make anything worse?
> **Gates:** baseline needs reports in `shots/baseline/SLUG/` and tiles in
> `tiles/baseline/SLUG/`. Verify needs `shots/verify/SLUG/` with no count above the baseline.

## Contents
- The matrix
- Baseline steps
- What to look for in the tiles
- Data-driven claims
- Triage the JSON
- Verify steps
- Mistakes to avoid

## The matrix

| Axis | Values |
|---|---|
| Width | phone 390 px, tablet 768 px, desktop 1440 px (add the project's own breakpoints if it defines others) |
| Theme | light, plus dark if the site supports it |
| Who | every role that can open the page, logged out, and free/unpaid where that changes the page |
| Locale | the default locale here; the `i18n` layer covers the others |

To tell whether the site has a dark theme, look for `darkMode` in the Tailwind config,
`next-themes`, `prefers-color-scheme` media queries, or a `.dark` / `[data-theme]` selector.
If the theme is stored rather than taken from the OS, pass its storage key, for example
`--theme-storage-key theme` for next-themes.

## Baseline steps

1. **Capture.** Run one command per role or state. Each command covers every width and theme:
   ```bash
   C="node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs capture --base http://localhost:3000"
   $C --path /dashboard --storage .website-audit/auth/coach.json --themes light,dark \
      --theme-storage-key theme --out .website-audit/shots/baseline
   $C --path /dashboard --label anon --themes light,dark --out .website-audit/shots/baseline
   ```
   For each combination the crawler writes a full-page PNG and a JSON report named
   `<label>__<width>__<theme>` into `shots/baseline/SLUG/`, and prints one summary line.
2. **Check that the capture is valid.** If a line says `redirected to /login` for a role that
   should have access, the session has expired: log in again (see `01-accounts.md`) and
   re-capture. If it says `NAVIGATION ERROR`, fix the server first.
3. **Tile:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/tile.py .website-audit/shots/baseline/SLUG \
     --out .website-audit/tiles/baseline/SLUG
   ```
4. **View every tile of every combination** with `Read`. Don't sample, because a defect on the
   phone/dark/free tile is as real as one on desktop.
5. **Triage the summary counts** (table below), and check the on-screen claims against the data.
6. **Add pages you find.** Compare the report's `links` with the progress file, and run
   `progress.py add` for any internal page that is not tracked.
7. **Write the findings** at the top of `.website-audit/pages/SLUG.md`, under "Baseline".
   The later layers fix them.

## What to look for in the tiles

- Content clipped, overlapping or running off the right edge; text truncated without a tooltip.
- Unreadable text, especially in light mode, where bare `-400` shades wash out.
- Broken or missing images and icons; layout shift from images without fixed dimensions.
- Empty, loading and error states that are blank or say nothing useful.
- Controls jammed against the screen edge on phone; sticky bars covering content.
- Inconsistency with other pages: different card styles, button sizes or heading scales.
- Anything visible that this role should not see.

## Data-driven claims

Read the numbers and sentences on screen as claims, and check each one against the API
response or the database:

- **Zero vs no data.** A zero shown where there is no data should be an empty state.
- **Singular and plural.** "1 matches" and "0 player" are bugs.
- **Comparisons.** Watch for "leads by 0 points", a tie named as a single winner, and
  percentages that do not add up.
- **Labels and rounding.** Labels must not promise features that don't exist, and rounding
  must not change a ranking.

## Triage the JSON

Read a report with `jq` on the fields you need, never whole, for example
`jq '.summary, .a11y[:5]' FILE`. Across many pages, run
`python3 ${CLAUDE_SKILL_DIR}/scripts/summarize.py table .website-audit/shots/baseline`.

| Field | Meaning | First check | Fixed in layer |
|---|---|---|---|
| `pageErrors`, `consoleErrors` | Uncaught exceptions and `console.error` calls | Re-run once; HMR errors that vanish are noise | functionality |
| `failedRequests` | Network failures not labelled as noise | The `noise` field explains labelled ones | functionality |
| `httpErrors` | Responses with status 400 or above | A 401 or 403 carries a `verify-gating` note; check the backend rule first | functionality |
| `slowestApiMs`, `timing` | Slowest fetch/XHR; TTFB, LCP, CLS | Compare with the targets in `04-functionality-latency.md` | functionality |
| `badText` | `NaN`, `undefined`, `null`, `[object Object]`, `Invalid Date` on screen | A formatter given missing data | functionality |
| `overflow` | The page scrolls sideways, or elements stick out past the viewport | `overflow.offenders` names the outermost elements | design |
| `lowContrast` | Text below WCAG AA on a solid background | Check both themes; text on gradients or images is skipped | design |
| `missingViewportMeta` | Phone run found no `width=device-width` viewport tag, so phones render at 980 px and shrink | Add the tag in the root layout | design |
| `a11ySerious`, `a11y` | axe-core WCAG A/AA violations, worst first | `a11y[].id` and `targets` | a11y |
| `smallTargets` | Touch targets under 24×24 px (WCAG 2.2 SC 2.5.8) on touch widths | Inline links in text are already excluded | a11y |
| `structure` | No or several `<h1>`, empty `<title>`, no `lang`, duplicate ids | `structure.issues` | a11y |
| `tokensInStorage` | JWT-like values, or keys named like tokens, in web storage | Key names only; values are never recorded | security |
| `rawKeys` | Raw i18n keys or unfilled `{placeholders}` on screen | Missing key or wrong namespace | i18n |
| `redirectedToLogin` | Session expired, or the role lacks access | Log in again, or confirm the gating is intended | — |

`a11y` reads `SKIPPED` when axe-core is not installed. Run `setup.sh` so the a11y gate can pass.

## Verify steps

Run these after the `i18n` layer, for the page as a whole:

1. Re-run the exact baseline matrix (same roles, widths, themes) with
   `--out .website-audit/shots/verify`.
2. Compare:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/summarize.py compare \
     .website-audit/shots/baseline/SLUG .website-audit/shots/verify/SLUG
   ```
   Every count must be equal or lower. Fix any increase. If an increase is correct (for
   example a newly visible section with a known third-party error), explain it in the note.
3. Tile the verify captures, and view the combinations marked `-> view these tiles`.
4. Run the project's lint, typecheck and tests, and quote the result in the note.
5. Commit the page's changes (see "Fix discipline" in SKILL.md), then
   `progress.py done ROUTE verify --note "..."`. The gate re-runs the compare.

## Mistakes to avoid

- Treating a capture made with an expired session as evidence.
- Viewing only desktop tiles because the phone ones "look the same".
- Copying a whole JSON report into the conversation: use `jq` or `summarize.py`.
- Re-capturing verify with fewer roles or themes than the baseline. The gate reports any
  missing combination as "not re-captured".
