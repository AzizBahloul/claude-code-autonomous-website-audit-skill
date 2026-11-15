#!/usr/bin/env python3
"""End-to-end tests for the audit scripts against a synthetic fixture site.

    python3 scripts/tests/test_scripts.py        # needs Playwright + Chromium (setup.sh)

The fixture in tests/fixture/ has one page with planted defects and clean pages with none.
Every check the crawler claims to make must fire on the defects page and stay silent on the
clean one; progress.py must refuse to mark layers done without their evidence.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
FIXTURE = HERE / "fixture"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(*cmd, cwd=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, cmd))} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen([sys.executable, "-m", "http.server", str(cls.port), "-d", str(FIXTURE), "--bind", "127.0.0.1"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        cls.tmp = Path(tempfile.mkdtemp(prefix="wa-test-"))
        cls.shots = cls.tmp / "shots"
        run("node", SCRIPTS / "crawl.mjs", "capture", "--base", cls.base, "--path", "/defects.html", "--path", "/clean/",
            "--path", "/scroller.html", "--path", "/api.html", "--viewports", "phone,desktop", "--keyboard", "--out", cls.shots)

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def report(self, page, combo):
        return json.loads((self.shots / page / f"anon__{combo}__light.json").read_text())


class CaptureTests(Fixture):
    def test_defects_are_found(self):
        r = self.report("defects.html", "desktop")
        s = r["summary"]
        self.assertTrue(s["overflow"])
        self.assertGreaterEqual(s["lowContrast"], 1)
        self.assertEqual(s["badText"], 1)
        self.assertEqual({k["kind"] for k in r["rawKeys"]}, {"raw-key", "unfilled-placeholder"})
        self.assertEqual(s["brokenImages"], 1)
        self.assertEqual(s["tokensInStorage"], 1)
        self.assertGreaterEqual(s["consoleErrors"], 1)
        ids = {v["id"] for v in r["a11y"]}
        self.assertTrue({"image-alt", "label", "html-has-lang"} <= ids, ids)
        self.assertIn("no visible <h1>", r["structure"]["issues"])
        self.assertTrue(r["structure"]["duplicateIds"])
        self.assertEqual(len(r["keyboard"]["noIndicator"]), 1)
        self.assertIn("No focus ring", r["keyboard"]["noIndicator"][0])
        self.assertEqual([p["text"] for p in r["keyboard"]["pointerOnly"]], ["Clickable div"])

    def test_phone_only_checks(self):
        phone = self.report("defects.html", "phone")
        self.assertTrue(phone["summary"]["missingViewportMeta"])
        self.assertIn("button.tiny", " ".join(t["selector"] for t in phone["smallTargets"]))
        self.assertNotIn("keyboard", phone)  # the probe runs on non-touch widths only
        desktop = self.report("defects.html", "desktop")
        self.assertFalse(desktop["summary"]["missingViewportMeta"])
        self.assertEqual(desktop["smallTargets"], [])

    def test_clean_page_is_silent(self):
        for combo in ("phone", "desktop"):
            s = self.report("clean", combo)["summary"]
            noisy = {k: v for k, v in s.items() if k not in ("status", "apiCalls", "slowestApiMs", "a11yScanned", "cls", "failedNoise") and v}
            self.assertEqual(noisy, {}, f"clean page flagged on {combo}: {noisy}")
            self.assertTrue(s["a11yScanned"])

    def test_inner_scroll_and_console_kinds(self):
        r = self.report("scroller.html", "desktop")
        self.assertEqual([c["selector"] for c in r["scrollContainers"]], ["div#pane"])
        self.assertEqual(r["summary"]["scrollContainers"], 1)
        self.assertEqual(r["summary"]["hydration"], 1)
        out = self.tmp / "expanded"
        run("node", SCRIPTS / "crawl.mjs", "capture", "--base", self.base, "--path", "/scroller.html",
            "--viewports", "desktop", "--expand-scrollers", "--no-axe", "--out", out)
        e = json.loads((out / "scroller.html" / "anon__desktop__light.json").read_text())
        self.assertTrue(e["scrollersExpanded"])
        self.assertEqual(e["summary"]["scrollContainers"], 0)

    def test_aria_snapshot_written_on_keyboard_runs(self):
        r = self.report("clean", "desktop")
        tree = Path(r["ariaSnapshot"]).read_text()
        self.assertIn('heading "Clean page"', tree)
        self.assertIn('button "Do it"', tree)

    def test_tokens_weight_seo_and_waterfall(self):
        r = json.loads((self.shots / "api.html" / "anon__desktop__light.json").read_text())
        self.assertGreater(r["designTokens"]["sampled"], 0)
        self.assertIn("Georgia", r["designTokens"]["fonts"])
        self.assertIn("weight", r["timing"])
        self.assertEqual(r["structure"]["robots"], "noindex")
        calls = r["api"]["calls"]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c["start"] for c in calls), "every API call records its start for waterfall analysis")
        out = run(sys.executable, SCRIPTS / "bench.py", "e2e", self.shots / "api.html" / "anon__desktop__light.json").stdout
        self.assertIn("waterfall depth 2", out)
        out = run(sys.executable, SCRIPTS / "summarize.py", "seo", self.shots).stdout
        self.assertIn("NOINDEX on public page api.html", out)
        out = run(sys.executable, SCRIPTS / "summarize.py", "drift", self.shots).stdout
        self.assertIn("font families:", out)
        self.assertIn("rgb(180, 30, 30)", out)

    def test_screenshots_and_tiles(self):
        shot = self.shots / "clean" / "anon__desktop__light.png"
        self.assertTrue(shot.exists())
        out = self.tmp / "tiles"
        run(sys.executable, SCRIPTS / "tile.py", shot, "--out", out)
        self.assertTrue(list(out.glob("*.tile-01-of-*.png")))


class DiscoverTests(Fixture):
    def test_discover_is_safe_and_finds_untracked(self):
        prog = self.tmp / "progress.json"
        prog.write_text(json.dumps({"layers": ["baseline"], "pages": [
            {"route": "/", "url": "/"}, {"route": "/players/[id]", "url": "/players/1"}, {"route": "/settings", "url": "/settings"}]}))
        out = self.tmp / "disc"
        run("node", SCRIPTS / "crawl.mjs", "discover", "--base", self.base, "--compare", prog, "--out", out)
        rep = json.loads((out / "anon.json").read_text())
        patterns = [p["pattern"] for p in rep["pages"]]
        self.assertNotIn("/logout/", patterns, "followed a logout link")
        self.assertIn("/logout/", [s["path"] for s in rep["skipped"]])
        self.assertEqual(sum(p.startswith("/players/") for p in patterns), 1, "ids were not collapsed")
        self.assertFalse(any(p.endswith(".pdf") for p in patterns))
        self.assertEqual(sorted(rep["untracked"]), ["/clean/", "/defects.html", "/hidden/", "/settings/?tab=billing", "/settings/?tab=profile"])
        hidden = [p for p in rep["pages"] if p["pattern"] == "/hidden/"]
        self.assertEqual(hidden[0]["from"], "(sitemap)", "sitemap page on the production host was not mapped to the local base")


class ProgressTests(Fixture):
    def setUp(self):
        self.state = self.tmp / f"state-{self._testMethodName}"
        (self.state).mkdir()
        inv = self.state / "inventory.json"
        inv.write_text(json.dumps({"pages": [{"route": "/clean/", "file": "clean/index.html"}]}))
        self.env = {"WEBSITE_AUDIT_DIR": str(self.state)}
        self.p(["init", "--inventory", str(inv)])
        # Page-gate tests start past the project phases; ProjectTests covers those.
        prog = self.state / "progress.json"
        state = json.loads(prog.read_text())
        state["project"]["status"] = "done"
        state["project"]["layers"] = {k: "done" for k in state["project"]["layers"]}
        prog.write_text(json.dumps(state))
        self.p(["start", "/clean/"])

    def p(self, args, check=True):
        return run(sys.executable, SCRIPTS / "progress.py", *args, env=self.env, check=check)

    def test_done_needs_note_and_evidence(self):
        r = self.p(["done", "/clean/", "baseline"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--note is required", r.stderr)
        r = self.p(["done", "/clean/", "baseline", "--note", "captured the full matrix"], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no capture reports", r.stderr)
        # Provide the evidence: captures and tiles.
        shutil.copytree(self.shots / "clean", self.state / "shots/baseline/clean")
        (self.state / "tiles/baseline/clean").mkdir(parents=True)
        (self.state / "tiles/baseline/clean/x.tile-01-of-01.png").write_bytes(b"x")
        self.p(["done", "/clean/", "baseline", "--note", "captured the full matrix, 0 findings"])
        r = self.p(["done", "/clean/", "design", "--note", "skipping ahead is refused"], check=False)
        self.assertIn("fixed order", r.stderr)

    def test_verify_compares_with_baseline(self):
        base = self.state / "shots/baseline/clean"
        shutil.copytree(self.shots / "clean", base)
        ver = self.state / "shots/verify/clean"
        shutil.copytree(self.shots / "clean", ver)
        sys.path.insert(0, str(SCRIPTS))
        from summarize import compare_dirs
        self.assertEqual(compare_dirs(base, ver)["problems"], [])
        f = ver / "anon__desktop__light.json"
        r = json.loads(f.read_text())
        r["summary"]["consoleErrors"] = 3
        f.write_text(json.dumps(r))
        problems = compare_dirs(base, ver)["problems"]
        self.assertTrue(any("consoleErrors 0->3" in x for x in problems), problems)
        (ver / "anon__phone__light.json").unlink()
        self.assertTrue(any("not re-captured" in x for x in compare_dirs(base, ver)["problems"]))

    def test_findings_block_their_layer(self):
        shutil.copytree(self.shots / "clean", self.state / "shots/baseline/clean")
        (self.state / "tiles/baseline/clean").mkdir(parents=True)
        (self.state / "tiles/baseline/clean/x.tile-01-of-01.png").write_bytes(b"x")
        out = self.p(["finding", "/clean/", "baseline", "high", "Total shows NaN", "--repro", "1. open /clean/ 2. see NaN"]).stdout
        self.assertIn("F-001", out)
        r = self.p(["done", "/clean/", "baseline", "--note", "captured the full matrix"], check=False)
        self.assertIn("open finding(s) F-001", r.stderr)
        r = self.p(["resolve", "F-001", "fixed"], check=False)
        self.assertNotEqual(r.returncode, 0, "fixed without --how/--test must be refused")
        self.p(["resolve", "F-001", "fixed", "--how", "guard formatter", "--test", "stats.test.ts"])
        self.p(["done", "/clean/", "baseline", "--note", "captured the full matrix"])
        self.assertIn("F-001", (self.state / "PROGRESS.md").read_text())

    def test_circuit_breaker(self):
        out = ""
        for i in (1, 2):
            self.p(["finding", "/clean/", "functionality", "medium", f"bug {i}"])
            out = self.p(["resolve", f"F-00{i}", "reverted", "--how", "broke the build"]).stdout
        self.assertIn("CIRCUIT BREAKER", out)

    def test_baseline_refuses_failed_capture(self):
        base = self.state / "shots/baseline/clean"
        shutil.copytree(self.shots / "clean", base)
        (self.state / "tiles/baseline/clean").mkdir(parents=True)
        (self.state / "tiles/baseline/clean/x.tile-01-of-01.png").write_bytes(b"x")
        f = base / "anon__phone__light.json"
        r = json.loads(f.read_text())
        r["navigationError"] = "net::ERR_CONNECTION_REFUSED"
        f.write_text(json.dumps(r))
        res = self.p(["done", "/clean/", "baseline", "--note", "captured the full matrix"], check=False)
        self.assertIn("navigation failed", res.stderr)

    def test_forced_gate_is_recorded(self):
        self.p(["done", "/clean/", "baseline", "--note", "no captures possible: page is a redirect", "--force"])
        state = json.loads((self.state / "progress.json").read_text())
        self.assertTrue(state["pages"][0]["notes"][0]["forced"])
        self.assertIn("FORCED", (self.state / "PROGRESS.md").read_text())

    def test_legacy_state_keeps_its_layers(self):
        prog = self.state / "progress.json"
        state = json.loads(prog.read_text())
        old = ["baseline", "code", "functionality", "design", "security", "i18n", "verify"]
        state["layers"] = old
        for pg in state["pages"]:
            pg["layers"] = {k: "pending" for k in old}
        prog.write_text(json.dumps(state))
        self.p(["add", "/new"])
        state = json.loads(prog.read_text())
        self.assertEqual(list(state["pages"][1]["layers"]), old)

    def test_slug_matches_crawler(self):
        self.p(["set-url", "/clean/", "/settings?tab=billing"])
        self.assertEqual(self.p(["slug", "/clean/"]).stdout.strip(), "settings_tab_billing")


APP = HERE / "fixture-app"


class ProjectTests(unittest.TestCase):
    """architecture.py, endpoints.py, hygiene.py, roles.py, bench.py and the @project gates,
    against fixture-app/: a Next.js frontend plus a FastAPI backend with planted defects."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wa-proj-"))
        cls.s = cls.tmp / ".website-audit"
        cls.s.mkdir()
        run(sys.executable, SCRIPTS / "inventory.py", ".", "--out", cls.s / "inventory.json", cwd=APP)
        run(sys.executable, SCRIPTS / "architecture.py", ".", "--inventory", cls.s / "inventory.json",
            "--out", cls.s / "architecture.json", "--md", cls.s / "ARCHITECTURE.md", cwd=APP)
        run(sys.executable, SCRIPTS / "endpoints.py", ".", "--inventory", cls.s / "inventory.json",
            "--architecture", cls.s / "architecture.json", "--out", cls.s / "endpoints.json", "--md", cls.s / "ENDPOINTS.md", cwd=APP)
        run(sys.executable, SCRIPTS / "hygiene.py", ".", "--tools", "none", "--out", cls.s / "hygiene.json", "--md", cls.s / "HYGIENE.md", cwd=APP)
        cls.arch = json.loads((cls.s / "architecture.json").read_text())
        cls.eps = json.loads((cls.s / "endpoints.json").read_text())
        cls.hyg = json.loads((cls.s / "hygiene.json").read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_architecture_maps_pages_and_reuse(self):
        comps = {c["file"]: c for c in self.arch["components"]}
        self.assertEqual(comps["web/components/ui/Button.tsx"]["kind"], "shared")
        self.assertEqual(comps["web/components/Header.tsx"]["page_count"], 3, "layout components reach every page")
        self.assertEqual(self.arch["unreferenced"], ["web/components/Unused.tsx"])
        self.assertIn("button", [d["core"] for d in self.arch["duplicate_names"]])
        tree = (self.s / "ARCHITECTURE.md").read_text()
        self.assertIn("- `/players`\n  - `/[id]`", tree, "dynamic page must nest under its own parent segment")

    def test_endpoints_link_pages_and_flag_broken_calls(self):
        by_path = {e["path"]: e for e in self.eps["endpoints"]}
        self.assertIn("/api/players/{player_id}", by_path, "FastAPI route with an APIRouter prefix")
        self.assertIn("/players/[id]", by_path["/api/players/{player_id}"]["pages"])
        self.assertEqual([c["url"] for c in self.eps["unmatched_calls"]], ["/api/missing-endpoint"])
        self.assertEqual(self.eps["unmatched_calls"][0]["method"], "POST")
        self.assertIn("/api/stats/legacy", [e["path"] for e in self.eps["orphan_endpoints"]])

    def test_hygiene_finds_copies_dead_code_and_leftovers(self):
        self.assertTrue(any(set(d["files"]) == {"web/app/page.tsx", "web/app/players/[id]/page.tsx"} for d in self.hyg["duplicate_blocks"]))
        self.assertEqual([c["file"] for c in self.hyg["commented_code"]], ["web/app/players/[id]/page.tsx"])
        self.assertEqual([d["file"] for d in self.hyg["debug_leftovers"]], ["web/app/players/[id]/page.tsx"])
        self.assertEqual([d["dependency"] for d in self.hyg["unused_dependencies"]], ["left-pad"], "react/next are implicit, never unused")
        self.assertIn("web/components/Unused.tsx", self.hyg["from_architecture"]["unreferenced_files"])

    def test_roles_found(self):
        out = self.tmp / "roles.json"
        run(sys.executable, SCRIPTS / "roles.py", ".", "--out", out, cwd=APP)
        roles = {r["role"]: r for r in json.loads(out.read_text())["roles"]}
        self.assertTrue({"admin", "coach", "scout"} <= set(roles))
        self.assertIn("route-folder", roles["admin"]["kinds"])

    def test_bench_measures_compares_and_refuses_remote(self):
        port = free_port()
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "-d", str(APP / "static"), "--bind", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            perf = self.tmp / "perf"
            for label in ("a", "b"):
                run(sys.executable, SCRIPTS / "bench.py", "run", "--base", f"http://127.0.0.1:{port}", "--url", "/api/health/",
                    "--n", "3", "--label", label, "--out-dir", perf)
            r = json.loads((perf / "bench-a.json").read_text())["results"][0]
            self.assertEqual(r["status"], [200])
            self.assertIsNotNone(r["p50"])
            r = run(sys.executable, SCRIPTS / "bench.py", "run", "--base", f"http://127.0.0.1:{port}", "--endpoints",
                    self.s / "endpoints.json", "--n", "1", "--label", "c", "--out-dir", perf)
            self.assertIn("SKIP  /api/players/{player_id}", r.stdout, "parameterised paths are skipped, never guessed")
        finally:
            srv.terminate()
            srv.wait()
        r = run(sys.executable, SCRIPTS / "bench.py", "run", "--base", "https://example.com", "--url", "/", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not local", r.stderr)
        slow = json.loads((perf / "bench-a.json").read_text())
        slow["results"][0]["p50"] += 500
        (perf / "bench-slow.json").write_text(json.dumps(slow))
        r = run(sys.executable, SCRIPTS / "bench.py", "compare", perf / "bench-a.json", perf / "bench-slow.json", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SLOWER", r.stdout)

    def test_bench_e2e_finds_waterfall(self):
        rep = self.tmp / "rep.json"
        calls = [{"method": "GET", "url": f"http://x/api/{i}", "status": 200, "ms": 50, "start": 1000 + i * 60} for i in range(4)]
        rep.write_text(json.dumps({"path": "/p", "api": {"calls": calls}, "timing": {}}))
        r = run(sys.executable, SCRIPTS / "bench.py", "e2e", rep)
        self.assertIn("waterfall depth 4", r.stdout)
        self.assertIn("WATERFALL", r.stdout)

    def test_project_phases_gate_pages(self):
        st = self.tmp / "gates"
        shutil.copytree(self.s, st)
        env = {"WEBSITE_AUDIT_DIR": str(st)}
        p = lambda *a, check=True: run(sys.executable, SCRIPTS / "progress.py", *a, env=env, check=check)
        p("init", "--inventory", st / "inventory.json")
        self.assertTrue(p("next").stdout.startswith("@project\tarchitecture"))
        r = p("start", "/", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("project phases first", r.stderr)
        p("done", "@project", "architecture", "--note", "mapped every page and component")
        r = p("done", "@project", "endpoints", "--note", "matched calls to routes", check=False)
        self.assertIn("no measurement", r.stderr)
        (st / "perf").mkdir(exist_ok=True)
        (st / "perf" / "bench-baseline.json").write_text('{"results": []}')
        p("finding", "@project", "endpoints", "high", "POST /api/missing-endpoint has no route")
        r = p("done", "@project", "endpoints", "--note", "matched calls to routes", check=False)
        self.assertIn("open finding", r.stderr)
        p("resolve", "F-001", "fixed", "--how", "removed the dead call")
        p("done", "@project", "endpoints", "--note", "matched calls to routes")
        p("finding", "@project", "hygiene", "medium", "Unused.tsx is dead")
        p("resolve", "F-002", "fixed", "--how", "deleted it")
        r = p("done", "@project", "hygiene", "--note", "removed the dead file", check=False)
        self.assertIn("smoke-capture", r.stderr, "a hygiene fix needs a before/after smoke capture")
        for d in ("before", "after"):
            (st / "shots" / "smoke" / d / "root").mkdir(parents=True)
            (st / "shots" / "smoke" / d / "root" / "x__desktop__light.json").write_text(json.dumps({"summary": {"pageErrors": 0}}))
        p("done", "@project", "hygiene", "--note", "removed the dead file")
        self.assertTrue(p("next").stdout.startswith("/"))
        self.assertIn("Project phases (done)", (st / "PROGRESS.md").read_text())

    def test_affected_pages_from_git_diff(self):
        repo = self.tmp / "aff"
        shutil.copytree(APP, repo)
        g = lambda *a: run("git", "-c", "user.email=t@example.test", "-c", "user.name=t", *a, cwd=repo)
        g("init", "-q")
        g("add", "-A")
        g("commit", "-qm", "init")
        st = self.tmp / "aff-state"
        st.mkdir()
        run(sys.executable, SCRIPTS / "inventory.py", ".", "--out", st / "inv.json", cwd=repo)
        run(sys.executable, SCRIPTS / "architecture.py", ".", "--inventory", st / "inv.json", "--out", st / "a.json", "--md", st / "A.md", cwd=repo)
        with open(repo / "web/components/ui/Button.tsx", "a") as f:
            f.write("// changed\n")
        out = run(sys.executable, SCRIPTS / "architecture.py", "--affected", "HEAD", "--architecture", st / "a.json", cwd=repo).stdout
        self.assertIn("2 page(s) affected", out)
        self.assertNotIn("/admin ", out, "the admin page does not use Button")

    def test_openapi_merge_lists_undocumented(self):
        spec = self.tmp / "openapi.json"
        spec.write_text(json.dumps({"openapi": "3.0.0", "paths": {"/api/players/{player_id}": {"get": {}}, "/api/teams": {"get": {}}}}))
        out = self.tmp / "ep-spec.json"
        run(sys.executable, SCRIPTS / "endpoints.py", ".", "--inventory", self.s / "inventory.json", "--architecture", self.s / "architecture.json",
            "--openapi", spec, "--out", out, "--md", self.tmp / "EP.md", cwd=APP)
        d = json.loads(out.read_text())
        self.assertEqual([u.split(" (")[0] for u in d["undocumented"]], ["GET /api/stats/legacy"])
        self.assertIn("/api/teams", [e["path"] for e in d["endpoints"] if e["framework"] == "openapi"])

    def test_role_matrix(self):
        port = free_port()
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "-d", str(APP / "static"), "--bind", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            eps = self.tmp / "eps-health.json"
            eps.write_text(json.dumps({"endpoints": [{"method": "GET", "path": "/api/health/", "pages": []}]}))
            storage = self.tmp / "coach.json"
            storage.write_text(json.dumps({"cookies": [{"name": "sid", "value": "x", "domain": "127.0.0.1"}]}))
            out = run(sys.executable, SCRIPTS / "bench.py", "matrix", "--base", f"http://127.0.0.1:{port}", "--endpoints", eps,
                      "--role", "anonymous=", "--role", f"coach={storage}", "--out-dir", self.tmp / "perf-m").stdout
        finally:
            srv.terminate()
            srv.wait()
        self.assertIn("| `/api/health/` | 200 | 200 |", out)
        self.assertIn("1 answer 200 to anonymous", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
