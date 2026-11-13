#!/usr/bin/env python3
"""List installed Claude skills that can serve each audit layer (design, security, i18n, a11y).

    python3 find_skills.py [--category design|security|i18n|a11y|testing] [--project .]

Scans the project's .claude/skills, ~/.claude/skills, and installed plugin skills under
~/.claude/plugins. Reads only SKILL.md frontmatter. The session's own skill list is also
authoritative: a skill shown there is usable even if this script misses it.
"""
import argparse
import re
from pathlib import Path

CATEGORIES = {
    "design": r"\b(design|ui|ux|frontend|front-end|visual|typograph|tailwind|css|layout|polish|aesthetic)",
    "security": r"\b(security|owasp|vulnerab|xss|csrf|injection|threat|auth[nz]?|secrets?)\b",
    "i18n": r"\b(i18n|l10n|translat|locali[sz]|internationali[sz])",
    "a11y": r"\b(a11y|accessib|wcag|aria|screen reader|contrast)",
    "testing": r"\b(playwright|e2e|end-to-end|webapp-testing|browser test)",
}


def frontmatter(path):
    text = path.read_text(errors="ignore")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    fm = {}
    for key in ("name", "description", "when_to_use"):
        km = re.search(rf"^{key}:\s*(.*(?:\n[ \t]+.*)*)", m.group(1), re.M)
        if km:
            fm[key] = re.sub(r"\s+", " ", km.group(1)).strip().strip("'\"|>")
    return fm


def skill_files(project):
    home = Path.home() / ".claude"
    roots = [("project", project / ".claude" / "skills"), ("personal", home / "skills")]
    for scope, base in roots:
        if base.is_dir():
            for f in sorted(base.glob("*/SKILL.md")):
                yield scope, f
    plugins = home / "plugins" / "cache"
    if plugins.is_dir():
        for f in sorted(plugins.rglob("SKILL.md")):
            if "node_modules" not in f.parts:
                yield "plugin", f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--category", choices=sorted(CATEGORIES))
    ap.add_argument("--project", default=".")
    a = ap.parse_args()

    seen, found = set(), {c: [] for c in CATEGORIES}
    for scope, f in skill_files(Path(a.project).resolve()):
        fm = frontmatter(f)
        if not fm or "name" not in fm or fm["name"] in seen:
            continue
        seen.add(fm["name"])
        blob = f"{fm['name']} {fm.get('description', '')} {fm.get('when_to_use', '')}".lower()
        for cat, pat in CATEGORIES.items():
            if re.search(pat, blob):
                found[cat].append((fm["name"], scope, f, fm.get("description", "")))

    cats = [a.category] if a.category else list(CATEGORIES)
    for cat in cats:
        items = found[cat]
        print(f"== {cat}: {len(items)} skill(s)")
        for name, scope, f, desc in items:
            print(f"  {name}  [{scope}]  {f}")
            if desc:
                print(f"      {desc[:160]}{'...' if len(desc) > 160 else ''}")
        if not items:
            print("  none installed")


if __name__ == "__main__":
    main()
