# claude-code-autonomous-website-audit-skill

A Claude Code skill that audits and repairs an entire website **page by page**. It lists
every route, signs in as every role, and captures each page at phone, tablet and desktop
widths in light and dark mode. Then it fixes **functionality and latency, design, security
and translation** issues in a fixed order, one page at a time. It doesn't stop until every
page in the inventory is finished.

It works on Next.js, Nuxt, SvelteKit, Remix / React Router, Astro, Gatsby, TanStack Router,
SPA router configs (React Router, Vue Router, Angular), Django, Laravel, Rails and plain HTML.

- **Inventory first.** A script lists every page and its components into a tracked
  checklist, so the run can resume and can't silently skip a page.
- **Every page gets the same treatment.** The same seven layers run in the same order, and
  the tracker refuses to mark a layer done out of order.
- **Captures without guesswork.** A Playwright crawler records page errors, failed requests,
  responses with status 400 or above, raw i18n keys on screen, horizontal overflow,
  `NaN`/`undefined` text, low contrast, broken images, tokens in storage and API latency.
  A tiler splits full-page screenshots so they can be read.
- **Safe by default.** It never mutates production, captures only the app under test with
  test accounts, kills only processes it started, and installs skills only from Anthropic's
  official repository.

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

### Phases

| Phase | What happens |
|---|---|
| 0. Setup | Reads your project's `CLAUDE.md` and test commands, starts local servers, confirms nothing points at production, and checks Playwright and Pillow. It picks your installed design and security skills; if no design skill is installed, it installs Anthropic's official `frontend-design` and tells you. |
| 1. Accounts | One verified browser session per role, plus logged-out and free/unpaid where those change a page. |
| 2. Inventory | Detects the framework and lists every page, its layouts and its components. Dynamic routes get real sample IDs. |
| 3. Per-page loop | The seven layers below, for every page, in order. |
| 4. Report | `.website-audit/REPORT.md`: fixes per page, security findings, performance before and after, API/schema/enum changes called out plainly, and the test accounts created. |

### The seven layers, for every page

| # | Layer | What it covers |
|---|---|---|
| 1 | baseline | Captures every width x theme x role, tiles the screenshots, triages crawler findings |
| 2 | code | Reads the route, layouts, components, data layer and every backend endpoint the page calls |
| 3 | functionality | Every control and CRUD flow on the local stack, a complexity pass, then latency from browser to database |
| 4 | design | Fixes using your existing tokens and components, checked in both themes at all widths |
| 5 | security | Server-side authorization, IDOR, data leaking to the wrong role, minors' data, tokens in storage, unsanitised HTML, direct client-to-backend calls |
| 6 | i18n | Missing keys, hardcoded copy, plurals, and number, percent, date and currency formats in every supported language |
| 7 | verify | Re-captures and compares with the baseline, runs your lint, typecheck and tests, and commits the page |

Every fix that can have a regression test gets one, and the test is shown to **fail on the old
code** before it counts. If your project allows commits, it commits in small units per page,
after your own lint, typecheck and tests pass. If it doesn't, the changes stay uncommitted and
are listed in the report.

### Lessons built in

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
  SKILL.md                       orchestration: phases, per-page loop, stop condition
  references/                    loaded only when their phase runs
    00-setup.md  01-accounts.md  02-inventory.md  capture.md
    03-code-read.md  04-functionality-latency.md  05-design.md
    06-security.md  07-i18n.md  08-report.md
  scripts/
    crawl.mjs                    Playwright crawler: capture | login | selftest
    tile.py                      splits tall screenshots into readable tiles
    inventory.py                 route + component inventory for many frameworks
    progress.py                  per-page checklist and stop condition
    i18n_check.py                compares translation catalogs across locales and against code
    find_skills.py               lists installed design/security/i18n/a11y skills
    install_skill.sh             installs a skill from github.com/anthropics/skills only
    setup.sh                     checks or installs Playwright, Chromium and Pillow
```

The scripts also work on their own, for example:

```bash
S=~/.claude/skills/autonomous-website-audit/scripts
python3 $S/inventory.py . --out .website-audit/inventory.json
node $S/crawl.mjs capture --base http://localhost:3000 --path /pricing --themes light,dark
python3 $S/tile.py .website-audit/shots/pricing --out .website-audit/tiles/pricing
```

## Requirements

- Claude Code
- Node.js 18+ (Playwright is reused from your project, or installed next to the scripts on
  first run by `setup.sh`)
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
