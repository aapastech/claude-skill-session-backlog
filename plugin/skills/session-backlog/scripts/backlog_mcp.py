#!/usr/bin/env python3
"""backlog_mcp.py - MCP server for the Aapastech Session Backlog.

Lets ANY MCP-capable assistant or agent framework (Claude, ChatGPT/Codex,
Cursor, Copilot, Gemini CLI, LangChain/LlamaIndex MCP adapters, your own
API-driven agent...) use the same local backlog that bl.py manages. It is a
thin stdio wrapper: every tool call runs bl.py, so the engine, the files and
the board stay identical whichever client wrote them.

Stdlib only, Python 3.8+, macOS / Windows / Linux. Speaks MCP over stdio
(newline-delimited JSON-RPC 2.0). Data never leaves this computer.

Backlog folder, first match wins:
  1. BACKLOG_DIR environment variable
  2. ~/Documents/Aapastech-Backlog
  3. ~/Documents/Claude-Backlog          (folder name used before v3)
If none exists, the backlog_init tool creates #2 and copies bl.py into it.
"""
import json, os, re, shutil, subprocess, sys, tempfile

VERSION = "3.1.1"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Aapastech-Backlog")
LEGACY_DIRS = [os.path.join(os.path.expanduser("~"), "Documents", "Claude-Backlog")]
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """Local task backlog shared by every AI session on this computer. Conversation memory is not the source of truth; the backlog is.
Every session: backlog_session once with a stable session_id (e.g. "api-20261018-1652"). Every turn after the first: backlog_open before acting.
New asks (including asides like "also", "later", "remind me"): backlog_add BEFORE starting work, then tell the user the captured IDs and ETAs.
Before working a task: backlog_claim (if refused, another live session owns it - leave it). After each meaningful step: backlog_update with log/next/outputs.
Waiting on the user: backlog_update status=waiting_user with next = exactly what you need. Close with backlog_done only after verifying the result.
End every reply that involved work with the one line from backlog_footer. ETAs are wall-clock time for the assistant to finish, e.g. 20m, 2h, 1h30m."""

STR = {"type": "string"}
SID = {"type": "string", "description": "Stable id for this session, reused on every call"}
TID = {"type": "string", "description": "Task id, e.g. T-0042"}
STATUS = {"type": "string", "enum": ["inbox", "ready", "in_progress", "blocked", "waiting_user", "done", "dropped"]}
TASK = {
    "type": "object",
    "properties": {
        "title": STR, "area": STR, "eta": {"type": "string", "description": "e.g. 20m, 2h, 1h30m, 1d"},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]}, "status": STATUS,
        "next": {"type": "string", "description": "Next concrete action"},
        "source": {"type": "string", "description": "Where the ask came from"},
        "parent": {"type": "string", "description": "Existing task id to nest under"},
        "children": {"type": "array", "items": {"type": "object"}, "description": "Sub-tasks, same shape"},
    },
    "required": ["title"],
}

def tool(name, desc, props, required=()):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": list(required)}}

