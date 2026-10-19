"""Rebuild dist/session-backlog.zip from plugin/skills/session-backlog (stdlib only).

Run from the repo root:  python3 tools/build_dist.py
"""
import os, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "plugin", "skills", "session-backlog")
OUT = os.path.join(ROOT, "dist", "session-backlog.zip")
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {".DS_Store"}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn in SKIP_FILES or fn.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, fn)
            # forward slashes inside the archive on every OS
            arc = "session-backlog/" + os.path.relpath(full, SRC).replace(os.sep, "/")
            z.write(full, arc)
print(OUT)
