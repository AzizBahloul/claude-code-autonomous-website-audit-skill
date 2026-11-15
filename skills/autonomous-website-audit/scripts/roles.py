#!/usr/bin/env python3
"""Find every role and UI-changing account state the code knows about, so the accounts phase
creates one test account per distinct interface instead of auditing everything as admin.

    python3 roles.py [ROOT] [--out .website-audit/roles-detected.json]

Looks for: role enums and choices, role comparisons (`role === 'coach'`, `user.role == "admin"`),
role helpers (`hasRole('x')`, `@Roles('x')`, `@PreAuthorize("hasRole('X')")`), Django flags
(is_staff, is_superuser), permission classes, plan / subscription tiers, route folders named
after a role (`app/(admin)/`, `/dashboard/coach`), and seed or factory files that already create
users of a role (the cheapest way to get an account).

Prints one line per candidate role with how often and where it appears. It is a starting list:
confirm each role in the guards and middleware, then write the matrix to roles.md.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import read, rel, walk  # noqa: E402

EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte", ".py", ".php", ".rb", ".go", ".java", ".kt", ".cs", ".prisma", ".sql"}
ROLE_WORD = r"(?:role|roles|user_?type|account_?type|usertype|kind|persona|group)"
PATTERNS = [
    ("comparison", re.compile(rf"\b{ROLE_WORD}\b['\"\]]?\s*(?:===?|!==?|==|!=|is)\s*['\"]([A-Za-z][\w-]{{1,30}})['\"]", re.I)),
    ("comparison", re.compile(rf"['\"]([A-Za-z][\w-]{{1,30}})['\"]\s*(?:===?|==)\s*[\w.?]*\b{ROLE_WORD}\b", re.I)),
    ("helper", re.compile(r"\b(?:hasRole|hasAnyRole|isRole|checkRole|requireRole|can|Roles|Role|authorize|role_required|roles_required)\(\s*['\"]([A-Za-z][\w-]{1,30})['\"]")),
    ("helper", re.compile(r"hasRole\(\s*\\?['\"]([A-Za-z][\w-]{1,30})\\?['\"]")),
    ("enum", re.compile(r"^\s*([A-Z][A-Z0-9_]{1,30})\s*=\s*['\"]([a-z][\w-]{1,30})['\"]", re.M)),
    ("flag", re.compile(r"\b(is_staff|is_superuser|is_admin|isAdmin|is_premium|isPremium|is_verified|isVerified|is_minor|isMinor|onboarded|is_onboarded|trial_ends|subscription_status|plan)\b")),
    ("permission", re.compile(r"\bclass\s+(Is\w+|Can\w+|Has\w+)\s*\(\s*(?:permissions\.)?BasePermission")),
]
ENUM_CONTEXT = re.compile(r"(role|type|tier|plan|status|kind|group|persona)", re.I)
ROUTE_DIR = re.compile(r"(?:^|/)(?:\((\w+)\)|(admin|coach|teacher|student|manager|staff|owner|member|scout|player|club|agent|vendor|seller|buyer|partner|parent|moderator|editor|author|doctor|patient|driver|customer|tenant|superadmin))(?:/|$)", re.I)
SEED = re.compile(r"(seed|fixture|factor(y|ies)|populate|demo[_-]?data)", re.I)
NOISE = {"get", "post", "put", "delete", "true", "false", "none", "null", "string", "number", "object", "button", "link",
         "submit", "text", "dialog", "img", "presentation", "navigation", "main", "alert", "status", "tab", "tabpanel",
         "menu", "menuitem", "listbox", "option", "checkbox", "radio", "switch", "region", "banner", "contentinfo",
         "complementary", "search", "form", "group", "list", "listitem", "row", "cell", "grid", "heading", "tooltip",
         "progressbar", "separator", "article", "figure", "application", "log", "marquee", "timer", "user"}


@dataclass
class Role:
    count: int = 0
    kinds: set = field(default_factory=set)
    where: list = field(default_factory=list)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out", default=".website-audit/roles-detected.json")
    a = ap.parse_args()
    root = Path(a.root).resolve()

    roles: defaultdict[str, Role] = defaultdict(Role)
    flags = defaultdict(list)
    seeds = []
    for f in walk(root):
        r = rel(f, root)
        if f.suffix not in EXT or re.search(r"(\.test\.|\.spec\.|\.d\.ts$|/locales?/)", r):
            continue
        text = read(f)
        is_seed = bool(SEED.search(r))
        for kind, rx in PATTERNS:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                if kind == "flag":
                    flags[m.group(1)].append(f"{r}:{line}")
                    continue
                if kind == "enum":
                    window = text[max(0, m.start() - 400):m.start()]
                    if not ENUM_CONTEXT.search("\n".join(window.splitlines()[-8:])):
                        continue
                    name = m.group(2)
                elif kind == "permission":
                    name = m.group(1)
                else:
                    name = m.group(1)
                if name.lower() in NOISE:
                    continue
                key = name.lower() if kind != "permission" else name
                roles[key].count += 1
                roles[key].kinds.add(kind)
                if len(roles[key].where) < 6:
                    roles[key].where.append(f"{r}:{line}")
                if is_seed:
                    seeds.append({"role": key, "where": f"{r}:{line}"})
        m = ROUTE_DIR.search(r)
        if m and f.stem in ("page", "+page", "index", "layout", "route", "views", "urls"):
            name = (m.group(1) or m.group(2)).lower()
            roles[name].count += 1
            roles[name].kinds.add("route-folder")
            if len(roles[name].where) < 6:
                roles[name].where.append(r)

    ranked = sorted(roles.items(), key=lambda kv: (-len(kv[1].kinds), -kv[1].count, kv[0]))
    result = {
        "roles": [{"role": k, "count": v.count, "kinds": sorted(v.kinds), "where": v.where} for k, v in ranked],
        "state_flags": {k: v[:6] for k, v in sorted(flags.items())},
        "seed_files_creating_roles": seeds[:50],
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"{len(ranked)} candidate role(s), {len(flags)} state flag(s) -> {out}")
    for k, v in ranked[:40]:
        print(f"  {k:<24} x{v.count:<4} [{', '.join(sorted(v.kinds))}]  {v.where[0]}")
    for k, v in sorted(flags.items()):
        print(f"  state: {k:<22} x{len(v):<4} {v[0]}")
    if seeds:
        print(f"  {len(seeds)} seed/fixture line(s) already create role users: see seed_files_creating_roles")


if __name__ == "__main__":
    main()
