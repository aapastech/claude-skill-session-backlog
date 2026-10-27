#!/usr/bin/env python3
"""bl.py - Aapastech Session Backlog engine: a local task backlog shared by every
AI session (any assistant, any model) that can run commands on this device.

Storage: one JSON file per task in tasks/, one per session in sessions/.
One-file-per-task plus a per-task lock means parallel sessions never overwrite
each other or both win a claim.
Every write regenerates board.html (visual board) and BOARD.md (text board).
All files are UTF-8 on every OS. Stdlib only, Python 3.8+, macOS / Windows / Linux.
Run with:  python3 <backlog>/bl.py <command> ...   (Windows: py -3 <backlog>\bl.py ...)
"""
import argparse, datetime as dt, json, os, re, secrets, sys, tempfile, time
from contextlib import contextmanager

VERSION = "3.3.1"
ENC = "utf-8"
ROOT = os.environ.get("BACKLOG_DIR") or os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(ROOT, "tasks")
SESS = os.path.join(ROOT, "sessions")
CONFIG = os.path.join(ROOT, "config.json")
DEFAULT_AREAS = ["Work", "Clients", "Product", "Marketing", "Admin", "Personal", "AI-Ops"]
DEFAULT_TITLE = "Aapastech Backlog"
LOCK_STALE_S = 30     # a lock older than this was left by a crashed process
LOCK_WAIT_S = 15      # give up waiting for a busy task after this long
SERVE_FILE = ".serve.json"  # set while `bl serve` runs: {"pid", "port"}

def read_json(path):
    # utf-8-sig also accepts files saved with a BOM (e.g. Windows Notepad)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)

def load_config():
    c = {"owner": "you", "areas": DEFAULT_AREAS, "stale_hours": 24, "title": DEFAULT_TITLE}
    if os.path.exists(CONFIG):
        try:
            c.update(read_json(CONFIG))
        except Exception:
            pass
    return c

CFG = load_config()
TITLE = CFG.get("title") or DEFAULT_TITLE
OWNER = CFG["owner"]
STALE_HOURS = int(CFG.get("stale_hours", 24))
AREAS = CFG["areas"]
STATUSES = ["inbox", "ready", "in_progress", "blocked", "waiting_user", "done", "dropped"]
OPEN = {"inbox", "ready", "in_progress", "blocked", "waiting_user"}
LABEL = {"inbox": "Inbox", "ready": "Ready", "in_progress": "In progress", "blocked": "Blocked",
         "waiting_user": f"Waiting on {OWNER}", "done": "Done", "dropped": "Dropped"}

for d in (TASKS, SESS):
    os.makedirs(d, exist_ok=True)


# ---------- helpers ----------
def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

def iso(t):
    return t.isoformat().replace("+00:00", "Z") if t else None

def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

def parse_eta(s):
    """'20m', '2h', '1h30m', '1d', '2w', '45' (minutes) -> minutes"""
    if s is None:
        return None
    s = str(s).strip().lower()
    if s.isdigit():
        return int(s)
    total, found = 0, False
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([wdhm])", s):
        found = True
        total += float(n) * {"w": 10080, "d": 1440, "h": 60, "m": 1}[u]
    if not found:
        sys.exit(f"bad duration '{s}' - use e.g. 20m, 2h, 1h30m, 1d, 2w")
    return int(round(total))

def parse_when(at=None, within=None):
    """--at 2026-11-15 | 2026-11-15T14:30 (local time; a bare date means 09:00)
    or an ISO time with Z/offset; --in 3d / 2w -> aware UTC datetime."""
    if within:
        return now() + dt.timedelta(minutes=parse_eta(within))
    s = str(at).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            s += "T09:00"
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        sys.exit(f"bad date '{at}' - use YYYY-MM-DD or YYYY-MM-DDTHH:MM (local time)")
    if d.tzinfo is None:
        d = d.astimezone()  # naive = this computer's local time
    return d.astimezone(dt.timezone.utc).replace(microsecond=0)

def local_str(d):
    return d.astimezone().strftime("%a %d %b %H:%M") if d else "?"

def fmt_min(m):
    if m is None:
        return "?"
    m = int(m)
    if m < 60:
        return f"{m}m"
    if m < 1440:
        h, r = divmod(m, 60)
        return f"{h}h{r:02d}m" if r else f"{h}h"
    d, r = divmod(m, 1440)
    return f"{d}d{r // 60}h" if r else f"{d}d"

def norm_area(a):
    if not a:
        return "Inbox"
    for k in AREAS:
        if k.lower() == a.strip().lower():
            return k
    return a.strip()

def replace_file(tmp, path):
    """os.replace with retries: on Windows the target can be briefly held open
    by another reader (a browser, another session), which raises PermissionError."""
    for attempt in range(20):
        try:
            os.replace(tmp, path)  # atomic
            return
        except PermissionError:
            if attempt == 19:
                try: os.remove(tmp)
                except OSError: pass
                raise
            time.sleep(0.05)

def write_text(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding=ENC, newline="\n") as f:
        f.write(text)
    try:
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; keep the usual readable mode
    except OSError:
        pass
    replace_file(tmp, path)

def write_json(path, obj):
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))

def tpath(tid):
    return os.path.join(TASKS, f"{tid}.json")

def load(tid):
    tid = norm_id(tid)
    p = tpath(tid)
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        sys.exit(f"no such task {tid}")
    return read_json(p)

def norm_id(tid):
    tid = tid.upper()
    return tid if tid.startswith("T-") else "T-" + tid.zfill(4)

@contextmanager
def task_lock(tid):
    """Exclusive lock on one task for a read-modify-write. A lock file created
    with O_EXCL works the same on every OS and filesystem; a stale lock left
    by a crashed process is broken after LOCK_STALE_S."""
    lock = os.path.join(TASKS, f".{norm_id(tid)}.lock")
    deadline = time.time() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > LOCK_STALE_S:
                    os.remove(lock)
                    continue
            except OSError:
                continue  # holder released it between our checks
            if time.time() > deadline:
                sys.exit(f"{norm_id(tid)} is busy (another session is writing it) - retry in a moment")
            time.sleep(0.05)
    try:
        yield
    finally:
        try: os.remove(lock)
        except OSError: pass

def all_tasks():
    out = []
    for fn in sorted(os.listdir(TASKS)):
        if fn.endswith(".json") and fn.startswith("T-"):
            try:
                out.append(read_json(os.path.join(TASKS, fn)))
            except Exception:
                pass  # empty placeholder of a task being created right now
    return out

def all_sessions():
    out = {}
    for fn in os.listdir(SESS):
        if fn.endswith(".json"):
            try:
                s = read_json(os.path.join(SESS, fn))
                out[s["id"]] = s
            except Exception:
                pass
    return out

def new_id():
    """Allocate next id atomically (O_EXCL) so two sessions never get the same one."""
    nums = [int(fn[2:6]) for fn in os.listdir(TASKS) if re.match(r"T-\d{4}\.json$", fn)]
    n = max(nums, default=0) + 1
    while True:
        tid = f"T-{n:04d}"
        try:
            fd = os.open(tpath(tid), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return tid
        except FileExistsError:
            n += 1

def touch_session(sid):
    if not sid:
        return
    p = os.path.join(SESS, safe(sid) + ".json")
    s = {}
    if os.path.exists(p):
        try: s = read_json(p)
        except Exception: s = {}
    s.setdefault("id", sid)
    s.setdefault("label", sid)
    s.setdefault("first_seen", iso(now()))
    s["last_seen"] = iso(now())
    write_json(p, s)

def safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)

def log(t, sid, msg):
    t.setdefault("log", []).append({"at": iso(now()), "session": sid, "msg": msg})

def set_eta(t, minutes, sid, reason=None):
    old = t.get("eta_minutes")
    t["eta_minutes"] = minutes
    base = parse_iso(t.get("started_at")) or now()
    if t["status"] != "in_progress":
        base = now()
    t["due_at"] = iso(base + dt.timedelta(minutes=minutes))
    if old is not None and old != minutes:
        log(t, sid, f"ETA {fmt_min(old)} -> {fmt_min(minutes)}" + (f" ({reason})" if reason else ""))

