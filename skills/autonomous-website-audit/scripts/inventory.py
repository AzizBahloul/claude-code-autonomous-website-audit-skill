#!/usr/bin/env python3
"""Enumerate every page route of a web project and the local components each page pulls in.

    python3 inventory.py [ROOT] [--out .website-audit/inventory.json] [--depth 2]

Detects, per package in the repo (monorepos included):
  Next.js (app/ and pages/), Nuxt, SvelteKit, Remix / React Router v7 file routes, Astro,
  Gatsby, TanStack Router file routes, SPA router configs (React Router, Vue Router, Angular),
  Django urls.py, Laravel routes/web.php, Rails config/routes.rb, and plain .html sites.

File-system routers are exact. Config/regex based detections are marked confidence "low":
read the router file to confirm them, and run the authoritative command the output lists
(e.g. `bin/rails routes`, `python manage.py show_urls`) when the framework has one.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

IGNORE_DIRS = {
    "node_modules", ".git", ".next", ".nuxt", ".output", ".svelte-kit", "dist", "build", "out",
    "coverage", ".venv", "venv", "env", "__pycache__", ".turbo", ".vercel", "vendor", ".website-audit",
    ".cache", "storybook-static", ".expo", "target", "tmp", ".idea", ".vscode",
}
JS_EXT = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".mdx")
RESOLVE_EXT = [".tsx", ".ts", ".jsx", ".js", ".mjs", ".vue", ".svelte", ".astro"]


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in filenames:
            yield Path(dirpath) / f


def read(p):
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def rel(p, root):
    return str(Path(p).resolve().relative_to(root)) if Path(p).resolve().is_relative_to(root) else str(p)


# ------------------------------------------------------------ package roots

def package_roots(root):
    """Yield (dir, deps) for every package.json in the tree."""
    for p in walk(root):
        if p.name != "package.json":
            continue
        try:
            data = json.loads(read(p))
        except json.JSONDecodeError:
            continue
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        yield p.parent, deps


# ----------------------------------------------------------- import graph

def strip_json_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'(^|[^:"\\])//.*$', r"\1", text, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def load_aliases(pkg_dir):
    aliases = {}
    for name in ("tsconfig.json", "jsconfig.json"):
        cfg = pkg_dir / name
        if not cfg.exists():
            continue
        try:
            data = json.loads(strip_json_comments(read(cfg)))
        except json.JSONDecodeError:
            continue
        opts = data.get("compilerOptions", {})
        base = pkg_dir / opts.get("baseUrl", ".")
        for pattern, targets in (opts.get("paths") or {}).items():
            if targets:
                aliases[pattern.rstrip("*")] = base / targets[0].rstrip("*")
    if not aliases:
        src = pkg_dir / "src"
        aliases = {"@/": src if src.is_dir() else pkg_dir, "~/": src if src.is_dir() else pkg_dir}
    return aliases


IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[\w*{}\s,$]+\s+from\s+)?|export\s+[\w*{}\s,$]+\s+from\s+|import\(\s*|require\(\s*)['"]([^'"]+)['"]"""
)


def resolve(spec, from_file, aliases):
    if spec.startswith("."):
        base = (from_file.parent / spec)
    else:
        base = None
        for prefix, target in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
            if spec.startswith(prefix):
                base = target / spec[len(prefix):]
                break
        if base is None:
            return None  # a package from node_modules
    candidates = [base] + [base.with_name(base.name + e) for e in RESOLVE_EXT] + [base / ("index" + e) for e in RESOLVE_EXT]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def components(entry, aliases, depth, root):
    seen, frontier, found = {entry.resolve()}, [entry], []
    for _ in range(depth):
        nxt = []
        for f in frontier:
            for spec in IMPORT_RE.findall(read(f)):
                target = resolve(spec, f, aliases)
                if target and target not in seen and target.is_relative_to(root) and "node_modules" not in target.parts:
                    seen.add(target)
                    found.append(rel(target, root))
                    nxt.append(target)
        frontier = nxt
    return sorted(found)


# ----------------------------------------------------- framework detectors

