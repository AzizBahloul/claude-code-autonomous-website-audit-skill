#!/usr/bin/env python3
"""Per-page progress tracker: the run's single source of truth for what is done.

Every page goes through the same layers in the same fixed order:
    baseline -> code -> functionality -> design -> a11y -> security -> i18n -> verify

State lives in .website-audit/progress.json; .website-audit/PROGRESS.md is regenerated on
every change so a human (or a resumed session) can read it.

`done` is gated on evidence, not on say-so. Every `done` needs a --note, and these layers
also need files on disk (SLUG is the page URL slugged the way crawl.mjs names folders):
    baseline  shots/baseline/SLUG/*.json and tiles/baseline/SLUG/*   (captured and tiled)
    code      pages/SLUG.md                                          (the dossier)
    a11y      shots/a11y/SLUG/*.json with a keyboard probe           (crawl.mjs --keyboard)
    verify    shots/verify/SLUG/*.json with no count above baseline  (summarize.py compare)
--force skips a gate; the note is then recorded as FORCED and the report must explain it.

Findings are the ledger of what was wrong. Log each one the moment it is confirmed, so an
interrupted session loses nothing, and resolve it before its layer can be marked done:
    progress.py finding ROUTE LAYER SEVERITY "title" [--repro "1. ... 2. ..."] [--evidence PATH]
    progress.py resolve ID fixed|deferred|reverted|not-a-bug --how TEXT [--commit SHA] [--test NAME]
    progress.py findings [--open] [--route ROUTE]
SEVERITY is critical | high | medium | low. Two reverts on one page trip the circuit breaker.

    progress.py init   [--inventory .website-audit/inventory.json]   create or merge pages
    progress.py add    ROUTE [--file F] [--url U]      a page found after the inventory
    progress.py set-url ROUTE URL                      concrete URL for a dynamic route
    progress.py next                                   the page and layer to work on now
    progress.py start  ROUTE
    progress.py done   ROUTE LAYER --note TEXT         in order, and only with evidence
    progress.py slug   ROUTE                           folder name used for this page's files
    progress.py reopen ROUTE LAYER                     send a page back to LAYER (regression)
    progress.py block  ROUTE --reason TEXT             needs the user; reported, never silent
    progress.py note   ROUTE TEXT                      record a finding or fix
    progress.py commit ROUTE SHA                       record a commit made for the page
    progress.py status                                 counts plus the table
    progress.py check  [--allow-blocked]               stop condition: exit 0 only when finished
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low"]
RESOLUTIONS = ["fixed", "deferred", "reverted", "not-a-bug"]
LAYERS = ["baseline", "code", "functionality", "design", "a11y", "security", "i18n", "verify"]
STATE_DIR = Path(os.environ.get("WEBSITE_AUDIT_DIR", ".website-audit"))
MIN_NOTE = 12


def layers_of(state):
    # A run keeps the layer list it was created with, so upgrading the skill mid-run
    # never reopens finished pages.
    return state.get("layers") or LAYERS


def slug(p):
    """Same rule as crawl.mjs slug(): the folder name for a page's captures and dossier."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", re.sub(r"^/+|/+$", "", p or ""))
    return (s or "root")[:120]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg, code=2):
    print(f"progress.py: {msg}", file=sys.stderr)
    sys.exit(code)


def load(path):
    if not path.exists():
        die(f"no state at {path}; run `progress.py init` first")
    return json.loads(path.read_text())


def save(state, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)
    render(state, path.with_name("PROGRESS.md"))


def new_page(route, file=None, url=None, dynamic=False, source="inventory", layers=None):
    return {
        "route": route,
        "file": file,
        "url": url if url else (None if dynamic else route),
        "dynamic": dynamic,
        "source": source,
        "status": "pending",
        "layers": {layer: "pending" for layer in (layers or LAYERS)},
        "notes": [],
        "commits": [],
        "findings": [],
        "blocked_reason": None,
        "started": None,
        "finished": None,
    }


def find(state, route):
    for p in state["pages"]:
        if p["route"] == route:
            return p
    die(f"route not in progress file: {route!r} (add it with `progress.py add`)")


def next_layer(page):
    for layer in page["layers"]:
        if page["layers"][layer] != "done":
            return layer
    return None


