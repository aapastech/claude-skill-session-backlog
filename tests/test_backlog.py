"""Stdlib tests for the Aapastech Session Backlog engine and MCP server.

Run from the repo root:  python3 -m unittest discover -s tests -v
(Windows: py -3 -m unittest discover -s tests -v)
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "skills", "session-backlog", "scripts")
BL = os.path.abspath(os.path.join(SCRIPTS, "bl.py"))
MCP = os.path.abspath(os.path.join(SCRIPTS, "backlog_mcp.py"))
import re as _re
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "skills", "session-backlog", "scripts", "bl.py"), encoding="utf-8") as _f:
    ENGINE_VERSION = _re.search(r'^VERSION = "([\d.]+)"', _f.read(), _re.M).group(1)
TITLE = "Epic A — hardening → BE · café"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bl-test-")
        shutil.copyfile(BL, os.path.join(self.root, "bl.py"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def bl(self, *args, env=None, stdin=None):
        e = dict(os.environ, BACKLOG_DIR=self.root)
        e.update(env or {})
        return subprocess.run([sys.executable, os.path.join(self.root, "bl.py")] + list(args),
                              input=stdin, capture_output=True, env=e, timeout=60)

    def task(self, tid):
        with open(os.path.join(self.root, "tasks", f"{tid}.json"), encoding="utf-8") as f:
            return json.load(f)

    def task_files(self):
        return sorted(f for f in os.listdir(os.path.join(self.root, "tasks")) if f.startswith("T-"))


class EngineTests(Base):
    def test_non_utf8_locale_keeps_unicode_and_does_not_crash(self):
        # What a default Windows (cp1252) or latin-1 system looks like to Python
        env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1"}
        r = self.bl("add", "--session", "s1", "--title", TITLE, "--area", "Work", env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(self.task("T-0001")["title"], TITLE)
        r = self.bl("update", "T-0001", "--session", "s1", "--log", "note — ✓", env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        with open(os.path.join(self.root, "board.html"), encoding="utf-8") as f:
            self.assertIn(TITLE, f.read())

    def test_batch_from_stdin_is_utf8(self):
        data = json.dumps([{"title": TITLE, "area": "Work", "children": [{"title": "kid — 1"}]}], ensure_ascii=False)
        r = self.bl("batch", "--session", "s1", stdin=data.encode("utf-8"),
                    env={"PYTHONUTF8": "0", "LC_ALL": "en_US.ISO8859-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.task("T-0001")["title"], TITLE)
        self.assertEqual(self.task("T-0002")["parent"], "T-0001")

    def test_batch_file_with_bom(self):
        path = os.path.join(self.root, "in.json")
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump([{"title": TITLE}], f, ensure_ascii=False)
        r = self.bl("batch", "--session", "s1", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.task("T-0001")["title"], TITLE)

    def test_invalid_input_leaves_no_orphan_task_file(self):
        self.assertNotEqual(self.bl("add", "--session", "s1", "--title", "x", "--eta", "soon").returncode, 0)
        self.assertNotEqual(self.bl("add", "--session", "s1", "--title", "x", "--parent", "T-0099").returncode, 0)
        self.assertEqual(self.task_files(), [])
        self.assertEqual(self.bl("add", "--session", "s1", "--title", "ok").returncode, 0)
        self.assertEqual(self.task_files(), ["T-0001.json"])

    def test_sub_task_in_progress_promotes_ready_parents(self):
        self.bl("add", "--session", "s1", "--title", "epic")
        self.bl("add", "--session", "s1", "--title", "story", "--parent", "T-0001")
        self.bl("add", "--session", "s1", "--title", "step", "--parent", "T-0002")
        self.assertEqual(self.bl("claim", "T-0003", "--session", "s2").returncode, 0)
        for tid in ("T-0001", "T-0002"):
            p = self.task(tid)
            self.assertEqual(p["status"], "in_progress")
            self.assertIsNone(p["claimed_by"])
        self.bl("add", "--session", "s1", "--title", "other epic")
        self.bl("add", "--session", "s1", "--title", "kid", "--parent", "T-0004", "--status", "in_progress")
        self.assertEqual(self.task("T-0004")["status"], "in_progress")
        self.bl("add", "--session", "s1", "--title", "third", "--status", "blocked")
        self.bl("add", "--session", "s1", "--title", "kid2", "--parent", "T-0006")
        self.bl("update", "T-0007", "--session", "s1", "--status", "in_progress")
        self.assertEqual(self.task("T-0006")["status"], "blocked")  # only inbox/ready move

    def test_concurrent_claims_have_exactly_one_winner(self):
        self.bl("add", "--session", "setup", "--title", "shared")
        for round_ in range(8):
            t = self.task("T-0001")
            t.update(status="ready", claimed_by=None, claimed_at=None)
            with open(os.path.join(self.root, "tasks", "T-0001.json"), "w", encoding="utf-8") as f:
                json.dump(t, f)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda s: self.bl("claim", "T-0001", "--session", s), ["A", "B", "C", "D"]))
            winners = [r for r in results if r.returncode == 0]
            self.assertEqual(len(winners), 1, f"round {round_}: {[r.stdout for r in results]}")
        leftovers = [f for f in os.listdir(os.path.join(self.root, "tasks")) if f.endswith(".lock")]
        self.assertEqual(leftovers, [])

    def test_concurrent_updates_lose_no_log_lines(self):
        self.bl("add", "--session", "s", "--title", "busy")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i: self.bl("update", "T-0001", "--session", f"s{i}", "--log", f"line {i}"), range(12)))
        msgs = [e["msg"] for e in self.task("T-0001")["log"]]
        for i in range(12):
            self.assertIn(f"line {i}", msgs)

    def test_stale_lock_is_broken(self):
        self.bl("add", "--session", "s", "--title", "t")
        lock = os.path.join(self.root, "tasks", ".T-0001.lock")
        open(lock, "w").close()
        old = os.path.getmtime(lock) - 120
        os.utime(lock, (old, old))
        r = self.bl("claim", "T-0001", "--session", "s")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(lock))

    def test_done_refused_while_children_open(self):
        self.bl("batch", "--session", "s", stdin=json.dumps([{"title": "p", "children": [{"title": "c"}]}]).encode())
        self.assertNotEqual(self.bl("done", "T-0001", "--session", "s").returncode, 0)
        self.assertEqual(self.bl("done", "T-0002", "--session", "s").returncode, 0)
        self.assertEqual(self.bl("done", "T-0001", "--session", "s").returncode, 0)

    def test_area_rename_updates_config_and_tasks(self):
        self.bl("init", "--owner", "Sam", "--areas", "Work,Claude-Ops")
        self.bl("add", "--session", "s", "--title", "t", "--area", "Claude-Ops")
        r = self.bl("areas", "--rename", "Claude-Ops", "AI-Ops")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.task("T-0001")["area"], "AI-Ops")
        with open(os.path.join(self.root, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["areas"], ["Work", "AI-Ops"])

    def test_board_title_is_configurable_and_brand_neutral(self):
        self.bl("init", "--owner", "Sam", "--title", "Team Board")
        with open(os.path.join(self.root, "board.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('"title": "Team Board"', html)
        self.assertNotIn("Claude", html)


class McpTests(Base):
    def start(self, root=None):
        env = dict(os.environ, BACKLOG_DIR=root or self.root)
        self.proc = subprocess.Popen([sys.executable, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env)
        self.next_id = 0

    def tearDown(self):
        if getattr(self, "proc", None):
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
            self.proc.stdout.close(); self.proc.stderr.close()
        super().tearDown()

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        if notify:
            return None
        return json.loads(self.proc.stdout.readline().decode("utf-8"))

    def tool(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["content"][0]["text"], res["isError"]

    def test_handshake_and_tool_list(self):
        self.start()
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(init["result"]["serverInfo"]["name"], "aapastech-backlog")
        self.assertIn("backlog_open", init["result"]["instructions"])
        self.rpc("notifications/initialized", notify=True)
        names = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertTrue({"backlog_add", "backlog_claim", "backlog_update", "backlog_done", "backlog_footer"} <= names)
        self.assertEqual(self.rpc("nope/method")["error"]["code"], -32601)
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_full_turn_loop_over_mcp(self):
        self.start()
        self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        text, err = self.tool("backlog_session", session_id="api-1", label="API · test", surface="api")
        self.assertFalse(err, text)
        text, err = self.tool("backlog_add", session_id="api-1",
                              tasks=[{"title": TITLE, "area": "Work", "eta": "20m", "children": [{"title": "step — 1"}]}])
        self.assertFalse(err, text)
        self.assertIn("T-0001", text)
        self.assertFalse(self.tool("backlog_claim", session_id="api-1", id="T-0002")[1])
        self.assertTrue(self.tool("backlog_claim", session_id="other", id="T-0002")[1])  # live claim refused
        self.assertFalse(self.tool("backlog_update", session_id="api-1", id="T-0002", log="found ✓",
                                   outputs=["report.md"], next="verify")[1])
        text, err = self.tool("backlog_done", session_id="api-1", id="T-0001")
        self.assertTrue(err, "parent must not close while a child is open")
        self.assertFalse(self.tool("backlog_done", session_id="api-1", id="T-0002", log="verified")[1])
        text, _ = self.tool("backlog_footer", session_id="api-1")
        self.assertIn("Still open: T-0001", text)
        self.assertEqual(self.task("T-0001")["title"], TITLE)
        self.assertIn("report.md", self.task("T-0002")["outputs"])
        text, err = self.tool("backlog_claim", session_id="api-1")
        self.assertTrue(err)
        self.assertIn("missing required argument", text)

    def test_init_creates_backlog_when_missing(self):
        fresh = os.path.join(self.root, "new-home", "Aapastech-Backlog")
        self.start(root=fresh)
        self.rpc("initialize", {})
        text, err = self.tool("backlog_open", session_id="s")
        self.assertTrue(err)
        self.assertIn("backlog_init", text)
        text, err = self.tool("backlog_init", owner="Sam", areas=["Work", "Personal"])
        self.assertFalse(err, text)
        self.assertTrue(os.path.exists(os.path.join(fresh, "bl.py")))
        self.assertIn("owner: Sam", text)

    def test_older_engine_in_backlog_folder_is_upgraded(self):
        target = os.path.join(self.root, "bl.py")
        with open(target, encoding="utf-8") as f:
            src = f.read()
        with open(target, "w", encoding="utf-8") as f:
            f.write(src.replace(f'VERSION = "{ENGINE_VERSION}"', 'VERSION = "2.1.0"'))
        self.start()
        self.rpc("initialize", {})
        text, err = self.tool("backlog_where")
        self.assertFalse(err, text)
        self.assertIn(f"engine {ENGINE_VERSION}", text)


if __name__ == "__main__":
    unittest.main()
