# Phase 0: Setup

> **Answers:** can the audit run safely here?
> **Exit gate:** local servers answer over HTTP, nothing points at production, `setup.sh`
> passes (Playwright, Chromium, axe-core, Pillow), and `.website-audit/skills.md` holds the
> design roster with every gap either filled or explained.

## Contents
- Read the project's rules
- Create the state directory
- Start local servers
- Make sure you are not pointed at production
- Tooling
- Build the skill roster
- Install what is missing
- Mistakes to avoid

## Read the project's rules

Read `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md` and the README in the target repo. Write down:

- how to start the frontend and the backend locally;
- the lint, typecheck and test commands;
- whether you may commit (many users forbid it unless asked);
- any paths, data or services marked off limits.

## Create the state directory

Run this from the target project's root (check `pwd` first in monorepos and sibling checkouts):

```bash
mkdir -p .website-audit/auth .website-audit/shots .website-audit/tiles .website-audit/pages
grep -qxF '.website-audit/' .git/info/exclude 2>/dev/null || echo '.website-audit/' >> .git/info/exclude
chmod 700 .website-audit/auth
```

`.git/info/exclude` keeps the state directory out of git without editing the project's
`.gitignore`. Run state, screenshots and session files must never be committed.

## Start local servers

Start the backend and the frontend in the background and record each PID:

```bash
npm run dev > .website-audit/frontend.log 2>&1 &  echo "frontend $!" >> .website-audit/pids
```

Check them with HTTP, not by eye. For example,
`curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/` should print `200` or a
redirect. If a server is already running and you did not start it, use it and leave it alone.
Restarting someone else's server needs the user's OK.

Dev servers inflate timings. When the latency layer needs real numbers, use a production
build (for example `next build && next start`) on another port.

## Make sure you are not pointed at production

Look at the frontend's env files (`.env.local`, `.env.development`, entries such as
`NEXT_PUBLIC_API_URL`, `VITE_API_URL` or `API_BASE_URL`) and at the backend's database URL.
If either one points at a production host, stop. Ask the user for a local or staging target
before running any test that writes data. A production URL for read-only browsing is
acceptable only after the user explicitly says yes.

## Tooling

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/setup.sh
```

This reuses the project's Playwright and axe-core if it has them, and otherwise installs them
next to the scripts (never into the project). It then checks that Chromium launches and that
Pillow is importable, so a passing run means `crawl.mjs`, `tile.py` and `summarize.py` will
work. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/<script> --help` for any script's options
instead of reading its source.

## Build the skill roster

The design, security, a11y and i18n layers run through skills the user already has, and the
design layer uses all of them (rule I9). List what is installed, then build the design roster:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/find_skills.py                 # every layer
python3 ${CLAUDE_SKILL_DIR}/scripts/find_skills.py --roster        # writes .website-audit/skills.md
```

`--roster` assigns every installed design skill to one concern: doctrine, critique, craft,
tokens, components, qa-gates, mobile, motion, copy, stress, data-viz, performance or
accessibility. The design layer runs each concern's skills at that step. Skills that restyle
(a new aesthetic, a redesign, logos, generated images) are listed separately and stay out of
the audit unless the user asks for a redesign (rule I5). Skills in the session's own skill list
count too. If one is missing from the roster, add it to `skills.md` by hand under its concern.

For the other layers, pick in this order:

- **Security.** 1. an installed OWASP-style review skill; 2. Anthropic's `claude-security`
  plugin from `anthropics/claude-plugins-official`, if installed (installing a plugin is the
  user's call: suggest `/plugin install`, don't run it); 3. Claude Code's built-in
  `/security-review` for the diff; 4. the checklist in `07-security.md`, which is
  self-contained. When several are installed, run the OWASP-style one on each page and a
  diff-review skill on the final diff.
- **Accessibility and i18n.** An installed a11y or WCAG audit skill backs the `a11y` layer.
  The reference files cover both layers on their own.

## Install what is missing

`skills.md` ends with "Missing concerns and vetted installs". Close each gap from a fixed set of
sources only:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/install_skill.sh --list           # vetted third-party entries
bash ${CLAUDE_SKILL_DIR}/scripts/install_skill.sh frontend-design  # Anthropic official
bash ${CLAUDE_SKILL_DIR}/scripts/install_skill.sh <vetted-name>    # from trusted_skills.json
```

The script installs from `github.com/anthropics/skills` or from an entry in
`scripts/trusted_skills.json` (a short, human-maintained allowlist, with a license and a reason
for each entry). It refuses anything else, and it refuses a skill that ships hooks, MCP or
settings files. It prints the exact source and commit and records both in
`~/.claude/skills/<name>/.installed-from`. Tell the user each install in one line: the name,
the repository and the commit.

- **No design skill at all:** install `frontend-design` (Anthropic) without waiting, then tell
  the user.
- **A concern with a vetted entry** (copy, motion, a11y, design review): install it and tell the
  user. The user asked for a full audit, and these are allowlisted.
- **Anything not on either list:** never install it on your own. Name the repository, say why
  it fits, and wait for the user's OK. If nobody answers, use the reference files and put the
  recommendation in the report.

Skills installed during a session appear in the skill list from the next session onward. Until
then, `Read` the installed `SKILL.md` directly. Re-run `find_skills.py --roster` after
installing, so `skills.md` is current. `webapp-testing` (Anthropic) is a useful companion for
writing Playwright flows.

## Mistakes to avoid

- Restarting or killing a dev server you did not start.
- Trusting an env file name: `.env.development` can still point at a production API.
- Measuring latency on a dev server and calling it slow. Compare before and after on the same build.
- Skipping `setup.sh` because Playwright "is obviously installed". Without axe-core the
  a11y gate cannot pass.
