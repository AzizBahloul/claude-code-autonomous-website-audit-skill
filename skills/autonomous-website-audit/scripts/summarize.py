#!/usr/bin/env python3
"""Read crawl.mjs capture reports and answer three questions without opening every JSON.

    summarize.py compare BASELINE_DIR VERIFY_DIR   did anything get worse? (exit 1 if so)
    summarize.py table   SHOTS_DIR                 one row per page and combination
    summarize.py repeats SHOTS_DIR [--min-pages 2] findings shared by several pages

compare pairs reports by file name (<label>__<viewport>__<theme>.json). Any count above the
baseline, or a baseline combination with no re-capture, is a problem. When both screenshots
exist and Pillow is installed it also prints how much of each image changed, so you know
which tiles to look at again.

repeats groups the same finding (same axe rule and target, same overflow offender, same
low-contrast selector...) across pages. Something that shows up on many pages lives in a
shared layout or component: fix it there once, then re-check every page that uses it.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Counts where lower is better. Booleans count as 0 or 1.
METRICS = [
    "pageErrors", "consoleErrors", "failedRequests", "httpErrors", "rawKeys", "badText",
    "overflow", "lowContrast", "brokenImages", "tokensInStorage", "a11ySerious", "smallTargets",
    "structure", "missingViewportMeta", "navigationError", "redirectedToLogin",
    "keyboardNoIndicator", "keyboardTrap", "pointerOnly",
]
SHORT = {
    "pageErrors": "pgErr", "consoleErrors": "console", "failedRequests": "failed", "httpErrors": "http4xx5xx",
    "rawKeys": "rawKeys", "badText": "badText", "overflow": "overflow", "lowContrast": "contrast",
    "brokenImages": "brokenImg", "tokensInStorage": "tokens", "a11ySerious": "a11y", "smallTargets": "targets",
    "structure": "struct", "missingViewportMeta": "noViewport", "navigationError": "navErr",
    "redirectedToLogin": "toLogin",
}


def reports(d):
    d = Path(d)
    return {f.name: json.loads(f.read_text()) for f in sorted(d.glob("*.json"))} if d.is_dir() else {}


def num(v):
    return int(v) if isinstance(v, bool) else (v or 0)


def pixel_change(a, b):
    """Percent of pixels that differ between two screenshots, or None without Pillow."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    try:
        ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    except OSError:
        return None
    note = "" if ia.size == ib.size else f" (height {ia.height}->{ib.height})"
    w, h = min(ia.width, ib.width), min(ia.height, ib.height)
    diff = ImageChops.difference(ia.crop((0, 0, w, h)), ib.crop((0, 0, w, h))).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = diff.histogram()[255]
    extra = abs(ia.width * ia.height - ib.width * ib.height)
    return round(100 * (changed + extra) / max(ia.width * ia.height, ib.width * ib.height), 2), note


def compare_dirs(base_dir, verify_dir):
    base, ver = reports(base_dir), reports(verify_dir)
    problems, lines = [], []
    if not base:
        problems.append(f"no baseline reports in {base_dir}")
    for name, b in base.items():
        v = ver.get(name)
        if v is None:
            problems.append(f"{name}: in the baseline but not re-captured")
            continue
        bs, vs = b.get("summary", {}), v.get("summary", {})
        worse = [f"{m} {num(bs.get(m))}->{num(vs.get(m))}" for m in METRICS if num(vs.get(m)) > num(bs.get(m))]
        better = [f"{m} {num(bs.get(m))}->{num(vs.get(m))}" for m in METRICS if num(vs.get(m)) < num(bs.get(m))]
        if bs.get("a11yScanned") and not vs.get("a11yScanned"):
            worse.append("a11y scan ran in baseline but not in verify")
        if (vs.get("cls") or 0) > max(0.1, (bs.get("cls") or 0) + 0.05):
            worse.append(f"cls {bs.get('cls')}->{vs.get('cls')}")
        for w in worse:
            problems.append(f"{name}: {w}")
        change = ""
        if b.get("screenshot") and v.get("screenshot"):
            pc = pixel_change(b["screenshot"], v["screenshot"])
            if pc is not None:
                change = f" | pixels changed {pc[0]}%{pc[1]}" + (" -> view these tiles" if pc[0] >= 0.5 else "")
        lines.append(f"{'WORSE' if worse else 'ok   '} {name}"
                     + (f" | worse: {', '.join(worse)}" if worse else "")
                     + (f" | better: {', '.join(better)}" if better else "") + change)
    for name in ver:
        if name not in base:
            lines.append(f"new   {name} (no baseline to compare with)")
    return {"problems": problems, "lines": lines}


