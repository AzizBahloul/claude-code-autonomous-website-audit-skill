#!/usr/bin/env python3
"""Project-wide code hygiene: duplicated code, dead code, unused dependencies, complexity and
known-vulnerable packages, frontend and backend.

    python3 hygiene.py [ROOT] [--out .website-audit/hygiene.json] [--md .website-audit/HYGIENE.md]
                       [--tools auto|none|fetch] [--window 6] [--big-file 400]

Built-in checks (no install, always run):
  duplicate blocks   the same run of >= --window normalised lines in two places (copy-paste)
  commented code     runs of >= 3 commented-out lines that parse like code
  debug leftovers    console.log / debugger / print( / var_dump / dd( in source
  big files          source files over --big-file lines (split candidates)
  unused deps        package.json dependencies no source file, config or script mentions

External tools (more exact; JSON output is saved under .website-audit/hygiene/):
  knip (JS/TS unused files, exports, deps; a --production pass finds code only tests keep
  alive), jscpd (duplication), deptry (unused Python deps), vulture (Python dead code),
  radon (Python complexity), npm audit / pip-audit (vulnerable dependencies).
  --tools auto   run only tools already installed (project node_modules/.bin, PATH)   [default]
  --tools fetch  also fetch missing ones with pinned versions via npx / uvx / pipx, outside the
                 project. This downloads packages: say so to the user (rule S5).
  --tools none   built-in checks only

Every result is a CANDIDATE. Before deleting code, grep for its name (dynamic imports,
reflection, string registries, framework conventions), then build and test.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import package_roots, read, rel, walk  # noqa: E402

CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro", ".py", ".php", ".rb", ".go",
            ".java", ".kt", ".cs", ".css", ".scss"}
SKIP = re.compile(r"(\.min\.|\.d\.ts$|(^|/)(migrations|generated|__generated__|vendor|locales?|i18n|fixtures?)/|\.lock$|-lock\.)", re.I)
TEST = re.compile(r"(\.test\.|\.spec\.|(^|/)(tests?|__tests__|e2e|cypress)/)", re.I)
TRIVIAL = re.compile(r"^([{}()\[\];,]+|</?\w+>|else:?|return;?|break;?|pass|end|\*/|/\*\*?|\*|#|//|\"\"\"|''')$")
IMPORT_LINE = re.compile(r"^(import |from \S+ import |export \* from|export \{|const \w+ = require\(|use |using |#include|package )")
CODEY = re.compile(r"(;\s*$|\)\s*\{?\s*$|=\s*[^=]|\breturn\b|\bif\s*\(|\bfor\s*\(|=>|\bdef \w+\(|\bconst |\blet |\bimport )")
DEBUG = {
    "js": re.compile(r"^\s*(console\.(log|debug|trace|dir)\(|debugger;?)"),
    "py": re.compile(r"^\s*(print\(|breakpoint\(\)|import pdb|pdb\.set_trace)"),
    "php": re.compile(r"^\s*(var_dump\(|print_r\(|dd\(|dump\()"),
}
# Loaded by a framework, a compiler or a config file rather than imported by source.
IMPLICIT_DEP = re.compile(r"^(react|react-dom|next|vue|nuxt|svelte|@sveltejs/kit|astro|typescript|tailwindcss|postcss|autoprefixer|"
                          r"sass|eslint|prettier|vite|webpack|@babel/core|core-js|regenerator-runtime|tslib)$|(-plugin|-preset|-config|-loader)(-|$)|^eslint-|^@tailwindcss/")
TOOLS = {
    # name: (detect binary, npx/pipx spec, argv after the binary, output parsing hint)
    # Pinned to major versions released well before this skill was vetted. knip: never --fix.
    "knip": ("knip", ("npx", "knip@5"), ["--reporter", "json", "--no-exit-code", "--no-progress"], "js"),
    "knip-production": ("knip", ("npx", "knip@5"), ["--production", "--reporter", "json", "--no-exit-code", "--no-progress"], "js"),
    "jscpd": ("jscpd", ("npx", "jscpd@4"), ["--silent", "--reporters", "json", "--output", "{out}/jscpd", "--ignore",
                                             "**/node_modules/**,**/dist/**,**/build/**,**/.next/**,**/coverage/**,**/.website-audit/**", "."], "any"),
    "deptry": ("deptry", ("pipx", "deptry==0.*"), [".", "--json-output", "{out}/deptry.json"], "py-manifest"),
    "vulture": ("vulture", ("pipx", "vulture==2.*"), [".", "--min-confidence", "80", "--exclude", ".venv,venv,node_modules,migrations,.website-audit"], "py"),
    "radon": ("radon", ("pipx", "radon==6.*"), ["cc", "-j", "-n", "C", "-e", "*/.venv/*,*/venv/*,*/node_modules/*,*/migrations/*", "."], "py"),
    "npm-audit": ("npm", None, ["audit", "--json", "--omit=dev"], "js-lock"),
    "pip-audit": ("pip-audit", ("pipx", "pip-audit==2.*"), ["-f", "json"], "py-req"),
}


def source_files(root):
    for f in walk(root):
        r = rel(f, root)
        if f.suffix in CODE_EXT and not SKIP.search(r):
            yield f, r


def lang(f):
    return "py" if f.suffix == ".py" else "php" if f.suffix == ".php" else "js" if f.suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro"} else None


def norm_line(ln):
    ln = re.sub(r"(['\"`]).*?\1", "S", ln.strip())
    return re.sub(r"\s+", " ", ln)


def duplicate_blocks(files, window):
    """Hash every run of `window` meaningful lines; runs seen in more than one place are copies."""
    seen = defaultdict(list)
    for f, r in files:
        if TEST.search(r) or f.suffix in (".css", ".scss"):
            continue
        lines = read(f).splitlines()
        meaningful = [(i + 1, norm_line(ln)) for i, ln in enumerate(lines)
                      if norm_line(ln) and not TRIVIAL.match(norm_line(ln)) and not IMPORT_LINE.match(ln.strip())]
        for k in range(len(meaningful) - window + 1):
            chunk = meaningful[k:k + window]
            if sum(len(t) for _, t in chunk) < window * 12:
                continue  # short lines (closing tags, one-word props) collide by accident
            h = hashlib.sha1("\n".join(t for _, t in chunk).encode()).hexdigest()
            seen[h].append((r, chunk[0][0], chunk[-1][0]))
    # Merge overlapping windows from the same pair of places into one range per location.
    clusters = defaultdict(lambda: defaultdict(list))
    for locs in seen.values():
        uniq = sorted(set(locs))
        if len(uniq) < 2 or len({(r, a // (window * 2)) for r, a, _ in uniq}) < 2:
            continue
        key = tuple(sorted({r for r, _, _ in uniq}))
        for r, a, b in uniq:
            clusters[key][r].append((a, b))
    out = []
    for files_key, per_file in clusters.items():
        ranges = {}
        for r, spans in per_file.items():
            spans.sort()
            merged = [list(spans[0])]
            for a, b in spans[1:]:
                if a <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            ranges[r] = [f"{a}-{b}" for a, b in merged]
        size = max(int(x.split("-")[1]) - int(x.split("-")[0]) + 1 for v in ranges.values() for x in v)
        out.append({"files": list(files_key), "ranges": ranges, "lines": size})
    return sorted(out, key=lambda d: (-d["lines"], d["files"]))


def commented_code(files):
    out = []
    for f, r in files:
        prefix = "#" if f.suffix in (".py", ".rb") else "//"
        run_start, run = None, 0
        lines = read(f).splitlines() + [""]
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            is_code_comment = s.startswith(prefix) and CODEY.search(s[len(prefix):]) and not re.match(r"^(#|//)\s*(eslint|ts-|noqa|type:|pylint|prettier|@ts|TODO|FIXME|NOTE|http)", s, re.I)
            if is_code_comment:
                run_start = run_start or i
                run += 1
            else:
                if run >= 3:
                    out.append({"file": r, "lines": f"{run_start}-{i - 1}", "count": run})
                run_start, run = None, 0
    return out


def debug_leftovers(files):
    out = []
    for f, r in files:
        lg = lang(f)
        if not lg or TEST.search(r) or re.search(r"(^|/)(scripts?|bin|cli|tools?)/", r):
            continue
        for i, ln in enumerate(read(f).splitlines(), 1):
            if DEBUG[lg].match(ln):
                out.append({"file": r, "line": i, "text": ln.strip()[:100]})
    return out


def big_files(files, limit):
    out = []
    for f, r in files:
        n = read(f).count("\n") + 1
        if n > limit and not TEST.search(r):
            out.append({"file": r, "lines": n})
    return sorted(out, key=lambda d: -d["lines"])


def unused_deps(root):
    """Dependencies of each package.json that nothing in that package mentions."""
    out = []
    for pkg, _ in package_roots(root):
        try:
            data = json.loads(read(pkg / "package.json"))
        except json.JSONDecodeError:
            continue
        deps = list((data.get("dependencies") or {}).keys())
        if not deps:
            continue
        blob_parts = [json.dumps(data.get("scripts", {}))]
        for f in walk(pkg):
            # package.json and lockfiles list every dependency, so they prove nothing.
            if f.name == "package.json" or "lock" in f.name.lower():
                continue
            if f.suffix in CODE_EXT or f.name.endswith((".json", ".cjs", ".mjs", ".yml", ".yaml", ".toml", ".html")):
                blob_parts.append(read(f))
        blob = "\n".join(blob_parts)
        for d in deps:
            if d.startswith("@types/") or IMPLICIT_DEP.search(d):
                continue
            pat = re.escape(d)
            if not re.search(rf"['\"`]{pat}(/[^'\"`]*)?['\"`]|\b{pat}\b", blob):
                out.append({"package": rel(pkg / "package.json", root), "dependency": d})
    return out


def js_package_dirs(root):
    """Top-level JS packages: the root if it has a package.json, else each outermost package."""
    if (root / "package.json").exists():
        return [root]
    dirs = sorted({p for p, _ in package_roots(root)}, key=lambda d: len(d.parts))
    outer = []
    for d in dirs:
        if not any(d.is_relative_to(o) for o in outer):
            outer.append(d)
    return outer[:10]


def run_tools(root, out_dir, mode):
    results = []
    for name, (_, _, _, scope) in TOOLS.items():
        if scope.startswith("js"):
            pkgs = js_package_dirs(root)
            if not pkgs:
                results.append({"tool": name, "status": "not-applicable"})
            for pkg in pkgs:
                r = run_tool(name, pkg, out_dir, mode, label=rel(pkg, root) or ".")
                results.append(r)
        else:
            results.append(run_tool(name, root, out_dir, mode))
    return results


def run_tool(name, root, out_dir, mode, label=None):
    binary, fetch, argv, scope = TOOLS[name]
    has_js = (root / "package.json").exists()
    has_py = any(root.glob("*.py")) or any(root.glob("*/*.py")) or (root / "pyproject.toml").exists() or (root / "requirements.txt").exists()
    if (scope.startswith("js") and not has_js) or (scope.startswith("py") and not has_py):
        return {"tool": name, "status": "not-applicable"}
    if scope == "js-lock" and not (root / "package-lock.json").exists():
        return {"tool": name, "status": "not-applicable", "reason": "no package-lock.json (use the project's own audit: pnpm audit / yarn npm audit)"}
    if scope == "py-manifest" and not ((root / "pyproject.toml").exists() or list(root.glob("requirements*.txt"))):
        return {"tool": name, "status": "not-applicable", "reason": "no pyproject.toml or requirements*.txt at the root"}
    if scope == "py-req" and not (root / "requirements.txt").exists():
        return {"tool": name, "status": "not-applicable", "reason": "no requirements.txt (run pip-audit inside the project's venv)"}
    local = root / "node_modules" / ".bin" / binary
    if local.exists():
        cmd = [str(local)]
    elif shutil.which(binary):
        cmd = [binary]
    elif mode == "fetch" and fetch:
        runner, spec = fetch
        if runner == "npx" and not shutil.which("npx"):
            return {"tool": name, "status": "skipped", "reason": "npx not installed"}
        if runner == "npx":
            cmd = ["npx", "--yes", spec]
        elif shutil.which("uvx"):
            cmd = ["uvx", "--from", spec, binary]
        elif shutil.which("pipx"):
            cmd = ["pipx", "run", "--spec", spec, binary]
        else:
            return {"tool": name, "status": "skipped", "reason": "neither uvx nor pipx is installed"}
    else:
        return {"tool": name, "status": "skipped", "reason": "not installed (re-run with --tools fetch to fetch a pinned copy)"}
    if name == "pip-audit":
        argv = argv + ["-r", "requirements.txt"]
    tag = name if not label or label == "." else f"{name}-{re.sub(r'[^A-Za-z0-9._-]+', '_', label)}"
    tool_out = out_dir / tag
    tool_out.mkdir(parents=True, exist_ok=True)
    full = cmd + [a.replace("{out}", str(tool_out)) for a in argv]
    try:
        p = subprocess.run(full, cwd=root, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"tool": name, "status": "timeout", "command": " ".join(full)}
    raw = out_dir / f"{tag}.out"
    raw.write_text(p.stdout + ("\n--- stderr ---\n" + p.stderr if p.stderr else ""))
    return {"tool": name if not label or label == "." else f"{name} ({label})", "status": "ran", "exit": p.returncode,
            "command": " ".join(full), "cwd": str(root), "output": str(raw), "summary": summarize_tool(name, p.stdout, tool_out)}


def summarize_tool(name, stdout, out_dir):
    try:
        if name in ("knip", "knip-production"):
            d = json.loads(stdout)
            files = d.get("files", [])
            issues = d.get("issues", [])
            count = lambda k: sum(len(i.get(k) or []) for i in issues)
            return {"unused_files": len(files), "unused_exports": count("exports"), "unused_types": count("types"),
                    "unused_dependencies": count("dependencies"), "unlisted": count("unlisted")}
        if name == "jscpd":
            d = json.loads((out_dir / "jscpd" / "jscpd-report.json").read_text())
            t = d.get("statistics", {}).get("total", {})
            return {"clones": t.get("clones"), "duplicated_lines": t.get("duplicatedLines"), "percentage": t.get("percentage")}
        if name == "deptry":
            d = json.loads((out_dir / "deptry.json").read_text())
            by = {}
            for x in d:
                code = x.get("error", {}).get("code")
                by[code] = by.get(code, 0) + 1
            return {"by_code": by, "note": "DEP002 unused, DEP003 transitive, DEP004 dev dependency used in production"}
        if name == "vulture":
            return {"findings": len([ln for ln in stdout.splitlines() if ln.strip()])}
        if name == "radon":
            d = json.loads(stdout or "{}")
            blocks = [b for v in d.values() if isinstance(v, list) for b in v]
            return {"complex_blocks_C_or_worse": len(blocks),
                    "worst": sorted(({"name": b.get("name"), "rank": b.get("rank"), "complexity": b.get("complexity")} for b in blocks),
                                    key=lambda b: -(b["complexity"] or 0))[:10]}
        if name == "npm-audit":
            return json.loads(stdout).get("metadata", {}).get("vulnerabilities")
        if name == "pip-audit":
            d = json.loads(stdout)
            deps = d.get("dependencies", d if isinstance(d, list) else [])
            return {"vulnerable_packages": sum(1 for x in deps if x.get("vulns"))}
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        return {"parse_error": str(e)[:120]}
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out", default=".website-audit/hygiene.json")
    ap.add_argument("--md", default=".website-audit/HYGIENE.md")
    ap.add_argument("--tools", choices=("auto", "none", "fetch"), default="auto")
    ap.add_argument("--window", type=int, default=6)
    ap.add_argument("--big-file", type=int, default=400)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    files = list(source_files(root))
    out_dir = Path(a.out).resolve().parent / "hygiene"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "root": str(root), "source_files": len(files),
        "duplicate_blocks": duplicate_blocks(files, a.window),
        "commented_code": commented_code(files),
        "debug_leftovers": debug_leftovers(files),
        "big_files": big_files(files, a.big_file),
        "unused_dependencies": unused_deps(root),
        "tools": [] if a.tools == "none" else run_tools(root, out_dir, a.tools),
    }
    arch = Path(a.out).with_name("architecture.json")
    if arch.exists():
        A = json.loads(arch.read_text())
        result["from_architecture"] = {"unreferenced_files": A.get("unreferenced", []), "duplicate_names": A.get("duplicate_names", []),
                                       "identical_files": A.get("identical_files", [])}
    eps = Path(a.out).with_name("endpoints.json")
    if eps.exists():
        E = json.loads(eps.read_text())
        result["from_endpoints"] = {"orphan_endpoints": [f"{e['method']} {e['path']} ({e['file']}:{e['line']})" for e in E.get("orphan_endpoints", [])],
                                    "unmatched_calls": [f"{c['method']} {c['url']} ({c['file']}:{c['line']})" for c in E.get("unmatched_calls", [])]}
    Path(a.out).write_text(json.dumps(result, indent=2))
    Path(a.md).write_text(render_md(result))

    print(f"{len(files)} source files: {len(result['duplicate_blocks'])} duplicate block(s), {len(result['commented_code'])} commented-out "
          f"block(s), {len(result['debug_leftovers'])} debug leftover(s), {len(result['big_files'])} big file(s), "
          f"{len(result['unused_dependencies'])} unused dependency candidate(s)")
    for t in result["tools"]:
        print(f"  {t['tool']:<10} {t['status']}" + (f"  {t.get('summary')}" if t.get("summary") else f"  {t.get('reason', '')}"))
    print(f"-> {a.out} and {a.md}")


def render_md(r):
    L = ["# Code hygiene", "", "Generated by `hygiene.py`. Every entry is a candidate: grep, build and test before deleting.", ""]
    fa = r.get("from_architecture", {})
    fe = r.get("from_endpoints", {})
    L += ["## Dead code", "", "### Unreferenced files (architecture.py)", ""]
    L += [f"- `{f}`" for f in fa.get("unreferenced_files", [])] or ["- none (or architecture.py not run)"]
    L += ["", "### Orphan endpoints (endpoints.py)", ""]
    L += [f"- {e}" for e in fe.get("orphan_endpoints", [])] or ["- none (or endpoints.py not run)"]
    L += ["", "### Frontend calls with no backend route (endpoints.py)", ""]
    L += [f"- {e}" for e in fe.get("unmatched_calls", [])] or ["- none"]
    L += ["", "### Commented-out code", ""]
    L += [f"- `{c['file']}` lines {c['lines']} ({c['count']} lines)" for c in r["commented_code"]] or ["- none"]
    L += ["", "### Unused dependencies (approximate)", ""]
    L += [f"- `{d['dependency']}` in `{d['package']}`" for d in r["unused_dependencies"]] or ["- none"]
    L += ["", "## Duplication and reuse", "", "### Repeated code blocks", ""]
    for d in r["duplicate_blocks"][:60]:
        L.append(f"- {d['lines']} lines: " + "; ".join(f"`{f}` {', '.join(rs)}" for f, rs in d["ranges"].items()))
    if not r["duplicate_blocks"]:
        L.append("- none")
    L += ["", "### Duplicate components (architecture.py)", ""]
    L += [f"- **{d['core']}**: " + ", ".join(f"`{x}`" for x in d["files"]) for d in fa.get("duplicate_names", [])]
    L += ["- identical: " + ", ".join(f"`{x}`" for x in g) for g in fa.get("identical_files", [])]
    if not fa.get("duplicate_names") and not fa.get("identical_files"):
        L.append("- none")
    L += ["", "## Complexity", "", "### Big files", ""]
    L += [f"- `{b['file']}`: {b['lines']} lines" for b in r["big_files"]] or ["- none"]
    L += ["", "## Debug leftovers", ""]
    L += [f"- `{d['file']}:{d['line']}` {d['text']}" for d in r["debug_leftovers"][:80]] or ["- none"]
    L += ["", "## External tools", ""]
    for t in r["tools"]:
        L.append(f"- **{t['tool']}**: {t['status']}" + (f" (`{t.get('command')}`) -> {t.get('summary')}" if t["status"] == "ran" else f" {t.get('reason', '')}"))
    if not r["tools"]:
        L.append("- not run (--tools none)")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
