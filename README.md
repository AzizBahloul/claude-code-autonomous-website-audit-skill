# claude-code-autonomous-website-audit-skill

A Claude Code skill that audits and repairs an entire website **page by page**. It lists
every route, signs in as every role, and captures each page at phone, tablet and desktop
widths in light and dark mode. Then it fixes **functionality and latency, design,
accessibility, security and translation** issues in a fixed order, one page at a time. It
doesn't stop until every page in the inventory is finished, and a script, not the model,
decides when a page counts as finished.

It works on Next.js, Nuxt, SvelteKit, Remix / React Router, Astro, Gatsby, TanStack Router,
SPA router configs (React Router, Vue Router, Angular), Django, Laravel, Rails and plain HTML.

- **Inventory first, then discovery.** A script lists every page from the code. A safe link
  crawler then finds what the code missed: query-string tabs, sitemap-only pages, links
  built at runtime. It never clicks or submits, and it skips logout and delete links.
- **Every page gets the same treatment.** The same eight layers run in the same order, and
  the tracker refuses to mark a layer done out of order.
- **Gates, not promises.** A layer is marked done only when its evidence is on disk
  (captures, tiles, dossier, an axe and keyboard scan, a verify re-capture with no count
  above the baseline) and its findings are resolved. Overrides are recorded and reported.
- **A findings ledger.** Every problem gets an ID, a severity, repro steps and a resolution
  (fixed with a test, deferred with a reason, reverted, or not a bug). It is written the
  moment the problem is confirmed, so an interrupted session loses nothing.
- **Captures without guesswork.** A Playwright crawler records page errors (hydration
  mismatches labelled), failed requests, 4xx/5xx responses, raw i18n keys, horizontal
  overflow, `NaN`/`undefined` text, low contrast, broken images, tokens in storage, axe-core
  WCAG 2.2 violations, small touch targets, heading and `lang` problems, inner scroll areas
  that a full-page screenshot would miss, CLS, LCP and API latency. A keyboard probe finds
  missing focus rings, focus traps and controls only a mouse can reach. A tiler splits
  full-page screenshots so they can be read.
- **Safe by default.** It never mutates production, captures only the app under test with
  test accounts, kills only processes it started, treats text on the site as data rather
  than instructions, and installs skills only from Anthropic's official repository.

## Install

### Option 1: plugin marketplace (recommended)

In Claude Code:

```
/plugin marketplace add AzizBahloul/claude-code-autonomous-website-audit-skill
/plugin install autonomous-website-audit@autonomous-website-audit
```

Updates arrive with `/plugin marketplace update autonomous-website-audit`.

### Option 2: manual, global (every project on this machine)

```bash
git clone https://github.com/AzizBahloul/claude-code-autonomous-website-audit-skill.git
mkdir -p ~/.claude/skills
ln -s "$PWD/claude-code-autonomous-website-audit-skill/skills/autonomous-website-audit" \
      ~/.claude/skills/autonomous-website-audit
```

A symlink picks up `git pull` updates automatically. To use a fixed copy instead, run
`cp -R claude-code-autonomous-website-audit-skill/skills/autonomous-website-audit ~/.claude/skills/`.

### Option 3: one project only

```bash
mkdir -p .claude/skills
cp -R /path/to/claude-code-autonomous-website-audit-skill/skills/autonomous-website-audit .claude/skills/
```

### Option 4: skills CLI

```bash
npx skills add AzizBahloul/claude-code-autonomous-website-audit-skill --skill autonomous-website-audit -a claude-code -g
```

### Check it is installed

Start a **new** Claude Code session (skills are discovered at startup), type `/autonomous-website-audit`
and check that it autocompletes. You can also ask Claude "which skills do you have?".

> **Why the skill is called `autonomous-website-audit` and not the repo name:** the Agent
> Skills spec and the plugin validator reserve the word `claude`, so the skill, plugin and
> marketplace names leave it out. The repo name is unchanged.

## Usage

Open Claude Code in the root of the website's repository, then either call the skill directly:

