# Phase 1: Accounts

> **Answers:** who can use this site, and what does each of them see?
> **Exit gate:** `.website-audit/roles.md` lists every role and state, and each one with its
> own interface has a storage state that a capture has verified.

## Contents
- Find every role and state
- Ask the user once
- Create the accounts nobody provided
- Save and verify sessions

## Find every role and state

Grep the code for anything that gives a user a different interface:

- role enums and columns, such as `role`, `is_staff`, `is_admin` or `user_type`;
- permission tables, RBAC or ACL code;
- route guards and middleware;
- route groups named after roles, such as `app/(coach)/`, `/admin` or `/dashboard/club`;
- backend permission classes and policies.

Also list the states that change what a page shows:

- logged out;
- free or unpaid vs premium;
- trial expired;
- onboarding not finished;
- minor vs adult;
- an empty account vs one full of data;
- a suspended account.

Write the matrix to `.website-audit/roles.md`:

| Role or state | Own interface? | Account source | Storage state |
|---|---|---|---|
| anonymous | yes | none | none |
| coach | yes | provided by the user | `.website-audit/auth/coach.json` |
| free player | yes | created from a local seed | `.website-audit/auth/player-free.json` |

## Ask the user once

In a single message, ask the user for a test account for every role and state that has its own
interface. Say why each one is needed. Ask for test accounts, never for the user's personal
login. Then carry on with the inventory while you wait.

## Create the accounts nobody provided

Create an account for each role the user cannot provide. Use the first of these that works,
and never create accounts on production without the user's explicit OK:

1. **The app's own seed or fixture tooling.** For example `npm run seed`,
   `python manage.py loaddata`, `rails db:seed`, `prisma db seed`, or a factory script.
2. **Its admin tooling against the local backend.** For example
   `python manage.py createsuperuser`, the admin panel, or an invite flow.
3. **The signup flow, run on the local stack.**
4. **A locally minted session.** Sign a token with the local dev secret against the local
   backend. Do this only when the auth scheme makes it safe and only for local use.

Use obviously fake identities, such as `audit+coach@example.test`, and a minor's birth date
only for a minor-role account. Record every account you create in `roles.md`, with how to
delete it, so the report can list them for cleanup.

## Save and verify sessions

Keep passwords in environment variables, never on the command line or in files:

```bash
export WEBSITE_AUDIT_PASSWORD='...'   # set by you or the user, never echoed back
node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs login --base http://localhost:3000 \
  --login-path /login --email audit+coach@example.test \
  --out .website-audit/auth/coach.json
```

The login helper handles one-step forms and two-step forms (email first, then password). For
OAuth, magic links or one-time codes, write a short Playwright script that saves
`context.storageState()`. Another option is to mint the session cookie locally and write the
storage-state JSON yourself.

Verify each session by crawling a page only that role can open:

```bash
node ${CLAUDE_SKILL_DIR}/scripts/crawl.mjs capture --base http://localhost:3000 --path /dashboard \
  --storage .website-audit/auth/coach.json --viewports desktop --no-screenshot
```

The output must not report `redirected to /login`, and `redirectedToLogin` in the JSON must be
false. Sessions expire mid-run. When a later capture redirects to login, log in again before
trusting the results.

## Mistakes to avoid

- Auditing only as an admin. Admins see everything, so missing guards stay invisible.
- Asking the user for their own login. Ask for test accounts, or create them locally.
- Logging in again inside every capture. Save one storage state per role and reuse it until a
  capture reports `redirectedToLogin`.
- Following a "log out" or "switch account" link in a hand-written flow with a saved session.
  It invalidates the session for every later capture.