def save(t, rebuild=True):
    t["updated_at"] = iso(now())
    write_json(tpath(t["id"]), t)
    if rebuild:
        build_board()

def promote_parents(t, sid):
    """A sub-task in progress means its parent is in progress too: move any
    inbox/ready ancestor to in_progress (started, not claimed - the session
    owns the sub-task, not the whole parent)."""
    seen, pid = {t["id"]}, t.get("parent")
    while pid and pid not in seen:
        seen.add(pid)
        with task_lock(pid):
            p = load(pid)
            if snoozed(p):
                unschedule(p, sid, f"sub-task {t['id']} started")
                save(p, rebuild=False)
            if p["status"] in ("inbox", "ready"):
                p["status"] = "in_progress"
                p["started_at"] = p.get("started_at") or iso(now())
                set_eta(p, p.get("eta_minutes") or 30, sid)
                log(p, sid, f"in progress: sub-task {t['id']} started")
                save(p, rebuild=False)
        pid = p.get("parent")

def is_stale(t):
    c = parse_iso(t.get("claimed_at"))
    return c is not None and now() - c > dt.timedelta(hours=STALE_HOURS)

def snoozed(t):
    """Scheduled for later: open, but hidden from active work until scheduled_for."""
    s = parse_iso(t.get("scheduled_for"))
    return t["status"] in OPEN and s is not None and s > now()

def returned(t):
    """Came back from a schedule and nobody has touched it since."""
    s = parse_iso(t.get("scheduled_for"))
    u = parse_iso(t.get("updated_at"))
    return t["status"] in OPEN and s is not None and s <= now() and (u is None or u <= s)

def overdue(t):
    d = parse_iso(t.get("due_at"))
    return t["status"] in OPEN and not snoozed(t) and d is not None and now() > d

def short(t):
    eta = fmt_min(t.get("eta_minutes"))
    flag = " OVERDUE" if overdue(t) else ""
    owner = f" @{t['claimed_by']}" if t.get("claimed_by") else ""
    when = ""
    if snoozed(t):
        when = f" [scheduled -> {local_str(parse_iso(t['scheduled_for']))}]"
    elif returned(t):
        when = " [back from schedule]"
    return f"{t['id']} [{LABEL[t['status']]}] ({t['area']}) {t['title']} - ETA {eta}{flag}{owner}{when}"

PRIO = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

def order_key(t):
    """Column order everywhere (board, BOARD.md, bl open): tasks back from a
    schedule first, then new unranked tasks (by priority, so they get noticed),
    then the order the user dragged on the board (rank)."""
    r = t.get("rank")
    return (0 if returned(t) else 1, 0 if r is None else 1, r if r is not None else 0,
            PRIO.get(t.get("priority"), 9), t["id"])

def set_order(ids, sid):
    """Rank the given tasks 1..n in this order (the user's drag on the board, or
    'put X first'). Only these tasks change; ranks compare within a column."""
    ids = [norm_id(i) for i in ids]
    for tid in ids:
        load(tid)  # unknown id -> clear error before anything changes
    for n, tid in enumerate(ids, 1):
        with task_lock(tid):
            t = load(tid)
            if t.get("rank") != n:
                t["rank"] = n
                save(t, rebuild=False)
    build_board()
    touch_session(sid)

def unschedule(t, sid, why):
    if t.get("scheduled_for"):
        t["scheduled_for"] = None
        log(t, sid, f"schedule cleared ({why})")


# ---------- commands ----------
def cmd_session(a):
    p = os.path.join(SESS, safe(a.id) + ".json")
    s = {}
    if os.path.exists(p):
        try: s = read_json(p)
        except Exception: s = {}
    s.update({"id": a.id, "label": a.label or s.get("label") or a.id,
              "surface": a.surface or s.get("surface"), "url": a.url or s.get("url")})
    s.setdefault("first_seen", iso(now()))
    s["last_seen"] = iso(now())
    write_json(p, s)
    build_board()  # labels and chat links live on the board
    print(f"session {a.id} registered")
    open_for(a.id)

def make_task(d, sid):
    status = d.get("status", "ready")
    if status not in STATUSES:
        sys.exit(f"bad status {status}")
    if not str(d.get("title") or "").strip():
        sys.exit("every task needs a title")
    eta = parse_eta(d.get("eta", "30m"))
    par = load(d["parent"]) if d.get("parent") else None
    tid = new_id()
    try:
        return _fill_task(tid, d, sid, status, eta, par)
    except BaseException:
        # never leave an empty placeholder behind (it would burn the id)
        p = tpath(tid)
        if os.path.exists(p) and os.path.getsize(p) == 0:
            os.remove(p)
        raise

def _fill_task(tid, d, sid, status, eta, par):
    t = {
        "id": tid, "title": d["title"].strip(), "area": norm_area(d.get("area")),
        "parent": (d.get("parent") or None), "status": status,
        "priority": d.get("priority", "P2"), "next_action": d.get("next", ""),
        "source": d.get("source", ""), "depends_on": d.get("depends_on", []),
        "created_at": iso(now()), "created_by": sid,
        "claimed_by": None, "claimed_at": None, "started_at": None,
        "completed_at": None, "completed_by": None,
        "eta_minutes": None, "due_at": None, "outputs": [], "log": [],
    }
    if par:
        t["parent"] = par["id"]
        if not d.get("area"):
            t["area"] = par["area"]
    set_eta(t, eta, sid)
    log(t, sid, "created")
    if status == "in_progress":
        t["claimed_by"], t["claimed_at"], t["started_at"] = sid, iso(now()), iso(now())
        set_eta(t, t["eta_minutes"], sid)
    save(t, rebuild=False)
    if status == "in_progress":
        promote_parents(t, sid)
    return t

def cmd_add(a):
    touch_session(a.session)
    t = make_task({"title": a.title, "area": a.area, "parent": a.parent, "status": a.status,
                   "priority": a.priority, "next": a.next, "source": a.source, "eta": a.eta,
                   "depends_on": a.depends_on or []}, a.session)
    build_board()
    print(short(t))

def cmd_batch(a):
    """JSON list of {title, area, eta, parent?, children?: [...], status?, next?, priority?}.
    'parent' may be an existing id; 'children' nest sub-tasks under the new task."""
    touch_session(a.session)
    if a.file == "-":
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    else:
        data = read_json(a.file)
    def walk(items, parent=None):
        for d in items:
            kids = d.pop("children", [])
            if parent and not d.get("parent"):
                d["parent"] = parent
            t = make_task(d, a.session)
            print(("  " if t["parent"] else "") + short(t))
            walk(kids, t["id"])
    walk(data)
    build_board()

def cmd_update(a):
    touch_session(a.session)
    with task_lock(a.id):
        t = _update(a)
    if t["status"] == "in_progress":
        promote_parents(t, a.session)
    build_board()
    print(short(t))

def _update(a):
    t = load(a.id)
    changed = []
    if a.title: t["title"] = a.title; changed.append("title")
    if a.area: t["area"] = norm_area(a.area); changed.append(f"area={t['area']}")
    if a.priority: t["priority"] = a.priority; changed.append(f"priority={a.priority}")
    if a.next is not None: t["next_action"] = a.next
    if a.parent: t["parent"] = load(a.parent)["id"]
    if a.status:
        if a.status not in STATUSES: sys.exit(f"bad status {a.status}")
        if a.status != t["status"]:
            changed.append(f"{t['status']} -> {a.status}")
            t["status"] = a.status
            if a.status == "in_progress" and snoozed(t):
                unschedule(t, a.session, "started early")
            if a.status == "in_progress" and not t.get("started_at"):
                t["started_at"] = iso(now())
                t["claimed_by"], t["claimed_at"] = a.session, iso(now())
                set_eta(t, t["eta_minutes"] or 30, a.session)
    if a.eta: set_eta(t, parse_eta(a.eta), a.session, a.eta_reason)
    for o in a.output or []:
        t["outputs"].append(o); changed.append(f"output: {o}")
    msg = "; ".join(changed)
    if a.log: msg = (msg + " | " if msg else "") + a.log
    if msg: log(t, a.session, msg)
    if t["claimed_by"] == a.session: t["claimed_at"] = iso(now())  # heartbeat
    save(t, rebuild=False)
    return t

