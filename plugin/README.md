# Session Backlog: a Claude skill

**Claude forgets tasks in long sessions. This skill stops that.**

Give Claude a long prompt with five things in it, dig into one of them for twenty turns, and the other four quietly disappear. This skill gives Claude a backlog on **your own computer**:

- **Capture before work.** Every task and sub-task is written down with an area, an ETA, and the session that created it. Claude plays it back to you with IDs like `T-0042`.
- **Re-read every turn.** Even in a deep dive on one sub-task, Claude checks what else is open before it answers.
- **Nothing ends silently.** Tasks close only after the result is checked, and every reply ends with one line:

```
Still open: T-0044 Draft 2 pricing layouts (In progress, ETA 30m); T-0046 Reply to supplier (Waiting on you)
```

- **A live board.** `board.html` groups tasks by area into To do, In progress, Blocked, Waiting on you and Done. Overdue ETAs show in red, and each card shows which Claude session created, worked on and finished it.
- **Parallel sessions are safe.** Each task is its own file, and sessions claim a task before working on it, so two sessions never overwrite each other or do the same work.

## Your data stays on your computer

The backlog is a plain folder, `~/Documents/Claude-Backlog`, with one JSON file per task. Nothing is sent to a cloud service, a shared doc or a connector. Every Claude session that can reach that computer uses the same backlog: Claude Code, the desktop app, and cloud or phone sessions linked to the computer.

## Install

### Claude Code

```
claude plugin marketplace add aapastech/claude-skill-session-backlog
claude plugin install session-backlog@aapastech
```

Or from inside a session: `/plugin install session-backlog --marketplace aapastech/claude-skill-session-backlog`

### Claude app (web, desktop, mobile)

1. Download [`dist/session-backlog.zip`](https://github.com/aapastech/claude-skill-session-backlog/raw/main/dist/session-backlog.zip).
2. In Claude, open **Customize → Skills**, click **+**, choose **Create skill → Upload a skill**, and select the ZIP.
3. Make sure the skill is switched on.

### Make it always-on (recommended)

Add this line to your Claude personal preferences (Settings → Profile), and for Claude Code to `~/.claude/CLAUDE.md`:

```
Always load the session-backlog skill at the start of every session and before any task with work in it.
```

## First use

Give Claude a few tasks. The first time, Claude will:

1. create `~/Documents/Claude-Backlog` (in the desktop app it asks for access to Documents first)
2. ask your name and the 3–8 areas your work falls into
3. capture your tasks and tell you where the board is

## Requirements

- Claude must be able to reach your computer: Claude Code, or the Claude desktop app with your computer linked.
- Python 3, which comes with macOS and Linux. Nothing else needs installing.
- If your computer is off or unreachable, Claude keeps the tasks in the session, queues the updates, and writes them to the backlog once the computer is back.

## License

MIT. Made by Shikha Gupta at [Aapastech](https://github.com/aapastech).