def next_app(pkg, app_dir, root, aliases, depth):
    pages, api = [], []
    for f in walk(app_dir):
        parts = f.relative_to(app_dir).parts
        stem, ext = f.stem, f.suffix
        if ext not in JS_EXT or stem not in ("page", "route"):
            continue
        segs, skip, slot = [], False, None
        for s in parts[:-1]:
            if s.startswith("_"):
                skip = True  # private folder, not routable
            elif re.match(r"^\(\.{1,3}\)", s):
                skip = True  # intercepting route renders inside another page
            elif s.startswith("(") and s.endswith(")"):
                continue  # route group
            elif s.startswith("@"):
                slot = s  # parallel route slot
            else:
                segs.append(s)
        if skip:
            continue
        route = "/" + "/".join(segs)
        params = re.findall(r"\[+\.{0,3}([^\]]+)\]+", route)
        item = {"route": route, "file": rel(f, root), "framework": "next-app", "dynamic": bool(params), "params": params, "confidence": "high"}
        if stem == "route":
            api.append(item)
            continue
        layouts = []
        d = f.parent
        while True:
            for name in ("layout", "template", "loading", "error", "not-found"):
                for e in JS_EXT:
                    if (d / (name + e)).is_file():
                        layouts.append(rel(d / (name + e), root))
            if d.resolve() == app_dir.resolve():
                break
            d = d.parent
        item["layouts"] = layouts
        if slot:
            item["notes"] = [f"parallel route slot {slot}: renders inside its parent layout"]
        item["components"] = components(f, aliases, depth, root)
        pages.append(item)
    for mw in ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js", "proxy.ts", "src/proxy.ts"):
        if (pkg / mw).is_file():
            for p in pages:
                p.setdefault("layouts", []).insert(0, rel(pkg / mw, root))
    return pages, api


def file_router(base_dir, root, aliases, depth, framework, exts, index_names=("index",), api_prefix=None, skip_names=()):
    pages, api = [], []
    for f in walk(base_dir):
        if f.suffix not in exts or f.stem in skip_names or f.stem.startswith("_"):
            continue
        parts = list(f.relative_to(base_dir).with_suffix("").parts)
        if parts and parts[-1] in index_names:
            parts = parts[:-1]
        parts = [p for p in parts if not (p.startswith("(") and p.endswith(")"))]
        route = "/" + "/".join(parts)
        params = re.findall(r"\[+\.{0,3}([^\]]+)\]+", route)
        item = {"route": route, "file": rel(f, root), "framework": framework, "dynamic": bool(params), "params": params, "confidence": "high"}
        if api_prefix and route.startswith(api_prefix):
            api.append(item)
            continue
        if f.suffix in (".ts", ".js") and framework == "astro":
            api.append(item)  # Astro endpoints
            continue
        item["components"] = components(f, aliases, depth, root)
        pages.append(item)
    return pages, api


def sveltekit(routes_dir, root, aliases, depth):
    pages, api = [], []
    for f in walk(routes_dir):
        if f.name not in ("+page.svelte", "+server.ts", "+server.js"):
            continue
        segs = [s for s in f.relative_to(routes_dir).parts[:-1] if not (s.startswith("(") and s.endswith(")"))]
        route = "/" + "/".join(segs)
        params = re.findall(r"\[+\.{0,3}([^\]=]+)(?:=[^\]]+)?\]+", route)
        item = {"route": route, "file": rel(f, root), "framework": "sveltekit", "dynamic": bool(params), "params": params, "confidence": "high"}
        if f.name.startswith("+server"):
            api.append(item)
            continue
        layouts = [rel(p, root) for p in [f.parent / "+layout.svelte", *[a / "+layout.svelte" for a in f.parent.parents]] if p.is_file() and p.resolve().is_relative_to(routes_dir.resolve())]
        item["layouts"] = layouts
        item["components"] = components(f, aliases, depth, root)
        pages.append(item)
    return pages, api


def remix_flat(routes_dir, root, aliases, depth):
    pages, api = [], []
    for f in sorted(routes_dir.iterdir()):
        if f.is_dir():
            entry = next((f / ("route" + e) for e in JS_EXT if (f / ("route" + e)).is_file()), None)
            if not entry:
                continue
            name = f.name
        elif f.suffix in JS_EXT:
            entry, name = f, f.stem
        else:
            continue
        segs = []
        for s in re.split(r"\.(?![^\[]*\])", name):
            if s == "_index" or (s.startswith("_") and s != "_"):
                continue  # index route or pathless layout segment
            s = s.rstrip("_")
            s = s.replace("[", "").replace("]", "")
            if s == "$":
                s = "*"
            elif s.startswith("($") and s.endswith(")"):
                s = ":" + s[2:-1] + "?"
            elif s.startswith("$"):
                s = ":" + s[1:]
            if s:
                segs.append(s)
        route = "/" + "/".join(segs)
        params = re.findall(r":(\w+)", route)
        item = {"route": route, "file": rel(entry, root), "framework": "remix/react-router", "dynamic": bool(params) or "*" in route, "params": params, "confidence": "high"}
        if not re.search(r"export\s+default", read(entry)):
            api.append(item)  # resource route: loader/action only
            continue
        item["components"] = components(entry, aliases, depth, root)
        pages.append(item)
    return pages, api