def cmd_compare(a):
    res = compare_dirs(a.baseline, a.verify)
    for line in res["lines"]:
        print(line)
    if res["problems"]:
        print(f"\n{len(res['problems'])} problem(s): fix them, or explain each increase in the page note.")
        for p in res["problems"]:
            print(f"  - {p}")
        sys.exit(1)
    print("\nNo count went up.")


def cmd_table(a):
    root = Path(a.shots)
    cols = list(SHORT)
    print("| page | combination | " + " | ".join(SHORT[c] for c in cols) + " | slowest api | cls |")
    print("|---|---|" + "---|" * len(cols) + "---|---|")
    for page_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for name, r in reports(page_dir).items():
            s = r.get("summary", {})
            cells = [str(num(s.get(c))) if num(s.get(c)) else "" for c in cols]
            print(f"| {page_dir.name} | {name[:-5]} | " + " | ".join(cells) + f" | {s.get('slowestApiMs') or ''} | {s.get('cls') if s.get('cls') is not None else ''} |")


def findings(r):
    """Yield (kind, key, detail) for every finding in one report that can recur across pages."""
    for v in r.get("a11y") or []:
        for t in v.get("targets", [])[:3]:
            yield "axe:" + v["id"], t, f"{v['impact']}: {v['help']}"
    for o in (r.get("overflow") or {}).get("offenders") or []:
        yield "overflow", o["selector"], f"right edge {o['right']} > {o['viewport']}"
    for c in r.get("lowContrast") or []:
        yield "contrast", f"{c['selector']} {c['color']}", f"{c['ratio']}:1 < {c['need']}:1"
    for t in r.get("smallTargets") or []:
        yield "small-target", t["selector"], f"{t['width']}x{t['height']}"
    for t in r.get("badText") or []:
        yield "bad-text", t["selector"], t["match"]
    for k in r.get("rawKeys") or []:
        yield "raw-key", k["text"], k["kind"]
    for e in r.get("consoleErrors") or []:
        yield "console", e["text"][:120], ""
    for e in r.get("pageErrors") or []:
        yield "page-error", e["message"][:120], ""
    for issue in (r.get("structure") or {}).get("issues") or []:
        yield "structure", issue, ""


def cmd_repeats(a):
    root = Path(a.shots)
    seen = defaultdict(lambda: {"pages": set(), "detail": ""})
    for page_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for r in reports(page_dir).values():
            for kind, key, detail in findings(r):
                e = seen[(kind, key)]
                e["pages"].add(page_dir.name)
                e["detail"] = e["detail"] or detail
    shared = sorted(((k, v) for k, v in seen.items() if len(v["pages"]) >= a.min_pages), key=lambda kv: -len(kv[1]["pages"]))
    if not shared:
        print(f"No finding appears on {a.min_pages} or more pages.")
        return
    print(f"{len(shared)} finding(s) on {a.min_pages}+ pages. Fix each once, in the shared code:\n")
    for (kind, key), v in shared:
        pages = sorted(v["pages"])
        print(f"[{len(pages)} pages] {kind}  {key}  {v['detail']}")
        print(f"    {', '.join(pages[:8])}{' ...' if len(pages) > 8 else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("compare"); s.add_argument("baseline"); s.add_argument("verify")
    s = sub.add_parser("table"); s.add_argument("shots")
    s = sub.add_parser("repeats"); s.add_argument("shots"); s.add_argument("--min-pages", type=int, default=2)
    a = ap.parse_args()
    {"compare": cmd_compare, "table": cmd_table, "repeats": cmd_repeats}[a.cmd](a)


if __name__ == "__main__":
    main()
