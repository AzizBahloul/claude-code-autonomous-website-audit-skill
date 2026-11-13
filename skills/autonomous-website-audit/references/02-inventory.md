# Phase 2: Inventory

## Contents
- Generate the inventory
- Confirm it
- Pages the file system does not show
- Dynamic routes
- Create the checklist

## Generate the inventory

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py . --out .website-audit/inventory.json
```

The script detects the framework for each package in the repo. It lists every page with its
file, its layout chain (for the Next.js app router and SvelteKit) and the local components it
imports up to two levels deep. API and endpoint routes go in a separate list. These are not
pages to audit on their own, but you will meet them in the `code` and `security` layers.

## Confirm it

- **File-system routers** (Next.js, Nuxt, SvelteKit, Remix, Astro, Gatsby) are exact.
- **Entries marked `low confidence`** were found by regex in router config (React Router, Vue
  Router, Angular, Django, Laravel). Open each router file and fix nested relative paths and
  include prefixes.
- **Run every command under `authoritative_commands`** (for example `bin/rails routes` or
  `python manage.py show_urls`). Add any page they show that the script missed.
- **Read every line under `warnings`.**

## Pages the file system does not show

Add these with `progress.py add ROUTE` whenever they render distinct UI:

- tabs or steps addressed by query string (`/settings?tab=billing`, `/onboarding?step=3`);
- modals with their own URL;
- role-specific variants served from one route that are different enough to need their own
  pass (for example `/dashboard` as coach vs as player);
- error pages: 404, 500 and the not-authorized page;
- email-link landing pages: verify email, reset password, accept invite.

After each page's baseline capture, compare the `links` array in its crawl reports with the
inventory, and add any internal path that does not match a tracked route.

## Dynamic routes

Pick a real ID from the local database or the seed for every `[id]`, `:id` or `<int:pk>`
route. Choose an ID the relevant role is allowed to see, and one with data in it, so empty
states don't hide the layout:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py set-url '/players/[id]' /players/42
```

Note in the page dossier which IDs belong to which role. The security layer needs an ID the
role must NOT see.

## Create the checklist

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/progress.py init --inventory .website-audit/inventory.json
```

Running `init` again merges in new pages and never drops pages already tracked. Keep the order
it creates. Do not reorder pages by how important they look, because every page gets the same
treatment. `.website-audit/PROGRESS.md` is the readable view, and it regenerates after every
change.
