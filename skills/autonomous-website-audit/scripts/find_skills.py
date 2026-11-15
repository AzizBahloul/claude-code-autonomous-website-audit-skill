#!/usr/bin/env python3
"""List installed Claude skills that can serve each audit layer (design, security, i18n, a11y),
and build the design-skill roster: every installed design skill assigned to the concern it covers.

    python3 find_skills.py [--category design|security|i18n|a11y|testing] [--project .]
    python3 find_skills.py --roster [--out .website-audit/skills.md]

--roster assigns EVERY installed design skill to one concern (tokens, critique, copy, motion,
mobile, stress test...) so the design layer runs each of them at the step it serves, instead of
picking one. Restyling skills (a new aesthetic, a redesign, logos, images) are listed separately:
they conflict with rule I5 and run only when the user asks for a redesign. Concerns no installed
skill covers are listed with the vetted skill to install from trusted_skills.json.

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


# Design concerns, in the order the design layer meets them. First match wins per skill.
DESIGN_CONCERNS = [
    # (concern, when it runs, name pattern, description pattern)
    ("doctrine", "house rules loaded once before any design work", r"doctrine", r"house rules"),
    ("critique", "heuristic review and adversarial critique of each page", r"design-review|critic|critique|heuristic", r"heuristic|critique|nielsen"),
    ("craft", "visual craft: hierarchy, spacing, typography, polish", r"frontend-design|impeccable|emil|apple-design|ui-ux-pro-max|design-taste-frontend", r"polish|typograph|visual hierarchy"),
    ("tokens", "tokens and the design system: reuse before create", r"token|design-system|governance|migrate-design", r"design tokens|design system"),
    ("components", "component specs and the 8 states", r"design-component|design-code|shadcn|ui-styling|pick-ui-library|figma", r"component spec|shadcn"),
    ("qa-gates", "lint for hard-coded values, visual regression, contrast gates", r"design-qa|visual-regression", r"visual regression|quality gate"),
    ("mobile", "phone and touch behaviour, safe areas, 100vh, tap targets", r"mobile", r"\bmobile\b|touch"),
    ("motion", "animation review: purpose, duration, easing, reduced motion", r"animat|motion", r"animation|motion"),
    ("copy", "UI copy: buttons, errors, empty states", r"ux-writing|copy", r"ui copy|microcopy"),
    ("stress", "worst-case data: long names, empty, huge counts, RTL", r"break-ui|stress", r"worst-case|edge case"),
    ("data-viz", "charts, dashboards, stat tiles", r"dataviz|data-dashboard|chart", r"chart|dashboard"),
    ("performance", "rendering performance and web vitals", r"^performance$|web-vitals|perf", r"web vitals|core web"),
    ("accessibility", "WCAG checks that back the a11y layer", r"a11y|wcag|accessib", r"wcag|accessib"),
]
RESTYLE = r"apply-aesthetic|brutalist|high-end-visual|gpt-taste|minimalist-ui|redesign|stitch-design|logo|brandkit|banner|imagegen|image-to-code|design-screen|slides|favicon|^brand$|^design$"
NOT_WEB_DESIGN = r"swift|remotion|video|autonomous-website-audit|security|owasp"


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
    ap.add_argument("--roster", action="store_true", help="assign every installed design skill to a concern")
    ap.add_argument("--out", default=".website-audit/skills.md")
    a = ap.parse_args()
    if a.roster:
        return roster(Path(a.project).resolve(), Path(a.out))

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


def roster(project, out):
    import json
    seen, rows, restyle = set(), {c: [] for c, _, _, _ in DESIGN_CONCERNS}, []
    for scope, f in skill_files(project):
        fm = frontmatter(f)
        if not fm or "name" not in fm or fm["name"] in seen:
            continue
        seen.add(fm["name"])
        name = fm["name"]
        blob = f"{name} {fm.get('description', '')}".lower()
        if not re.search(CATEGORIES["design"], blob) and not re.search(CATEGORIES["a11y"], blob):
            continue
        if re.search(NOT_WEB_DESIGN, name.lower()):
            continue
        if re.search(RESTYLE, name.lower()):
            restyle.append((name, scope, f))
            continue
        hit = next((c for c, _, npat, _ in DESIGN_CONCERNS if re.search(npat, name.lower())), None) \
            or next((c for c, _, _, dpat in DESIGN_CONCERNS if re.search(dpat, blob)), None)
        if hit:
            rows[hit].append((name, scope, f))
    trusted = {}
    tf = Path(__file__).with_name("trusted_skills.json")
    if tf.exists():
        trusted = json.loads(tf.read_text()).get("skills", {})
    lines = ["# Design skill roster", "",
             "Generated by `find_skills.py --roster`. Every skill below runs in the design layer at its concern's step. "
             "Load a skill the first time its step comes up, then follow it on every page.", "",
             "| Concern | When | Skills |", "|---|---|---|"]
    missing = []
    for concern, when, _, _ in DESIGN_CONCERNS:
        names = ", ".join(f"`{n}` ({sc})" for n, sc, _ in rows[concern])
        if not names:
            missing.append(concern)
        lines.append(f"| {concern} | {when} | {names or '**none installed**'} |")
    lines += ["", "## Restyling skills (only when the user asks for a redesign; rule I5)", ""]
    lines += [f"- `{n}` ({sc})" for n, sc, _ in restyle] or ["- none"]
    lines += ["", "## Missing concerns and vetted installs", ""]
    for concern in missing:
        cands = [k for k, v in trusted.items() if concern in v.get("concerns", [])]
        lines.append(f"- {concern}: " + (", ".join(f"`install_skill.sh {k}` ({trusted[k]['repo']})" for k in cands) if cands else "no vetted skill; the reference file covers it"))
    if not missing:
        lines.append("- none")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    for concern, _, _, _ in DESIGN_CONCERNS:
        print(f"{concern:<14} " + (", ".join(n for n, _, _ in rows[concern]) or "NONE"))
    print(f"restyle-only   {', '.join(n for n, _, _ in restyle) or 'none'}")
    print(f"{sum(len(v) for v in rows.values())} design skill(s) on the roster, {len(missing)} concern(s) uncovered -> {out}")


if __name__ == "__main__":
    main()
