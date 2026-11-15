# Project phase: endpoints

> **Answers:** which backend endpoints exist, which page calls each one, which calls are
> broken, which endpoints nothing uses, and how fast each one answers?
> **Gate:** `endpoints.json` and `ENDPOINTS.md` exist, and when there are endpoints, a
> baseline measurement in `perf/bench-*.json`. Close with `progress.py done @project endpoints`.

## Steps

1. **Map endpoints, calls and pages:**
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/endpoints.py . --inventory .website-audit/inventory.json \
     --architecture .website-audit/architecture.json
   ```
   It reads backend routes for Express, Koa, Hono, Fastify, NestJS, FastAPI, Flask, Django and
   DRF, Laravel, Spring, Go and Next.js/Nuxt route handlers. It reads frontend calls from
   fetch, axios, ky, `$fetch`, `useFetch`, SWR and `<client>.get(...)`, and matches them by
   path suffix, so a router prefix mounted in another file still matches. Rails: run
   `bin/rails routes`. For anything else the script can't parse, list the endpoints from the
   router by hand in `ENDPOINTS.md`.
2. **Prefer the backend's own spec or route list** when it has one. With an OpenAPI spec
   (FastAPI `/openapi.json`, NestJS Swagger, drf-spectacular), re-run step 1 with `--openapi
   http://localhost:8000/openapi.json` (or a file). The spec becomes the primary list: routes
   only in the spec are added, and code routes missing from it are listed as undocumented.
   Otherwise run `python manage.py show_urls`, `php artisan route:list --json` or
   `bin/rails routes`, and add anything the script missed to `ENDPOINTS.md` by hand. Never
   truncate the list.
3. **Read the two problem lists:**
   - **Unmatched frontend calls** (no backend route) are the most likely real bugs: a renamed
     endpoint, a typo, a removed feature. Reproduce each one (open the page and watch the
     request, or `curl` it). Log each confirmed one as an `@project endpoints` finding,
     severity `high` when a user flow depends on it.
   - **Orphan endpoints** (no caller and no observed traffic) are dead backend code, or they
     serve a mobile app, a webhook, a cron job or a third party. Grep the whole repo (and ask
     about other clients when in doubt) before calling one dead. Log each confirmed one under
     `hygiene`.
4. **Measure every GET endpoint** against the LOCAL backend, signed in as the role that uses it:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/bench.py run --base http://localhost:8000 \
     --endpoints .website-audit/endpoints.json --storage .website-audit/auth/coach.json \
     --param id=42 --param slug=demo --label baseline
   ```
   Seven sequential samples after a warm-up give p50, p95, max, status, bytes and any
   `Server-Timing` header. Parameterised paths are skipped until you pass `--param NAME=VALUE`
   (use the sample IDs from the inventory phase). `bench.py` refuses a non-local host. Endpoints
   that write (POST, PUT, PATCH, DELETE) are timed in the `functionality` layer, inside real
   flows on the local stack, and never here.
5. **Triage the slow and the heavy ones.** p50 over 300 ms locally, or a response flagged
   `LARGE` (over 100 KB, usually an unpaginated list or whole objects where the page needs
   three fields), gets a finding. The cause and the fix belong to the `functionality` layer of the first page
   that uses the endpoint (see [04](04-functionality-latency.md), "End-to-end latency"). Log
   it on that page, not on `@project`, so it is fixed with a test in context.
6. **Check the contract across endpoints.** The same entity should have the same shape
   everywhere (a player is not `{id, name}` here and `{player_id, full_name}` there), and lists
   should share one pagination shape, errors one error shape, and money one representation.
   Inconsistencies go in as `@project endpoints` findings at `medium`. Fixing them changes API
   shapes, which the report calls out plainly.
7. **Take the access matrix once** (`bench.py matrix`, see
   [07-security](07-security.md)) for every role. Keep `perf/matrix.json` for the security
   layer of each page. An endpoint that answers 200 to anonymous is a lead, not a verdict.
8. **Run the backend's own test suite once** and quote the result in the note. A failing suite
   is a finding before any page work starts.
9. After the first baseline captures exist, re-run step 1 with `--observed
   .website-audit/shots/baseline` to add real browser traffic per page.

## What a good note looks like

`--note "41 endpoints (fastapi), 63 calls; 2 unmatched calls (F-003, F-004, fixed), 5 orphans
(3 dead -> hygiene, 2 used by the mobile app); 38 GETs measured, 4 over 300ms logged on their
pages; backend tests: 212 passed"`

## Mistakes to avoid

- Deleting an orphan endpoint that a mobile app or webhook still calls.
- Benchmarking a dev server with debug toolbars and calling it slow. Compare runs on the same
  build, and note which build you measured.
- Treating a 401 from the bench as slow or broken. Pass the role's storage state.
- Running a load test. `bench.py` is sequential on purpose: you are measuring latency, not
  capacity, and you must not take down a shared machine.
