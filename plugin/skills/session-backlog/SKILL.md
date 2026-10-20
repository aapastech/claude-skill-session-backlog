---
name: session-backlog
description: ALWAYS load at the start of every session and before any task with work in it. Captures every task and sub-task into a backlog kept on the user's own computer, with area, ETA and session, and updates it every turn so no work is forgotten in long or parallel sessions.
---

# Aapastech Session Backlog: never lose a task

Long sessions and multi-task prompts make AI assistants drop work. Early asks fade while a later turn digs into one detail. This skill fixes that with a backlog that lives **on the user's own computer**. Conversation memory is not the source of truth; the backlog is. A task that isn't in the backlog doesn't exist, and a task in the backlog isn't finished until it's marked Done or Dropped.

It works with any assistant or agent that can either run shell commands on the user's computer or call MCP tools. Every session on that computer, from any tool or model, shares the same backlog.

**Privacy rule:** task data stays on the device that runs the work. Never copy the backlog to a cloud service, a hosted page, a connector or a shared doc. If no user device is reachable, use Offline mode (section 9) instead.

## 1. Find the backlog and run the engine

**If your tool list has `backlog_*` tools** (the MCP server, section 11), use them. They map one-to-one to the commands below and handle the folder, encoding and upgrades for you. Skip the rest of this section.

Otherwise the backlog is a folder containing `bl.py` (the engine), `config.json`, `tasks/` (one JSON file per task), `sessions/`, `board.html` and `BOARD.md`. Look for it in this order:
1. A location the user has told you about, in their preferences, memory or project instructions.
2. The `BACKLOG_DIR` environment variable, if it's set.
3. `~/Documents/Aapastech-Backlog` (the default).
4. `~/Documents/Claude-Backlog` (the folder name used before v3; keep using it if that's where the user's tasks are).

**Running it.** In this document `bl <command>` is shorthand. Always type the full command; never store it in a shell variable (`$BL …` fails in zsh, the default macOS shell).

| Shell | Command |
|---|---|
| macOS / Linux (bash, zsh) | `python3 ~/Documents/Aapastech-Backlog/bl.py <command> …` |
| Windows PowerShell | `py -3 "$HOME\Documents\Aapastech-Backlog\bl.py" <command> …` (if `py` is missing, try `python`) |
| Windows cmd | `py -3 "%USERPROFILE%\Documents\Aapastech-Backlog\bl.py" <command> …` |
| Git Bash on Windows | `py -3 ~/Documents/Aapastech-Backlog/bl.py <command> …` |

`bl where` prints the folder and engine version it is using. On Windows, `Documents` is often moved into OneDrive (`%USERPROFILE%\OneDrive\Documents`). If `~\Documents` doesn't exist, look there, and offer to set `BACKLOG_DIR` so every session finds the same folder.

**Python.** The engine needs Python 3.8+ and nothing else.
- macOS: the first `python3` call on a fresh Mac may open Apple's "install command line developer tools" dialog. Tell the user it's expected and safe to accept, or install Python from python.org.
- Windows: Python is usually not installed. If `py -3` and `python` both fail, ask the user to install Python 3 from python.org with "Add python.exe to PATH" ticked, or `winget install Python.Python.3.12`. Don't fall back to anything else.

## 2. First run on a device (bootstrap)