def gate(state_dir, page, layer):
    """Return a list of reasons the layer cannot be marked done yet (empty = ok)."""
    sl = slug(page["url"])
    problems = []
    jsons = lambda d: sorted((state_dir / d / sl).glob("*.json")) if (state_dir / d / sl).is_dir() else []
    open_here = [f["id"] for f in page.get("findings", []) if f["status"] == "open" and (f["layer"] == layer or layer == "verify")]
    if open_here:
        problems.append(f"open finding(s) {', '.join(open_here)}: resolve each as fixed, deferred, reverted or not-a-bug")
    if layer == "baseline":
        files = jsons("shots/baseline")
        if not files:
            problems.append(f"no capture reports in {state_dir}/shots/baseline/{sl}/ (run crawl.mjs capture --out {state_dir}/shots/baseline)")
        for f in files:
            r = json.loads(f.read_text())
            if r.get("navigationError"):
                problems.append(f"{f.name}: navigation failed ({r['navigationError'][:80]}); a failed capture is not a baseline")
            elif r.get("signedIn") and r.get("summary", {}).get("redirectedToLogin"):
                problems.append(f"{f.name}: signed-in capture landed on the login page; log in again and re-capture")
        tiles = state_dir / "tiles/baseline" / sl
        if not tiles.is_dir() or not any(tiles.iterdir()):
            problems.append(f"no tiles in {tiles}/ (run tile.py, then Read every tile)")
    elif layer == "code":
        dossier = state_dir / "pages" / f"{sl}.md"
        if not dossier.exists() or dossier.stat().st_size < 200:
            problems.append(f"dossier {dossier} is missing or nearly empty")
    elif layer == "a11y":
        files = jsons("shots/a11y")
        if not any("keyboard" in json.loads(f.read_text()) for f in files):
            problems.append(f"no keyboard probe in {state_dir}/shots/a11y/{sl}/ (run crawl.mjs capture --keyboard --viewports desktop --out {state_dir}/shots/a11y)")
        elif not any(json.loads(f.read_text()).get("a11y") is not None for f in files):
            problems.append("the a11y capture has no axe results (axe-core missing? run setup.sh)")
    elif layer == "verify":
        base, ver = state_dir / "shots/baseline" / sl, state_dir / "shots/verify" / sl
        if not jsons("shots/verify"):
            problems.append(f"no re-capture in {ver}/ (run the baseline matrix again with --out {state_dir}/shots/verify)")
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from summarize import compare_dirs
            problems += compare_dirs(base, ver)["problems"]
    return problems


def render(state, md_path):
    pages = state["pages"]
    counts = {s: sum(1 for p in pages if p["status"] == s) for s in ("done", "in_progress", "pending", "blocked")}
    lines = [
        "# Website audit progress",
        "",
        f"Updated {state['updated']}. Pages: {len(pages)} total, {counts['done']} done, "
        f"{counts['in_progress']} in progress, {counts['pending']} pending, {counts['blocked']} blocked.",
        "",
        "Generated by `progress.py`. Edit through the script, not by hand.",
        "",
        "| # | Route | URL | Status | " + " | ".join(layers_of(state)) + " | Findings (open) | Commits |",
        "|---|---|---|---|" + "---|" * len(layers_of(state)) + "---|---|",
    ]
    mark = {"done": "done", "pending": "-", "in_progress": "..."}
    for i, p in enumerate(pages, 1):
        url = p["url"] or "**needs URL**"
        cells = [mark.get(p["layers"][layer], p["layers"][layer]) for layer in layers_of(state)]
        fs_ = p.get("findings", [])
        opened = sum(f["status"] == "open" for f in fs_)
        lines.append(f"| {i} | `{p['route']}` | {url} | {p['status']} | " + " | ".join(cells) + f" | {len(fs_)} ({opened}) | {len(p['commits'])} |")
    blocked = [p for p in pages if p["status"] == "blocked"]
    if blocked:
        lines += ["", "## Blocked (needs the user)", ""]
        lines += [f"- `{p['route']}`: {p['blocked_reason']}" for p in blocked]
    allf = [(p, f) for p in pages for f in p.get("findings", [])]
    if allf:
        lines += ["", "## Findings", "", "| ID | Route | Layer | Severity | Status | Title | Resolution |", "|---|---|---|---|---|---|---|"]
        order = {s: i for i, s in enumerate(SEVERITIES)}
        for p, f in sorted(allf, key=lambda pf: (pf[1]["status"] != "open", order.get(pf[1]["severity"], 9), pf[1]["id"])):
            res = (f.get("how") or "") + (f" ({f['commit']})" if f.get("commit") else "")
            lines.append(f"| {f['id']} | `{p['route']}` | {f['layer']} | {f['severity']} | {f['status']} | {f['title']} | {res} |")
    noted = [p for p in pages if p["notes"]]
    if noted:
        lines += ["", "## Notes", ""]
        for p in noted:
            lines.append(f"### `{p['route']}`")
            lines += [f"- [{n['layer'] or 'general'}]{' **FORCED**' if n.get('forced') else ''} {n['text']}" for n in p["notes"]]
            lines.append("")
    md_path.write_text("\n".join(lines) + "\n")


