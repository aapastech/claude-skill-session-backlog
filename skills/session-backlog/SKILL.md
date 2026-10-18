---
name: session-backlog
description: ALWAYS load at the start of every session and before any task with work in it. Captures every task and sub-task into a backlog kept on the user's own computer, with area, ETA and session, and updates it every turn so no work is forgotten in long or parallel sessions.
---

# Session backlog: never lose a task

Long sessions and multi-task prompts make Claude drop work. Early asks fade while a later turn digs into one detail. This skill fixes that with a backlog that lives **on the user's own computer**. Conversation memory is not the source of truth; the backlog is. A task that isn't in the backlog doesn't exist, and a task in the backlog isn't finished until it's marked Done or Dropped.

**Privacy rule:** task data stays on the device that runs the work. Never copy the backlog to a cloud service, a hosted page, a connector or a shared doc. If no user device is reachable, use Offline mode (section 9) instead.

## 1. Find the backlog

The backlog is a folder containing `bl.py` (the engine), `config.json`, `tasks/` (one JSON file per task), `sessions/`, `board.html` and `BOARD.md`.

Look for it in this order:
1. A location the user has told you about, in their preferences, memory or project instructions.
2. `~/Documents/Claude-Backlog` (the default).
3. The `BACKLOG_DIR` environment variable, if it's set.

From here on, `$BL` means `python3 <backlog folder>/bl.py`.

**How to run it, by surface:**

| You are running… | How |
|---|---|
| A local shell on the user's computer (Claude Code, terminal) | Run `$BL` directly. |
| Claude desktop or a cloud/phone session linked to the user's computer (device tools such as `device_bash`, `device_request_folder_access`; load them via ToolSearch if deferred) | The backlog folder (or its parent, e.g. `~/Documents`) must be a connected folder. If it isn't, call `device_request_folder_access` once with the reason "to keep your task backlog on this computer". Then run `device_bash`: `python3 $HOME/mnt/<folder>/…/bl.py`. |
| No user computer reachable (web or phone chat with no linked computer, Mac asleep or app closed) | Offline mode (section 9). Don't create the backlog anywhere else. |

## 2. First run on a device (bootstrap)

If no backlog exists yet:
1. Create the folder `~/Documents/Claude-Backlog`. In a linked desktop/cloud session, connect `~/Documents` first, then `mkdir` inside it.
2. Copy `scripts/bl.py` from this skill's own folder into the backlog folder. Use a plain copy in a local shell. Through a device bridge, stage the file and commit it to the device (e.g. write it to the outputs folder, then `device_commit_files`). `bl.py` is a single stdlib-only Python 3 file with the board built in. No installs are needed.
3. Ask the user one short question: what name to show for tasks waiting on them, and which areas their work falls into (3–8 areas, e.g. clients, products, admin, personal). Suggest areas from what you already know about them. Then run:
   `$BL init --owner "<name>" --areas "Area1,Area2,…,Personal,Claude-Ops"`
4. Tell them the board's location: `file://…/Claude-Backlog/board.html`. Offer to open it in their browser, and suggest they pin the tab.
5. Suggest they make the skill always-on by adding this line to their Claude personal preferences: *"Always load the session-backlog skill at the start of every session and before any task with work in it."*

**Upgrades:** if `$BL version` prints a lower version than `scripts/bl.py` in this skill (look for `VERSION = "…"`), copy the new `bl.py` over the old one. Task data and config are untouched.

## 3. Session identity (once per session)

Pick a stable session id and reuse it on every call:
- Cloud session: the `session_…` id from the session URL, if you know it. Pass the URL as `--url`.
- Otherwise: `<surface>-<YYYYMMDD-HHMM>` using the session start time, e.g. `desktop-20261018-1652`, `code-20261018-0910`, `sched-20261019-0600`.

```
$BL session --id <SID> --label "<Surface> · <main topic>" --surface cloud|desktop|code|scheduled [--url <link>]
```
This prints the session's open tasks plus unclaimed or stale tasks from elsewhere. Mention overdue or waiting-on-user tasks in one line. Don't dump the whole list.

## 4. The turn loop (every turn with work in it)

**A. Re-read first.** At the start of every turn after the first, run `$BL open --session <SID>`. Do this especially when the prompt is a deep dive into one sub-task. This step is what stops other work being forgotten. Never skip it.

**B. Capture before work.** Every new ask gets captured in one batch before any work starts. That includes asides like "also…", "remind me…", "later…", "don't forget…".
```
echo '[
 {"title":"Redesign pricing page","area":"Product","eta":"1h","priority":"P1",
  "source":"prompt 16:52: redo pricing page","next":"Pull current copy",
  "children":[{"title":"Audit current copy","eta":"10m"},
              {"title":"Draft 2 layouts","eta":"30m"},
              {"title":"Publish chosen layout","eta":"20m"}]},
 {"title":"Reply to supplier","area":"Admin","eta":"15m","status":"waiting_user","next":"User to confirm quantity"}
]' | $BL batch --session <SID>
```
Play the capture back in one compact line so the user can correct it:
> Captured: T-0042 Pricing page (1h) → T-0043 audit (10m), T-0044 layouts (30m), T-0045 publish (20m); T-0046 Supplier reply (waiting on you).

Check `$BL list` first, and update an existing task instead of creating a duplicate. Use `--parent T-0042` to add a sub-task under an existing task.

