#!/usr/bin/env python3
"""Map the frontend's architecture: every page, the layouts and components it pulls in, which
pages share each component, which source files nothing reaches, and which components look like
duplicates of each other.

    python3 architecture.py [ROOT] [--inventory .website-audit/inventory.json]
                            [--out .website-audit/architecture.json] [--md .website-audit/ARCHITECTURE.md]

Needs inventory.py's output. Builds the full import graph of the JS/TS/Vue/Svelte/Astro sources
(tsconfig path aliases included), then:

  pages        page -> layouts -> every local file it reaches (transitively)
  components   component file -> the pages that reach it (shared / single-page / unused)
  unreferenced source files no entry point reaches: dead-file CANDIDATES. Confirm each with a
               grep for its name (dynamic imports, string-based registries and framework magic
               files are invisible to a static graph) before deleting anything.
  duplicates   components with the same core name in different folders (Button vs ui/Button vs
               CustomButton) and files whose normalised content is identical

Read the summary it prints, then ARCHITECTURE.md. Open the JSON only with jq.

    python3 architecture.py --affected REV [--architecture .website-audit/architecture.json]

Diff-aware mode: lists every page that reaches a file changed since git revision REV (working
tree included). Use it after fixing a shared component to know which pages to re-check, and on
resume to see what changed under pages already finished.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import IMPORT_RE, load_aliases, package_roots, read, rel, resolve, walk  # noqa: E402

SRC_EXT = {".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".vue", ".svelte", ".astro"}
COMPONENT_DIRS = re.compile(r"(^|/)(components?|ui|design-system|shared|widgets|elements|primitives|common|layouts?)(/|$)", re.I)
# Files a framework or tool loads by convention; never call them unreferenced.
ENTRY_NAME = re.compile(
    r"(^|/)(page|layout|template|loading|error|not-found|global-error|default|route|middleware|instrumentation"
    r"|_app|_document|_error|app|main|index|root|entry[-.]client|entry[-.]server|server|client|router|routes"
    r"|\+page|\+layout|\+error|\+server|\+page\.server|\+layout\.server|hooks\.server|hooks\.client"
    r"|sw|service-worker|setupTests|vitest\.setup|jest\.setup)\.[a-z]+$", re.I)
NON_SOURCE = re.compile(r"(\.test\.|\.spec\.|\.stories\.|\.story\.|\.d\.ts$|(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright|stories|\.storybook|scripts|mocks?|fixtures?)/|\.config\.[a-z]+$|(^|/)(vite|next|nuxt|svelte|astro|tailwind|postcss|babel|jest|vitest|eslint|prettier|webpack|rollup|playwright)\.config)", re.I)
AFFIX = re.compile(r"^(custom|new|old|my|base|app|simple|basic|generic|default|legacy|shared|common|core|ui)|(v\d+|\d+|old|new|legacy|copy|backup|tmp|component|comp)$", re.I)


GENERIC_ENTRY = {"index", "app", "main", "root", "server", "client", "router", "routes"}


def is_entry(path):
    """Convention-loaded file? Generic names (index, app, main...) count only at a package or
    src/ root; a components/ui/index.ts barrel must not make every re-exported file look used."""
    m = ENTRY_NAME.search(path)
    if not m:
        return False
    if Path(path).stem.lower() not in GENERIC_ENTRY:
        return True
    parent = Path(path).parent
    return parent.name in ("", ".", "src", "app", "client", "server", "frontend", "web") or len(parent.parts) <= 1


def is_component(path):
    name = Path(path).name
    if Path(path).suffix in (".vue", ".svelte", ".astro"):
        return True
    return bool(COMPONENT_DIRS.search(path)) or (Path(path).suffix in (".tsx", ".jsx") and name[:1].isupper())


def core_name(path):
    stem = Path(path).stem
    if stem.lower() == "index":
        stem = Path(path).parent.name
    s = re.sub(r"[-_.]", "", stem)
    prev = None
    while prev != s and len(s) > 3:
        prev, s = s, AFFIX.sub("", s)
    return s.lower()


def normalised_hash(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|\s)//.*$", "", text, flags=re.M)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(("import ", "export {", "'use ", '"use '))]
    if len(lines) < 8:
        return None
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


def build_graph(root):
    """Return {file: set(imported local files)} for every source file under root."""
    pkgs = sorted((p for p, _ in package_roots(root)), key=lambda p: -len(p.parts)) or [root]
    alias_cache = {}

    def aliases_for(f):
        for p in pkgs:
            if f.is_relative_to(p):
                if p not in alias_cache:
                    alias_cache[p] = load_aliases(p)
                return alias_cache[p]
        return load_aliases(root)

    graph = {}
    for f in walk(root):
        if f.suffix not in SRC_EXT:
            continue
        f = f.resolve()
        al = aliases_for(f)
        deps = set()
        for spec in IMPORT_RE.findall(read(f)):
            t = resolve(spec, f, al)
            if t and t.is_relative_to(root) and "node_modules" not in t.parts:
                deps.add(t)
        graph[f] = deps
    return graph


def reach(graph, starts):
    seen, stack = set(), [s for s in starts if s in graph]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        stack.extend(graph.get(f, ()))
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--inventory", default=".website-audit/inventory.json")
    ap.add_argument("--out", default=".website-audit/architecture.json")
    ap.add_argument("--md", default=".website-audit/ARCHITECTURE.md")
    ap.add_argument("--affected", metavar="REV", help="list pages reaching files changed since REV")
    ap.add_argument("--architecture", default=".website-audit/architecture.json")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.affected:
        return affected(root, a.affected, Path(a.architecture))
    inv_path = Path(a.inventory)
    if not inv_path.exists():
        sys.exit(f"architecture.py: no inventory at {inv_path}; run inventory.py first")
    inv = json.loads(inv_path.read_text())

    graph = build_graph(root)
    R = lambda f: rel(f, root)
    absf = lambda p: (root / p).resolve()

    pages = []
    entry_files = set()
    for p in inv.get("pages", []):
        if not p.get("file"):
            continue
        files = [absf(p["file"])] + [absf(x) for x in p.get("layouts", [])]
        entry_files.update(files)
        reached = reach(graph, files) - set(files)
        pages.append({
            "route": p["route"], "file": p["file"], "layouts": p.get("layouts", []),
            "reaches": sorted(R(f) for f in reached),
            "components": sorted(R(f) for f in reached if is_component(R(f))),
        })
    for s in inv.get("api", []):
        if s.get("file"):
            entry_files.add(absf(s["file"]))
    # Convention-loaded files and non-source files (tests, stories, configs) are roots too.
    for f in graph:
        r = R(f)
        if is_entry(r) or NON_SOURCE.search(r):
            entry_files.add(f)

    used_by = defaultdict(list)
    for pg in pages:
        for c in pg["components"]:
            used_by[c].append(pg["route"])
    all_components = sorted(R(f) for f in graph if is_component(R(f)) and not NON_SOURCE.search(R(f)))
    reachable = reach(graph, entry_files)
    unreferenced = sorted(R(f) for f in graph if f not in reachable and not NON_SOURCE.search(R(f)))
    importers = defaultdict(set)
    for f, deps in graph.items():
        for d in deps:
            importers[d].add(R(f))

    components = []
    for c in all_components:
        n = len(used_by.get(c, []))
        components.append({
            "file": c, "pages": sorted(used_by.get(c, [])), "page_count": n,
            "importers": len(importers.get(absf(c), ())),
            "kind": "shared" if n >= 2 else "single-page" if n == 1 else "not-on-any-page",
        })

    by_core = defaultdict(list)
    for c in all_components:
        cn = core_name(c)
        if len(cn) >= 3:
            by_core[cn].append(c)
    name_dups = [{"core": k, "files": v} for k, v in sorted(by_core.items()) if len(v) > 1 and len({Path(x).parent for x in v}) > 1]
    by_hash = defaultdict(list)
    for f in graph:
        r = R(f)
        if NON_SOURCE.search(r):
            continue
        h = normalised_hash(read(f))
        if h:
            by_hash[h].append(r)
    content_dups = [sorted(v) for v in by_hash.values() if len(v) > 1]

    result = {
        "root": str(root), "source_files": len(graph), "pages": pages, "components": components,
        "unreferenced": unreferenced, "duplicate_names": name_dups, "identical_files": content_dups,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    Path(a.md).write_text(render_md(result))

    shared = sum(c["kind"] == "shared" for c in components)
    orphan = sum(c["kind"] == "not-on-any-page" for c in components)
    print(f"{len(graph)} source files, {len(pages)} page(s), {len(components)} component file(s): "
          f"{shared} shared, {len(components) - shared - orphan} single-page, {orphan} on no page")
    print(f"{len(unreferenced)} unreferenced file(s) (dead-file candidates), {len(name_dups)} duplicate-name group(s), "
          f"{len(content_dups)} identical-file group(s)")
    print(f"-> {out} and {a.md}")


def affected(root, rev, arch_path):
    import subprocess
    if not arch_path.exists():
        sys.exit(f"architecture.py: no map at {arch_path}; run architecture.py first")
    arch = json.loads(arch_path.read_text())
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True)
    if top.returncode != 0:
        sys.exit("architecture.py: not a git repository")
    git_root = Path(top.stdout.strip())
    out = subprocess.run(["git", "diff", "--name-only", rev], cwd=root, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"architecture.py: git diff {rev} failed: {out.stderr.strip()}")
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True).stdout
    map_root = Path(arch["root"])
    changed = set()
    for line in (out.stdout + untracked).splitlines():
        f = (git_root / line.strip()).resolve()
        if line.strip() and f.is_relative_to(map_root):
            changed.add(str(f.relative_to(map_root)))
    hits = []
    for pg in arch["pages"]:
        why = sorted(changed & ({pg["file"]} | set(pg["layouts"]) | set(pg["reaches"])))
        if why:
            hits.append((pg["route"], why))
    print(f"{len(changed)} changed file(s) since {rev}; {len(hits)} page(s) affected")
    for route, why in hits:
        print(f"  {route:<40} via {', '.join(why[:4])}{' ...' if len(why) > 4 else ''}")
    unmapped = sorted(c for c in changed if not any(c in w for _, w in hits))
    if unmapped:
        print(f"{len(unmapped)} changed file(s) reach no page (backend, config, tests, or new files: re-run architecture.py):")
        for c in unmapped[:20]:
            print(f"  {c}")


def route_tree(pages):
    """Pages nested under their URL segments; segments with no page of their own appear as plain nodes."""
    lines, printed = [], set()
    for pg in sorted(pages, key=lambda p: (p["route"].strip("/").split("/"), p["file"])):
        segs = [s for s in pg["route"].strip("/").split("/") if s]
        for d in range(len(segs) - 1):
            prefix = "/".join(segs[:d + 1])
            if prefix not in printed:
                printed.add(prefix)
                lines.append(f"{'  ' * d}- `/{segs[d]}`")
        printed.add("/".join(segs))
        depth = max(len(segs) - 1, 0)
        label = "/" + (segs[-1] if segs else "")
        lines.append(f"{'  ' * depth}- `{label}`  ({pg['route']})  {pg['file']}  "
                     f"[{len(pg['layouts'])} layout(s), {len(pg['components'])} component(s)]")
    return lines


def render_md(r):
    comps = r["components"]
    L = ["# Architecture map", "",
         "Generated by `architecture.py`. Regenerate after structural changes; do not edit by hand.", "",
         f"- Source files: {r['source_files']}",
         f"- Pages: {len(r['pages'])}",
         f"- Component files: {len(comps)} ({sum(c['kind'] == 'shared' for c in comps)} shared, "
         f"{sum(c['kind'] == 'single-page' for c in comps)} single-page, {sum(c['kind'] == 'not-on-any-page' for c in comps)} on no page)",
         f"- Unreferenced files: {len(r['unreferenced'])}",
         f"- Duplicate-name groups: {len(r['duplicate_names'])}; identical-file groups: {len(r['identical_files'])}",
         "", "## Route tree", ""]
    L += route_tree(r["pages"])
    L += ["", "## Pages, layouts and components", ""]
    for pg in sorted(r["pages"], key=lambda p: p["route"]):
        L.append(f"### `{pg['route']}`")
        L.append(f"- Entry: `{pg['file']}`")
        if pg["layouts"]:
            L.append("- Layout chain: " + " > ".join(f"`{x}`" for x in pg["layouts"]))
        if pg["components"]:
            L.append("- Components:")
            L += [f"  - `{c}`" for c in pg["components"]]
        else:
            L.append("- Components: none local")
        L.append("")
    L += ["## Component reuse (most shared first)", "", "| Component | Pages | Importers | Kind |", "|---|---|---|---|"]
    for c in sorted(comps, key=lambda c: (-c["page_count"], c["file"])):
        L.append(f"| `{c['file']}` | {c['page_count']} | {c['importers']} | {c['kind']} |")
    L += ["", "## Dead-file candidates", "",
          "Nothing the static graph knows about imports these. Grep each name before deleting: dynamic "
          "imports, string registries and framework conventions are invisible here.", ""]
    L += [f"- `{f}`" for f in r["unreferenced"]] or ["- none"]
    L += ["", "## Possible duplicate components", ""]
    for d in r["duplicate_names"]:
        L.append(f"- **{d['core']}**: " + ", ".join(f"`{f}`" for f in d["files"]))
    for g in r["identical_files"]:
        L.append("- identical content: " + ", ".join(f"`{f}`" for f in g))
    if not r["duplicate_names"] and not r["identical_files"]:
        L.append("- none")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
