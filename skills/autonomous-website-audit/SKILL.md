---
name: autonomous-website-audit
description: Audits and repairs an entire website page by page. Inventories every route, signs in as every role, captures each page at phone, tablet and desktop widths in light and dark mode, then fixes functionality and latency, design, security and translation issues in that fixed order, one page at a time, until every page in the inventory is finished. Use when the user asks to audit, QA, crawl, fix or polish a whole site or web app, to check every page or every screen size, or to run a full design, functionality, security and i18n pass across all routes.
license: MIT
---

# Autonomous website audit

Audit and fix a website one page at a time, giving every page the same depth and the same
layers in the same order. The run is resumable and cannot silently skip a page: the
inventory feeds a tracked checklist, and the run stops only when that checklist says so.

Scripts live in `${CLAUDE_SKILL_DIR}/scripts/`. If that variable is empty, use the directory
holding this SKILL.md. Run state lives in the target project under `.website-audit/`.

## Rules that override everything below

1. **Never mutate production.** Run every create, update or delete test against a local
   backend. Browse a production site read-only, and only after the user explicitly says yes.
2. **Capture only the app under test**, in the headless browser, signed in with test
   accounts. Never screenshot the user's screen, desktop or apps, and never open their
   personal files or use them as test input.
3. **Kill only processes you started, by PID.** Record each PID in `.website-audit/pids` as
   you start it.
4. **No page is skipped or skimmed.** Every page in the inventory gets every layer.
   Only the user can excuse a page (`progress.py block`, then acknowledged in the report).
5. **Never install a skill from an unvetted source silently.** See
   [references/00-setup.md](references/00-setup.md).
6. **Session files are secrets.** Storage-state files hold live sessions. Keep them in
   `.website-audit/auth/`, keep that directory out of git, and never print their contents.
7. **The project's own rules win.** Its `CLAUDE.md` and `AGENTS.md` may forbid commits or
   name the test commands. Follow them over this skill.

## Phases

Work through these phases in order. For each one, read its reference file when the phase
starts, not before.

| Phase | Read | Exit criterion |
|---|---|---|
| 0. Setup | [references/00-setup.md](references/00-setup.md) | Local servers answer; `setup.sh` passes; design and security skills chosen |
| 1. Accounts | [references/01-accounts.md](references/01-accounts.md) | One verified storage state per role, plus logged-out and free/unpaid where those exist |
| 2. Inventory | [references/02-inventory.md](references/02-inventory.md) | `progress.py init` done; every dynamic route has a concrete URL |
| 3. Per-page loop | the table below | `progress.py check` exits 0 |
| 4. Report | [references/08-report.md](references/08-report.md) | `.website-audit/REPORT.md` written; own processes stopped |

## The per-page loop

```bash
P="python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py"
$P next                  # prints: ROUTE <tab> LAYER <tab> URL
$P start ROUTE           # one page at a time
# ...do the layer...
$P done ROUTE LAYER --note "what was found and fixed"
```

Each page goes through all seven layers in this order. `progress.py` refuses to mark a layer
done out of order.

| Layer | Read | Done when |
|---|---|---|
| `baseline` | [references/capture.md](references/capture.md) | The full capture matrix has been taken, tiled and viewed; crawler findings are triaged |
| `code` | [references/03-code-read.md](references/03-code-read.md) | Route, layouts, components, data layer and every backend endpoint the page calls have been read; dossier written |
| `functionality` | [references/04-functionality-latency.md](references/04-functionality-latency.md) | Every control and CRUD flow works, the complexity pass is done, and latency is measured end to end |
| `design` | [references/05-design.md](references/05-design.md) | Issues are fixed with existing tokens and components, in both themes and at all three widths |
| `security` | [references/06-security.md](references/06-security.md) | Authorization, data exposure, storage, HTML injection and the request path have been checked and fixed |
| `i18n` | [references/07-i18n.md](references/07-i18n.md) | Every string and number format is checked in every supported locale |
| `verify` | [references/capture.md](references/capture.md) | Re-capture shows no regressions; lint, typecheck and tests pass; the page's changes are committed |

Then go to the next page and treat it exactly like the last one: same depth, same layers.
A page that looks simple still gets every layer, because simple-looking pages are where
stale copy, missing auth checks and broken empty states hide.

If verifying a later page breaks something on an earlier one, run `progress.py reopen ROUTE
LAYER` on the earlier page and redo it from that layer.

### Committing

If the project allows commits, commit in small units, at least one per page, and keep
security and performance fixes in separate commits so they stand out. Before every commit,
run the project's own lint, typecheck and tests, and only commit when they pass. Then record
the commit with `progress.py commit ROUTE SHA`. If the project forbids commits, leave the tree
dirty and list the changes per page in the report.

### Regression tests

Every fix that can have a test gets one, and that test must be shown to fail on the old code:

```bash
git stash push -- <fixed source files>   # keep the new test, drop the fix
<run the single test>                    # must FAIL
git stash pop                            # restore the fix
<run the single test>                    # must PASS
```

A test that passes both ways proves nothing; rewrite it.

## Stop condition

The run is finished only when `python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py check` exits 0.
While it exits 1, take the next page and keep going; do not stop to ask whether to continue.
Only something that needs the user stops the run: a missing credential, a production
mutation, or an ambiguous product decision. In that case run `progress.py block ROUTE --reason
...`, carry on with the other pages, and raise the blocked pages together at the end. Pass
`check --allow-blocked` only after the user has acknowledged them.

## Traps that cost real runs

- **Noise is not a bug.** Aborted video preloads, resets from an external CDN, and dev-server
  HMR crashes that vanish on a re-run are noise. `crawl.mjs` labels these; re-run before
  acting on one.
- **A 403 can be intended gating** (premium content, minors' data). Check the backend rule
  before "fixing" it, and make sure the UI explains the refusal.
- **Light mode is where bare `-400` shades become unreadable.** Check both themes every time.
- **On-screen claims must match the data.** Watch for zeros shown when there is no data,
  wrong singular or plural, "leads by 0 points", a tie named as one winner, and labels that
  promise features that don't exist.
- **Never view a full-page capture directly.** Tile it with `tile.py` first.
