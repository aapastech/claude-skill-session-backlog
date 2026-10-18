#!/usr/bin/env python3
"""bl.py - session-backlog engine: a local task backlog shared by every Claude session on this device.

Storage: one JSON file per task in tasks/, one per session in sessions/.
One-file-per-task means parallel sessions never overwrite each other.
Every write regenerates board.html (visual board) and BOARD.md (text board).
Stdlib only. Run with:  python3 <backlog>/bl.py <command> ...
"""
import argparse, datetime as dt, html, json, os, re, sys, tempfile

VERSION = "2.0.0"
ROOT = os.environ.get("BACKLOG_DIR") or os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(ROOT, "tasks")
SESS = os.path.join(ROOT, "sessions")
CONFIG = os.path.join(ROOT, "config.json")
DEFAULT_AREAS = ["Work", "Clients", "Product", "Marketing", "Admin", "Personal", "Claude-Ops"]

def load_config():
    c = {"owner": "you", "areas": DEFAULT_AREAS, "stale_hours": 24}
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG) as f:
                c.update(json.load(f))
        except Exception:
            pass
    return c

CFG = load_config()
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
    """'20m', '2h', '1h30m', '1d', '45' (minutes) -> minutes"""
    if s is None:
        return None
    s = str(s).strip().lower()
    if s.isdigit():
        return int(s)
    total, found = 0, False
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([dhm])", s):
        found = True
        total += float(n) * {"d": 1440, "h": 60, "m": 1}[u]
    if not found:
        sys.exit(f"bad ETA '{s}' - use e.g. 20m, 2h, 1h30m, 1d")
    return int(round(total))

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

def write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # atomic

def tpath(tid):
    return os.path.join(TASKS, f"{tid}.json")

def load(tid):
    tid = tid.upper()
    if not tid.startswith("T-"):
        tid = "T-" + tid.zfill(4)
    p = tpath(tid)
    if not os.path.exists(p):
        sys.exit(f"no such task {tid}")
    with open(p) as f:
        return json.load(f)

def all_tasks():
    out = []
    for fn in sorted(os.listdir(TASKS)):
        if fn.endswith(".json") and fn.startswith("T-"):
            try:
                with open(os.path.join(TASKS, fn)) as f:
                    out.append(json.load(f))
            except Exception:
                pass
    return out

def all_sessions():
    out = {}
    for fn in os.listdir(SESS):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(SESS, fn)) as f:
                    s = json.load(f)
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
        with open(p) as f:
            s = json.load(f)
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

def is_stale(t):
    c = parse_iso(t.get("claimed_at"))
    return c is not None and now() - c > dt.timedelta(hours=STALE_HOURS)

def overdue(t):
    d = parse_iso(t.get("due_at"))
    return t["status"] in OPEN and d is not None and now() > d

def short(t):
    eta = fmt_min(t.get("eta_minutes"))
    flag = " OVERDUE" if overdue(t) else ""
    owner = f" @{t['claimed_by']}" if t.get("claimed_by") else ""
    return f"{t['id']} [{LABEL[t['status']]}] ({t['area']}) {t['title']} - ETA {eta}{flag}{owner}"


# ---------- commands ----------
def cmd_session(a):
    p = os.path.join(SESS, safe(a.id) + ".json")
    s = {}
    if os.path.exists(p):
        with open(p) as f:
            s = json.load(f)
    s.update({"id": a.id, "label": a.label or s.get("label") or a.id,
              "surface": a.surface or s.get("surface"), "url": a.url or s.get("url")})
    s.setdefault("first_seen", iso(now()))
    s["last_seen"] = iso(now())
    write_json(p, s)
    print(f"session {a.id} registered")
    open_for(a.id)

def make_task(d, sid):
    tid = new_id()
    status = d.get("status", "ready")
    if status not in STATUSES:
        sys.exit(f"bad status {status}")
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
    if t["parent"]:
        par = load(t["parent"])
        t["parent"] = par["id"]
        if not d.get("area"):
            t["area"] = par["area"]
    set_eta(t, parse_eta(d.get("eta", "30m")), sid)
    log(t, sid, "created")
    if status == "in_progress":
        t["claimed_by"], t["claimed_at"], t["started_at"] = sid, iso(now()), iso(now())
        set_eta(t, t["eta_minutes"], sid)
    save(t, rebuild=False)
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
    data = json.load(sys.stdin if a.file == "-" else open(a.file))
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
    save(t)
    print(short(t))

