# Phase 4: Report and cleanup

Run this only after `progress.py check` exits 0, or exits 0 with `--allow-blocked` once the
user has acknowledged the blocked pages.

## Write `.website-audit/REPORT.md`

1. **Summary.** Pages audited, pages blocked, issues fixed by layer, and commits made.
2. **Per page.** A table: route, issues found and fixed per layer, commit SHAs, and anything
   left open with the reason.
3. **Security findings.** Their own section: severity, the role that could exploit each one,
   the fix, and the test that proves it.
4. **Performance.** Before and after numbers per endpoint and page, and any N+1 queries or
   waterfalls removed.
5. **Changes to call out plainly.** Do not bury these in a file list:
   - changed API response shapes;
   - database schema changes and migrations;
   - changed enums;
   - new dependencies.
6. **Flagged but not fixed.** Problems outside the audited pages, or decisions that belong
   to the user.
7. **Skills installed during the run.** Name, source URL and commit, from
   `~/.claude/skills/<name>/.installed-from`.
8. **Test accounts created.** Each account, where it was created, and how to remove it.
9. **Checks.** Quote the last lint, typecheck and test output; don't paraphrase it.

## Cleanup

- Stop only the processes you started, using the PIDs in `.website-audit/pids`
  (`kill <pid>`). Never `pkill` by name.
- Leave `.website-audit/` in place for the user. It holds the evidence and the resumable state.
- Do not delete files you did not create. If a test left a file the user did not create
  somewhere outside `.website-audit/`, list it in the report and let the user decide.
- Do not push unless the user asked. Say whether the tree is committed or dirty.

## Tell the user

Give a short message: the totals, the blocked pages that need them, the security fixes, the
changes that alter contracts (API shape, schema, enum), and the path to `REPORT.md`.
