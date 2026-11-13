# Capture: the baseline and verify layers

## Contents
- The matrix
- Run it
- Tile, then look
- What to look for in the tiles
- Data-driven claims
- Triage the JSON
- Verify layer

## The matrix

| Axis | Values |
|---|---|
| Width | phone 390 px, tablet 768 px, desktop 1440 px (add more if the project defines other breakpoints) |
| Theme | light, plus dark if the site supports it |
| Who | every role that can open the page, logged out, and free/unpaid where that changes the page |
| Locale | the default locale here; other locales are covered in the `i18n` layer |

To tell whether the site has a dark theme, look for `darkMode` in the Tailwind config,
`next-themes`, `prefers-color-scheme` media queries or a `.dark` / `[data-theme]` selector. If
the theme is stored rather than taken from the OS, pass its storage key, for example
`--theme-storage-key theme` for next-themes.

## Run it

Run one command per role or state. Each command covers every width and theme:

```bash
C="node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs capture --base http://localhost:3000"
$C --path /dashboard --storage .website-audit/auth/coach.json --themes light,dark \
   --theme-storage-key theme --out .website-audit/shots/baseline
$C --path /dashboard --label anon --themes light,dark --out .website-audit/shots/baseline
```

For each combination the crawler writes a full-page PNG and a JSON report, both named
`<label>__<width>__<theme>`. It also prints one summary line per combination.

## Tile, then look

Never open a full-page PNG directly. Tall images get scaled down until the text can't be
read. Tile them first:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/tile.py .website-audit/shots/baseline/dashboard \
  --out .website-audit/tiles/baseline/dashboard
```

`Read` every tile of every combination. Don't sample, because a defect on the phone/dark/free
tile is as real as one on desktop.

## What to look for in the tiles

- Content clipped, overlapping or running off the right edge; text truncated without a tooltip.
- Unreadable text, especially in light mode, where bare `-400` colour shades wash out.
- Broken or missing images and icons; layout shift from images without fixed dimensions.
- Empty, loading and error states that are blank or say nothing useful.
- Touch targets under about 44 px on phone, and controls jammed against the edge.
- Inconsistency with other pages: different card styles, button sizes or heading scales.
- Anything visible that the role should not see.

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

Every report has a `summary` block. For each non-zero count:

| Field | Meaning | First check |
|---|---|---|
| `pageErrors`, `consoleErrors` | Uncaught exceptions and `console.error` calls | Re-run once; dev-server HMR errors that vanish are noise |
| `failedRequests` | Network failures not labelled as noise | The `noise` field explains labelled ones; see `04-functionality-latency.md` |
| `httpErrors` | Responses with status 400 or above | A 401 or 403 carries a `verify-gating` note; check the backend rule first |
| `rawKeys` | Raw i18n keys or unfilled `{placeholders}` on screen | Missing key or wrong namespace |
| `badText` | `NaN`, `undefined`, `null`, `[object Object]` or `Invalid Date` shown on screen | A formatter given missing data |
| `overflow` | The page scrolls sideways, or elements stick out past the viewport | `overflow.offenders` names the outermost elements |
| `lowContrast` | Text below WCAG AA against a solid background | Check both themes; elements on gradients or images are skipped |
| `tokensInStorage` | JWT-like values, or keys named like tokens, in local or session storage | Security layer |
| `missingViewportMeta` | Phone run found no `<meta name="viewport" content="width=device-width">`, so phones render the page at 980 px and shrink it | Add the tag in the root layout or document head |
| `redirectedToLogin` | The session expired, or the role lacks access | Log in again, or confirm the gating is intended |

Write what you find in `.website-audit/pages/<slug>.md`. The later layers fix it.

## Verify layer

After the `i18n` layer, check the page as a whole:

1. Re-run the same matrix into `.website-audit/shots/verify`, then tile it and view it.
2. Compare the summary lines with the baseline. Every count should be equal or lower. Explain
   any increase, or fix it.
3. Run the project's lint, typecheck and tests.
4. Commit the page's changes (see "Committing" in SKILL.md).
5. Mark the page done with `progress.py done ROUTE verify`.
