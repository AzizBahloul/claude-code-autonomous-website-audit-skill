#!/usr/bin/env python3
"""Compare translation catalogs across locales and against the keys the code uses.

    python3 i18n_check.py [ROOT] [--source en] [--scan-code src] [--out .website-audit/i18n.json]
    python3 i18n_check.py --catalog messages/   # explicit catalog dir instead of auto-detect

Finds catalogs laid out as <dir>/<locale>.json, <dir>/<locale>/<namespace>.json, Rails-style
<locale>.yml, and gettext <locale>/LC_MESSAGES/*.po. For every locale it reports:
  missing       keys in the source locale but absent here
  extra         keys here that the source locale does not have
  empty         keys with an empty value
  untranslated  values identical to the source text (candidates; brand names are fine)
  placeholders  keys whose {var} / {{var}} / %(var)s / %{var} sets differ from the source
With --scan-code it also lists t('key') calls whose key is missing from the source catalog
(heuristic: namespaces from useTranslations('Ns') / getTranslations('Ns') are applied per file).
"""
import argparse
import json
import re
import sys
from pathlib import Path

LOCALE_RE = re.compile(r"^[a-z]{2,3}([-_][A-Za-z]{2,4})?$")
CATALOG_DIRS = {"locales", "locale", "messages", "lang", "langs", "i18n", "translations", "dictionaries", "intl"}
IGNORE = {"node_modules", ".git", ".next", "dist", "build", ".venv", "venv", "vendor", ".website-audit"}
PH_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}|%\((\w+)\)s|%\{(\w+)\}|\{\s*(\w+)\s*(?:,[^{}]*(?:\{[^{}]*\}[^{}]*)*)?\}")


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj, ensure_ascii=False)
    else:
        out[prefix] = "" if obj is None else str(obj)
    return out


def parse_po(path):
    entries, msgid, msgstr, cur = {}, None, None, None
    for line in path.read_text(errors="ignore").splitlines() + [""]:
        line = line.strip()
        if line.startswith("msgid "):
            if msgid:
                entries[msgid] = msgstr or ""
            msgid, msgstr, cur = json.loads(line[6:]), "", "id"
        elif line.startswith("msgstr"):
            msgstr, cur = json.loads(line.split(" ", 1)[1]), "str"
        elif line.startswith('"') and cur:
            if cur == "id":
                msgid += json.loads(line)
            else:
                msgstr += json.loads(line)
        elif not line and msgid:
            entries[msgid] = msgstr or ""
            msgid, cur = None, None
    return entries


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print(f"i18n_check: PyYAML missing, skipping {path} (pip install --user pyyaml)", file=sys.stderr)
        return {}
    data = yaml.safe_load(path.read_text(errors="ignore")) or {}
    # Rails files nest everything under the locale key.
    if isinstance(data, dict) and len(data) == 1 and LOCALE_RE.match(str(next(iter(data)))):
        data = next(iter(data.values()))
    return flatten(data)


def discover(root):
    """Return {locale: {key: value}} from every catalog dir found."""
    catalogs = {}

    def merge(locale, entries, ns=None):
        bucket = catalogs.setdefault(locale.replace("_", "-"), {})
        for k, v in entries.items():
            bucket[f"{ns}.{k}" if ns else k] = v

    dirs = ([root] if root.name in CATALOG_DIRS else []) + [
        d for d in root.rglob("*")
        if d.is_dir() and d.name in CATALOG_DIRS and not (IGNORE & set(d.relative_to(root).parts))
    ]
    for d in dirs:
        for f in sorted(d.iterdir()):
            if f.is_file() and LOCALE_RE.match(f.stem):
                if f.suffix == ".json":
                    merge(f.stem, flatten(json.loads(f.read_text(errors="ignore") or "{}")))
                elif f.suffix in (".yml", ".yaml"):
                    merge(f.stem, load_yaml(f))
            elif f.is_dir() and LOCALE_RE.match(f.name):
                for g in sorted(f.rglob("*")):
                    if g.suffix == ".json":
                        merge(f.name, flatten(json.loads(g.read_text(errors="ignore") or "{}")), ns=g.stem)
                    elif g.suffix == ".po":
                        merge(f.name, parse_po(g))
                    elif g.suffix in (".yml", ".yaml"):
                        merge(f.name, load_yaml(g), ns=g.stem)
    return catalogs


