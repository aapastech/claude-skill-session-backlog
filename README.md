# Aapastech Session Backlog

**AI assistants forget tasks in long sessions. This stops that.**

Give an assistant a long prompt with five things in it, dig into one of them for twenty turns, and the other four quietly disappear. Session Backlog gives every AI session a shared backlog on **your own computer**:

- **Capture before work.** Every task and sub-task is written down with an area, an ETA, and the session that created it, and played back to you with IDs like `T-0042`.
- **Re-read every turn.** Even in a deep dive on one sub-task, the assistant checks what else is open before it answers.
- **Nothing ends silently.** Tasks close only after the result is checked, and every reply ends with one line:

```
Still open: T-0044 Draft 2 pricing layouts (In progress, ETA 30m); T-0046 Reply to supplier (Waiting on you)
```

- **A live board.** `board.html` groups tasks by area into To do, In progress, Blocked, Waiting on you and Done. Overdue ETAs show in red, and each card shows which session created, worked on and finished it.
- **Parallel sessions are safe.** Each task is its own file and every change takes a short per-task lock, so two sessions never overwrite each other or both claim the same work.
- **Any assistant, any model.** It's a skill for assistants that read skill files, a plain command-line tool for anything that can run a shell command, and an MCP server for everything else.

## Your data stays on your computer

The backlog is a plain folder, `~/Documents/Aapastech-Backlog`, with one JSON file per task. Nothing is sent to a cloud service, a shared doc or a connector. Every session that can reach that computer uses the same backlog, whichever tool or model it runs on.

## Install

Pick the route that matches your tool. They all share the same backlog folder.

### Claude Code

```
claude plugin marketplace add aapastech/claude-skill-session-backlog
claude plugin install session-backlog@aapastech
```

### Claude app (web, desktop, mobile)

1. Download [`dist/session-backlog.zip`](https://github.com/aapastech/claude-skill-session-backlog/raw/main/dist/session-backlog.zip).
2. Open **Customize → Skills**, click **+**, choose **Create skill → Upload a skill**, and select the ZIP.
3. Make sure the skill is switched on.

### Other coding agents (Codex, Cursor, Copilot, Gemini CLI, …)

Copy `plugin/skills/session-backlog/` into the folder your agent loads skills or rules from. Agents that read the `SKILL.md` format pick it up as-is. For the rest, point your project or user instructions file (e.g. `AGENTS.md`) at `SKILL.md`.

### Any MCP client, or your own API-driven agent

`plugin/skills/session-backlog/scripts/backlog_mcp.py` is an MCP server that needs only Python. Add it to your client's MCP config:

```json
{
  "mcpServers": {
    "aapastech-backlog": {
      "command": "python3",
      "args": ["/path/to/session-backlog/scripts/backlog_mcp.py"]
    }
  }
}
```

On Windows use `"command": "py"` and `"args": ["-3", "C:\\path\\to\\backlog_mcp.py"]`. Set `"env": {"BACKLOG_DIR": "…"}` to use a folder other than `~/Documents/Aapastech-Backlog`.

The server tells the model how to use the backlog when it connects, so models that have never seen `SKILL.md` still follow the same capture → claim → update → done loop. For an agent you call through an API, run the server on the machine that owns the backlog and connect it with your framework's MCP client. Don't run it inside a temporary cloud sandbox: the backlog would vanish with the sandbox.

### Make it always-on (recommended)

Add this line to your assistant's personal preferences or user instructions file (for Claude Code, `~/.claude/CLAUDE.md`):

```
Always load the session-backlog skill at the start of every session and before any task with work in it.
```

## First use

Give your assistant a few tasks. The first time, it will:

1. create `~/Documents/Aapastech-Backlog` (a desktop app may ask for access to Documents first)
2. ask your name and the 3–8 areas your work falls into
3. capture your tasks and tell you where the board is

## Requirements

- **Python 3.8 or newer.** No packages to install.
  - **macOS:** if Python isn't installed yet, the first run may open Apple's "install command line developer tools" dialog. Accepting it is safe, or install Python from [python.org](https://www.python.org/downloads/).
  - **Windows:** install Python from [python.org](https://www.python.org/downloads/) with "Add python.exe to PATH" ticked, or run `winget install Python.Python.3.12`. The engine is run as `py -3 …`.
  - **Linux:** usually preinstalled (`python3`).
- **macOS, Windows and Linux** are all supported. Files are UTF-8 everywhere, so titles with dashes, accents or emoji are safe on every system.
- The assistant must be able to reach your computer: a local coding agent, a desktop app linked to the computer, or an MCP client running on it.
- If your computer is off or unreachable, the assistant keeps the tasks in the session, queues the updates, and writes them to the backlog once the computer is back.

## Upgrading from v2

v3 creates new backlogs in `~/Documents/Aapastech-Backlog`. An existing `~/Documents/Claude-Backlog` keeps working without changes: the skill and the MCP server look for it when the new folder doesn't exist, and upgrade its `bl.py` (your tasks and settings are untouched). To switch to the new name, close your other AI sessions, then rename the folder. To rename the old default area, run `bl.py areas --rename Claude-Ops AI-Ops`.

## What's inside

```
plugin/skills/session-backlog/SKILL.md                    the rules the assistant follows every session
plugin/skills/session-backlog/scripts/bl.py               the backlog engine (one stdlib-only Python file, board included)
plugin/skills/session-backlog/scripts/backlog_mcp.py      MCP server wrapping the engine, for any MCP client or model
plugin/.claude-plugin/plugin.json                         plugin manifest (Claude Code)
.claude-plugin/marketplace.json                           lets Claude Code install it by name
dist/session-backlog.zip                                  the skill packaged for upload
tests/test_backlog.py                                     stdlib test suite: python3 -m unittest discover -s tests
tools/build_dist.py                                       rebuilds dist/session-backlog.zip
```

`bl.py` has a small CLI (`init`, `where`, `session`, `batch`, `add`, `claim`, `update`, `done`, `drop`, `list`, `show`, `footer`, `board`, `areas`). See the command reference at the end of `SKILL.md`.

## License

MIT. Made by Shikha Gupta at [Aapastech](https://github.com/aapastech). Claude is a trademark of Anthropic; this project is independent and not affiliated with Anthropic.
