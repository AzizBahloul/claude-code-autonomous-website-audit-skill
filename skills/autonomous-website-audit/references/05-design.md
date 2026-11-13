# Layer: design

> **Answers:** is this page consistent with the site and unbroken at every width, in both themes?
> **Gate:** every finding in this layer is resolved, and the note names what changed.

Fix this page's design issues, following the design skill chosen in setup. The goal is
consistency with the rest of the site, not a redesign (rule I5).

**Steps:** find the existing tokens and components, work through the checklist for every
width and both themes, fix with what exists, then re-capture and view the changed areas.

## Reuse before you create

Before writing any style or component, find what exists:

- **Tokens:** the Tailwind config (`theme.extend`, or `@theme` in v4), CSS custom properties,
  and theme files (`tokens.*`, `theme.ts`, `globals.css`).
- **Components:** the design-system folder (`components/ui`, `packages/ui`, `src/design-system`)
  and how other pages build the same pattern.
- **Search for the pattern first**, for example `grep -rn "EmptyState\|StatCard" src/`.

Use the existing token or component. Add a new one only when nothing fits, and then add it to
the shared layer, not the page. Never hard-code a colour, spacing value or radius that exists
as a token.

## Checklist (both themes, all three widths)

- **Hierarchy.** One clear primary action, with the heading scale matching sibling pages.
- **Spacing and alignment.** Values come from the spacing scale, with consistent gutters and no
  near-miss alignments.
- **Responsive layout.**
  - Phone (390): nothing runs off the edge, tables scroll inside their own container, touch
    targets are about 44 px or more, and sticky elements do not cover content.
  - Tablet (768): no awkward half layouts.
  - Desktop (1440): line length stays readable.
- **Contrast.** Text meets WCAG AA (4.5:1 for body text, 3:1 for large text) in BOTH themes.
  Light mode is where `text-*-400` and similar bare light shades fail. Find them with
  `grep -rnE "text-[a-z]+-(300|400)\b" <page files>`, then check each one has a darker
  light-mode value or a `dark:` pair. The crawler's `lowContrast` list gives the computed
  ratios.
- **States.** Loading, empty, error, disabled, hover, focus-visible and selected all exist and
  look intentional.
- **Copy fit.** Long names, long translations and large numbers do not break the layout. Check
  the longest locale in the i18n layer.
- **Viewport tag.** `missingViewportMeta` on phone means the layout is rendered at 980 px and
  shrunk. Add `width=device-width, initial-scale=1` in the root layout.
- **Inner scroll areas.** `scrollContainers` in the report means the full-page capture cut
  off content inside a scrolling panel. Re-capture with `--expand-scrollers` before judging it.

Labels, focus, keyboard and screen-reader issues belong to the next layer, `a11y`. Fix
contrast here, because it is part of the colour system.
- **Data claims on screen.** See `capture.md`.

## Verify

Re-capture the affected widths and themes, tile the captures and view the changed areas. Do
the same for any shared component you touched, on at least one other page that uses it.

Run `python3 ${CLAUDE_SKILL_DIR}/scripts/summarize.py repeats .website-audit/shots/baseline`
after the first few pages. A finding repeated across pages belongs to a shared component:
fix it there once.

## Mistakes to avoid

- Hard-coding a colour, spacing or radius that exists as a token.
- Restyling a page so it looks better than its siblings. Consistency beats local taste.
- Fixing light mode and breaking dark mode, or desktop and breaking phone. Re-capture both.
- Editing a shared component without checking at least one other page that uses it.
