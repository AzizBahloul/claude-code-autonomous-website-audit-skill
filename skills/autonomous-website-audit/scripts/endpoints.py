#!/usr/bin/env python3
"""Inventory the backend's endpoints, the frontend calls that hit them, and the pages behind
those calls.

    python3 endpoints.py [ROOT] [--inventory .website-audit/inventory.json]
                         [--architecture .website-audit/architecture.json]
                         [--observed .website-audit/shots/baseline]
                         [--openapi openapi.json | http://localhost:8000/openapi.json]
                         [--out .website-audit/endpoints.json] [--md .website-audit/ENDPOINTS.md]

Backend routes are read by regex from: Express / Koa / Hono / Fastify, NestJS, FastAPI, Flask,
Django + DRF routers, Laravel, Spring, Go (gin / echo / chi / net/http), Next.js route handlers
and Nuxt server routes (from the inventory). Rails is not parsed: run `bin/rails routes`.

Frontend calls are read from fetch / axios / ky / $fetch / useFetch / useSWR and from any
`<client>.get|post|put|patch|delete('...')`. Template parts (`${id}`) become parameters.

Matching is by path SUFFIX: a backend route `/players/{id}` matches a call to
`/api/v1/players/${id}` even when the mount prefix lives in another file. With
--observed, the API calls crawl.mjs recorded in a real browser are matched too, which is the
ground truth for "which endpoints does this page use".

With --openapi (a file, or the LOCAL backend's spec URL: FastAPI /openapi.json, NestJS
Swagger, drf-spectacular), the spec is the primary list: spec routes the regex missed are added,
and code routes missing from the spec are listed as undocumented.

Output buckets, all of them leads to verify, not verdicts:
  orphan endpoints   no frontend call and no observed traffic: dead backend code, or used by
                     a mobile app, webhook, cron or third party. Grep before deleting.
  unmatched calls    a frontend call no backend route matches: a broken call, a removed
                     endpoint, an external API, or a route this script cannot parse.
"""
import argparse
import importlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import read, rel, walk  # noqa: E402

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
FRONT_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte", ".astro"}
Q = r"""(['"`])"""

BACKEND_PATTERNS = [
    # (framework, file filter, regex with groups method/path, method default)
    ("express", (".js", ".ts", ".mjs", ".cjs"),
     re.compile(r"\b(?:app|router|server|api|routes?|\w+Router|fastify|hono)\.(get|post|put|patch|delete|all)\(\s*" + Q + r"(/[^'\"`]*)\2"), None),
    ("fastapi", (".py",),
     re.compile(r"@\s*\w+\.(get|post|put|patch|delete|api_route)\(\s*" + Q + r"([^'\"`]*)\2"), None),
    ("flask", (".py",),
     re.compile(r"@\s*\w+\.route\(\s*" + Q + r"([^'\"`]*)\1([^)]*)\)"), "flask"),
    ("laravel", (".php",),
     re.compile(r"Route::(get|post|put|patch|delete|any)\(\s*" + Q + r"([^'\"`]*)\2"), None),
    ("go", (".go",),
     re.compile(r"\.(GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\(\s*\"(/[^\"]*)\""), None),
    ("go", (".go",),
     re.compile(r"HandleFunc\(\s*\"((?:(GET|POST|PUT|PATCH|DELETE) )?/[^\"]*)\""), "gohandle"),
]
NEST_CTRL = re.compile(r"@Controller\(\s*(?:" + Q + r"([^'\"`]*)\1)?")
NEST_ROUTE = re.compile(r"@(Get|Post|Put|Patch|Delete|All)\(\s*(?:" + Q + r"([^'\"`]*)\2)?\s*\)")
SPRING_CLASS = re.compile(r"@RequestMapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\"([^\"]*)\"[^)]*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+)?class")
SPRING_ROUTE = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\"([^\"]*)\"")
PY_PREFIX = re.compile(r"(?:APIRouter|Blueprint)\([^)]*?(?:prefix|url_prefix)\s*=\s*['\"]([^'\"]+)['\"]", re.S)
DJANGO_PATH = re.compile(r"\b(?:re_)?path\(\s*r?['\"]([^'\"]*)['\"]\s*,\s*([\w.]+)")
DRF_REGISTER = re.compile(r"\.register\(\s*r?['\"]([^'\"]*)['\"]\s*,\s*([\w.]+)")
NEXT_METHOD = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")

