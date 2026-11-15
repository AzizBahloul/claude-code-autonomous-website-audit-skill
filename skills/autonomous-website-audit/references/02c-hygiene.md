# Project phase: hygiene

> **Answers:** what is duplicated, dead, needlessly complex or vulnerable across the whole
> codebase, frontend and backend, and what can be removed or merged safely before page work?
> **Gate:** `hygiene.json` and `HYGIENE.md` exist, every `hygiene` finding is resolved, and if
> any fix landed, a smoke capture of every page after it shows nothing worse than before.

Cleaning first means every page layer afterwards works on less code, and fixes a shared piece
once instead of once per copy.

## Steps

1. **Smoke-capture every page before touching code** (desktop, no screenshots: fast). For a
   site with roles, run it once per role, adding `--storage .website-audit/auth/ROLE.json
   --label ROLE` so the files don't overwrite each other:
   ```bash
   node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs capture --base http://localhost:3000 \
     $(jq -r '.pages[] | select(.url and .url != "-") | "--path " + .url' .website-audit/progress.json) \
     --viewports desktop --no-screenshot --out .website-audit/shots/smoke/before
   ```
2. **Run the scan.** Built-in checks always run. External tools run when installed. With
   `--tools fetch` the script also fetches pinned versions through npx or pipx, outside the
   project. Fetching downloads packages, so tell the user first (rule S5):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/hygiene.py . --tools auto
   ```
   `HYGIENE.md` merges its results with the dead files and duplicates from `architecture.py`
   and the orphans and broken calls from `endpoints.py`.
3. **Make the tools trustworthy before trusting them.** If knip prints configuration hints,
   resolve them first: teach it the project's entry points and workspaces in a `knip.json`
   kept under `.website-audit/` (pass `--config`). Never use knip's `ignore` to silence
   results, because it hides real dead code along with the noise. For duplicates, prefer pairs
   that change together: `git log --format= --name-only -- FILE_A FILE_B | sort | uniq -c`
   shows whether two copies have been edited in step (real duplication) or drifted apart on
   purpose.
4. **Verify, then log** each confirmed item as `progress.py finding @project hygiene ...`:

   | Kind | Verify before acting | Usual fix |
   |---|---|---|
   | Unreferenced file, unused export (knip) | grep the name; check lazy routes, registries, string imports | delete it |
   | Orphan endpoint, dead function (vulture) | grep the whole repo; ask about other clients | delete handler, route, serializer and its tests together |
   | Duplicate component (`Button` vs `CustomButton`) | diff them; list every caller | keep the design-system one, extend it with a variant if needed, migrate callers, delete the copy |
   | Repeated code block | read both copies: same intent, or coincidence? | extract to the helper the project already has, or one new shared helper |
   | Commented-out code | git keeps history | delete it |
   | Debug leftovers | is it intentional logging? | delete it, or route it through the project's logger |
   | Unused dependency | grep configs and scripts; knip or deptry (DEP002) confirms | remove from the manifest, reinstall, build |
   | Big file / complex function (radon C or worse) | is it hot code with tests? | split only with tests in place, in its own commit |
   | Vulnerable dependency (npm audit, pip-audit) | is it reachable at runtime, or dev-only? | upgrade within the major version; a major bump is a user decision |

5. **Fix in small, separate commits**, if the project allows commits: one per kind (dead files,
   then duplicates, then dependencies). After each one, run the project's build, typecheck,
   lint and tests. Never let a hygiene commit change behaviour. If a deletion breaks a test,
   restore the file: it wasn't dead.
6. **Smoke-capture again** into `shots/smoke/after` (same command, new `--out`). The gate runs
   `summarize.py compare` per page. Any page that got worse means a "dead" piece was alive:
   revert that change and resolve its finding as `reverted`.
7. **Leave the rest to the pages.** Deep refactors of page-local code belong to that page's
   `functionality` layer (complexity pass). Here, fix only what is safe across the whole
   project. Resolve the others as `deferred --how "page X functionality layer"`.

## Mistakes to avoid

- Deleting code on a tool's word. knip, vulture and the built-in checks all report candidates.
- One giant cleanup commit. When something breaks, nobody can tell which deletion did it.
- Merging two components that look alike but behave differently, such as a `Button` that
  submits forms and a `CustomButton` that only links. Read both first.
- Upgrading a dependency across a major version during an audit. Propose it in the report.
- Treating a vulnerability in a dev-only tool as critical. Say what it reaches at runtime.