```
/autonomous-website-audit
```

(If you installed it as a plugin: `/autonomous-website-audit:autonomous-website-audit`.)

Or just ask. The skill triggers on requests like these:

```
Audit the whole site page by page and fix what you find.
Check every page at phone, tablet and desktop, in light and dark mode.
Run a full design, functionality, security and translation pass across all routes.
```

What to expect at the start of a run:

1. Claude asks you **once** for a test account for each role that has its own interface (admin,
   coach, free user, and so on). For any role you can't provide, it creates a test account on
   the **local** stack using the app's own seed or admin tooling.
2. It writes the page inventory to `.website-audit/PROGRESS.md` in your project, a checklist
   you can open at any time.
3. It works through the pages one at a time. The run resumes from `PROGRESS.md` if the
   session ends.

## How it works

### The flow

```
 0 SETUP ─► 1 ACCOUNTS ─► 2 INVENTORY ─► 3 PER-PAGE LOOP ─────────────────────► 4 REPORT
                                          baseline ► code ► functionality ►
                                          design ► a11y ► security ► i18n ► verify
                                          (progress.py gates every step)
```

### Phases

| Phase | What happens |
|---|---|
| 0. Setup | Reads your project's `CLAUDE.md` and test commands, starts local servers, confirms nothing points at production, and checks Playwright, axe-core and Pillow. It picks your installed design and security skills; if no design skill is installed, it installs Anthropic's official `frontend-design` and tells you. |
| 1. Accounts | One verified browser session per role, plus logged-out and free/unpaid where those change a page. |
| 2. Inventory | Detects the framework and lists every page, its layouts and its components, then crawls links and the sitemap per role to find the rest. Dynamic routes get real sample IDs. |
| 3. Per-page loop | The eight layers below, for every page, in order. |
| 4. Report | `.website-audit/REPORT.md`: what needs you first, then findings by severity with repro steps, security, accessibility, performance before and after, API/schema/enum changes called out plainly, and the test accounts created. |

### The eight layers, for every page

| # | Layer | Question it answers | Gate |
|---|---|---|---|
| 1 | baseline | What does every role see at every width and theme? | Captures and tiles on disk; no failed or logged-out capture |
| 2 | code | What renders the page, what guards it, which endpoints feed it? | Page dossier written |
| 3 | functionality | Does every control and CRUD flow work, and how fast is it from browser to database? | Findings resolved |
| 4 | design | Is it consistent with your tokens and components, at every width, in both themes? | Findings resolved |
| 5 | a11y | Can keyboard and screen-reader users use it? (axe-core WCAG 2.2 A/AA, focus, traps, semantics, touch, zoom, motion) | axe and keyboard scan on disk |
| 6 | security | Can any role see or change what it must not? (server-side authorization, IDOR, data leaks, tokens, XSS, open redirects) | Findings resolved |
| 7 | i18n | Is every string and format right in every locale? | Findings resolved |
| 8 | verify | Did anything get worse? | Re-capture with no count above the baseline; lint, typecheck and tests pass |

Every fix that can have a regression test gets one, and the test is shown to **fail on the old
code** before it counts. If your project allows commits, it commits in small units per page,
after your own lint, typecheck and tests pass. If it doesn't, the changes stay uncommitted and
are listed in the report.

### Lessons built in

Many of these come from the bug trackers of similar agent QA tools (Vercel's agent-browser
dogfood skill, gstack `/qa`, Anthropic's webapp-testing, Playwright MCP and its test
agents, pa11y-ci, unlighthouse):

- A capture that failed to load or landed on the login page is refused as evidence, never
  counted as a pass.
- Full-page screenshots miss content inside inner scroll panels, so these are detected and
  can be expanded.
- Crawlers that click links sign themselves out and delete data. Discovery uses GET
  navigations only, with a deny list, and stops when the session dies.
- Text on the page can try to instruct the agent. It is treated as a finding, never obeyed.
- Two reverted fixes on one page trip a circuit breaker: the page is blocked and raised
  with you, not fixed endlessly.