def cmd_claim(a):
    touch_session(a.session)
    t = load(a.id)
    cur = t.get("claimed_by")
    if t["status"] in ("done", "dropped"):
        sys.exit(f"{t['id']} is {t['status']} - nothing to claim")
    if cur and cur != a.session and not is_stale(t) and not a.force:
        sys.exit(f"CLAIMED by {cur} at {t['claimed_at']} (not stale). Pick another task or use --force with the user's OK.")
    if cur and cur != a.session:
        log(t, a.session, f"took over from {cur}" + (" (stale)" if is_stale(t) else " (forced)"))
    t["claimed_by"], t["claimed_at"] = a.session, iso(now())
    t["status"] = "in_progress"
    t["started_at"] = t.get("started_at") or iso(now())
    set_eta(t, parse_eta(a.eta) if a.eta else (t.get("eta_minutes") or 30), a.session)
    log(t, a.session, "claimed")
    save(t)
    print(short(t))

def cmd_done(a):
    touch_session(a.session)
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
    save(t)
    print(short(t))
    if t.get("parent"):
        sib = [k for k in all_tasks() if k.get("parent") == t["parent"] and k["status"] in OPEN]
        if not sib:
            print(f"All sub-tasks of {t['parent']} are closed - verify and close {t['parent']} too.")

def cmd_drop(a):
    touch_session(a.session)
    t = load(a.id)
    t["status"] = "dropped"
    log(t, a.session, f"DROPPED: {a.reason}")
    save(t)
    print(short(t))

def cmd_show(a):
    t = load(a.id)
    print(json.dumps(t, indent=2, ensure_ascii=False))

def filt(a):
    ts = all_tasks()
    if not a.all: ts = [t for t in ts if t["status"] in OPEN]
    if a.area: ts = [t for t in ts if t["area"].lower() == a.area.lower()]
    if a.session: ts = [t for t in ts if a.session in (t.get("created_by"), t.get("claimed_by"), t.get("completed_by"))]
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
    ts = [t for t in all_tasks() if t["status"] in OPEN]
    mine = [t for t in ts if sid in (t.get("created_by"), t.get("claimed_by"))]
    other = [t for t in ts if t not in mine and not (t.get("claimed_by") and not is_stale(t))]
    print(f"--- open in this session ({len(mine)}) ---")
    for t in mine: print("  " + short(t) + (f"  | next: {t['next_action']}" if t.get("next_action") else ""))
    print(f"--- unclaimed/stale elsewhere ({len(other)}) ---")
    for t in other[:25]: print("  " + short(t))

def cmd_open(a):
    open_for(a.session)

def cmd_footer(a):
    """One-line 'still open' footer for the end of a reply."""
    ts = [t for t in all_tasks() if t["status"] in OPEN and a.session in (t.get("created_by"), t.get("claimed_by"))]
    if a.exclude: ts = [t for t in ts if t["id"] not in a.exclude]
    if not ts:
        print("Backlog: nothing else open in this session."); return
    parts = [f"{t['id']} {t['title'][:40]} ({LABEL[t['status']]}, ETA {fmt_min(t.get('eta_minutes'))}{', OVERDUE' if overdue(t) else ''})" for t in ts]
    print("Still open: " + "; ".join(parts))

def cmd_board(a):
    build_board()
    print(os.path.join(ROOT, "board.html"))


# ---------- board ----------
def build_board():
    ts = all_tasks()
    sess = all_sessions()
    # BOARD.md
    lines = [f"# Backlog board  (generated {iso(now())})", ""]
    for st in STATUSES:
        group = [t for t in ts if t["status"] == st]
        if st in ("done", "dropped"): group = group[-30:]
        if not group: continue
        lines.append(f"## {LABEL[st]} ({len(group)})")
        for t in group:
            lines.append(f"- {short(t)}  | by {t.get('created_by')}" + (f" | done by {t.get('completed_by')}" if t.get('completed_by') else ""))
        lines.append("")
    with open(os.path.join(ROOT, "BOARD.md"), "w") as f:
        f.write("\n".join(lines))
    # board.html
    data = json.dumps({"tasks": ts, "sessions": sess, "generated": iso(now()),
                       "statuses": STATUSES, "labels": LABEL, "stale_hours": STALE_HOURS},
                      ensure_ascii=False).replace("</", "<\\/")
    custom = os.path.join(ROOT, "board_template.html")
    if os.path.exists(custom):
        with open(custom) as f:
            tpl = f.read()
    else:
        tpl = BOARD_TEMPLATE
    out = tpl.replace("/*__DATA__*/null", data)
    tmp = os.path.join(ROOT, ".board.tmp")
    with open(tmp, "w") as f:
        f.write(out)
    os.replace(tmp, os.path.join(ROOT, "board.html"))


