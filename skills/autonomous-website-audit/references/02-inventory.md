# Phase 2: Inventory

> **Answers:** which pages exist, for which roles?
> **Exit gate:** `progress.py init` is done, `crawl.mjs discover --compare` lists no untracked
> page for any role, and every dynamic route has a concrete URL.

## Contents
- Steps
- Pages the file system does not show
- Dynamic routes
- Mistakes to avoid

## Steps

1. **Generate the inventory from the code:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py . --out .website-audit/inventory.json
   ```
   The script detects the framework for each package in the repo. It lists every page with
   its file, its layout chain (Next.js app router and SvelteKit) and the local components it
   imports up to two levels deep. API routes go in a separate list: they are not pages, but
   the `code` and `security` layers use them.
2. **Confirm it.**
   - **File-system routers** (Next.js, Nuxt, SvelteKit, Remix, Astro, Gatsby) are exact.
   - **Entries marked `low confidence`** came from regex over router config (React Router,
     Vue Router, Angular, Django, Laravel). Open each router file and fix nested relative
     paths and include prefixes.
   - **Run every command under `authoritative_commands`** (for example `bin/rails routes` or
     `python manage.py show_urls`), and add any page they show that the script missed.
   - **Read every line under `warnings`.**
3. **Create the checklist:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py init --inventory .website-audit/inventory.json
   ```
   Running `init` again merges in new pages and never drops tracked ones. Keep the order it
   creates. Don't reorder pages by how important they look, because every page gets the same
   treatment.
4. **Give every dynamic route a URL** (see below).
5. **Discover what the code missed** by crawling the running app's links once per role,
   anonymous included:
   ```bash
   node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs discover --base http://localhost:3000 \
     --storage .website-audit/auth/coach.json --compare .website-audit/progress.json
   ```
   `discover` follows `<a href>` links with GET navigations only. It never clicks or submits,
   and it never follows links that look like they change state (logout, delete, revoke,
   cancel, impersonate, `/api/`; add more with `--deny REGEX`). It collapses IDs, so
   `/players/42` and `/players/43` count as one page. Each `NEW` line is a page the progress
   file does not track: add the ones that render distinct UI.
   If it prints `SESSION LOST`, a link signed the session out: add that link to `--deny`, log
   in again and re-run. If it prints `STOPPED at --max-pages`, raise the limit.
6. **Add the pages from the next section** with `progress.py add ROUTE [--url URL]`.

`.website-audit/PROGRESS.md` is the readable view of the checklist, and it regenerates after
every change.

## Pages the file system does not show

Add these whenever they render distinct UI:

- tabs or steps addressed by query string (`/settings?tab=billing`, `/onboarding?step=3`);
- modals with their own URL;
- role-specific variants served from one route that differ enough to need their own pass
  (for example `/dashboard` as coach vs as player);
- error pages: 404, 500 and the not-authorized page;
- email-link landing pages: verify email, reset password, accept invite;
- pages reachable only through a button or a form (no `<a href>`), which `discover` can't see.
  Find them in the router and the code.

## Dynamic routes

For every `[id]`, `:id` or `<int:pk>` route, pick a real ID from the local database or the
seed. Choose one the relevant role is allowed to see, and one with data in it, so that empty
states don't hide the layout:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py set-url '/players/[id]' /players/42
```

Also note an ID the role must NOT see. The security layer needs it for the IDOR check. Record
both in the page's dossier during the `code` layer.

## Mistakes to avoid

- Trusting the regex inventory of a config-based router without opening the router file.
- Running `discover` only as an admin. Each role sees different links.
- Adding every `?page=2` or `?sort=name` as a page. Only query parameters that change the
  layout (tab, step, view, section, mode) are pages.
- Crawling with a hand-written script that clicks every button. That is how audits delete data
  and sign themselves out.