def cmd_claim(a):
    touch_session(a.session)
    with task_lock(a.id):
        t = _claim(a)
    promote_parents(t, a.session)
    build_board()
    print(short(t))

def _claim(a):
    t = load(a.id)
    cur = t.get("claimed_by")
    if t["status"] in ("done", "dropped"):
        sys.exit(f"{t['id']} is {t['status']} - nothing to claim")
    if cur and cur != a.session and not is_stale(t) and not a.force:
        sys.exit(f"CLAIMED by {cur} at {t['claimed_at']} (not stale). Pick another task or use --force with the user's OK.")
    if cur and cur != a.session:
        log(t, a.session, f"took over from {cur}" + (" (stale)" if is_stale(t) else " (forced)"))
    if snoozed(t):
        unschedule(t, a.session, "claimed early")
    t["claimed_by"], t["claimed_at"] = a.session, iso(now())
    t["status"] = "in_progress"
    t["started_at"] = t.get("started_at") or iso(now())
    set_eta(t, parse_eta(a.eta) if a.eta else (t.get("eta_minutes") or 30), a.session)
    log(t, a.session, "claimed")
    save(t, rebuild=False)
    return t

def cmd_done(a):
    touch_session(a.session)
    with task_lock(a.id):
        t = _done(a)
    build_board()
    print(short(t))
    if t.get("parent"):
        sib = [k for k in all_tasks() if k.get("parent") == t["parent"] and k["status"] in OPEN]
        if not sib:
            print(f"All sub-tasks of {t['parent']} are closed - verify and close {t['parent']} too.")

def _done(a):
    t = load(a.id)
    kids = [k for k in all_tasks() if k.get("parent") == t["id"] and k["status"] in OPEN]
    if kids and not a.force:
        print(f"REFUSED: {t['id']} still has open sub-tasks - finish or drop them first (or --force):")
        for k in kids: print("  " + short(k))
        sys.exit(1)
    t["status"], t["completed_at"], t["completed_by"] = "done", iso(now()), a.session
    for o in a.output or []:
        t["outputs"].append(o)
    st = parse_iso(t.get("started_at")) or parse_iso(t["created_at"])
    actual = int((now() - st).total_seconds() // 60)
    t["actual_minutes"] = actual
    log(t, a.session, f"DONE (actual {fmt_min(actual)} vs ETA {fmt_min(t.get('eta_minutes'))})"
        + (f" | {a.log}" if a.log else ""))
    t["next_action"] = ""
    save(t, rebuild=False)
    return t

def cmd_drop(a):
    touch_session(a.session)
    with task_lock(a.id):
        t = load(a.id)
        t["status"] = "dropped"
        log(t, a.session, f"DROPPED: {a.reason}")
        save(t, rebuild=False)
    build_board()
    print(short(t))

def cmd_schedule(a):
    """Defer a task (and its open sub-tasks) until a date; it leaves the active
    columns, counts, overdue list and session prompts, then returns by itself."""
    touch_session(a.session)
    if not a.clear and not (a.at or getattr(a, "in")):
        sys.exit("give --at DATE, --in 3d/2w, or --clear")
    root = load(a.id)
    ids = [root["id"]] + [k["id"] for k in descendants(root["id"]) if k["status"] in OPEN]
    when = None if a.clear else parse_when(a.at, getattr(a, "in"))
    if when is not None and when <= now():
        sys.exit("that time is already past - pick a future date")
    for tid in ids:
        with task_lock(tid):
            t = load(tid)
            if t["status"] not in OPEN:
                if tid == root["id"]:
                    sys.exit(f"{tid} is {t['status']} - nothing to schedule")
                continue
            if a.clear:
                unschedule(t, a.session, a.reason or "back to active now")
                set_eta(t, t.get("eta_minutes") or 30, a.session)
            else:
                if t["status"] == "in_progress":
                    t["status"] = "ready"
                    t["claimed_by"] = t["claimed_at"] = None
                t["scheduled_for"], t["scheduled_by"] = iso(when), a.session
                t["schedule_reason"] = a.reason or ""
                t["due_at"] = iso(when + dt.timedelta(minutes=t.get("eta_minutes") or 30))
                log(t, a.session, f"scheduled until {local_str(when)}"
                    + (f" ({a.reason})" if a.reason else "") + (f" with {root['id']}" if tid != root["id"] else ""))
            save(t, rebuild=False)
    build_board()
    for tid in ids:
        print(short(load(tid)))

def descendants(tid):
    ts = all_tasks()
    out, frontier = [], [tid]
    while frontier:
        kids = [k for k in ts if k.get("parent") in frontier and k not in out]
        out += kids
        frontier = [k["id"] for k in kids]
    return out

def cmd_order(a):
    set_order(a.ids, a.session)
    for tid in a.ids:
        print(short(load(tid)))

def serve_info():
    """{"pid", "port"} of a running `bl serve`, or None."""
    p = os.path.join(ROOT, SERVE_FILE)
    try:
        info = read_json(p)
        import socket
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=0.5):
            return info
    except Exception:
        return None