def cmd_init(a):
    c = load_config()
    if a.owner: c["owner"] = a.owner
    if a.areas: c["areas"] = [x.strip() for x in a.areas.split(",") if x.strip()]
    c["version"] = VERSION
    write_json(CONFIG, c)
    global OWNER, AREAS
    OWNER, AREAS = c["owner"], c["areas"]
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
    print(", ".join(c["areas"]))

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

    s = sp.add_parser("show"); s.add_argument("id"); s.set_defaults(f=cmd_show)

    s = sp.add_parser("list"); s.add_argument("--all", action="store_true"); s.add_argument("--area")
    s.add_argument("--session"); s.add_argument("--json", action="store_true"); s.set_defaults(f=cmd_list)

    s = sp.add_parser("open"); s.add_argument("--session", required=True); s.set_defaults(f=cmd_open)
    s = sp.add_parser("footer"); s.add_argument("--session", required=True); s.add_argument("--exclude", nargs="*")
    s.set_defaults(f=cmd_footer)
    s = sp.add_parser("board"); s.set_defaults(f=cmd_board)
    s = sp.add_parser("init"); s.add_argument("--owner"); s.add_argument("--areas", help="comma separated")
    s.set_defaults(f=cmd_init)
    s = sp.add_parser("version"); s.set_defaults(f=cmd_version)
    s = sp.add_parser("areas"); s.add_argument("--add", nargs="*"); s.set_defaults(f=cmd_areas)

    a = p.parse_args()
    a.f(a)

BOARD_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Backlog</title>
<style>
:root{
  --bg:#f6f5f2;--panel:#ffffff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;
  --accent:#c2562f;--ok:#2f7d4f;--warn:#b7791f;--bad:#b42318;--chip:#efede7;--wait:#6941c6;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#171715;--panel:#21211f;--ink:#ecebe6;--muted:#9c9a92;--line:#33322e;
  --accent:#e07a50;--ok:#55b07c;--warn:#e0a84a;--bad:#f0645a;--chip:#2b2a27;--wait:#a48afb;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
h1{font-size:18px;margin:0 12px 0 0;letter-spacing:-.01em}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px 10px;font-size:12px;color:var(--muted)}
.stat b{color:var(--ink);font-size:14px;margin-right:4px}
.stat.bad b{color:var(--bad)}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
select,input{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 8px;font:inherit;font-size:13px}
label.tog{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:4px}
.gen{font-size:11px;color:var(--muted);width:100%}
main{padding:16px 20px 60px}
.lane{margin-bottom:22px}
.lane h2{font-size:14px;margin:0 0 8px;display:flex;gap:8px;align-items:center}
.lane h2 .n{font-weight:400;color:var(--muted);font-size:12px}
.cols{display:grid;grid-template-columns:repeat(5,minmax(200px,1fr));gap:10px;overflow-x:auto}
.col{background:var(--chip);border-radius:10px;padding:8px;min-height:60px}
.col h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:2px 4px 8px}
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
@media (max-width:700px){header{padding:12px 16px}main{padding:12px 16px}.cols{grid-template-columns:repeat(5,78vw)}}
</style>
</head>
<body>
<header>
  <h1>Claude Backlog</h1>
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
const L = D.labels, S = D.sessions;
const COLS = [["inbox","ready"],["in_progress"],["blocked"],["waiting_user"],["done","dropped"]];
const COLNAME = ["To do","In progress","Blocked",L.waiting_user,"Done"];
const OPEN = new Set(["inbox","ready","in_progress","blocked","waiting_user"]);
const now = Date.now();
const byId = Object.fromEntries(D.tasks.map(t=>[t.id,t]));
const esc = s => String(s ?? "").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const sl = id => { if(!id) return ""; const s=S[id]; return s ? (s.label||id) : id; };
const slink = id => { if(!id) return "—"; const s=S[id]; const lab=esc(sl(id)); return s&&s.url?`<a href="${esc(s.url)}" target="_blank">${lab}</a>`:lab; };
const fm = m => m==null?"?":m<60?m+"m":m<1440?(Math.floor(m/60)+"h"+(m%60?String(m%60).padStart(2,"0")+"m":"")):(Math.floor(m/1440)+"d");
const over = t => OPEN.has(t.status) && t.due_at && Date.parse(t.due_at) < now;
const rel = iso => { if(!iso) return ""; const d=(Date.parse(iso)-now)/60000; const a=Math.abs(Math.round(d)); return d>=0?"in "+fm(a):fm(a)+" ago"; };
const local = iso => iso? new Date(iso).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";

