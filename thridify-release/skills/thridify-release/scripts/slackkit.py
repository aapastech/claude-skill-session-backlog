"""Block Kit helpers + Claude Automation bot poster for #release-thridify.

Token: $SLACK_BOT_TOKEN, else read from the installed thridify-slack-bot skill
file. Never printed. Posts with curl, because python.org 3.14 on the release
Mac ships no CA bundle (urllib fails TLS to slack.com).

Usage from a message script:
    import sys; sys.path.insert(0, "<this folder>")
    from slackkit import *
    post([header("..."), divider(), heading("Release notes"), group("Title", "bullet")], "fallback text")
"""
import json, os, re, subprocess, tempfile, glob

CHANNEL = "C07D2F85MEH"  # #release-thridify (private)

def _token():
    if os.environ.get("SLACK_BOT_TOKEN", "").startswith("xoxb-"):
        return os.environ["SLACK_BOT_TOKEN"]
    pats = glob.glob(os.path.expanduser(
        "~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills/thridify-slack-bot/SKILL.md"))
    for p in pats:
        m = re.search(r"xoxb-[A-Za-z0-9-]{20,}", open(p, encoding="utf-8").read())
        if m:
            return m.group(0)
    raise SystemExit("bot token not found")

# ---- rich text pieces -------------------------------------------------------
def t(s, bold=False, code=False, italic=False):
    el = {"type": "text", "text": s}
    st = {k: True for k, v in (("bold", bold), ("code", code), ("italic", italic)) if v}
    if st: el["style"] = st
    return el
def b(s): return t(s, bold=True)
def c(s): return t(s, code=True)
def a(url, label): return {"type": "link", "url": url, "text": label}
def e(name): return {"type": "emoji", "name": name}
def _norm(parts): return [t(p) if isinstance(p, str) else p for p in parts]
def line(*parts): return {"type": "rich_text_section", "elements": _norm(parts)}

def ul(*items, indent=0):
    return {"type": "rich_text_list", "style": "bullet", "indent": indent,
            "elements": [line(*i) if isinstance(i, (list, tuple)) else line(i) for i in items]}
def ol(*items, offset=0):
    return {"type": "rich_text_list", "style": "ordered", "indent": 0, "offset": offset,
            "elements": [line(*i) if isinstance(i, (list, tuple)) else line(i) for i in items]}
def rich(*els): return {"type": "rich_text", "elements": list(els)}

# ---- blocks -------------------------------------------------------------------
def header(s): return {"type": "header", "text": {"type": "plain_text", "text": s[:150], "emoji": True}}
def context(md): return {"type": "context", "elements": [{"type": "mrkdwn", "text": md}]}
def divider(): return {"type": "divider"}
def heading(s): return rich(line(b(s)))
def group(title, *bullets):
    """Bold group title followed by a bullet list, as one rich_text block."""
    return rich(line(b(title)), ul(*bullets))

# ---- api ----------------------------------------------------------------------
def api(method, payload):
    tok = _token()
    with tempfile.TemporaryDirectory() as d:
        body = os.path.join(d, "body.json"); out = os.path.join(d, "out.json")
        with open(body, "w", encoding="utf-8") as f: json.dump(payload, f)
        subprocess.run(["curl", "-s", "-X", "POST", f"https://slack.com/api/{method}",
                        "-H", "Content-Type: application/json; charset=utf-8",
                        "-H", "Authorization: Bearer " + tok,
                        "--data-binary", "@" + body, "-o", out], check=True)
        return json.load(open(out, encoding="utf-8"))

def post(blocks, text, thread_ts=None, update_ts=None, unfurl=False):
    assert len(blocks) <= 50, f"{len(blocks)} blocks > 50"
    p = {"channel": CHANNEL, "text": text, "blocks": blocks,
         "unfurl_links": unfurl, "unfurl_media": False}
    if thread_ts: p["thread_ts"] = thread_ts
    if update_ts:
        p["ts"] = update_ts; r = api("chat.update", p)
    else:
        r = api("chat.postMessage", p)
    print("ok:", r.get("ok"), "| error:", r.get("error", ""), "| ts:", r.get("ts", ""),
          "| detail:", json.dumps(r.get("response_metadata", {}))[:400] if not r.get("ok") else "")
    return r

def permalink(ts):
    r = api("chat.getPermalink", {"channel": CHANNEL, "message_ts": ts})
    return r.get("permalink", "")