FRONT_CALLS = [
    (re.compile(r"\b(?:fetch|\$fetch|useFetch|useLazyFetch|ofetch|useSWR|useSWRImmutable|ky)\(\s*" + Q + r"(.+?)\1"), None),
    (re.compile(r"\b(?:axios|ky|\$axios|\$http|http|api|client|request|instance|service|\w*[Aa]pi\w*|\w*[Cc]lient\w*|\w*[Hh]ttp\w*)\.(get|post|put|patch|delete|head)\(\s*" + Q + r"(.+?)\2"), "m"),
]
METHOD_NEAR = re.compile(r"method\s*:\s*['\"`](\w+)['\"`]", re.I)


def norm(path):
    """Split a route or URL path into segments; every parameter becomes '*'."""
    path = re.sub(r"\$\{[^}]*\}", "*", path)            # JS template parts
    path = re.sub(r"\(\?P<\w+>[^)]*\)", "*", path)       # Django regex groups
    path = path.split("?")[0].split("#")[0]
    path = re.sub(r"^\^|\$$", "", path)
    segs = []
    for s in path.strip("/").split("/"):
        if not s:
            continue
        if re.fullmatch(r"\{[^}]*\}|:\w+\??|<[^>]*>|\[\.{0,3}[^\]]*\]|\*+|\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24}", s, re.I):
            segs.append("*")
        else:
            segs.append(s.lower())
    return segs


def suffix_match(ep, call):
    """Score how well endpoint segments match the END of the call segments (0 = no match)."""
    if not ep or len(ep) > len(call):
        return 0
    tail = call[-len(ep):]
    literal = 0
    for a, b in zip(ep, tail):
        if a == "*" or b == "*":
            continue
        if a != b:
            return 0
        literal += 1
    return literal * 10 + len(ep) if literal else 0


def backend_routes(root, inv):
    eps = []
    add = lambda fw, method, path, f, line: eps.append({"framework": fw, "method": method.upper(), "path": "/" + path.strip("/") if path.strip("/") else "/", "file": rel(f, root), "line": line})
    for f in walk(root):
        ext = f.suffix
        if ext not in (".js", ".ts", ".mjs", ".cjs", ".py", ".php", ".go", ".java", ".kt"):
            continue
        if re.search(r"(\.test\.|\.spec\.|(^|/)tests?/|/__tests__/)", rel(f, root)):
            continue
        text = read(f)
        lineno = lambda pos, text=text: text.count("\n", 0, pos) + 1
        prefix = ""
        if ext == ".py":
            pm = PY_PREFIX.search(text)
            prefix = pm.group(1) if pm else ""
        for fw, exts, rx, mode in BACKEND_PATTERNS:
            if ext not in exts:
                continue
            for m in rx.finditer(text):
                if mode == "flask":
                    path, rest = m.group(2), m.group(3)
                    meths = re.findall(r"['\"](\w+)['\"]", rest.split("methods", 1)[1]) if "methods" in rest else ["GET"]
                    for meth in meths:
                        add(fw, meth, prefix + path, f, lineno(m.start()))
                elif mode == "gohandle":
                    full = m.group(1)
                    meth, _, path = full.partition(" ") if " " in full else ("ALL", "", full)
                    add(fw, meth, path, f, lineno(m.start()))
                else:
                    meth, path = m.group(1), m.group(3) if m.lastindex >= 3 else m.group(2)
                    meth = "ALL" if meth.lower() in ("all", "any", "api_route") else meth
                    add(fw, meth, (prefix if ext == ".py" else "") + path, f, lineno(m.start()))
        if ext in (".ts", ".js") and "@Controller" in text:
            cm = NEST_CTRL.search(text)
            base = (cm.group(2) or "") if cm else ""
            for m in NEST_ROUTE.finditer(text):
                add("nestjs", m.group(1), f"{base}/{m.group(3) or ''}", f, lineno(m.start()))
        if ext in (".java", ".kt"):
            cm = SPRING_CLASS.search(text)
            base = cm.group(1) if cm else ""
            for m in SPRING_ROUTE.finditer(text):
                meth = "ALL" if m.group(1) == "Request" else m.group(1)
                add("spring", meth, f"{base}/{m.group(2)}", f, lineno(m.start()))
        if ext == ".py" and f.name == "urls.py":
            for m in DJANGO_PATH.finditer(text):
                if "include(" in text[m.start():m.start() + 200].split("\n")[0]:
                    continue
                add("django", "ALL", m.group(1), f, lineno(m.start()))
            for m in DRF_REGISTER.finditer(text):
                add("django-rest", "ALL", m.group(1), f, lineno(m.start()))
                add("django-rest", "ALL", m.group(1) + "/{pk}", f, lineno(m.start()))
    for s in inv.get("api", []):
        f = root / s["file"] if s.get("file") else None
        meths = NEXT_METHOD.findall(read(f)) if f and f.is_file() else []
        for meth in meths or ["ALL"]:
            eps.append({"framework": s.get("framework", "file-route"), "method": meth, "path": s["route"], "file": s.get("file"), "line": 1})
    uniq = {}
    for e in eps:
        uniq.setdefault((e["method"], e["path"], e["file"]), e)
    return sorted(uniq.values(), key=lambda e: (e["path"], e["method"]))