If no backlog exists yet:
1. Create the folder `~/Documents/Aapastech-Backlog` (or the user's chosen location).
2. Copy `scripts/bl.py` from this skill's own folder into it. It is a single stdlib-only Python file with the board built in.
3. Ask the user one short question: what name to show for tasks waiting on them, and which areas their work falls into (3–8 areas, e.g. clients, products, admin, personal). Suggest areas from what you already know about them. Then run:
   `bl init --owner "<name>" --areas "Area1,Area2,…,Personal,AI-Ops"`
   Add `--title "<Board title>"` if they want their own board name (default "Aapastech Backlog").
4. Tell them the board's location: `file://…/Aapastech-Backlog/board.html`. Offer to open it (`bl board --open`), and suggest they pin the tab.
5. Suggest they make the skill always-on, e.g. in their assistant's personal preferences or instructions file: *"Always load the session-backlog skill at the start of every session and before any task with work in it."*

**Upgrades:** if `bl version` prints a lower version than `scripts/bl.py` in this skill (look for `VERSION = "…"`), copy the new `bl.py` over the old one. Task data and config are untouched.

## 3. Session identity (once per session)

Pick a stable session id and reuse it on every call:
- **Always pass a deep link as `--url` when you can get one.** The board's "Open chat ↗" button uses it to drop the user straight back into this conversation:
  - Claude Code in the Claude desktop app: call the session tool `get_session` with `"self"` (load `mcp__ccd_session_mgmt__get_session` via tool search if deferred) and pass its `link` (`claude://claude.ai/epitaxy/local_…`).
  - claude.ai chat (web, desktop or phone): `https://claude.ai/chat/<conversation id>` if you can see it. Claude Code on the web: its `https://claude.ai/code/…` session URL.
  - Never guess or construct a link you weren't given. No link is better than a wrong one.
- If you started without a link and get one later, re-run `bl session` with the same id and `--url`.
- Otherwise: `<surface>-<YYYYMMDD-HHMM>` using the session start time, e.g. `desktop-20261018-1652`, `code-20261018-0910`, `api-20261018-1200`, `sched-20261019-0600`.

```
bl session --id <SID> --label "<Surface> · <main topic>" --surface code|desktop|cloud|api|scheduled [--url <link>]
```
This prints the session's open tasks plus unclaimed or stale tasks from elsewhere. Mention overdue or waiting-on-user tasks in one line. Don't dump the whole list.

## 4. The turn loop (every turn with work in it)

**A. Re-read first.** At the start of every turn after the first, run `bl open --session <SID>`. Do this especially when the prompt is a deep dive into one sub-task. This step is what stops other work being forgotten. Never skip it.

**B. Capture before work.** Every new ask gets captured in one batch before any work starts. That includes asides like "also…", "remind me…", "later…", "don't forget…". Write the batch to a JSON file with your file-writing tool, then pass the path. A file works in every shell; piping JSON through `echo` breaks in Windows shells and mangles non-ASCII text.
```json
[
 {"title":"Redesign pricing page","area":"Product","eta":"1h","priority":"P1",
  "source":"prompt 16:52: redo pricing page","next":"Pull current copy",
  "children":[{"title":"Audit current copy","eta":"10m"},
              {"title":"Draft 2 layouts","eta":"30m"},
              {"title":"Publish chosen layout","eta":"20m"}]},
 {"title":"Reply to supplier","area":"Admin","eta":"15m","status":"waiting_user","next":"User to confirm quantity"}
]
```
`bl batch --session <SID> <path-to-file.json>` (put the file in your scratch or temp folder, not the user's project).

Play the capture back in one compact line so the user can correct it:
> Captured: T-0042 Pricing page (1h) → T-0043 audit (10m), T-0044 layouts (30m), T-0045 publish (20m); T-0046 Supplier reply (waiting on you).

Check `bl list` first, and update an existing task instead of creating a duplicate. Add `"parent":"T-0042"` (or `--parent T-0042` on `bl add`) to put a sub-task under an existing task.

**C. Claim, then work.** Run `bl claim <ID> --session <SID> [--eta 20m]` before starting each task. If the claim is refused because another live session owns it, leave it alone. Claims are locked, so two sessions can never both win one. Work in parallel where you can. The orchestrating session updates the backlog for its subagents, passing each subagent its task ID.

**D. Update as you go.**
`bl update <ID> --session <SID> --log "decision / finding" [--next "…"] [--output <path or link>] [--status blocked|waiting_user|ready]`
- Record the user's decisions in their own words.
- Every file or link you produce goes in `--output`.
- Waiting on the user means `--status waiting_user --next "<exactly what you need>"`.

**E. Close honestly.** Run `bl done <ID> --session <SID> --log "delivered …" --output …` only after you've **verified** the result. A parent task can't close while its sub-tasks are open. To abandon a task, run `bl drop <ID> --session <SID> --reason "…"`. Never delete anything.

**E2. Schedule for later.** When the user deprioritises something to a date ("do the DMCA filing in November", "push credential rotation 2 weeks", "remind me about this next Monday"), don't drop it:
`bl schedule <ID> --session <SID> --at 2026-11-15 [--reason "…"]`, or use `--in 2w` / `--in 3d`. A bare date means 09:00 local time.
- The task and its open sub-tasks leave the active columns, counts, overdue list and session prompts, and sit in the board's **Scheduled** column until that time. They then come back by themselves, marked "back from schedule". No background job is needed.
- Record the user's reason in their words. If the task was in progress, it is released (it is not being worked until it returns).
- `bl schedule <ID> --session <SID> --clear` brings it back now. Claiming or starting a scheduled task also clears its schedule.
- When `bl open` shows a "back from schedule" section, tell the user in one line which tasks came back and why they were deferred.
- `bl list --scheduled` shows what's coming back, soonest first.

**F. Footer.** End every reply that involved work with the one-line output of `bl footer --session <SID> [--exclude <IDs just reported>]`.

## 5. Areas

Every top-level task gets an area from `config.json` (`bl areas` lists them). Sub-tasks inherit their parent's area. Pick the closest fit. Add a new area (`bl areas --add "Name"`) only when a new ongoing line of work clearly appears, and mention it when you do. `bl areas --rename OLD NEW` renames an area in the config and on every task.

## 6. ETA (every task has one)

- The ETA is the wall-clock time for the assistant to finish, assuming parallel work. It's usually 5–60 minutes, sometimes hours, and rarely days. Format: `10m`, `45m`, `2h`, `1h30m`, `1d`.
- Include time to verify the result.
- The countdown starts at claim or creation. The board flags overdue tasks in red.
- When you'll overrun, re-estimate straight away: `--eta 1h --eta-reason "…"`. Never let a task go silently overdue.
- For a `waiting_user` task, the ETA is the time to finish after the user replies.

## 7. Cross-session rules

- A claim stays live while its session keeps updating the task. After 24 hours untouched it's **stale**, and any session may take it over (the takeover is logged).
- Never use `--force` on a live claim without the user's OK.
- Before a long stretch of work ends, leave each claimed task with a `--next` a fresh session could act on cold. Release unfinished work with `--status ready --log "released: <state>"`.
- For "what's open", "where are we" or "what did the other session do", answer from `bl list` (`--all`, `--area X`, `--session Y`), not from conversation memory.
- The backlog belongs to one device. Sessions on other devices, or in the cloud, use it only through a link to that device.
- If a command says a task is busy, another session is writing it at that instant. Retry once after a moment.

## 8. When to skip capture

Skip capture only for a one-off question answered fully in the same reply. Capture everything else: anything with a deliverable, anything spanning more than one turn, and anything deferred. If in doubt, capture it.

## 9. Offline mode (no user device reachable)

1. Mirror the tasks in your platform's own task list if it has one, or a checklist in your reply if not.
2. Queue each intended `bl.py` operation, one JSON object per line, in `backlog-pending.jsonl` in your scratch space (never the user's project): `{"cmd":"add","args":{…},"at":"<iso>"}`.
3. Tell the user once: "Your computer isn't reachable, so backlog updates are queued and will sync when it's back."
4. Retry the device every turn. Once it's reachable, replay the queue in order and run `bl open`.
5. If the session ends before syncing, the last reply must list the unsynced tasks in full.

## 10. The board

`board.html` regenerates on every write and refreshes itself every 30 seconds. It shows:
- swimlanes by area, with columns To do / In progress / Blocked / Waiting on <owner> / Scheduled / Done
- an "Open chat ↗" link on each card that reopens the session working on it (or the one that scheduled or captured it), when that session registered a `--url`
- the ETA and countdown on each card, with overdue tasks in red
- which session created, worked on and completed each task
- a click-through to the full log and outputs

`BOARD.md` is the same board as text.

**When the user asks to see it** ("show my backlog", "open the board", "what's open?"):
- On the computer that holds the backlog, run `bl board --open` to open it in their default browser.
- Where no browser on that computer is reachable (e.g. a phone chat), paste a short summary from `bl list` grouped by area, with overdue and waiting-on-user tasks first.
- The board is a local file. It is never published, uploaded or shared.

## 11. Platform notes

- **MCP clients (any assistant or agent framework).** `scripts/backlog_mcp.py` is a stdlib MCP server over stdio exposing `backlog_session`, `backlog_open`, `backlog_add`, `backlog_claim`, `backlog_update`, `backlog_schedule`, `backlog_done`, `backlog_drop`, `backlog_list`, `backlog_show`, `backlog_footer`, `backlog_board`, `backlog_init` and `backlog_where`. Register it with command `python3` (Windows `py`, args `-3 <path>`) and args `<path to backlog_mcp.py>`, optionally with env `BACKLOG_DIR`. It runs the same `bl.py`, so the shell and MCP routes share one backlog.
- **API-driven agents.** A model called through an API has no access to the user's files by itself. The developer runs the MCP server (or `bl.py` behind their own tool) on the machine that owns the backlog and gives the model those tools. Hosted code sandboxes are temporary, so a backlog there is lost when the sandbox ends; don't keep it there.
- **Claude desktop / cloud / phone sessions linked to the user's computer** (device tools such as `device_bash`, `device_request_folder_access`, `device_commit_files`; load them via your tool search if they are deferred). The backlog folder or its parent (e.g. `~/Documents`) must be a connected folder; if it isn't, call `device_request_folder_access` once with the reason "to keep your task backlog on this computer". Then run commands through `device_bash` using the connected path (e.g. `python3 $HOME/mnt/Documents/Aapastech-Backlog/bl.py …`). To install `bl.py` through the bridge, stage the file and commit it to the device (`device_commit_files`).
- **Claude Code / Codex / Cursor / other coding agents** run the engine directly in their shell, per the table in section 1.

## Command reference
```
bl init    [--owner NAME] [--areas "A,B,C"] [--title T]       first-time setup / change owner, areas or board title
bl version                                                    engine version
bl where                                                      backlog folder, engine version, board link
bl areas   [--add NAME …] [--rename OLD NEW]                  list / add / rename areas
bl session --id SID --label L --surface S [--url U]           register a session; prints open tasks
bl open    --session SID                                      this session's open tasks + unclaimed ones
bl batch   --session SID file.json|-                          add many (nested "children" = sub-tasks)
bl add     --session SID --title T --area A --eta 20m [--parent ID] [--status S] [--next N] [--priority P1]
bl claim   ID --session SID [--eta 20m] [--force]
bl update  ID --session SID [--status S] [--eta E --eta-reason R] [--next N] [--log L] [--output O] [--area A] [--title T]
bl schedule ID --session SID (--at YYYY-MM-DD[THH:MM] | --in 2w | --clear) [--reason R]   defer until a date
bl done    ID --session SID [--log L] [--output O] [--force]
bl drop    ID --session SID --reason R
bl list    [--all] [--area A] [--session SID] [--scheduled] [--json]
bl show    ID
bl footer  --session SID [--exclude IDs]
bl board   [--open]                                            rebuild board.html / BOARD.md; --open shows it in the browser
```
Statuses: `inbox`, `ready`, `in_progress`, `blocked`, `waiting_user`, `done`, `dropped`. Priorities run from `P0` (urgent) to `P3`; the default is `P2`.
