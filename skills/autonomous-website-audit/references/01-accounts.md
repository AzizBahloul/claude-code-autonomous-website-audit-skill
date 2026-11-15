# Phase 1: Accounts

> **Answers:** who can use this site, and what does each of them see?
> **Exit gate:** `.website-audit/roles.md` lists every role and state, and each one with its
> own interface has a storage state that a capture has verified.

## Contents
- Find every role and state
- Ask the user once, then don't wait
- Create an account for every role
- Save and verify sessions
- Prove each role's interface is distinct

## Find every role and state

Start with the detector, then confirm each candidate in the guards:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/roles.py .
```

It lists role names from enums, comparisons (`role === 'coach'`), helpers (`hasRole`,
`@Roles`), permission classes, state flags (`is_staff`, `is_premium`, `plan`), route folders
named after a role (`app/(admin)/`), and the seed or factory lines that already create users of
each role. A role that appears in several kinds of evidence is almost certainly real. Then grep
the code for anything it missed that gives a user a different interface:

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

## Ask the user once, then don't wait

In a single message, list the roles you found and say you will create a local test account for
each one. Ask only whether they would rather supply specific test accounts (for example a
staging admin with real-looking data). Never ask for the user's personal login. Don't wait for
the answer: create the accounts below and carry on. A user-supplied account replaces yours when
it arrives.

## Create an account for every role

Every role and state with its own interface gets its own account: admin, every staff or
manager tier, every end-user type, and the states (free vs premium, onboarding unfinished,
empty vs full). Use the first of these that works, and never create accounts on production
without the user's explicit OK:

1. **The app's own seed or fixture tooling.** For example `npm run seed`,
   `python manage.py loaddata`, `rails db:seed`, `prisma db seed`, or a factory script.
2. **Its admin tooling against the local backend.** For example
   `python manage.py createsuperuser`, the admin panel, or an invite flow.
3. **The signup flow, run on the local stack.**
4. **A locally minted session.** Sign a token with the local dev secret against the local
   backend. Do this only when the auth scheme makes it safe and only for local use.

Use obviously fake identities, such as `audit+coach@example.test`, and a minor's birth date
only for a minor-role account. Give each account realistic data: an admin with nothing to
administer, or a coach with no players, hides most of the interface. Use the seed, or create a
few records through the local API as that role.

**Typical commands for an admin or staff account** (local database only):

| Stack | Command |
|---|---|
| Django | `python manage.py createsuperuser --noinput --username audit-admin --email audit+admin@example.test` with `DJANGO_SUPERUSER_PASSWORD` set in the environment; other roles via `python manage.py shell -c "..."` setting the role field or group |
| Rails | `bin/rails runner 'User.create!(email: "audit+admin@example.test", password: ENV["WEBSITE_AUDIT_PASSWORD"], role: :admin)'` |
| Laravel | `php artisan tinker --execute='App\Models\User::factory()->create(["email"=>"audit+admin@example.test","role"=>"admin"])'` |
| Prisma / Node | a short script in `.website-audit/` using the project's own client and password hasher, run with `node` |
| Supabase / Firebase local emulators | the emulator's admin API, then set the role claim or profile row |
| FastAPI / SQLAlchemy | a short script using the project's own session and `hash_password`, never raw SQL with a guessed hash |

Always hash passwords with the project's own function. A hand-rolled hash that the app can't
verify wastes an hour. Record every account you create in `roles.md`, with how to
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

## Prove each role's interface is distinct

Run `crawl.mjs discover` once per role (the inventory phase does this anyway) and add a column
to `roles.md` with the number of pages each role reaches and the routes only that role reaches.
Two roles that reach exactly the same pages and see the same navigation probably don't need
separate passes. Note that, and keep the cheaper one. A role that reaches a page it should not
is a security finding for that page.

## Mistakes to avoid

- Auditing only as an admin. Admins see everything, so missing guards stay invisible.
- Asking the user for their own login. Ask for test accounts, or create them locally.
- Logging in again inside every capture. Save one storage state per role and reuse it until a
  capture reports `redirectedToLogin`.
- Following a "log out" or "switch account" link in a hand-written flow with a saved session.
  It invalidates the session for every later capture.
