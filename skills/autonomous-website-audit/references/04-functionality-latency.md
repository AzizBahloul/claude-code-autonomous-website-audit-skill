# Layer: functionality, complexity, latency

## Contents
- Separate noise from bugs
- 401 and 403: intended or broken?
- Exercise the page
- Fix, then prove the test
- Complexity pass
- End-to-end latency

## Separate noise from bugs

Before you call a request failure a bug, rule out noise. `crawl.mjs` labels the usual noise in
the `noise` field:

| Label | What it is | Action |
|---|---|---|
| `aborted-media-preload` | The browser cancelled a video or audio preload | None |
| `aborted-prefetch` | Router prefetch cancelled by navigation | None |
| `external-cdn-or-network` | A third-party host reset or timed out | None, unless the page breaks without it |
| `dev-server-hmr` | Hot-reload socket or chunk errors | Re-run the capture; a bug only if it persists |
| `third-party-telemetry` | Blocked analytics | None |
| `missing-sourcemap`, `missing-icon` | Dev-only 404s | Minor; fix icons, ignore sourcemaps |

Re-run once before acting on any page error that could be a dev-server crash. An error that
disappears on the re-run was not caused by the page.

## 401 and 403: intended or broken?

A 403 may be a feature: premium-only data, minors' data, another club's records. Before
changing anything:

1. Find the backend rule that produced it: the permission class, policy or guard.
2. If the refusal is intended, the bug is in the UI. The page must not request what the role
   can't have, or it must explain the refusal ("Upgrade to see match stats"), not show an empty
   card or a spinner.
3. If the refusal is wrong, fix the rule. Then re-check the security layer for every other
   role.

## Exercise the page

Use the role that owns the page, then every other role that can reach it. Run everything on
the LOCAL stack:

- Every link, button, tab, filter, sort, search, pagination control, modal and form. That
  includes validation errors, double submit, and back/forward navigation.
- Full CRUD for whatever the page manages: create, then read it back; update, then read it
  back; delete, then confirm it is gone. Check the database or API, not just the UI.
- Empty, loading and error states. Point at an empty account, slow the network with
  Playwright's `page.route` delays, and force a 500.
- Refresh mid-flow. Deep-link straight into the page. Open it with a stale session.

The project's own e2e suite is the first choice. Add to it rather than writing throwaway
scripts when it exists. Otherwise write short Playwright scripts under `.website-audit/flows/`.

## Fix, then prove the test

Fix the root cause, not the symptom. Every fix that can be tested gets a regression test that
fails on the old code. Use the stash procedure in SKILL.md. Performance fixes, such as an N+1
query or a request waterfall, go in their own commit and get their own line in the report.

## Complexity pass

Simplify only after the page behaves correctly, and keep behaviour identical:

- Delete dead code, unused props and state, and commented-out blocks the page owns.
- Merge duplicated fetch, format and validation logic into the helper the project already has.
- Flatten deeply nested conditionals, and replace effects that only derive state.
- Leave shared components alone unless you check every caller. Shared refactors get their own
  commit.

Re-run the page's tests after the pass.

## End-to-end latency

Measure from the browser through to the database:

1. **From the crawl report.** `timing` gives TTFB, DOMContentLoaded, load and LCP;
   `api.slowest` lists the slowest fetch and XHR calls.
2. **Per endpoint, against the backend directly.** Measure with the role's session. Pass the
   token from an environment variable and never print it:
   `curl -s -o /dev/null -w '%{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s\n' -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/...`
   Run each a few times and use the median.
3. **Causes to look for:**
   - N+1 queries: turn on ORM query logging and count queries per request.
   - Request waterfalls: sequential awaits that could run in parallel.
   - Over-fetching: whole objects when the page needs three fields.
   - Missing pagination or indexes.
   - Large client bundles on the page.
   - Duplicate requests from effects that run twice.
4. **Rough local targets:** API p50 under 300 ms, page TTFB under 600 ms with a production
   build, no request chain more than three deep. Dev mode is slower, so compare before and
   after on the same build rather than against the targets.

Record before and after numbers in the dossier.