def cmd_serve(a):
    """Serve the board on 127.0.0.1 so drag-to-reorder is saved for every
    session. Only this computer can reach it; writes need the per-run token
    baked into the served page, and the Host header must be this server."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    token = secrets.token_urlsafe(24)
    port = a.port
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *x): pass
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def _ok_host(self):
            return self.headers.get("Host", "") in hosts
        def do_GET(self):
            if not self._ok_host(): return self._send(403, b"forbidden")
            if self.path.split("?")[0] in ("/", "/board.html"):
                return self._send(200, board_html({"token": token}).encode(ENC), "text/html; charset=utf-8")
            self._send(404, b"not found")
        def do_POST(self):
            if not self._ok_host() or self.headers.get("X-BL-Token") != token:
                return self._send(403, b"forbidden")
            origin = self.headers.get("Origin")
            if origin and origin.split("//", 1)[-1] not in hosts:
                return self._send(403, b"forbidden")
            if self.path != "/api/order":
                return self._send(404, b"not found")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)).decode(ENC))
                ids = [str(i) for i in body["ids"]][:500]
                if not all(re.fullmatch(r"T-\d{1,6}", i) for i in ids):
                    raise ValueError("bad id")
                set_order(ids, "board")
            except SystemExit as e:
                return self._send(400, str(e).encode(ENC))
            except Exception as e:
                return self._send(400, f"bad request: {e}".encode(ENC))
            self._send(204)

    live = serve_info()
    if live:  # already serving this backlog: reuse it
        url = f"http://127.0.0.1:{live['port']}/"
        print(f"board already live at {url}")
        if a.open:
            import webbrowser
            webbrowser.open(url)
        return
    srv = None
    for p_try in ([port] if port == 0 else range(port, port + 20)):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p_try), H)
            break
        except OSError:
            continue  # taken by another program: try the next port
    if srv is None:
        sys.exit(f"ports {port}-{port + 19} are all in use; pass --port N")
    port = srv.server_address[1]
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    write_json(os.path.join(ROOT, SERVE_FILE), {"pid": os.getpid(), "port": port})
    url = f"http://127.0.0.1:{port}/"
    print(f"board live at {url}  (drag-to-reorder saves for every session; Ctrl+C stops)", flush=True)
    if a.open:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: os.remove(os.path.join(ROOT, SERVE_FILE))
        except OSError: pass

def cmd_show(a):
    t = load(a.id)
    print(json.dumps(t, indent=2, ensure_ascii=False))

def filt(a):
    ts = all_tasks()
    if not a.all: ts = [t for t in ts if t["status"] in OPEN]
    if a.area: ts = [t for t in ts if t["area"].lower() == a.area.lower()]
    if a.session: ts = [t for t in ts if a.session in (t.get("created_by"), t.get("claimed_by"), t.get("completed_by"))]
    if getattr(a, "scheduled", False): ts = sorted((t for t in ts if snoozed(t)), key=lambda t: t["scheduled_for"])
    return ts

def cmd_list(a):
    ts = filt(a)
    if a.json:
        print(json.dumps(ts, indent=1, ensure_ascii=False)); return
    if not ts:
        print("(no tasks)"); return
    by = {}
    for t in ts: by.setdefault(t["area"], []).append(t)
    for area in sorted(by):
        print(f"== {area} ==")
        roots = [t for t in by[area] if not t.get("parent") or t["parent"] not in {x['id'] for x in by[area]}]
        for r in roots:
            print("  " + short(r))
            for k in by[area]:
                if k.get("parent") == r["id"]: print("     - " + short(k))
            if r.get("next_action") and r["status"] in OPEN: print(f"       next: {r['next_action']}")

def open_for(sid):
    allopen = sorted((t for t in all_tasks() if t["status"] in OPEN), key=order_key)
    later = [t for t in allopen if snoozed(t)]
    ts = [t for t in allopen if not snoozed(t)]
    back = [t for t in ts if returned(t)]
    if back:
        print(f"--- back from schedule, tell the user ({len(back)}) ---")
        for t in back: print("  " + short(t) + (f"  | why deferred: {t['schedule_reason']}" if t.get("schedule_reason") else ""))
    mine = [t for t in ts if sid in (t.get("created_by"), t.get("claimed_by"))]
    other = [t for t in ts if t not in mine and not (t.get("claimed_by") and not is_stale(t))]
    print(f"--- open in this session ({len(mine)}) ---")
    for t in mine: print("  " + short(t) + (f"  | next: {t['next_action']}" if t.get("next_action") else ""))
    print(f"--- unclaimed/stale elsewhere ({len(other)}) ---")
    for t in other[:25]: print("  " + short(t))
    if later:
        print(f"--- scheduled for later ({len(later)}, hidden until then; bl list --scheduled) ---")

def cmd_open(a):
    open_for(a.session)

def cmd_footer(a):
    """One-line 'still open' footer for the end of a reply."""
    ts = [t for t in all_tasks() if t["status"] in OPEN and not snoozed(t) and a.session in (t.get("created_by"), t.get("claimed_by"))]
    if a.exclude: ts = [t for t in ts if t["id"] not in a.exclude]
    if not ts:
        print("Backlog: nothing else open in this session."); return
    parts = [f"{t['id']} {t['title'][:40]} ({LABEL[t['status']]}, ETA {fmt_min(t.get('eta_minutes'))}{', OVERDUE' if overdue(t) else ''})" for t in ts]
    print("Still open: " + "; ".join(parts))

def cmd_board(a):
    build_board()
    path = os.path.join(ROOT, "board.html")
    live = serve_info()
    if live:
        url = f"http://127.0.0.1:{live['port']}/"
        print(url)
        if a.open:
            import webbrowser
            webbrowser.open(url)
        return
    print("file://" + path)
    if a.open:
        import subprocess, webbrowser
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            elif os.name == "nt":
                os.startfile(path)
            else:
                webbrowser.open("file://" + path)
        except Exception as e:
            print(f"could not open a browser here ({e}); open the file:// link above")


# ---------- board ----------
def build_board():
    """Best effort: the task files are the source of truth, so a board that
    cannot be written right now (file held open on Windows) must not fail
    the command that changed a task. The next write rebuilds it."""
    try:
        _build_board()
    except OSError as e:
        print(f"(board not refreshed this time: {e})", file=sys.stderr)

def _build_board():
    ts = all_tasks()
    sess = all_sessions()
    # BOARD.md
    lines = [f"# {TITLE}  (generated {iso(now())})", ""]
    later = sorted((t for t in ts if snoozed(t)), key=lambda t: t["scheduled_for"])
    for st in STATUSES:
        group = sorted((t for t in ts if t["status"] == st and t not in later), key=order_key)
        if st in ("done", "dropped"): group = group[-30:]
        if not group: continue
        lines.append(f"## {LABEL[st]} ({len(group)})")
        for t in group:
            lines.append(f"- {short(t)}  | by {t.get('created_by')}" + (f" | done by {t.get('completed_by')}" if t.get('completed_by') else ""))
        lines.append("")
    if later:
        lines.append(f"## Scheduled ({len(later)})")
        for t in later:
            lines.append(f"- {short(t)}" + (f"  | why: {t['schedule_reason']}" if t.get("schedule_reason") else ""))
        lines.append("")
    write_text(os.path.join(ROOT, "BOARD.md"), "\n".join(lines))
    write_text(os.path.join(ROOT, "board.html"), board_html(None, ts, sess))

def board_html(api=None, ts=None, sess=None):
    """The board page. api={"token"} when served by `bl serve` (enables saving
    drag-to-reorder); None for the static board.html file."""
    ts = all_tasks() if ts is None else ts
    sess = all_sessions() if sess is None else sess
    js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    data = js({"tasks": ts, "sessions": sess, "generated": iso(now()), "title": TITLE,
               "home": os.path.expanduser("~").replace("\\", "/"), "owner": OWNER,
               "statuses": STATUSES, "labels": LABEL, "stale_hours": STALE_HOURS})
    custom = os.path.join(ROOT, "board_template.html")
    if os.path.exists(custom):
        with open(custom, encoding="utf-8-sig") as f:
            tpl = f.read()
    else:
        tpl = BOARD_TEMPLATE
    return tpl.replace("/*__DATA__*/null", data).replace("/*__API__*/null", js(api))


def cmd_init(a):
    c = load_config()
    if a.owner: c["owner"] = a.owner
    if a.areas: c["areas"] = [x.strip() for x in a.areas.split(",") if x.strip()]
    if a.title: c["title"] = a.title
    c["version"] = VERSION
    write_json(CONFIG, c)
    global OWNER, AREAS, TITLE
    OWNER, AREAS, TITLE = c["owner"], c["areas"], c.get("title") or DEFAULT_TITLE
    LABEL["waiting_user"] = f"Waiting on {OWNER}"
    build_board()
    print(f"backlog ready at {ROOT}")
    print(f"owner: {c['owner']} | areas: {', '.join(c['areas'])}")
    print(f"board: file://{os.path.join(ROOT, 'board.html')}")

def cmd_version(a):
    print(VERSION)

def cmd_areas(a):
    c = load_config()
    if a.add:
        for x in a.add:
            if x not in c["areas"]: c["areas"].append(x)
        write_json(CONFIG, c)
    if a.rename:
        old, new = a.rename
        c["areas"] = [new if x == old else x for x in c["areas"]]
        if new not in c["areas"]: c["areas"].append(new)
        write_json(CONFIG, c)
        moved = 0
        for t in all_tasks():
            if t.get("area") == old:
                with task_lock(t["id"]):
                    cur = load(t["id"])
                    cur["area"] = new
                    log(cur, "bl", f"area {old} -> {new}")
                    save(cur, rebuild=False)
                moved += 1
        build_board()
        print(f"renamed area {old} -> {new} on {moved} task(s)")
    print(", ".join(c["areas"]))

def cmd_where(a):
    print(ROOT)
    print(f"engine {VERSION} | board file://{os.path.join(ROOT, 'board.html')}")

def main():
    p = argparse.ArgumentParser(prog="bl")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("session"); s.add_argument("--id", required=True); s.add_argument("--label")
    s.add_argument("--surface"); s.add_argument("--url"); s.set_defaults(f=cmd_session)

    s = sp.add_parser("add"); s.add_argument("--session", required=True); s.add_argument("--title", required=True)
    s.add_argument("--area"); s.add_argument("--parent"); s.add_argument("--status", default="ready")
    s.add_argument("--priority", default="P2"); s.add_argument("--next", default=""); s.add_argument("--source", default="")
    s.add_argument("--eta", default="30m"); s.add_argument("--depends-on", nargs="*"); s.set_defaults(f=cmd_add)

    s = sp.add_parser("batch"); s.add_argument("--session", required=True); s.add_argument("file", nargs="?", default="-")
    s.set_defaults(f=cmd_batch)

    s = sp.add_parser("update"); s.add_argument("id"); s.add_argument("--session", required=True)
    for k in ("title", "area", "status", "priority", "parent", "eta", "eta-reason", "log"): s.add_argument("--" + k)
    s.add_argument("--next"); s.add_argument("--output", action="append"); s.set_defaults(f=cmd_update)

    s = sp.add_parser("claim"); s.add_argument("id"); s.add_argument("--session", required=True)
    s.add_argument("--eta"); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_claim)

    s = sp.add_parser("done"); s.add_argument("id"); s.add_argument("--session", required=True)
    s.add_argument("--log"); s.add_argument("--output", action="append"); s.add_argument("--force", action="store_true")
    s.set_defaults(f=cmd_done)

    s = sp.add_parser("drop"); s.add_argument("id"); s.add_argument("--session", required=True)
    s.add_argument("--reason", required=True); s.set_defaults(f=cmd_drop)

    s = sp.add_parser("schedule", help="defer a task (and its open sub-tasks) until a date")
    s.add_argument("id"); s.add_argument("--session", required=True)
    s.add_argument("--at", help="YYYY-MM-DD (09:00 local) or YYYY-MM-DDTHH:MM local")
    s.add_argument("--in", help="e.g. 3d, 2w, 4h")
    s.add_argument("--reason", default=""); s.add_argument("--clear", action="store_true", help="bring it back now")
    s.set_defaults(f=cmd_schedule)

    s = sp.add_parser("order", help="set the order of tasks within their column, first = top")
    s.add_argument("ids", nargs="+"); s.add_argument("--session", required=True); s.set_defaults(f=cmd_order)

    s = sp.add_parser("serve", help="serve the board on 127.0.0.1 so drag-to-reorder is saved")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--open", action="store_true")
    s.set_defaults(f=cmd_serve)

    s = sp.add_parser("show"); s.add_argument("id"); s.set_defaults(f=cmd_show)

    s = sp.add_parser("list"); s.add_argument("--all", action="store_true"); s.add_argument("--area")
    s.add_argument("--session"); s.add_argument("--json", action="store_true")
    s.add_argument("--scheduled", action="store_true", help="only tasks scheduled for later, soonest first")
    s.set_defaults(f=cmd_list)

    s = sp.add_parser("open"); s.add_argument("--session", required=True); s.set_defaults(f=cmd_open)
    s = sp.add_parser("footer"); s.add_argument("--session", required=True); s.add_argument("--exclude", nargs="*")
    s.set_defaults(f=cmd_footer)
    s = sp.add_parser("board"); s.add_argument("--open", action="store_true", help="open the board in the default browser")
    s.set_defaults(f=cmd_board)
    s = sp.add_parser("init"); s.add_argument("--owner"); s.add_argument("--areas", help="comma separated")
    s.add_argument("--title", help="board title (default: Aapastech Backlog)")
    s.set_defaults(f=cmd_init)
    s = sp.add_parser("version"); s.set_defaults(f=cmd_version)
    s = sp.add_parser("areas"); s.add_argument("--add", nargs="*")
    s.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"), help="rename an area on config and every task")
    s.set_defaults(f=cmd_areas)
    s = sp.add_parser("where"); s.set_defaults(f=cmd_where)

    for stream in (sys.stdout, sys.stderr):
        try:
            # a cp1252 / latin-1 console shows '?' instead of crashing on an em dash
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    a = p.parse_args()
    a.f(a)

BOARD_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backlog</title>
<style>
:root{
  --bg:#f6f5f2;--panel:#ffffff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;
  --accent:#c2562f;--ok:#2f7d4f;--warn:#b7791f;--bad:#b42318;--chip:#efede7;--wait:#6941c6;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#171715;--panel:#21211f;--ink:#ecebe6;--muted:#9c9a92;--line:#33322e;
  --accent:#e07a50;--ok:#55b07c;--warn:#e0a84a;--bad:#f0645a;--chip:#2b2a27;--wait:#a48afb;}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  display:flex;flex-direction:column;overflow:hidden}
header{flex:none;position:relative;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
h1{font-size:18px;margin:0 12px 0 0;letter-spacing:-.01em}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px 10px;font-size:12px;color:var(--muted)}
.stat b{color:var(--ink);font-size:14px;margin-right:4px}
.stat.bad b{color:var(--bad)}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
select,input{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 8px;font:inherit;font-size:13px}
label.tog{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:4px}
.gen{font-size:11px;color:var(--muted);width:100%}
main{flex:1;min-height:0;overflow:auto;padding:0 20px 60px;overscroll-behavior:contain}
.lane{margin-bottom:22px;width:max-content;min-width:100%}
.lane h2{font-size:14px;margin:0;height:40px;display:flex;gap:8px;align-items:center;
  position:sticky;top:0;z-index:4;background:var(--bg)}
.lane h2 .n{font-weight:400;color:var(--muted);font-size:12px}
.cols{display:grid;grid-template-columns:repeat(5,minmax(210px,1fr)) minmax(170px,.75fr);gap:10px}
.col{background:var(--chip);border-radius:10px;padding:0 8px 8px;min-height:60px}
.col h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 -8px 6px;padding:10px 12px 8px;
  position:sticky;top:40px;z-index:3;background:var(--chip);border-radius:10px 10px 0 0;display:flex;justify-content:space-between}
.col h3 .k{font-weight:400;letter-spacing:0}
.col.sched h3{opacity:.75}
.pill{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted);background:transparent;border:1px dashed var(--line);
  border-radius:999px;padding:3px 10px;margin-bottom:5px;cursor:pointer;white-space:nowrap;overflow:hidden}
.pill:hover{border-color:var(--wait);color:var(--ink)}
.pill b{font-weight:600;color:var(--wait);flex:none}
.pill span{overflow:hidden;text-overflow:ellipsis}
.grip{cursor:grab;touch-action:none;user-select:none;color:var(--muted);font-size:14px;line-height:1;padding:2px 6px 2px 0;opacity:.55}
.card:hover .grip{opacity:1}
.grip:hover{color:var(--ink)}
.card.dragging{opacity:.9;box-shadow:0 8px 24px rgba(0,0,0,.25);position:relative;z-index:10;cursor:grabbing}
.drop-gap{height:6px;border-radius:4px;background:var(--accent);margin:0 0 8px}
.toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--panel);border:1px solid var(--line);
  border-radius:8px;padding:8px 14px;font-size:12px;color:var(--muted);z-index:20;box-shadow:0 4px 16px rgba(0,0,0,.2);max-width:92vw}
.card{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px;cursor:pointer}
.card:hover{border-color:var(--accent)}
.card.s-in_progress{border-left-color:var(--accent)}
.card.s-blocked{border-left-color:var(--bad)}
.card.s-waiting_user{border-left-color:var(--wait)}
.card.s-done{border-left-color:var(--ok);opacity:.8}
.card.s-dropped{opacity:.55;text-decoration:line-through}
.card .top{display:flex;justify-content:space-between;gap:6px;font-size:11px;color:var(--muted)}
.card .title{font-weight:600;margin:3px 0 4px}
.card .sub{font-size:12px;color:var(--muted);margin-top:2px}
.kids{margin:6px 0 0;padding:0;list-style:none;font-size:12px}
.kids li{display:flex;gap:6px;align-items:baseline;padding:1px 0}
.dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--muted);display:inline-block}
.dot.in_progress{background:var(--accent)}.dot.done{background:var(--ok)}.dot.blocked{background:var(--bad)}.dot.waiting_user{background:var(--wait)}
.eta{font-weight:600}
.eta.over{color:var(--bad)}
.who{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.who span{background:var(--chip);border-radius:5px;padding:1px 6px;font-size:11px;color:var(--muted)}
.who span.c{color:var(--ok)}
.who a.chat{background:var(--chip);border-radius:5px;padding:1px 6px;font-size:11px;color:var(--accent);text-decoration:none}
.who a.chat:hover{text-decoration:underline}
.when{display:inline-block;margin-top:4px;font-size:11px;border-radius:5px;padding:1px 6px;background:var(--chip);color:var(--wait)}
.when.back{color:var(--accent)}
.card.snz{opacity:.82}
details.res{margin-top:6px;border-top:1px dashed var(--line);padding-top:4px;font-size:12px}
details.res summary{cursor:pointer;color:var(--muted);list-style:none;user-select:none}
details.res summary::-webkit-details-marker{display:none}
details.res summary::before{content:"▸ ";}
details.res[open] summary::before{content:"▾ ";}
details.res .rs{margin-top:4px;display:grid;gap:4px}
details.res .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
details.res ul{margin:0;padding-left:16px}
details.res li{margin:1px 0;word-break:break-word}
details.res .lg{color:var(--muted);word-break:break-word}
details.res .sub-r{padding:3px 0;border-bottom:1px dashed var(--line)}
.lnk{word-break:break-all}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--ink);max-width:680px;width:92vw;padding:20px}
dialog::backdrop{background:rgba(0,0,0,.35)}
dialog h2{margin:0 0 4px;font-size:17px}
dialog table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
dialog td{padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}
dialog td:first-child{color:var(--muted);width:130px}
.log{font-size:12px;max-height:260px;overflow:auto;border-top:1px solid var(--line);padding-top:6px}
.log div{padding:3px 0;border-bottom:1px dashed var(--line)}
.log time{color:var(--muted);margin-right:6px}
a{color:var(--accent)}
button{background:var(--chip);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 12px;font:inherit;cursor:pointer}
.empty{color:var(--muted);padding:40px;text-align:center}
@media (max-width:700px){header{padding:12px 16px}main{padding:0 16px 40px}.cols{grid-template-columns:repeat(5,78vw) 60vw}}
</style>
</head>
<body>
<header>
  <h1 id="ttl">Backlog</h1>
  <div class="stats" id="stats"></div>
  <div class="controls">
    <input id="q" placeholder="Search…" size="14">
    <select id="area"><option value="">All areas</option></select>
    <select id="sess"><option value="">All sessions</option></select>
    <label class="tog"><input type="checkbox" id="showdone"> Done/dropped</label>
  </div>
  <div class="gen" id="gen"></div>
</header>
<main id="main"></main>
<dialog id="dlg"></dialog>
<script>
const D = /*__DATA__*/null;
const API = /*__API__*/null;  // set when served by `bl serve`: drag order is then saved for every session
document.title = D.title || "Backlog"; document.getElementById("ttl").textContent = D.title || "Backlog";
const L = D.labels, S = D.sessions;
const COLS = [["inbox","ready"],["in_progress"],["blocked"],["waiting_user"],[],["done","dropped"]];
const COLNAME = ["To do","In progress","Blocked",L.waiting_user,"Scheduled","Done"];
const PRIO = {P0:0,P1:1,P2:2,P3:3};
const SCHED_COL = 4;
const OPEN = new Set(["inbox","ready","in_progress","blocked","waiting_user"]);
const now = Date.now();
const byId = Object.fromEntries(D.tasks.map(t=>[t.id,t]));
const esc = s => String(s ?? "").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const sl = id => { if(!id) return ""; const s=S[id]; return s ? (s.label||id) : id; };
// http(s) links open a tab; app links (claude://...) hand off to the app, so no target
const tgt = u => /^https?:/i.test(u) ? ' target="_blank"' : "";
const surl = id => id && S[id] && S[id].url ? S[id].url : null;
const slink = id => { if(!id) return "—"; const u=surl(id); const lab=esc(sl(id)); return u?`<a href="${esc(u)}"${tgt(u)}>${lab} ↗</a>`:lab; };
// the chat to jump back into: whoever is working it, else whoever deferred it, else whoever captured it
const chatOf = t => [OPEN.has(t.status)?t.claimed_by:t.completed_by, t.scheduled_by, t.created_by].find(surl);
const fm = m => m==null?"?":m<60?m+"m":m<1440?(Math.floor(m/60)+"h"+(m%60?String(m%60).padStart(2,"0")+"m":"")):(Math.floor(m/1440)+"d");
const snz = t => OPEN.has(t.status) && t.scheduled_for && Date.parse(t.scheduled_for) > now;
const back = t => OPEN.has(t.status) && t.scheduled_for && Date.parse(t.scheduled_for) <= now && (!t.updated_at || Date.parse(t.updated_at) <= Date.parse(t.scheduled_for));
const over = t => OPEN.has(t.status) && !snz(t) && t.due_at && Date.parse(t.due_at) < now;
const colOf = t => snz(t) ? SCHED_COL : COLS.findIndex(c=>c.includes(t.status));
const rel = iso => { if(!iso) return ""; const d=(Date.parse(iso)-now)/60000; const a=Math.abs(Math.round(d)); return d>=0?"in "+fm(a):fm(a)+" ago"; };
const local = iso => iso? new Date(iso).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";

function etaHtml(t){
  if(t.status==="done") return `<span class="eta">done in ${fm(t.actual_minutes)}</span> <span>(ETA ${fm(t.eta_minutes)})</span>`;
  if(t.status==="dropped") return "dropped";
  if(snz(t)) return `<span class="eta">ETA ${fm(t.eta_minutes)}</span> · back ${rel(t.scheduled_for)}`;
  const o = over(t);
  return `<span class="eta ${o?"over":""}">ETA ${fm(t.eta_minutes)}</span> · ${o?"overdue "+rel(t.due_at).replace(" ago",""):"due "+rel(t.due_at)}`;
}

function fillFilters(){
  const areas=[...new Set(D.tasks.map(t=>t.area))].sort();
  const a=document.getElementById("area"); areas.forEach(x=>a.add(new Option(x,x)));
  const ss=new Set(); D.tasks.forEach(t=>["created_by","claimed_by","completed_by"].forEach(k=>t[k]&&ss.add(t[k])));
  const s=document.getElementById("sess"); [...ss].sort().forEach(x=>s.add(new Option(sl(x),x)));
  try{ const st=JSON.parse(localStorage.getItem("bl-filters")||"{}");
    for(const k of ["q","area","sess"]) if(st[k]!=null) document.getElementById(k).value=st[k];
    document.getElementById("showdone").checked=!!st.showdone; }catch(e){}
}
function saveFilters(){ try{ localStorage.setItem("bl-filters",JSON.stringify({q:q.value,area:area.value,sess:sess.value,showdone:showdone.checked})); }catch(e){} }

function render(){
  saveFilters();
  const fq=q.value.toLowerCase(), fa=area.value, fs=sess.value, sd=showdone.checked;
  const match = t => (!fa||t.area===fa) && (!fs||[t.created_by,t.claimed_by,t.completed_by].includes(fs))
     && (!fq||(t.id+" "+t.title+" "+(t.next_action||"")).toLowerCase().includes(fq))
     && (sd||OPEN.has(t.status));
  const vis = D.tasks.filter(match);
  const later=D.tasks.filter(snz);
  const open=D.tasks.filter(t=>OPEN.has(t.status)&&!snz(t));
  document.getElementById("stats").innerHTML =
    `<div class="stat"><b>${open.length}</b>open</div>`+
    `<div class="stat"><b>${open.filter(t=>t.status==="in_progress").length}</b>in progress</div>`+
    `<div class="stat"><b>${open.filter(t=>t.status==="waiting_user").length}</b>${esc(L.waiting_user.toLowerCase())}</div>`+
    `<div class="stat bad"><b>${open.filter(over).length}</b>overdue</div>`+
    `<div class="stat"><b>${later.length}</b>scheduled</div>`+
    `<div class="stat"><b>${D.tasks.filter(t=>t.status==="done"&&t.completed_at&&now-Date.parse(t.completed_at)<864e5).length}</b>done today</div>`;
  document.getElementById("gen").textContent = "Updated "+local(D.generated)+" · auto-refreshes every 30s";
  const visIds=new Set(vis.map(t=>t.id));
  // a card is a top-level task, or a sub-task whose parent is hidden or sits in a different column
  // (an in-progress sub-task of a To-do parent must show under In progress, or the counts look wrong)
  const roots = vis.filter(t=>!t.parent || !visIds.has(t.parent) || colOf(t)!==colOf(byId[t.parent]));
  const lanes={}; roots.forEach(t=>(lanes[t.area]??=[]).push(t));
  const m=document.getElementById("main");
  if(!roots.length){ m.innerHTML=`<div class="empty">Nothing here. Tasks appear as soon as an AI session captures them.</div>`; return; }
  m.innerHTML = Object.keys(lanes).sort().map(ar=>{
    const ts=lanes[ar];
    return `<section class="lane"><h2>${esc(ar)} <span class="n">${ts.length} task${ts.length>1?"s":""}</span></h2><div class="cols">`+
      COLS.map((st,i)=>{
        const cs=ts.filter(t=>colOf(t)===i);
        const body = i===SCHED_COL
          ? cs.sort((a,b)=>a.scheduled_for.localeCompare(b.scheduled_for)).map(pill).join("")
          : cs.sort(byOrder(ar,i)).map(card).join("");
        return `<div class="col${i===SCHED_COL?" sched":""}" data-lane="${esc(ar)}" data-col="${i}"><h3><span>${COLNAME[i]}</span><span class="k">${cs.length||""}</span></h3>${body}</div>`;
      }).join("")+
      `</div></section>`;
  }).join("");
}
// ---- order: back-from-schedule, then new (unranked, by priority), then the user's drag order ----
const LKEY = "bl-order";
const localOrder = () => { if(API) return {}; try{ return JSON.parse(localStorage.getItem(LKEY)||"{}"); }catch(e){ return {}; } };
const LO = localOrder();
function byOrder(lane, col){
  const lo = LO[lane+"|"+col] || [];
  const rk = t => { const i=lo.indexOf(t.id); return i>=0 ? i+1 : t.rank; };
  return (a,b)=> (back(b)-back(a)) || ((rk(a)!=null)-(rk(b)!=null)) || ((rk(a)??0)-(rk(b)??0))
    || ((PRIO[a.priority]??9)-(PRIO[b.priority]??9)) || a.id.localeCompare(b.id);
}
function pill(t){
  const n=openDescendants(t).length;
  const tip=`${t.id} · ${t.title}${t.schedule_reason?" — "+t.schedule_reason:""}`;
  return `<div class="pill" onclick="openT('${t.id}')" title="${esc(tip)}"><b>${esc(new Date(t.scheduled_for).toLocaleDateString([], {month:"short",day:"numeric"}))}</b><span>${esc(t.id)} ${esc(t.title)}${n?` (+${n})`:""}</span></div>`;
}
function toast(msg){
  const el=document.createElement("div"); el.className="toast"; el.textContent=msg; document.body.appendChild(el);
  setTimeout(()=>el.remove(), 4000);
}
// Drag by the ⋮⋮ grip, within one column only (pointer events: mouse, pen and touch).
let dragging=false;
function startDrag(ev, grip){
  const cardEl=grip.closest(".card"), col=cardEl.parentElement;
  ev.preventDefault(); ev.stopPropagation(); grip.setPointerCapture(ev.pointerId);
  dragging=true;
  const gap=document.createElement("div"); gap.className="drop-gap";
  const y0=ev.clientY; cardEl.classList.add("dragging");
  const sc=document.getElementById("main");
  const move=e=>{
    cardEl.style.transform=`translateY(${e.clientY-y0}px)`;
    const sibs=[...col.querySelectorAll(":scope>.card:not(.dragging)")];
    const before=sibs.find(c=>{ const r=c.getBoundingClientRect(); return e.clientY < r.top + r.height/2; });
    before ? col.insertBefore(gap,before) : col.appendChild(gap);
    const r=sc.getBoundingClientRect();  // edge auto-scroll
    if(e.clientY < r.top+40) sc.scrollTop-=12; else if(e.clientY > r.bottom-40) sc.scrollTop+=12;
  };
  const up=()=>{
    grip.removeEventListener("pointermove",move); grip.removeEventListener("pointerup",up); grip.removeEventListener("pointercancel",up);
    cardEl.classList.remove("dragging"); cardEl.style.transform="";
    if(gap.parentElement){ col.insertBefore(cardEl,gap); gap.remove(); }
    dragging=false;
    saveOrder(col);
  };
  grip.addEventListener("pointermove",move); grip.addEventListener("pointerup",up); grip.addEventListener("pointercancel",up);
}
function saveOrder(col){
  const ids=[...col.querySelectorAll(":scope>.card")].map(c=>c.dataset.id);
  const key=col.dataset.lane+"|"+col.dataset.col;
  if(API){
    fetch("/api/order",{method:"POST",headers:{"Content-Type":"application/json","X-BL-Token":API.token},body:JSON.stringify({ids})})
      .then(r=>{ if(!r.ok) throw new Error(r.status); ids.forEach((id,i)=>{ if(byId[id]) byId[id].rank=i+1; }); })
      .catch(()=>toast("Could not save the new order. Is bl serve still running?"));
  } else {
    LO[key]=ids; try{ localStorage.setItem(LKEY,JSON.stringify(LO)); }catch(e){}
    toast("Order saved in this browser only. Open the board with `bl serve` so every session follows it.");
  }
}
document.addEventListener("pointerdown",ev=>{ const g=ev.target.closest&&ev.target.closest(".grip"); if(g) startDrag(ev,g); });

// ---- resources: every link / local file a task mentions, reachable from the card ----
const RX = /(https?:\/\/[^\s'"<>)\]]+|claude:\/\/[^\s'"<>)\]]+|file:\/\/[^\s'"<>)\]]+|(?:~|\/Users|\/home|[A-Za-z]:\\)[\/\\][^\s'"<>)\],;|]+)/g;
const trimP = u => u.replace(/[.,;:]+$/,"");
const toHref = u => /^(https?|file|claude):/.test(u) ? u
  : u.startsWith("~") ? "file://"+encodeURI((D.home||"")+u.slice(1))
  : /^[A-Za-z]:\\/.test(u) ? "file:///"+encodeURI(u.replace(/\\/g,"/"))
  : "file://"+encodeURI(u);
const label = u => {
  if(/^claude:/.test(u)) return "Open session ↗";
  let m = u.match(/github\.com\/([^/]+)\/([^/]+)\/(pull|issues|commit)\/([0-9a-f]+)/);
  if(m) return m[3]==="commit" ? `${m[2]}@${m[4].slice(0,7)}` : `${m[2]}#${m[4]}`;
  if(/^file:|^[~\/]|^[A-Za-z]:\\/.test(u)){ const d=decodeURI(u.replace(/^file:\/\//,"")); return "📄 "+d.split(/[\/\\]/).filter(Boolean).slice(-2).join("/"); }
  try{ const x=new URL(u); return x.hostname.replace(/^www\./,"")+(x.pathname.length>1?x.pathname.slice(0,40):""); }catch(e){ return u.slice(0,60); }
};
const linkify = txt => { const s=String(txt??""); let out="", last=0;
  s.replace(RX,(m,_g,off)=>{ const u=trimP(m); out+=esc(s.slice(last,off))+`<a class="lnk" href="${esc(toHref(u))}"${tgt(toHref(u))} onclick="event.stopPropagation()">${esc(label(u))}</a>`; last=off+u.length; return m; });
  return out+esc(s.slice(last)); };
function resources(t){
  const seen=new Set(), list=[];
  const add=u=>{ u=trimP(u); const h=toHref(u); if(!seen.has(h)){ seen.add(h); list.push([h,label(u),u]); } };
  (t.outputs||[]).forEach(o=>{ const hits=String(o).match(RX); hits?hits.forEach(add):list.push([null,o,o]); });
  [t.next_action,t.source,...(t.log||[]).map(e=>e.msg)].forEach(x=>(String(x??"").match(RX)||[]).forEach(add));
  return list;
}
// Open sub-tasks at every depth: sub-tasks have no card of their own, so their
// links and next steps must be reachable from the root card.
function openDescendants(t, depth=1){
  return D.tasks.filter(k=>k.parent===t.id).flatMap(k=>[
    ...(OPEN.has(k.status)?[[k,depth]]:[]), ...openDescendants(k, depth+1)]);
}
function subBlock(t){
  const subs=openDescendants(t);
  if(!subs.length) return ["",0];
  let n=0;
  const html=subs.map(([k,depth])=>{
    const r=resources(k); n+=r.length;
    const links=r.map(([h,l,raw])=>h?`<a class="lnk" href="${esc(h)}"${tgt(h)} title="${esc(raw)}" onclick="event.stopPropagation()">${esc(l)}</a>`:esc(l)).join(" · ");
    return `<div class="sub-r" style="margin-left:${(depth-1)*10}px"><a href="#" class="lnk" onclick="event.preventDefault();event.stopPropagation();openT('${k.id}')">${esc(k.id)}</a> ${esc(k.title)} <span class="lg">(${esc(L[k.status])})</span>
      ${k.next_action?`<div class="lg">Next: ${linkify(k.next_action)}</div>`:""}${links?`<div>${links}</div>`:""}</div>`;
  }).join("");
  return [`<div><div class="lbl">Open sub-tasks</div>${html}</div>`, n];
}
function resBlock(t){
  const res=resources(t), logs=(t.log||[]).slice(-3).reverse();
  const [subs, subLinks]=subBlock(t);
  if(!res.length && !logs.length && !t.source && !subs) return "";
  const li=res.map(([h,l,raw])=>h?`<li><a class="lnk" href="${esc(h)}"${tgt(h)} title="${esc(raw)}" onclick="event.stopPropagation()">${esc(l)}</a></li>`:`<li>${esc(l)}</li>`).join("");
  return `<details class="res" onclick="event.stopPropagation()"><summary>Details${res.length+subLinks?` · ${res.length+subLinks} link${res.length+subLinks>1?"s":""}`:""}</summary><div class="rs">
    ${li?`<div><div class="lbl">Links &amp; files</div><ul>${li}</ul></div>`:""}
    ${t.next_action&&OPEN.has(t.status)?`<div><div class="lbl">Next</div>${linkify(t.next_action)}</div>`:""}
    ${t.source?`<div><div class="lbl">Source</div><span class="lg">${linkify(t.source)}</span></div>`:""}
    ${subs}
    ${logs.length?`<div><div class="lbl">Latest</div>${logs.map(e=>`<div class="lg"><b>${esc(local(e.at))}</b> ${linkify(e.msg)}</div>`).join("")}</div>`:""}
  </div></details>`;
}
function card(t){
  const kids=D.tasks.filter(k=>k.parent===t.id);
  const kd = kids.length? `<ul class="kids">`+kids.map(k=>`<li onclick="event.stopPropagation();openT('${k.id}')" style="cursor:pointer" title="Open ${esc(k.id)}"><span class="dot ${k.status}"></span><span>${esc(k.id)} ${esc(k.title)} <span style="color:var(--muted)">· ${k.status==="done"?"done":snz(k)?"scheduled "+local(k.scheduled_for):"ETA "+fm(k.eta_minutes)}${over(k)?" ⚠":""}</span></span></li>`).join("")+`</ul>`:"";
  const par = t.parent&&byId[t.parent] ? `<div class="sub">↳ part of ${esc(t.parent)} ${esc(byId[t.parent].title)}</div>`:"";
  const when = snz(t) ? `<div class="when" title="${esc(t.schedule_reason||"")}">⏰ back ${esc(local(t.scheduled_for))}${t.schedule_reason?" · "+esc(t.schedule_reason):""}</div>`
    : back(t) ? `<div class="when back">↻ back from schedule${t.schedule_reason?" · was deferred: "+esc(t.schedule_reason):""}</div>` : "";
  const cu = surl(chatOf(t));
  const chat = cu ? `<a class="chat" href="${esc(cu)}"${tgt(cu)} onclick="event.stopPropagation()" title="Open ${esc(sl(chatOf(t)))}">Open chat ↗</a>` : "";
  return `<div class="card s-${t.status}${snz(t)?" snz":""}" data-id="${esc(t.id)}" onclick="if(!dragging)openT('${t.id}')">
    <div class="top"><span><span class="grip" title="Drag to reorder" onclick="event.stopPropagation()">⠿</span>${esc(t.id)} · ${esc(t.priority||"")}</span><span>${L[t.status]}</span></div>
    <div class="title">${esc(t.title)}</div>${par}${when}
    <div class="sub">${etaHtml(t)}</div>
    ${t.next_action&&OPEN.has(t.status)?`<div class="sub">Next: ${linkify(t.next_action)}</div>`:""}
    ${kd}
    ${resBlock(t)}
    <div class="who"><span>created by ${esc(sl(t.created_by))}</span>${t.claimed_by&&OPEN.has(t.status)?`<span>worked by ${esc(sl(t.claimed_by))}</span>`:""}${t.completed_by?`<span class="c">completed by ${esc(sl(t.completed_by))}</span>`:""}${chat}</div>
  </div>`;
}
function openT(id){
  const t=byId[id]; const d=document.getElementById("dlg");
  const row=(k,v)=>`<tr><td>${k}</td><td>${v}</td></tr>`;
  d.innerHTML=`<h2>${esc(t.id)} · ${esc(t.title)}</h2><div style="color:var(--muted);font-size:13px">${esc(t.area)} · ${L[t.status]} · ${esc(t.priority||"")}</div>
  <table>${row("ETA",etaHtml(t))}${row("Due",local(t.due_at))}
  ${t.scheduled_for?row(snz(t)?"Scheduled":"Was scheduled",local(t.scheduled_for)+" by "+slink(t.scheduled_by)+(t.schedule_reason?" — "+esc(t.schedule_reason):"")):""}${row("Next action",linkify(t.next_action)||"—")}
  ${row("Parent",t.parent?esc(t.parent+" "+(byId[t.parent]?.title||"")):"—")}
  ${row("Created",local(t.created_at)+" by "+slink(t.created_by))}
  ${row("Worked by",slink(t.claimed_by))}${row("Completed",t.completed_at?local(t.completed_at)+" by "+slink(t.completed_by):"—")}
  ${row("Source",linkify(t.source)||"—")}
  ${row("Links & files",resources(t).map(([h,l,raw])=>h?`<a class="lnk" href="${esc(h)}"${tgt(h)}>${esc(raw)}</a>`:esc(l)).join("<br>")||"—")}</table>
  <div class="log">${(t.log||[]).slice().reverse().map(e=>`<div><time>${local(e.at)}</time><b>${esc(sl(e.session))}</b> — ${linkify(e.msg)}</div>`).join("")}</div>
  <p style="text-align:right;margin:12px 0 0"><button onclick="dlg.close()">Close</button></p>`;
  d.showModal();
}
fillFilters(); render();
["q","area","sess","showdone"].forEach(k=>document.getElementById(k).addEventListener("input",render));
// refresh every 30s, but never mid-drag or with a dialog open, and keep the scroll position
try{ const p=JSON.parse(sessionStorage.getItem("bl-scroll")||"null"); if(p){ const m=document.getElementById("main"); m.scrollTop=p[0]; m.scrollLeft=p[1]; } }catch(e){}
setInterval(()=>{ if(dragging||dlg.open) return; const m=document.getElementById("main");
  try{ sessionStorage.setItem("bl-scroll",JSON.stringify([m.scrollTop,m.scrollLeft])); }catch(e){}
  location.reload(); },30000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