def call_args(text, start, limit=800):
    """The text inside the call's own parentheses, so a later call's options are never read."""
    i = text.find("(", start)
    depth = 0
    for j in range(i, min(len(text), i + limit)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:i + limit]


def frontend_calls(root, backend_files):
    calls = []
    for f in walk(root):
        if f.suffix not in FRONT_EXT or rel(f, root) in backend_files:
            continue
        if re.search(r"(\.test\.|\.spec\.|\.stories\.|(^|/)(tests?|__tests__|e2e|mocks?)/)", rel(f, root)):
            continue
        text = read(f)
        for rx, mode in FRONT_CALLS:
            for m in rx.finditer(text):
                if mode == "m":
                    meth, raw = m.group(1).upper(), m.group(3)
                else:
                    raw = m.group(2)
                    near = METHOD_NEAR.search(call_args(text, m.start()))
                    meth = near.group(1).upper() if near else "GET"
                url = re.sub(r"^\$\{[^}]*\}", "", raw)          # ${API_BASE}/players -> /players
                if url.startswith("http"):
                    url = urlparse(url).path
                if not url.startswith("/") or url.startswith("//"):
                    continue
                calls.append({"method": meth, "url": raw, "path": url.split("?")[0], "file": rel(f, root),
                              "line": text.count("\n", 0, m.start()) + 1})
    return calls


def best(eps, method, segs):
    """Indices of the best-matching endpoints; several when two backends serve the same path."""
    scored = []
    for i, e in enumerate(eps):
        if e["method"] not in ("ALL", method) and not (method == "HEAD" and e["method"] == "GET"):
            continue
        s = suffix_match(e["_segs"], segs)
        if s:
            scored.append((s, i))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [i for s, i in scored if s == top]


def openapi_endpoints(src):
    if src.startswith("http"):
        import urllib.request
        host = urlparse(src).hostname or ""
        if not re.match(r"^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?)$", host):
            sys.exit("endpoints.py: --openapi URL must be on the local backend")
        with urllib.request.urlopen(src, timeout=15) as r:
            spec = json.loads(r.read())
    else:
        text = Path(src).read_text()
        try:
            spec = json.loads(text)
        except json.JSONDecodeError:
            try:
                yaml = importlib.import_module("yaml")  # optional dependency
            except ImportError:
                sys.exit("endpoints.py: YAML spec needs PyYAML; convert it to JSON (yq -o json) first")
            spec = yaml.safe_load(text)
    prefix = ""
    if spec.get("servers"):
        prefix = urlparse(spec["servers"][0].get("url", "")).path.rstrip("/")
    out = []
    for path, ops in (spec.get("paths") or {}).items():
        for meth in ops:
            if meth.lower() in METHODS:
                out.append({"framework": "openapi", "method": meth.upper(), "path": prefix + path, "file": src, "line": 0})
    return out