def placeholders(text):
    return {next(x for x in m if x) for m in PH_RE.findall(text)}


def scan_code(code_dir):
    used = {}
    base = Path(code_dir)
    for f in base.rglob("*"):
        if f.suffix not in (".tsx", ".ts", ".jsx", ".js", ".vue", ".svelte") or IGNORE & set(f.relative_to(base).parts):
            continue
        text = f.read_text(errors="ignore")
        ns = re.findall(r"""(?:useTranslations|getTranslations|useTranslation)\(\s*['"]([\w.-]+)['"]""", text)
        prefix = ns[0] + "." if len(set(ns)) == 1 else ""
        for m in re.finditer(r"""(?<![\w.])t\(\s*['"]([\w.-]+)['"]""", text):
            key = m.group(1)
            used.setdefault(prefix + key, []).append(f"{f}:{text[:m.start()].count(chr(10)) + 1}")
    return used


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--catalog", help="catalog directory (skip auto-detection)")
    ap.add_argument("--source", help="source locale (default: en if present, else the largest catalog)")
    ap.add_argument("--scan-code", help="code directory to scan for t('key') calls")
    ap.add_argument("--out", default=".website-audit/i18n.json")
    ap.add_argument("--show", type=int, default=15, help="keys to print per finding type (default 15)")
    a = ap.parse_args()

    catalogs = discover(Path(a.catalog or a.root).resolve())
    if not catalogs:
        sys.exit("i18n_check: no translation catalogs found (pass --catalog DIR); the site may be single-language")
    source = a.source or ("en" if "en" in catalogs else max(catalogs, key=lambda k: len(catalogs[k])))
    if source not in catalogs:
        sys.exit(f"i18n_check: source locale {source!r} not found; have {sorted(catalogs)}")
    src = catalogs[source]

    report = {"source": source, "locales": sorted(catalogs), "per_locale": {}}
    print(f"locales: {', '.join(sorted(catalogs))} (source: {source}, {len(src)} keys)")
    for loc, entries in sorted(catalogs.items()):
        r = {
            "keys": len(entries),
            "empty": sorted(k for k, v in entries.items() if not v.strip()),
        }
        if loc != source:
            r["missing"] = sorted(set(src) - set(entries))
            r["extra"] = sorted(set(entries) - set(src))
            r["untranslated"] = sorted(k for k, v in entries.items() if k in src and v == src[k] and len(v) > 3 and re.search(r"[A-Za-z]{3}", v))
            r["placeholders"] = sorted(k for k, v in entries.items() if k in src and placeholders(v) != placeholders(src[k]))
        report["per_locale"][loc] = r
        print(f"\n== {loc}: {r['keys']} keys")
        for kind in ("missing", "extra", "empty", "untranslated", "placeholders"):
            items = r.get(kind)
            if items:
                print(f"  {kind}: {len(items)}")
                for k in items[: a.show]:
                    print(f"    {k}")
                if len(items) > a.show:
                    print(f"    ... {len(items) - a.show} more in {a.out}")

    if a.scan_code:
        used = scan_code(a.scan_code)
        missing = {k: v for k, v in used.items() if k not in src}
        report["code_keys_missing_from_source"] = missing
        print(f"\n== code: {len(used)} distinct t() keys, {len(missing)} not in the {source} catalog (heuristic)")
        for k, where in list(missing.items())[: a.show]:
            print(f"    {k}  ({where[0]})")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nfull report -> {out}")


if __name__ == "__main__":
    main()