**C. Claim, then work.** Run `$BL claim <ID> --session <SID> [--eta 20m]` before starting each task. If the claim is refused because another live session owns the task, leave it alone. Work in parallel where you can. The orchestrating session updates the backlog for its subagents, passing each subagent its task ID.

**D. Update as you go.**
`$BL update <ID> --session <SID> --log "decision / finding" [--next "…"] [--output <path or link>] [--status blocked|waiting_user|ready]`
- Record the user's decisions in their own words.
- Every file or link you produce goes in `--output`.
- Waiting on the user means `--status waiting_user --next "<exactly what you need>"`.

**E. Close honestly.** Run `$BL done <ID> --session <SID> --log "delivered …" --output …` only after you've **verified** the result. A parent task can't close while its sub-tasks are open. To abandon a task, run `$BL drop <ID> --session <SID> --reason "…"`. Never delete anything.

**F. Footer.** End every reply that involved work with the one-line output of `$BL footer --session <SID> [--exclude <IDs just reported>]`.

## 5. Areas

Every top-level task gets an area from `config.json` (`$BL areas` lists them). Sub-tasks inherit their parent's area. Pick the closest fit. Add a new area (`$BL areas --add "Name"`) only when a new ongoing line of work clearly appears, and mention it when you do.

## 6. ETA (every task has one)

- The ETA is the wall-clock time for Claude to finish, assuming parallel work. It's usually 5–60 minutes, sometimes hours, and rarely days. Format: `10m`, `45m`, `2h`, `1h30m`, `1d`.
- Include time to verify the result.
- The countdown starts at claim or creation. The board flags overdue tasks in red.
- When you'll overrun, re-estimate straight away: `--eta 1h --eta-reason "…"`. Never let a task go silently overdue.
- For a `waiting_user` task, the ETA is the time to finish after the user replies.

## 7. Cross-session rules

- A claim stays live while its session keeps updating the task. After 24 hours untouched it's **stale**, and any session may take it over (the takeover is logged).
- Never use `--force` on a live claim without the user's OK.
- Before a long stretch of work ends, leave each claimed task with a `--next` a fresh session could act on cold. Release unfinished work with `--status ready --log "released: <state>"`.
- For "what's open", "where are we" or "what did the other session do", answer from `$BL list` (`--all`, `--area X`, `--session Y`), not from conversation memory.
- The backlog belongs to one device. Sessions on other devices, or in the cloud, use it only through a link to that device.

## 8. When to skip capture

Skip capture only for a one-off question answered fully in the same reply. Capture everything else: anything with a deliverable, anything spanning more than one turn, and anything deferred. If in doubt, capture it.

## 9. Offline mode (no user device reachable)

1. Mirror the tasks in the session's own task list (TaskCreate/TaskUpdate, or a checklist in your reply if those tools are missing).
2. Queue each intended `bl.py` operation, one JSON object per line, in `backlog-pending.jsonl` in the session's scratch space: `{"cmd":"add","args":{…},"at":"<iso>"}`.
3. Tell the user once: "Your computer isn't reachable, so backlog updates are queued. Open the Claude desktop app to sync."
4. Retry the device every turn. Once it's reachable, replay the queue in order and run `$BL open`.
5. If the session ends before syncing, the last reply must list the unsynced tasks in full.

## 10. The board

`board.html` regenerates on every write and refreshes itself every 30 seconds. It shows:
- swimlanes by area, with columns To do / In progress / Blocked / Waiting on <owner> / Done
- the ETA and countdown on each card, with overdue tasks in red
- which session created, worked on and completed each task
- a click-through to the full log and outputs

`BOARD.md` is the same board as text.

**When the user asks to see it** ("show my backlog", "open the board", "what's open?"):
- On the computer that holds the backlog, run `$BL board --open` to open it in their default browser. Through a device bridge, open `file://<backlog folder>/board.html` with the browser tools available on that computer.
- Where no browser on that computer is reachable (e.g. a phone chat), paste a short summary from `$BL list` grouped by area, with overdue and waiting-on-user tasks first.
- The board is a local file. It is never published, uploaded or shared.

## Command reference
```
$BL init    [--owner NAME] [--areas "A,B,C"]                first-time setup / change owner or areas
$BL version                                               engine version
$BL areas   [--add NAME …]                                list / add areas
$BL session --id SID --label L --surface S [--url U]      register a session; prints open tasks
$BL open    --session SID                                 this session's open tasks + unclaimed ones
$BL batch   --session SID file.json|-                     add many (nested "children" = sub-tasks)
$BL add     --session SID --title T --area A --eta 20m [--parent ID] [--status S] [--next N] [--priority P1]
$BL claim   ID --session SID [--eta 20m] [--force]
$BL update  ID --session SID [--status S] [--eta E --eta-reason R] [--next N] [--log L] [--output O] [--area A] [--title T]
$BL done    ID --session SID [--log L] [--output O] [--force]
$BL drop    ID --session SID --reason R
$BL list    [--all] [--area A] [--session SID] [--json]
$BL show    ID
$BL footer  --session SID [--exclude IDs]
$BL board   [--open]                                       rebuild board.html / BOARD.md; --open shows it in the browser
```
Statuses: `inbox`, `ready`, `in_progress`, `blocked`, `waiting_user`, `done`, `dropped`. Priorities run from `P0` (urgent) to `P3`; the default is `P2`.