def cmd_init(a, path):
    inv_path = Path(a.inventory)
    if not inv_path.exists():
        die(f"inventory not found: {inv_path} (run inventory.py first)")
    inv = json.loads(inv_path.read_text())
    if path.exists():
        state = json.loads(path.read_text())
    else:
        state = {"created": now(), "layers": LAYERS, "pages": []}
    known = {(p.get("inventory_route", p["route"]), p["file"]) for p in state["pages"]}
    ids = {p["route"] for p in state["pages"]}
    items = inv.get("pages", [])
    shared = {r for r in (i["route"] for i in items) if sum(j["route"] == r for j in items) > 1}
    added = 0
    for item in items:
        route, file = item["route"], item.get("file")
        if (route, file) in known:
            continue
        # The same route from several files (monorepo apps) must stay separate pages, never merge.
        page_id = f"{route} [{file}]" if route in shared or route in ids else route
        page = new_page(page_id, file, dynamic=item.get("dynamic", False), layers=layers_of(state))
        page["inventory_route"] = route
        if page_id != route and not page["dynamic"]:
            page["url"] = route
        state["pages"].append(page)
        known.add((route, file))
        ids.add(page_id)
        added += 1
    save(state, path)
    need_url = [p["route"] for p in state["pages"] if not p["url"]]
    print(f"{added} page(s) added, {len(state['pages'])} tracked -> {path} and {path.with_name('PROGRESS.md')}")
    if need_url:
        print(f"{len(need_url)} dynamic route(s) need a concrete URL (progress.py set-url ROUTE URL):")
        for r in need_url:
            print(f"  {r}")


def cmd_add(a, path):
    state = load(path)
    if any(p["route"] == a.route for p in state["pages"]):
        die(f"already tracked: {a.route}", 0)
    dynamic = bool(re.search(r"\[|/:|<|\{", a.route)) and not a.url
    state["pages"].append(new_page(a.route, a.file, a.url, dynamic=dynamic, source="added", layers=layers_of(state)))
    save(state, path)
    print(f"added {a.route}")


def cmd_set_url(a, path):
    state = load(path)
    find(state, a.route)["url"] = a.url
    save(state, path)
    print(f"{a.route} -> {a.url}")


def cmd_next(a, path):
    state = load(path)
    for status in ("in_progress", "pending"):
        for p in state["pages"]:
            if p["status"] == status:
                url = p["url"] or "NEEDS-URL"
                print(f"{p['route']}\t{next_layer(p)}\t{url}")
                return
    blocked = [p["route"] for p in state["pages"] if p["status"] == "blocked"]
    print("ALL DONE" + (f" except {len(blocked)} blocked page(s): " + ", ".join(blocked) if blocked else ""))


def cmd_start(a, path):
    state = load(path)
    p = find(state, a.route)
    if not p["url"]:
        die(f"{a.route} is dynamic and has no URL yet; run `progress.py set-url` first")
    others = [q["route"] for q in state["pages"] if q["status"] == "in_progress" and q["route"] != a.route]
    if others and not a.force:
        die(f"finish the page in progress first: {', '.join(others)} (one page at a time; --force to override)")
    p["status"] = "in_progress"
    p["started"] = p["started"] or now()
    p["blocked_reason"] = None
    save(state, path)
    print(f"started {a.route}; next layer: {next_layer(p)}")