ROUTE_CFG_PATTERNS = [
    re.compile(r"""<Route\b[^>]*?\bpath\s*=\s*[{]?\s*['"`]([^'"`]*)['"`]"""),
    re.compile(r"""\bpath\s*:\s*['"`]([^'"`]*)['"`]"""),
    re.compile(r"""\broute\(\s*['"`]([^'"`]*)['"`]"""),
]


def spa_config(pkg, root, framework):
    pages = []
    src = pkg / "src" if (pkg / "src").is_dir() else pkg
    for f in walk(src):
        if f.suffix not in JS_EXT + (".vue",):
            continue
        text = read(f)
        if not re.search(r"Route\b|createBrowserRouter|createRouter|RouterModule|Routes\b|routes\s*[:=]", text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat in ROUTE_CFG_PATTERNS:
                for m in pat.finditer(line):
                    path = m.group(1)
                    if path.startswith(("http", "./", "../")) or path.endswith(JS_EXT):
                        continue
                    notes = [] if path.startswith("/") or path in ("", "*") else ["relative to its parent route"]
                    params = re.findall(r":(\w+)", path)
                    pages.append({
                        "route": path if path.startswith("/") else ("/" + path if path else "/"),
                        "file": f"{rel(f, root)}:{i}", "framework": framework, "dynamic": bool(params) or "*" in path,
                        "params": params, "confidence": "low", "notes": notes,
                    })
    return pages


def tanstack_file_routes(pkg, root, aliases, depth):
    pages = []
    for f in walk(pkg):
        if f.suffix not in JS_EXT:
            continue
        m = re.search(r"""createFileRoute\(\s*['"`]([^'"`]+)['"`]""", read(f))
        if m:
            route = re.sub(r"/_[^/]+", "", m.group(1)).rstrip("/") or "/"
            params = re.findall(r"\$(\w+)", route)
            pages.append({"route": route, "file": rel(f, root), "framework": "tanstack-router", "dynamic": bool(params),
                          "params": params, "confidence": "high", "components": components(f, aliases, depth, root)})
    return pages


def django(root):
    pages = []
    for f in walk(root):
        if f.name != "urls.py":
            continue
        for i, line in enumerate(read(f).splitlines(), 1):
            m = re.search(r"""\b(re_)?path\(\s*r?['"]([^'"]*)['"]""", line)
            if not m or "include(" in line:
                continue
            route = "/" + m.group(2).lstrip("^").rstrip("$")
            params = re.findall(r"<(?:\w+:)?(\w+)>|\(\?P<(\w+)>", route)
            params = [a or b for a, b in params] if params and isinstance(params[0], tuple) else params
            pages.append({"route": route, "file": f"{rel(f, root)}:{i}", "framework": "django", "dynamic": bool(params),
                          "params": params, "confidence": "low", "notes": ["prefix from include() not applied; confirm with show_urls"]})
    return pages


def laravel(root):
    pages = []
    web = root / "routes" / "web.php"
    if web.is_file():
        for i, line in enumerate(read(web).splitlines(), 1):
            m = re.search(r"""Route::(get|view|match|any)\(\s*['"]([^'"]+)['"]""", line)
            if m:
                route = "/" + m.group(2).lstrip("/")
                params = re.findall(r"\{(\w+)\??\}", route)
                pages.append({"route": route, "file": f"routes/web.php:{i}", "framework": "laravel", "dynamic": bool(params),
                              "params": params, "confidence": "low", "notes": ["group prefixes not applied; confirm with route:list"]})
    return pages


def static_html(root):
    pages = []
    for f in walk(root):
        if f.suffix == ".html":
            r = "/" + str(f.relative_to(root))
            r = r[: -len("index.html")] if r.endswith("index.html") else r
            pages.append({"route": r or "/", "file": rel(f, root), "framework": "static-html", "dynamic": False, "params": [], "confidence": "high"})
    return pages


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out", default=".website-audit/inventory.json")
    ap.add_argument("--depth", type=int, default=2, help="import depth for the component list (default 2)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not root.is_dir():
        sys.exit(f"inventory.py: not a directory: {root}")

    pages, api, frameworks, commands, warnings = [], [], [], [], []

    def add(fw, result):
        p, s = result if isinstance(result, tuple) else (result, [])
        if p or s:
            frameworks.append(fw)
        pages.extend(p)
        api.extend(s)

    for pkg, deps in package_roots(root):
        aliases = load_aliases(pkg)
        where = rel(pkg, root) or "."
        if "next" in deps:
            for d in ("app", "src/app"):
                if (pkg / d).is_dir():
                    add(f"next-app ({where}/{d})", next_app(pkg, pkg / d, root, aliases, a.depth))
            for d in ("pages", "src/pages"):
                if (pkg / d).is_dir():
                    add(f"next-pages ({where}/{d})", file_router(pkg / d, root, aliases, a.depth, "next-pages", JS_EXT, api_prefix="/api", skip_names=("_app", "_document", "_error", "_middleware")))
        elif "nuxt" in deps:
            for d in ("pages", "app/pages"):
                if (pkg / d).is_dir():
                    add(f"nuxt ({where}/{d})", file_router(pkg / d, root, aliases, a.depth, "nuxt", (".vue",)))
            if (pkg / "server" / "api").is_dir():
                api.extend({"route": "/api/" + str(f.relative_to(pkg / "server" / "api").with_suffix("")), "file": rel(f, root), "framework": "nuxt"} for f in walk(pkg / "server" / "api"))
        elif "@sveltejs/kit" in deps and (pkg / "src" / "routes").is_dir():
            add(f"sveltekit ({where})", sveltekit(pkg / "src" / "routes", root, aliases, a.depth))
        elif any(k.startswith("@remix-run/") or k == "@react-router/dev" for k in deps) and (pkg / "app" / "routes").is_dir():
            add(f"remix/react-router ({where})", remix_flat(pkg / "app" / "routes", root, aliases, a.depth))
            for cfg in ("app/routes.ts", "app/routes.js"):
                if (pkg / cfg).is_file():
                    warnings.append(f"{where}/{cfg} defines routes in config too; read it and add any route missing here")
        elif "astro" in deps and (pkg / "src" / "pages").is_dir():
            add(f"astro ({where})", file_router(pkg / "src" / "pages", root, aliases, a.depth, "astro", (".astro", ".md", ".mdx", ".html", ".ts", ".js")))
        elif "gatsby" in deps and (pkg / "src" / "pages").is_dir():
            add(f"gatsby ({where})", file_router(pkg / "src" / "pages", root, aliases, a.depth, "gatsby", JS_EXT))
        elif "@tanstack/react-router" in deps:
            add(f"tanstack-router ({where})", tanstack_file_routes(pkg, root, aliases, a.depth) or spa_config(pkg, root, "tanstack-router"))
        elif any(k in deps for k in ("react-router-dom", "react-router", "vue-router", "@angular/router", "wouter", "preact-router")):
            fw = next(k for k in ("react-router-dom", "react-router", "vue-router", "@angular/router", "wouter", "preact-router") if k in deps)
            add(f"{fw} ({where})", spa_config(pkg, root, fw))
            warnings.append(f"{where}: routes read from router config by regex; nested paths may be relative to a parent. Read the router file to confirm.")

    if (root / "manage.py").is_file() or any(True for _ in root.glob("*/manage.py")):
        add("django", django(root))
        commands.append("python manage.py show_urls   # needs django-extensions; authoritative URL list")
    if (root / "config" / "routes.rb").is_file():
        frameworks.append("rails")
        commands.append("bin/rails routes   # authoritative; add each GET route that renders a page")
        warnings.append("Rails routes are not parsed here; run `bin/rails routes` and add pages with progress.py add")
    if (root / "artisan").is_file():
        add("laravel", laravel(root))
        commands.append("php artisan route:list --method=GET --json   # authoritative")
    if not pages and not frameworks:
        add("static-html", static_html(root))

    # De-duplicate (same route + file), keep a stable order.
    uniq = {}
    for p in pages:
        uniq.setdefault((p["route"], p["file"]), p)
    pages = sorted(uniq.values(), key=lambda p: (p["route"], p["file"]))
    dup_routes = {p["route"] for p in pages if sum(q["route"] == p["route"] for q in pages) > 1}
    if dup_routes:
        warnings.append("same route from several files (monorepo apps, or relative config paths): " + ", ".join(sorted(dup_routes)) +
                        ". Prefix them per app with progress.py add if they are different pages.")
    if not pages:
        warnings.append("no pages detected: read the router by hand and add pages with progress.py add")

    result = {
        "root": str(root),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frameworks": frameworks,
        "authoritative_commands": commands,
        "warnings": warnings,
        "pages": pages,
        "api": sorted(api, key=lambda p: p["route"]),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    print(f"frameworks: {', '.join(frameworks) or 'none detected'}")
    print(f"{len(pages)} page(s), {len(api)} API/endpoint route(s) -> {out}")
    for p in pages:
        flag = " [dynamic: " + ",".join(p["params"]) + "]" if p.get("dynamic") else ""
        conf = "" if p.get("confidence") == "high" else "  (low confidence)"
        print(f"  {p['route']:<45} {p['file']}{flag}{conf}  components:{len(p.get('components', []))}")
    for c in commands:
        print(f"run: {c}")
    for w in warnings:
        print(f"warning: {w}")


if __name__ == "__main__":
    main()
