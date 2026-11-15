#!/usr/bin/env python3
"""Measure backend endpoints directly, compare runs, and break page latency into server time
versus everything between the browser and the server.

    bench.py run     --base URL [--endpoints .website-audit/endpoints.json] [--page ROUTE]
                     [--url /api/x ...] [--param id=42 ...] [--n 7] [--warmup 1]
                     [--storage .website-audit/auth/ROLE.json | --token-env VAR]
                     [--label baseline] [--out-dir .website-audit/perf]
    bench.py compare BEFORE.json AFTER.json [--tolerance 0.2] [--min-ms 20]
    bench.py e2e     SHOTS_DIR_OR_REPORT [--bench .website-audit/perf/bench-baseline.json]
    bench.py matrix  --base URL --endpoints .website-audit/endpoints.json
                     --role anonymous= --role coach=.website-audit/auth/coach.json ... [--param ...]

run       Sequential requests per endpoint (no load test, no parallelism) after warm-up.
          Reports p50, p95, max, status, bytes and any Server-Timing header. GET and HEAD
          only. Endpoints whose path has parameters need --param NAME=VALUE (or a '*' value
          fills any parameter); the rest are listed as skipped, never guessed.
          Refuses a non-local --base unless --allow-remote (which the user must approve).
          Session cookies come from a Playwright storage state, or a bearer token from an
          environment variable. Neither is ever printed or written to the output.
          Stops an endpoint after 3 server errors, and backs off on 429 (rate limited).
          Responses over 100 KB are flagged: lists that should be paginated or trimmed.
compare   Exit 1 when any endpoint's p50 got slower by more than --tolerance (fraction)
          AND more than --min-ms AND more than the baseline's own spread (p95 - p50: a change
          inside the noise is no change), or when an endpoint's status changed.
e2e       For every API call crawl.mjs recorded in the browser: browser time, server p50
          from the bench file, and the difference (network, CORS preflight, queueing,
          serialisation). Also the request waterfall: how many calls had to wait for an
          earlier one to finish (crawl.mjs records each call's start).
matrix    One GET per endpoint per role (a role with no storage file is anonymous), printed
          as an endpoint x role table of status codes. An endpoint that answers 200 to
          anonymous, or to a role that can't open the page using it, is a lead for the
          security layer. Check the backend rule before calling it a bug (rule I4).
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

LOCAL = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|[\w-]+\.(test|local|localhost))$", re.I)
PARAM = re.compile(r"\{(\w+)[^}]*\}|:(\w+)\??|<(?:\w+:)?(\w+)>|\[\.{0,3}(\w+)\]|\*")


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def fill(path, params):
    missing = []

    def sub(m):
        name = next((g for g in m.groups() if g), None)
        if name and name in params:
            return params[name]
        if "*" in params:
            return params["*"]
        missing.append(name or "*")
        return m.group(0)
    out = PARAM.sub(sub, path)
    return out, missing


def cookie_header(storage, host):
    data = json.loads(Path(storage).read_text())
    pairs = []
    for c in data.get("cookies", []):
        d = c.get("domain", "").lstrip(".")
        if host == d or host.endswith("." + d) or d in ("localhost", host):
            pairs.append(f"{c['name']}={c['value']}")
    return "; ".join(pairs)


def one(url, headers, method, timeout):
    req = urllib.request.Request(url, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status, st = r.status, r.headers.get("Server-Timing")
    except urllib.error.HTTPError as e:
        body, status, st = e.read() or b"", e.code, e.headers.get("Server-Timing")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return {"error": str(getattr(e, "reason", e))[:120], "ms": (time.perf_counter() - t0) * 1000}
    return {"status": status, "ms": (time.perf_counter() - t0) * 1000, "bytes": len(body), "server_timing": st}


def cmd_run(a):
    base = a.base.rstrip("/")
    host = urlparse(base).hostname or ""
    if not LOCAL.match(host) and not a.allow_remote:
        sys.exit(f"bench.py: {host} is not local. Benchmark the local stack; --allow-remote only with the user's explicit OK.")
    params = dict(p.split("=", 1) for p in a.param)
    targets = [("GET", u) for u in a.url]
    if a.endpoints and not a.url:
        data = json.loads(Path(a.endpoints).read_text())
        for e in data.get("endpoints", []):
            if e["method"] not in ("GET", "HEAD", "ALL"):
                continue
            if a.page and a.page not in e.get("pages", []) and not any(o.get("page") == a.page for o in e.get("observed", [])):
                continue
            targets.append(("GET" if e["method"] == "ALL" else e["method"], e["path"]))
    if not targets:
        sys.exit("bench.py: nothing to measure (pass --url, or --endpoints with GET routes)")
    headers = {"Accept": "application/json", "User-Agent": "website-audit-bench"}
    if a.storage:
        ck = cookie_header(a.storage, host)
        if ck:
            headers["Cookie"] = ck
    if a.token_env:
        import os
        tok = os.environ.get(a.token_env)
        if not tok:
            sys.exit(f"bench.py: ${a.token_env} is empty")
        headers["Authorization"] = f"Bearer {tok}"

    results, skipped = [], []
    for method, path in dict.fromkeys(targets):
        filled, missing = fill(path, params)
        if missing:
            skipped.append({"path": path, "missing": missing})
            continue
        url = base + (filled if filled.startswith("/") else "/" + filled)
        for _ in range(a.warmup):
            one(url, headers, method, a.timeout)
        samples, server_errors = [], 0
        for _ in range(a.n):
            s_ = one(url, headers, method, a.timeout)
            if s_.get("status") == 429:
                time.sleep(2)
            server_errors += 1 if (s_.get("status") or 0) >= 500 or "error" in s_ else 0
            samples.append(s_)
            if server_errors >= 3:
                break
        ok = [s for s in samples if "error" not in s]
        ms = [s["ms"] for s in ok]
        row = {"method": method, "path": path, "url_path": urlparse(url).path, "n": len(ok),
               "status": sorted({s["status"] for s in ok}), "p50": pct(ms, .5), "p95": pct(ms, .95),
               "max": round(max(ms), 1) if ms else None, "bytes": ok[-1]["bytes"] if ok else None,
               "server_timing": next((s["server_timing"] for s in ok if s.get("server_timing")), None),
               "errors": sorted({s["error"] for s in samples if "error" in s})}
        results.append(row)
        flag = "  ERROR " + "; ".join(row["errors"]) if row["errors"] else ""
        if (row["bytes"] or 0) > 100 * 1024:
            flag += "  LARGE (over 100 KB: paginate or trim fields)"
        if 429 in row["status"]:
            flag += "  RATE-LIMITED (numbers are not latency)"
        print(f"{method:<5} {path:<50} {','.join(map(str, row['status'])) or '-':<8} p50 {row['p50']}ms  p95 {row['p95']}ms  "
              f"{row['bytes'] or 0}B{flag}")
    for s in skipped:
        print(f"SKIP  {s['path']}  (needs --param {', '.join(s['missing'])}=VALUE)")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"bench-{a.label}.json"
    out.write_text(json.dumps({"base": base, "label": a.label, "n": a.n, "page": a.page,
                               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "results": results, "skipped": skipped}, indent=2))
    slow = [r for r in results if (r["p50"] or 0) > 300]
    print(f"{len(results)} endpoint(s) measured, {len(skipped)} skipped, {len(slow)} with p50 over 300ms -> {out}")


def cmd_compare(a):
    A = {(r["method"], r["path"]): r for r in json.loads(Path(a.before).read_text())["results"]}
    B = {(r["method"], r["path"]): r for r in json.loads(Path(a.after).read_text())["results"]}
    bad = 0
    for k in sorted(A.keys() | B.keys()):
        x, y = A.get(k), B.get(k)
        if not x or not y:
            print(f"{'ONLY-BEFORE' if x else 'ONLY-AFTER':<11} {k[0]} {k[1]}")
            continue
        if x["p50"] is None or y["p50"] is None:
            continue
        d = y["p50"] - x["p50"]
        spread = (x["p95"] or x["p50"]) - x["p50"]
        worse = d > a.min_ms and d > x["p50"] * a.tolerance and d > spread
        status_changed = x["status"] != y["status"]
        tag = "SLOWER" if worse else "STATUS" if status_changed else "faster" if d < -a.min_ms else "same"
        bad += worse or status_changed
        print(f"{tag:<11} {k[0]} {k[1]}  p50 {x['p50']} -> {y['p50']}ms ({d:+.1f})" + (f"  status {x['status']} -> {y['status']}" if status_changed else ""))
    print(f"{bad} regression(s)")
    sys.exit(1 if bad else 0)


def cmd_e2e(a):
    src = Path(a.reports)
    files = sorted(src.rglob("*.json")) if src.is_dir() else [src]
    bench = {}
    if a.bench and Path(a.bench).exists():
        for r in json.loads(Path(a.bench).read_text())["results"]:
            bench[r["url_path"]] = r
    for f in files:
        try:
            r = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        calls = [c for c in (r.get("api") or {}).get("calls", []) if c.get("ms") is not None]
        if not calls:
            continue
        print(f"== {r.get('path')}  [{r.get('viewport')}/{r.get('theme')}]  ttfb {((r.get('timing') or {}).get('ttfbMs'))}ms  "
              f"lcp {((r.get('timing') or {}).get('lcpMs'))}ms  {len(calls)} API call(s)")
        for c in sorted(calls, key=lambda c: -c["ms"])[: a.top]:
            p = urlparse(c["url"]).path
            srv = bench.get(p)
            gap = f"server p50 {srv['p50']}ms, other {c['ms'] - srv['p50']:.0f}ms" if srv and srv["p50"] is not None else "no direct measurement"
            print(f"   {c['method']:<5} {p:<50} browser {c['ms']}ms  ({gap})")
        timed = sorted((c for c in calls if c.get("start")), key=lambda c: c["start"])
        if timed:
            depth, longest = {}, 0
            for i, c in enumerate(timed):
                waits_on = [j for j in range(i) if timed[j]["start"] + timed[j]["ms"] <= c["start"]]
                depth[i] = 1 + max((depth[j] for j in waits_on), default=0)
                longest = max(longest, depth[i])
            span = max(c["start"] + c["ms"] for c in timed) - timed[0]["start"]
            warn = "  WATERFALL: more than 3 sequential steps" if longest > 3 else ""
            print(f"   waterfall depth {longest}, first API call to last response {span}ms{warn}")
        elif calls:
            print("   no start times in this report (captured before crawl.mjs recorded them); re-capture for waterfall depth")


def cmd_matrix(a):
    base = a.base.rstrip("/")
    host = urlparse(base).hostname or ""
    if not LOCAL.match(host) and not a.allow_remote:
        sys.exit(f"bench.py: {host} is not local; the matrix runs against the local stack only")
    params = dict(p.split("=", 1) for p in a.param)
    roles = []
    for r in a.role:
        name, _, storage = r.partition("=")
        h = {"Accept": "application/json", "User-Agent": "website-audit-bench"}
        if storage:
            ck = cookie_header(storage, host)
            if ck:
                h["Cookie"] = ck
        roles.append((name, h))
    data = json.loads(Path(a.endpoints).read_text())
    rows, skipped = [], []
    for e in data.get("endpoints", []):
        if e["method"] not in ("GET", "ALL"):
            continue
        filled, missing = fill(e["path"], params)
        if missing:
            skipped.append(e["path"])
            continue
        url = base + (filled if filled.startswith("/") else "/" + filled)
        cells = {}
        for name, h in roles:
            res = one(url, h, "GET", a.timeout)
            cells[name] = res.get("status") or "ERR"
            time.sleep(0.2)  # at most a handful of requests per second
        rows.append({"path": e["path"], "pages": e.get("pages", []), "status": cells})
    names = [n for n, _ in roles]
    print("| endpoint | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for r in rows:
        print(f"| `{r['path']}` | " + " | ".join(str(r["status"][n]) for n in names) + " |")
    open_to_anon = [r["path"] for r in rows if any(n.lower().startswith(("anon", "public", "guest")) and r["status"][n] == 200 for n in names)]
    for s_ in skipped:
        print(f"SKIP  {s_}  (needs --param)")
    print(f"{len(rows)} endpoint(s) x {len(names)} role(s); {len(open_to_anon)} answer 200 to anonymous: confirm each is meant to be public")
    out = Path(a.out_dir) / "matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"roles": names, "rows": rows, "skipped": skipped, "open_to_anonymous": open_to_anon}, indent=2))
    print(f"-> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--base", required=True)
    s.add_argument("--endpoints")
    s.add_argument("--page")
    s.add_argument("--url", action="append", default=[])
    s.add_argument("--param", action="append", default=[])
    s.add_argument("--n", type=int, default=7)
    s.add_argument("--warmup", type=int, default=1)
    s.add_argument("--timeout", type=float, default=15)
    s.add_argument("--storage")
    s.add_argument("--token-env")
    s.add_argument("--label", default="baseline")
    s.add_argument("--out-dir", default=".website-audit/perf")
    s.add_argument("--allow-remote", action="store_true")
    s = sub.add_parser("compare")
    s.add_argument("before")
    s.add_argument("after")
    s.add_argument("--tolerance", type=float, default=0.2)
    s.add_argument("--min-ms", type=float, default=20)
    s = sub.add_parser("e2e")
    s.add_argument("reports")
    s.add_argument("--bench")
    s.add_argument("--top", type=int, default=8)
    s = sub.add_parser("matrix")
    s.add_argument("--base", required=True)
    s.add_argument("--endpoints", required=True)
    s.add_argument("--role", action="append", required=True, help="NAME=STORAGE_JSON, or NAME= for anonymous")
    s.add_argument("--param", action="append", default=[])
    s.add_argument("--timeout", type=float, default=15)
    s.add_argument("--out-dir", default=".website-audit/perf")
    s.add_argument("--allow-remote", action="store_true")
    a = ap.parse_args()
    {"run": cmd_run, "compare": cmd_compare, "e2e": cmd_e2e, "matrix": cmd_matrix}[a.cmd](a)


if __name__ == "__main__":
    main()