def cmd_done(a, path):
    state = load(path)
    if a.layer not in layers_of(state):
        die(f"unknown layer {a.layer!r}; layers are: {', '.join(layers_of(state))}")
    p = find(state, a.route)
    if p["status"] != "in_progress":
        die(f"{a.route} is {p['status']}; run `progress.py start {a.route}` first")
    if not a.note or len(a.note.strip()) < MIN_NOTE:
        die("--note is required: what you checked, what you found and what you fixed "
            "(\"nothing found\" is a valid finding, but name what you checked)")
    expected = next_layer(p)
    if a.layer != expected:
        die(f"layers run in a fixed order; next for {a.route} is {expected!r}, not {a.layer!r}")
    problems = gate(path.parent, p, a.layer)
    if problems and not a.force:
        die(f"{a.route} {a.layer} is not done yet:\n  - " + "\n  - ".join(problems)
            + "\n(--force only for a reason the report will explain)", 1)
    p["layers"][a.layer] = "done"
    p["notes"].append({"layer": a.layer, "text": a.note, "at": now(), **({"forced": problems} if problems else {})})
    following = next_layer(p)
    if following is None:
        p["status"] = "done"
        p["finished"] = now()
    save(state, path)
    print(f"{a.route}: {a.layer} done; " + (f"next layer: {following}" if following else "page complete"))


def cmd_reopen(a, path):
    state = load(path)
    order = layers_of(state)
    if a.layer not in order:
        die(f"unknown layer {a.layer!r}")
    p = find(state, a.route)
    for layer in order[order.index(a.layer):]:
        p["layers"][layer] = "pending"
    p["status"] = "in_progress"
    p["finished"] = None
    save(state, path)
    print(f"{a.route} reopened at {a.layer}")


def cmd_block(a, path):
    state = load(path)
    p = find(state, a.route)
    p["status"] = "blocked"
    p["blocked_reason"] = a.reason
    save(state, path)
    print(f"{a.route} blocked: {a.reason}")


def cmd_note(a, path):
    state = load(path)
    p = find(state, a.route)
    p["notes"].append({"layer": next_layer(p) if p["status"] == "in_progress" else None, "text": a.text, "at": now()})
    save(state, path)
    print("noted")


def cmd_commit(a, path):
    state = load(path)
    find(state, a.route)["commits"].append(a.sha)
    save(state, path)
    print(f"recorded {a.sha} for {a.route}")


def all_findings(state):
    return [(p, f) for p in state["pages"] for f in p.setdefault("findings", [])]


def cmd_finding(a, path):
    state = load(path)
    p = find(state, a.route)
    if a.layer not in layers_of(state):
        die(f"unknown layer {a.layer!r}; layers are: {', '.join(layers_of(state))}")
    if a.severity not in SEVERITIES:
        die(f"severity must be one of {', '.join(SEVERITIES)}")
    n = 1 + max([int(f["id"][2:]) for _, f in all_findings(state)] or [0])
    fid = f"F-{n:03d}"
    p["findings"].append({"id": fid, "layer": a.layer, "severity": a.severity, "title": a.title,
                          "repro": a.repro, "evidence": a.evidence, "status": "open", "at": now()})
    save(state, path)
    print(f"{fid} logged on {a.route} [{a.layer}, {a.severity}]")


def cmd_resolve(a, path):
    state = load(path)
    hit = [(p, f) for p, f in all_findings(state) if f["id"] == a.id]
    if not hit:
        die(f"no finding {a.id}")
    p, f = hit[0]
    if a.status == "fixed" and not (a.test or a.how):
        die("a fixed finding needs --how, and --test when the fix can be tested")
    if a.status in ("deferred", "not-a-bug") and not a.how:
        die(f"{a.status} needs --how: the reason, which the report quotes")
    f.update({"status": a.status, "how": a.how, "commit": a.commit, "test": a.test, "resolved": now()})
    save(state, path)
    print(f"{a.id} -> {a.status}")
    reverts = sum(x["status"] == "reverted" for x in p["findings"])
    if reverts >= 2:
        print(f"CIRCUIT BREAKER: {reverts} reverted fixes on {p['route']}. Stop fixing this page: "
              f"`progress.py block '{p['route']}' --reason ...`, move on, and raise it with the user.")


