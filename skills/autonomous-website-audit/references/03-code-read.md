# Layer: code

Read the page before changing it. Every later layer depends on this one.

## What to read

1. **The route file**: the page entry from the inventory.
2. **The layout chain**: every layout, template and middleware that wraps the page (listed
   under `layouts` in the inventory), plus route guards and redirects. Many auth and gating
   rules live here rather than in the page.
3. **The components**: everything under `components` in the inventory. Note which are shared
   design-system components and which are page-local.
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
- Endpoints: GET /api/players/:id (PlayerDetailView, IsCoachOfClub),
  GET /api/players/:id/stats (premium only, intended 403 for free accounts)
- Shared components: Card, StatTile, EmptyState (components/ui)
- Data shown per role: coach sees the full profile; scout sees no contact fields
- Sample IDs: 42 (coach's own club), 77 (another club: must be refused)
- Suspected issues from reading: ...
```

The "intended 403" lines matter. They stop the functionality layer from "fixing" deliberate
gating.

## Load the design skill

On the first page only, load the design skill chosen in setup, so its guidance is in context
for every `design` layer after this one.
