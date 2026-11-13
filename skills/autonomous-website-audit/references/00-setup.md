# Phase 0: Setup

## Contents
- Read the project's rules
- Create the state directory
- Start local servers
- Make sure you are not pointed at production
- Tooling
- Choose the design and security skills

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

This reuses the project's Playwright if it has one, and otherwise installs Playwright next to
the scripts. It then checks that Chromium launches and that Pillow is importable, so a passing
run means `crawl.mjs` and `tile.py` will work.

## Choose the design and security skills

The design and security layers run through skills the user already has. List what is installed:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/find_skills.py
```

Use the skills in the session's own skill list as well. Then pick one skill per layer:

- **Design.** In order of preference:
  1. a project-local design skill;
  2. `frontend-design` (Anthropic);
  3. another design skill the user installed, such as a UI/UX or design-review skill.
- **Security.** In order of preference:
  1. an installed OWASP-style review skill;
  2. Claude Code's built-in `/security-review` for the diff;
  3. the checklist in `06-security.md`, which is self-contained.
- **Accessibility and i18n.** Use an installed skill when there is one. The reference files
  cover both layers on their own.

If no design skill is installed, install Anthropic's official one, then tell the user what
was installed and from where:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/install_skill.sh frontend-design
```

The script installs only from `github.com/anthropics/skills`. It prints the exact source and
commit, and it records both in `~/.claude/skills/<name>/.installed-from`. Skills installed
during a session appear in the skill list from the next session onward. Until then, `Read`
the installed `SKILL.md` directly. `webapp-testing` from the same repository is a useful
companion for writing Playwright flows.

Anthropic's repository has no security skill. Never install a third-party skill without the
user's OK: name the repository, say why you picked it, and wait. If nobody is there to answer,
use the built-in options above and put the recommendation in the report.

Load the chosen design skill once, during the first page's `code` layer, and follow it in every
`design` layer after that.