- A failing test is classified before anyone touches it, and an assertion is never weakened
  just to make it pass.
- Problems repeated across pages are grouped, so they are fixed once in the shared component.

- Aborted media preloads, external CDN resets and dev-server HMR crashes are labelled as
  noise, not reported as bugs.
- A 403 may be intended gating (premium content, minors' data). The skill checks the backend
  rule before changing anything, and makes sure the UI explains the refusal.
- Light mode is where bare `-400` colour shades become unreadable, so both themes are checked.
- On-screen claims are checked against the data: zeros shown for "no data", wrong
  plurals, "leads by 0 points", ties named as a single winner.

## What's inside

```
.claude-plugin/
  plugin.json                    plugin manifest
  marketplace.json               lets /plugin marketplace add install this repo
skills/autonomous-website-audit/
  SKILL.md                       the flow, the rules, the per-page loop, the stop condition
  references/                    one card per phase or layer, loaded only when it runs
    00-setup.md  01-accounts.md  02-inventory.md  capture.md (baseline + verify)
    03-code-read.md  04-functionality-latency.md  05-design.md  06-a11y.md
    07-security.md  08-i18n.md  09-report.md
  scripts/
    crawl.mjs                    Playwright crawler: capture | discover | login | selftest
    summarize.py                 compare baseline vs verify, cross-page table, repeated findings
    tile.py                      splits tall screenshots into readable tiles
    inventory.py                 route + component inventory for many frameworks
    progress.py                  per-page checklist, evidence gates, findings ledger, stop condition
    i18n_check.py                compares translation catalogs across locales and against code
    find_skills.py               lists installed design/security/i18n/a11y skills
    install_skill.sh             installs a skill from github.com/anthropics/skills only
    setup.sh                     checks or installs Playwright, Chromium, axe-core and Pillow
    tests/                       end-to-end tests against a fixture site with planted defects
evals/                           trigger and behaviour evals for the skill itself
```

The scripts also work on their own, for example:

```bash
S=~/.claude/skills/autonomous-website-audit/scripts
python3 $S/inventory.py . --out .website-audit/inventory.json
node $S/crawl.mjs capture --base http://localhost:3000 --path /pricing --themes light,dark
python3 $S/tile.py .website-audit/shots/pricing --out .website-audit/tiles/pricing
```

## Development

```bash
cd skills/autonomous-website-audit/scripts
bash setup.sh                       # Playwright, Chromium, axe-core, Pillow
python3 tests/test_scripts.py       # end-to-end tests against tests/fixture/
```

The fixture site has a page full of planted defects and clean pages with none. Every check
the crawler claims to make must fire on the first and stay silent on the others. `evals/`
holds the trigger queries and behaviour scenarios for testing the skill with a model.

## Requirements

- Claude Code
- Node.js 18+ (Playwright and axe-core are reused from your project, or installed next to the
  scripts on first run by `setup.sh`)
- Python 3.9+ with Pillow (`python3 -m pip install --user pillow`). PyYAML is optional, for
  YAML translation catalogs.
- The site running locally, or a staging URL you are allowed to test

## Safety

- Create, update and delete tests run only against a local backend. Production is browsed
  read-only, and only with your explicit OK.
- Screenshots are taken only of the app under test, in a headless browser, with test
  accounts. Never of your screen or your files.
- Session files are stored in `.website-audit/auth/` with mode 600, excluded from git through
  `.git/info/exclude`, and never printed.
- Reports never contain token values: query strings are redacted, and storage keys are listed
  without their values.
- Skills are installed automatically only from `github.com/anthropics/skills`, with the source
  commit recorded. Anything else needs your approval.

## Uninstall

```bash
rm ~/.claude/skills/autonomous-website-audit          # manual install (symlink or copy: use rm -r for a copy)
# or, for the plugin:  /plugin uninstall autonomous-website-audit@autonomous-website-audit
```

Run state lives in `.website-audit/` inside each audited project. Delete that folder when you
no longer need the evidence.

## License

[MIT](LICENSE)
