# Project phase: architecture

> **Answers:** what is the site made of: every page, the layouts and components behind it, and
> which pieces are shared, single-use or unused?
> **Gate:** `architecture.json` and `ARCHITECTURE.md` exist (`progress.py done @project architecture`).

This runs once, after the inventory and before any page. Every later step leans on it: the
`code` layer quotes it, `design` uses its reuse table to fix shared components once, and
`hygiene` uses its dead-file and duplicate lists. Don't fix anything here: log what you find.

## Steps

1. **Generate the map** from the target project's root:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/architecture.py . --inventory .website-audit/inventory.json
   ```
   It prints one summary line. `ARCHITECTURE.md` holds:
   - **Route tree.** Pages nested under their URL segments.
   - **Per page.** The entry file, the layout chain, and every local component it reaches
     through imports (transitively, path aliases included).
   - **Component reuse.** Each component file with the number of pages that reach it: `shared`
     (2 or more pages), `single-page`, or `not-on-any-page`.
   - **Dead-file candidates.** Source files that no page, layout, API route, convention file,
     test or config reaches.
   - **Possible duplicate components.** The same core name in different folders (`Button`,
     `ui/Button`, `CustomButton`), and files with identical normalised content.
2. **Cross-check with the terminal.** The static graph misses dynamic imports
   (`import(\`./${name}\`)`, `React.lazy`, `defineAsyncComponent`), string registries and
   framework magic files. For every dead-file candidate and every `not-on-any-page`
   component, run the check below. Anything with a real reference is not dead.
   ```bash
   grep -rn --include='*.[jt]s*' --include='*.vue' --include='*.svelte' "ComponentName" . | grep -v node_modules
   ```
3. **Read the reuse table top-down.** The most shared components carry the most risk. Note them
   in `ARCHITECTURE.md` under a `## Notes` heading you add at the end. The design and a11y layers
   fix these once, then re-check their pages.
4. **Log what is confirmed** as `@project` findings, so the hygiene phase fixes them:
   ```bash
   P="python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py"
   $P finding @project architecture low "Unused.tsx: no importer, no dynamic reference" \
     --evidence .website-audit/ARCHITECTURE.md
   ```
   A finding logged in `architecture` must be resolved before the phase closes. Usually that
   means `resolve ... deferred --how "fixed in hygiene"`, plus a new hygiene finding for it.
   Simpler: log dead code and duplicates straight under `hygiene` and keep `architecture`
   findings for structural problems: a page with no layout guard, or a circular import.
5. Close the phase with a note that quotes the summary line:
   `$P done @project architecture --note "<summary line>; N candidates confirmed dead"`.

## Monorepos and several apps

The map covers every package in the repo. When two apps share a `packages/ui`, the reuse
table counts pages from both. Note in `ARCHITECTURE.md` which app each page belongs to (the
route IDs from `progress.py init` already carry the file path when routes collide).

## Mistakes to avoid

- Deleting a dead-file candidate on the script's word. Grep, and check the router config for
  lazy routes, first.
- Treating `not-on-any-page` as unused. API routes, emails, Storybook and server code also
  import components. The `importers` column shows whether anything imports the file at all.
- Restructuring folders in this phase. The map documents the architecture. It doesn't change it.