def observed_calls(shots_dir):
    """API calls crawl.mjs saw, keyed by the page path each report captured."""
    out = []
    for f in sorted(Path(shots_dir).rglob("*.json")):
        try:
            r = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for c in (r.get("api") or {}).get("calls", []):
            out.append({"page": r.get("path"), "method": c.get("method", "GET"), "path": urlparse(c.get("url", "")).path,
                        "status": c.get("status"), "ms": c.get("ms")})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--inventory", default=".website-audit/inventory.json")
    ap.add_argument("--architecture", default=".website-audit/architecture.json")
    ap.add_argument("--observed", help="shots directory of crawl.mjs captures (real browser traffic)")
    ap.add_argument("--openapi", help="OpenAPI spec file, or the local backend's spec URL")
    ap.add_argument("--out", default=".website-audit/endpoints.json")
    ap.add_argument("--md", default=".website-audit/ENDPOINTS.md")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    inv = json.loads(Path(a.inventory).read_text()) if Path(a.inventory).exists() else {}
    arch = json.loads(Path(a.architecture).read_text()) if Path(a.architecture).exists() else {}

    eps = backend_routes(root, inv)
    undocumented = []
    if a.openapi:
        spec = openapi_endpoints(a.openapi)
        spec_keys = {(e["method"], tuple(norm(e["path"]))) for e in spec}
        code_keys = set()
        for e in eps:
            k = (e["method"], tuple(norm(e["path"])))
            code_keys.add(k)
            # Documented if a spec route with the same method ends with the code route's segments
            # (the code often lacks the mount prefix), or the other way round.
            segs_c = list(k[1])
            e["documented"] = any(
                (m == e["method"] or e["method"] == "ALL") and (suffix_match(segs_c, list(segs)) or suffix_match(list(segs), segs_c))
                for m, segs in spec_keys)
            if not e["documented"]:
                undocumented.append(f"{e['method']} {e['path']} ({e['file']}:{e['line']})")
        for e in spec:
            k = (e["method"], tuple(norm(e["path"])))
            if not any(suffix_match(list(c[1]), list(k[1])) and c[0] in (k[0], "ALL") for c in code_keys):
                eps.append(e)
    for e in eps:
        e["_segs"] = norm(e["path"])
        e.update(callers=[], pages=[], observed=[])
    backend_files = {e["file"] for e in eps if e["framework"] not in ("next-app", "next-pages", "nuxt", "sveltekit", "file-route")}
    calls = frontend_calls(root, backend_files)

    # file -> pages that reach it (the page file itself, its layouts, or anything they import)
    file_pages = defaultdict(set)
    for pg in arch.get("pages", []) or inv.get("pages", []):
        for f in [pg.get("file")] + pg.get("layouts", []) + pg.get("reaches", pg.get("components", [])):
            if f:
                file_pages[f].add(pg["route"])

    unmatched = []
    for c in calls:
        c["pages"] = sorted(file_pages.get(c["file"], []))
        hits = best(eps, c["method"], norm(c["path"]))
        if not hits:
            unmatched.append(c)
            continue
        c["endpoint"] = [f"{eps[i]['method']} {eps[i]['path']}" for i in hits]
        for i in hits:
            eps[i]["callers"].append(f"{c['file']}:{c['line']}")
            eps[i]["pages"] = sorted(set(eps[i]["pages"]) | set(c["pages"]))

    observed_unmatched = []
    if a.observed:
        seen = set()
        for o in observed_calls(a.observed):
            key = (o["page"], o["method"], o["path"])
            if key in seen:
                continue
            seen.add(key)
            hits = best(eps, o["method"], norm(o["path"]))
            if not hits:
                observed_unmatched.append(o)
            for i in hits:
                eps[i]["observed"].append({"page": o["page"], "status": o["status"], "ms": o["ms"]})

    for e in eps:
        e.pop("_segs")
    orphans = [e for e in eps if not e["callers"] and not e["observed"]]
    result = {"root": str(root), "endpoints": eps, "calls": calls, "orphan_endpoints": orphans,
              "unmatched_calls": unmatched, "observed_unmatched": observed_unmatched, "undocumented": undocumented,
              "observed_source": a.observed}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    Path(a.md).write_text(render_md(result))

    fws = sorted({e["framework"] for e in eps})
    print(f"{len(eps)} backend endpoint(s) [{', '.join(fws) or 'none detected'}], {len(calls)} frontend call(s)")
    print(f"{len(orphans)} orphan endpoint(s), {len(unmatched)} unmatched frontend call(s)"
          + (f", {len(observed_unmatched)} observed call(s) with no backend route" if a.observed else ""))
    if a.openapi:
        print(f"{sum(e['framework'] == 'openapi' for e in eps)} route(s) only in the spec added; {len(undocumented)} code route(s) missing from the spec")
    if not eps:
        print("warning: no backend routes parsed. Rails: run `bin/rails routes`; others: read the router and list endpoints by hand.")
    print(f"-> {out} and {a.md}")