function etaHtml(t){
  if(t.status==="done") return `<span class="eta">done in ${fm(t.actual_minutes)}</span> <span>(ETA ${fm(t.eta_minutes)})</span>`;
  if(t.status==="dropped") return "dropped";
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
  const open=D.tasks.filter(t=>OPEN.has(t.status));
  document.getElementById("stats").innerHTML =
    `<div class="stat"><b>${open.length}</b>open</div>`+
    `<div class="stat"><b>${open.filter(t=>t.status==="in_progress").length}</b>in progress</div>`+
    `<div class="stat"><b>${open.filter(t=>t.status==="waiting_user").length}</b>waiting on you</div>`+
    `<div class="stat bad"><b>${open.filter(over).length}</b>overdue</div>`+
    `<div class="stat"><b>${D.tasks.filter(t=>t.status==="done"&&t.completed_at&&now-Date.parse(t.completed_at)<864e5).length}</b>done today</div>`;
  document.getElementById("gen").textContent = "Updated "+local(D.generated)+" · auto-refreshes every 30s";
  const visIds=new Set(vis.map(t=>t.id));
  // a card is a top-level task (no parent, or parent not visible); sub-tasks render inside parent card AND own column if parent hidden
  const roots = vis.filter(t=>!t.parent || !visIds.has(t.parent));
  const lanes={}; roots.forEach(t=>(lanes[t.area]??=[]).push(t));
  const m=document.getElementById("main");
  if(!roots.length){ m.innerHTML=`<div class="empty">Nothing here. Tasks appear as soon as a Claude session captures them.</div>`; return; }
  m.innerHTML = Object.keys(lanes).sort().map(ar=>{
    const ts=lanes[ar];
    return `<section class="lane"><h2>${esc(ar)} <span class="n">${ts.length} task${ts.length>1?"s":""}</span></h2><div class="cols">`+
      COLS.map((st,i)=>`<div class="col"><h3>${COLNAME[i]}</h3>`+
        ts.filter(t=>st.includes(t.status)).sort((a,b)=>(a.priority||"").localeCompare(b.priority||"")||a.id.localeCompare(b.id)).map(card).join("")+`</div>`).join("")+
      `</div></section>`;
  }).join("");
}
function card(t){
  const kids=D.tasks.filter(k=>k.parent===t.id);
  const kd = kids.length? `<ul class="kids">`+kids.map(k=>`<li><span class="dot ${k.status}"></span><span>${esc(k.id)} ${esc(k.title)} <span style="color:var(--muted)">· ${k.status==="done"?"done":"ETA "+fm(k.eta_minutes)}${over(k)?" ⚠":""}</span></span></li>`).join("")+`</ul>`:"";
  const par = t.parent&&byId[t.parent] ? `<div class="sub">↳ part of ${esc(t.parent)} ${esc(byId[t.parent].title)}</div>`:"";
  return `<div class="card s-${t.status}" onclick="openT('${t.id}')">
    <div class="top"><span>${esc(t.id)} · ${esc(t.priority||"")}</span><span>${L[t.status]}</span></div>
    <div class="title">${esc(t.title)}</div>${par}
    <div class="sub">${etaHtml(t)}</div>
    ${t.next_action&&OPEN.has(t.status)?`<div class="sub">Next: ${esc(t.next_action)}</div>`:""}
    ${kd}
    <div class="who"><span>created by ${esc(sl(t.created_by))}</span>${t.claimed_by&&OPEN.has(t.status)?`<span>worked by ${esc(sl(t.claimed_by))}</span>`:""}${t.completed_by?`<span class="c">completed by ${esc(sl(t.completed_by))}</span>`:""}</div>
  </div>`;
}
function openT(id){
  const t=byId[id]; const d=document.getElementById("dlg");
  const row=(k,v)=>`<tr><td>${k}</td><td>${v}</td></tr>`;
  d.innerHTML=`<h2>${esc(t.id)} · ${esc(t.title)}</h2><div style="color:var(--muted);font-size:13px">${esc(t.area)} · ${L[t.status]} · ${esc(t.priority||"")}</div>
  <table>${row("ETA",etaHtml(t))}${row("Due",local(t.due_at))}${row("Next action",esc(t.next_action)||"—")}
  ${row("Parent",t.parent?esc(t.parent+" "+(byId[t.parent]?.title||"")):"—")}
  ${row("Created",local(t.created_at)+" by "+slink(t.created_by))}
  ${row("Worked by",slink(t.claimed_by))}${row("Completed",t.completed_at?local(t.completed_at)+" by "+slink(t.completed_by):"—")}
  ${row("Source",esc(t.source)||"—")}
  ${row("Outputs",(t.outputs||[]).map(o=>/^https?:|^file:/.test(o)?`<a href="${esc(o)}" target="_blank">${esc(o)}</a>`:esc(o)).join("<br>")||"—")}</table>
  <div class="log">${(t.log||[]).slice().reverse().map(e=>`<div><time>${local(e.at)}</time><b>${esc(sl(e.session))}</b> — ${esc(e.msg)}</div>`).join("")}</div>
  <p style="text-align:right;margin:12px 0 0"><button onclick="dlg.close()">Close</button></p>`;
  d.showModal();
}
fillFilters(); render();
["q","area","sess","showdone"].forEach(k=>document.getElementById(k).addEventListener("input",render));
setTimeout(()=>{ if(!document.getElementById("dlg").open) location.reload(); else setInterval(()=>{ if(!dlg.open) location.reload(); },5000); },30000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
