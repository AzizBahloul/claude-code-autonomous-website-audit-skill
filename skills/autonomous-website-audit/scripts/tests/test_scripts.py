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
            "--path", "/scroller.html", "--viewports", "phone,desktop", "--keyboard", "--out", cls.shots)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