def cmd_findings(a, path):
    state = load(path)
    rows = [(p, f) for p, f in all_findings(state) if (not a.open or f["status"] == "open") and (not a.route or p["route"] == a.route)]
    for p, f in rows:
        print(f"{f['id']}\t{f['status']}\t{f['severity']}\t{f['layer']}\t{p['route']}\t{f['title']}")
    by = {s: sum(f["severity"] == s for _, f in rows) for s in SEVERITIES}
    print(f"{len(rows)} finding(s): " + ", ".join(f"{v} {k}" for k, v in by.items()))


def cmd_slug(a, path):
    p = find(load(path), a.route)
    print(slug(p["url"] or p["route"]))


def cmd_status(a, path):
    state = load(path)
    render(state, path.with_name("PROGRESS.md"))
    print(path.with_name("PROGRESS.md").read_text())


def cmd_check(a, path):
    state = load(path)
    unfinished = [p for p in state["pages"] if p["status"] in ("pending", "in_progress")]
    still_open = [(p, f) for p, f in all_findings(state) if f["status"] == "open" and p["status"] != "blocked"]
    for p, f in still_open:
        print(f"OPEN      {f['id']} {p['route']} [{f['severity']}] {f['title']}")
    blocked = [p for p in state["pages"] if p["status"] == "blocked"]
    for p in unfinished:
        print(f"NOT DONE  {p['route']}  (next layer: {next_layer(p)})")
    for p in blocked:
        print(f"BLOCKED   {p['route']}  ({p['blocked_reason']})")
    if unfinished or still_open or (blocked and not a.allow_blocked):
        print(f"\nNot finished: {len(unfinished)} unfinished, {len(blocked)} blocked of {len(state['pages'])} pages, {len(still_open)} open finding(s).")
        sys.exit(1)
    forced = [(p["route"], n["layer"]) for p in state["pages"] for n in p["notes"] if n.get("forced")]
    print(f"All {len(state['pages'])} pages finished" + (f" ({len(blocked)} blocked, acknowledged by the user)" if blocked else "") + ".")
    if forced:
        print(f"{len(forced)} gate(s) were forced; explain each in the report: " + ", ".join(f"{r} {l}" for r, l in forced))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=str(STATE_DIR / "progress.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--inventory", default=str(STATE_DIR / "inventory.json"))
    s = sub.add_parser("add"); s.add_argument("route"); s.add_argument("--file"); s.add_argument("--url")
    s = sub.add_parser("set-url"); s.add_argument("route"); s.add_argument("url")
    sub.add_parser("next")
    s = sub.add_parser("start"); s.add_argument("route"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("done"); s.add_argument("route"); s.add_argument("layer"); s.add_argument("--note"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("reopen"); s.add_argument("route"); s.add_argument("layer")
    s = sub.add_parser("block"); s.add_argument("route"); s.add_argument("--reason", required=True)
    s = sub.add_parser("note"); s.add_argument("route"); s.add_argument("text")
    s = sub.add_parser("commit"); s.add_argument("route"); s.add_argument("sha")
    s = sub.add_parser("slug"); s.add_argument("route")
    s = sub.add_parser("finding"); s.add_argument("route"); s.add_argument("layer"); s.add_argument("severity"); s.add_argument("title")
    s.add_argument("--repro"); s.add_argument("--evidence")
    s = sub.add_parser("resolve"); s.add_argument("id"); s.add_argument("status", choices=RESOLUTIONS)
    s.add_argument("--how"); s.add_argument("--commit"); s.add_argument("--test")
    s = sub.add_parser("findings"); s.add_argument("--open", action="store_true"); s.add_argument("--route")
    sub.add_parser("status")
    s = sub.add_parser("check"); s.add_argument("--allow-blocked", action="store_true")
    a = ap.parse_args()
    handler = globals()["cmd_" + a.cmd.replace("-", "_")]
    handler(a, Path(a.state))


if __name__ == "__main__":
    main()