TOOLS = [
    tool("backlog_where", "Show which backlog folder and engine version this server uses.", {}),
    tool("backlog_init", "First-time setup (or change owner/areas): creates the backlog folder if missing.",
         {"owner": {"type": "string", "description": "Name shown on tasks waiting on the user"},
          "areas": {"type": "array", "items": STR, "description": "3-8 areas the user's work falls into"},
          "title": {"type": "string", "description": "Board title"}}),
    tool("backlog_session", "Register this session once; returns its open tasks plus unclaimed/stale ones.",
         {"session_id": SID, "label": {"type": "string", "description": "e.g. 'API · pricing page'"},
          "surface": {"type": "string", "description": "e.g. code, desktop, cloud, api, scheduled"},
          "url": STR}, ["session_id"]),
    tool("backlog_open", "Re-read open tasks for this session plus unclaimed ones. Call at the start of every turn.",
         {"session_id": SID}, ["session_id"]),
    tool("backlog_add", "Capture new tasks (with nested children for sub-tasks) before starting work.",
         {"session_id": SID, "tasks": {"type": "array", "items": TASK}}, ["session_id", "tasks"]),
    tool("backlog_claim", "Claim a task before working on it. Refused if another live session owns it.",
         {"session_id": SID, "id": TID, "eta": STR,
          "force": {"type": "boolean", "description": "Only with the user's explicit OK"}}, ["session_id", "id"]),
    tool("backlog_update", "Record progress: log a decision/finding, next action, outputs, status or ETA change.",
         {"session_id": SID, "id": TID, "status": STATUS, "log": STR, "next": STR,
          "outputs": {"type": "array", "items": STR, "description": "Files or links produced"},
          "eta": STR, "eta_reason": STR, "area": STR, "title": STR,
          "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]}}, ["session_id", "id"]),
    tool("backlog_done", "Close a task after the result is verified. Refused while sub-tasks are open.",
         {"session_id": SID, "id": TID, "log": STR, "outputs": {"type": "array", "items": STR}},
         ["session_id", "id"]),
    tool("backlog_drop", "Abandon a task with a reason (nothing is ever deleted).",
         {"session_id": SID, "id": TID, "reason": STR}, ["session_id", "id", "reason"]),
    tool("backlog_list", "List tasks grouped by area; answer 'what is open' from this, not from memory.",
         {"all": {"type": "boolean", "description": "Include done/dropped"}, "area": STR,
          "session_id": {"type": "string", "description": "Only tasks this session touched"}}),
    tool("backlog_show", "Full JSON of one task including its log.", {"id": TID}, ["id"]),
    tool("backlog_footer", "One-line 'Still open: ...' footer for the end of a reply.",
         {"session_id": SID, "exclude": {"type": "array", "items": STR}}, ["session_id"]),
    tool("backlog_board", "Rebuild the local HTML board and return its file:// link; open=true opens it here.",
         {"open": {"type": "boolean"}}),
]


def engine_version(path):
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'^VERSION = "([\d.]+)"', f.read(), re.M)
        return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    except OSError:
        return (0,)


_checked = set()

def upgrade_engine(root):
    """Same rule as the skill: an older bl.py in the backlog folder is replaced
    by the one shipped next to this server. Only the engine file changes;
    tasks, sessions and config are never touched."""
    if root in _checked:
        return
    _checked.add(root)
    mine, theirs = os.path.join(HERE, "bl.py"), os.path.join(root, "bl.py")
    if os.path.exists(mine) and engine_version(mine) > engine_version(theirs):
        shutil.copyfile(mine, theirs)


def backlog_dir():
    env = os.environ.get("BACKLOG_DIR")
    candidates = [env] if env else [DEFAULT_DIR] + LEGACY_DIRS
    for d in candidates:
        if os.path.exists(os.path.join(d, "bl.py")):
            upgrade_engine(d)
            return d
    return None


def run_bl(root, args, stdin_file=None):
    env = dict(os.environ, BACKLOG_DIR=root, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, os.path.join(root, "bl.py")] + args + ([stdin_file] if stdin_file else [])
    p = subprocess.run(cmd, capture_output=True, env=env, timeout=60)
    out = (p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")).strip()
    return out or "(no output)", p.returncode != 0


def ensure_root():
    root = backlog_dir()
    if root:
        return root
    root = os.environ.get("BACKLOG_DIR") or DEFAULT_DIR
    os.makedirs(root, exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "bl.py"), os.path.join(root, "bl.py"))
    return root


def opt(args, flag, value):
    if value not in (None, "", []):
        args += [flag, str(value)]


def call(name, a):
    if name == "backlog_init":
        root = ensure_root()
        args = ["init"]
        opt(args, "--owner", a.get("owner"))
        if a.get("areas"):
            args += ["--areas", ",".join(a["areas"])]
        opt(args, "--title", a.get("title"))
        return run_bl(root, args)

    root = backlog_dir()
    if not root:
        return ("No backlog on this computer yet. Ask the user for their name and 3-8 work areas, "
                "then call backlog_init."), True
    if name == "backlog_where":
        return run_bl(root, ["where"])

    sid = a.get("session_id")
    if name == "backlog_session":
        args = ["session", "--id", sid]
        opt(args, "--label", a.get("label")); opt(args, "--surface", a.get("surface")); opt(args, "--url", a.get("url"))
        return run_bl(root, args)
    if name == "backlog_open":
        return run_bl(root, ["open", "--session", sid])
    if name == "backlog_add":
        fd, path = tempfile.mkstemp(suffix=".json", prefix="bl-batch-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(a.get("tasks") or [], f, ensure_ascii=False)
            return run_bl(root, ["batch", "--session", sid], stdin_file=path)
        finally:
            os.remove(path)
    if name == "backlog_claim":
        args = ["claim", a["id"], "--session", sid]
        opt(args, "--eta", a.get("eta"))
        if a.get("force"): args.append("--force")
        return run_bl(root, args)
    if name == "backlog_update":
        args = ["update", a["id"], "--session", sid]
        for key, flag in (("status", "--status"), ("log", "--log"), ("eta", "--eta"), ("eta_reason", "--eta-reason"),
                          ("area", "--area"), ("title", "--title"), ("priority", "--priority")):
            opt(args, flag, a.get(key))
        if a.get("next") is not None:
            args += ["--next", a["next"]]
        for o in a.get("outputs") or []:
            args += ["--output", o]
        return run_bl(root, args)
    if name == "backlog_done":
        args = ["done", a["id"], "--session", sid]
        opt(args, "--log", a.get("log"))
        for o in a.get("outputs") or []:
            args += ["--output", o]
        return run_bl(root, args)
    if name == "backlog_drop":
        return run_bl(root, ["drop", a["id"], "--session", sid, "--reason", a["reason"]])
    if name == "backlog_list":
        args = ["list"]
        if a.get("all"): args.append("--all")
        opt(args, "--area", a.get("area")); opt(args, "--session", a.get("session_id"))
        return run_bl(root, args)
    if name == "backlog_show":
        return run_bl(root, ["show", a["id"]])
    if name == "backlog_footer":
        args = ["footer", "--session", sid]
        if a.get("exclude"):
            args += ["--exclude"] + list(a["exclude"])
        return run_bl(root, args)
    if name == "backlog_board":
        return run_bl(root, ["board"] + (["--open"] if a.get("open") else []))
    return f"unknown tool {name}", True


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "aapastech-backlog", "version": VERSION},
                "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        try:
            text, is_error = call(name, params.get("arguments") or {})
        except KeyError as e:
            text, is_error = f"missing required argument {e}", True
        except Exception as e:  # never kill the server over one bad call
            text, is_error = f"{type(e).__name__}: {e}", True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}
    raise LookupError(method)


def send(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main():
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if "id" not in msg:
            continue  # notification (e.g. notifications/initialized): no reply
        try:
            send({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)})
        except LookupError:
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "error": {"code": -32601, "message": f"method not found: {msg.get('method')}"}})


if __name__ == "__main__":
    main()