def render_md(r):
    L = ["# Endpoint map", "", "Generated by `endpoints.py`. Matches are by path suffix and regex: verify before acting.", "",
         f"- Backend endpoints: {len(r['endpoints'])}", f"- Frontend calls: {len(r['calls'])}",
         f"- Orphan endpoints (no caller, no observed traffic): {len(r['orphan_endpoints'])}",
         f"- Unmatched frontend calls: {len(r['unmatched_calls'])}", ""]
    by_page = defaultdict(set)
    for e in r["endpoints"]:
        for p in e["pages"]:
            by_page[p].add(f"{e['method']} {e['path']}")
        for o in e["observed"]:
            by_page[f"{o['page']} (observed)"].add(f"{e['method']} {e['path']}")
    L += ["## Endpoints per page", ""]
    for p in sorted(by_page):
        L.append(f"- `{p}`: " + ", ".join(f"`{x}`" for x in sorted(by_page[p])))
    if not by_page:
        L.append("- no page could be linked to an endpoint (run architecture.py first, and pass --observed)")
    L += ["", "## All endpoints", "", "| Method | Path | Handler | Callers | Pages | Observed |", "|---|---|---|---|---|---|"]
    for e in r["endpoints"]:
        L.append(f"| {e['method']} | `{e['path']}` | `{e['file']}:{e['line']}` | {len(e['callers'])} | {len(e['pages'])} | {len(e['observed'])} |")
    L += ["", "## Orphan endpoints", "", "No frontend caller and no observed traffic. Could be dead, or used by a mobile app, webhook, cron job or third party: grep before deleting.", ""]
    L += [f"- {e['method']} `{e['path']}`  ({e['file']}:{e['line']})" for e in r["orphan_endpoints"]] or ["- none"]
    L += ["", "## Unmatched frontend calls", "", "No backend route matches. A broken call, an external API, or a route the parser missed.", ""]
    L += [f"- {c['method']} `{c['url']}`  ({c['file']}:{c['line']})" for c in r["unmatched_calls"]] or ["- none"]
    if r.get("undocumented"):
        L += ["", "## In the code but not in the OpenAPI spec", ""]
        L += [f"- {u}" for u in r["undocumented"]]
    if r.get("observed_unmatched"):
        L += ["", "## Observed in the browser with no backend route", ""]
        L += [f"- {o['method']} `{o['path']}` on `{o['page']}` (status {o['status']})" for o in r["observed_unmatched"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
