# Layer: code

> **Answers:** what renders this page, what guards it, and which endpoints feed it?
> **Gate:** the dossier `.website-audit/pages/SLUG.md` exists (`progress.py slug ROUTE` names SLUG).

Read the page before changing it. Every later layer depends on this one. Don't fix anything
in this layer. Log suspected problems as findings for the layer that will fix them.

## What to read

Start from the project maps. Don't re-derive what they already list:

```bash
sed -n "/^### \`ROUTE\`/,/^### /p" .website-audit/ARCHITECTURE.md     # layouts + components
grep -n "ROUTE" .website-audit/ENDPOINTS.md                            # endpoints this page calls
```

1. **The route file**: the page entry from the inventory.
2. **The layout chain**: every layout, template and middleware that wraps the page (listed
   under `layouts` in the inventory), plus route guards and redirects. Many auth and gating
   rules live here rather than in the page.
3. **The components**: every component the architecture map lists for this page. Note which
   are `shared` (fix those once, and re-check the other pages that use them) and which are
   page-local. Note duplicates the map flagged that this page uses.
4. **The data layer**: hooks, loaders, server actions, stores, the API client and the
   formatters the page uses.
5. **Every backend endpoint the page calls.** Trace each `fetch`, `axios` call or client
   method to its handler in the backend: the route definition, the view or controller, the
   serializer, the permission class and the query. Cross-check against the `api.calls` list
   in the baseline crawl, which shows what actually ran.
6. **The i18n namespace** the page uses, and where its strings come from.

## Write the dossier

Write `.website-audit/pages/<slug>.md` (the slug matches the shots directory):

```markdown
# /players/[id]
- Entry: app/(coach)/players/[id]/page.tsx (client component)
- Guards: middleware.ts requires a session; layout redirects non-coach roles to /home
- Endpoints: GET /api/players/:id (PlayerDetailView, IsCoachOfClub; bench p50 84ms),
  GET /api/players/:id/stats (premium only, intended 403 for free accounts; p50 410ms: slow)
- Shared components: Card, StatTile, EmptyState (components/ui)
- Data shown per role: coach sees the full profile; scout sees no contact fields
- Sample IDs: 42 (coach's own club), 77 (another club: must be refused)
- Suspected issues from reading: ...
```

The "intended 403" lines matter. They stop the functionality layer from "fixing" deliberate
gating.

## Load the design roster

On the first page only, read `.website-audit/skills.md` (the roster from setup) and load the
`doctrine` and `craft` skills, so their guidance is in context for every `design` layer after
this one. The other roster skills load at their own step in the design layer.

## Mistakes to avoid

- Reading the page file but not its layouts and middleware, where most guards live.
- Taking the UI's word for which endpoints run. The baseline's `api.calls` is what actually ran.
- Skipping the backend because "this is a frontend audit". Authorization, over-fetching and
  N+1 queries are all on the server.
