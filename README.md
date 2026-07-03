# Overleaf Git Sync

Guarded Git synchronization for Overleaf projects edited by AI agents.

`overleaf-git-sync` is a small CLI plus Claude/Codex skill that keeps an Overleaf Git project fresh before an agent reads or edits LaTeX files, then commits and pushes only the files the agent changed.

It is the Git-based sibling of a Dropbox-sync workflow: instead of relying on Dropbox to move files in the background, the agent runs a safe `git fetch` / fast-forward before editing and a scoped `git commit && git push` afterward.

## Who It Is For

Use this if:

- you use Overleaf's Git integration (`https://git.overleaf.com/<project_id>`);
- you edit the paper locally with Claude Code, Codex, or another coding agent;
- you also edit on overleaf.com and want the agent to avoid stale local files;
- you want agent edits pushed back to Overleaf automatically through Git.

You do not need this if you only edit locally, only edit in the Overleaf web editor, or use Dropbox sync instead of Git.

## One-Line Agent Install Prompt

Paste this into Claude Code or Codex:

```text
Install Overleaf Git Sync from https://github.com/ashessqy126/overleaf-git-sync: first confirm `python3` and `git` are available, and install `tmux` too if I want supervised background watcher or automation health checks; then clone the repo, run `python3 scripts/install.py --all`, restart the agent, and use the `overleaf-git-sync` skill before editing Overleaf Git projects.
```

## Manual Install

```bash
git clone https://github.com/ashessqy126/overleaf-git-sync.git
cd overleaf-git-sync
python3 scripts/install.py --all
```

The installer:

- creates `~/.local/bin/overleaf-git-sync`;
- installs the Claude skill at `~/.claude/skills/overleaf-git-sync/SKILL.md`;
- adds a Claude Code `PreToolUse` hook for `Read|Edit|Write|MultiEdit`;
- installs the Codex plugin into `~/codex-plugins/plugins/overleaf-git-sync`;
- adds the plugin to `~/codex-plugins/.agents/plugins/marketplace.json`;
- registers `~/codex-plugins` as a Codex plugin marketplace when the `codex` CLI is available.

Restart Claude Code and start a new Codex thread after installation.

## Project Setup

In each Overleaf Git project:

```bash
cd /path/to/paper
git remote add overleaf https://git.overleaf.com/<project_id>
overleaf-git-sync init --remote overleaf --branch master
```

If your local branch is `main`, use `--branch main`. The command writes `.overleaf-git-sync.json` so hooks and agents know this repo is opted in.

## Agent Activation Prompts

After installation, paste one of these prompts into Claude Code or Codex from inside an Overleaf Git project. The agent should still follow the safety workflow: run `sync-before` before reading or editing supported LaTeX files, and use `sync-after` only for files it actually changed.

Set up a project:

```text
Use overleaf-git-sync: set up this Overleaf Git project for safe AI edits. Detect the Overleaf remote, initialize the marker, and report the configured remote and branch.
```

Safely edit and push a paper:

```text
Use overleaf-git-sync: sync this Overleaf Git project before editing, update main.tex, then commit and push only the changed LaTeX files back to Overleaf.
```

Commit existing local LaTeX edits:

```text
Use overleaf-git-sync: inspect the current LaTeX changes, then run sync-after with exactly the supported files I changed and push them to Overleaf.
```

Start near-real-time auto-pull:

```text
Use overleaf-git-sync: start supervised background auto-pull for this Overleaf project with watch-supervisor at a 5 second interval. Do not use a long-running subagent.
```

Add automation health checks:

```text
Use overleaf-git-sync: create a Codex automation that checks this project's supervised watcher every 5 minutes with watch-health --restart-missing --interval 5, and reports attention-needed states.
```

Check or stop background syncing:

```text
Use overleaf-git-sync: show the supervised watcher status and recent logs for this project.
```

```text
Use overleaf-git-sync: stop the supervised background watcher for this project.
```

Handle a pending same-file update:

```text
Use overleaf-git-sync: inspect the watcher status. If it reports a mergeable same-file remote update, run reconcile and report any conflict markers before continuing.
```

Install or inspect hooks:

```text
Use overleaf-git-sync: show the hook-config command and explain how the PreToolUse hook protects LaTeX reads and edits.
```

## Optional: Poll Like Dropbox

Git does not update your local working tree in the background by itself. If you want a Dropbox-like local auto-pull loop, run:

```bash
overleaf-git-sync watch . --interval 5
```

The watcher is deliberately pull-only:

- it runs `sync-before` every interval;
- it only fast-forwards from Overleaf;
- it shares a per-worktree lock with `sync-before`, `sync-after`, and `reconcile`, so a helper watcher and the main agent do not mutate the Git worktree at the same time;
- it lets Git pull non-overlapping remote changes even when other local files are dirty;
- when the same dirty file is also updated remotely, it performs a temporary dry-run merge and reports whether the update is line-mergeable or a same-position conflict;
- it never commits or pushes automatically.

Use this when you want Overleaf web edits to appear locally while you are mostly reading or waiting. If you want the older stricter behavior, pass `--require-clean` so the watcher skips whenever the worktree has local changes.

If watch reports that a same-file update appears mergeable, apply it explicitly:

```bash
overleaf-git-sync reconcile .
```

`reconcile` stashes local changes, fast-forwards to Overleaf, then pops the stash. If Git detects a true same-position conflict, it leaves conflict markers in the affected files and reports line ranges such as `section1.tex:143-158`.

## Supervised Watcher and Automation Health Checks

For a watcher that should survive outside the current agent turn, prefer the built-in supervisor instead of a long-running subagent:

```bash
overleaf-git-sync watch-supervisor start . --interval 5
```

The supervisor uses `tmux` to run the normal pull-only watcher in the background. It does not add a second sync path; it only manages the same `watch` command.

Useful supervisor commands:

```bash
overleaf-git-sync watch-supervisor status .
overleaf-git-sync watch-supervisor logs .
overleaf-git-sync watch-supervisor restart . --interval 5
overleaf-git-sync watch-supervisor stop .
```

For Codex automations or cron-style monitors, run a health check every few minutes:

```bash
overleaf-git-sync watch-health . --restart-missing --interval 5
```

`watch-health` checks that the supervised watcher is alive and that the latest watcher status is healthy. With `--restart-missing`, it restarts the supervised watcher if the session is gone. It reports attention-needed states such as pending conflicts, diverged history, repeated lock skips, stale output, or current fetch errors. It does not run `sync-before` itself, so it avoids creating a second polling loop.

Exit codes are intended for automation:

- `0`: watcher is running and healthy, or was restarted because it was missing;
- `1`: watcher is missing and `--restart-missing` was not passed;
- `2`: watcher is running but the latest status needs attention.

## Agent Workflow

Before editing supported files (`.tex`, `.bib`, `.cls`, `.sty`, `.bst`):

```bash
overleaf-git-sync sync-before main.tex
```

After editing, pass exactly the files the agent changed:

```bash
overleaf-git-sync sync-after main.tex refs.bib -m "Update paper"
```

`sync-after` stages only those files, commits them, and pushes `HEAD` to the configured Overleaf branch.

## Safety Rules

- Only runs in repos with a `git.overleaf.com` remote or `.overleaf-git-sync.json`.
- Uses a Git-directory lock (`overleaf-git-sync.lock`) so concurrent agent helpers do not pull, reconcile, commit, or push at the same time.
- `sync-before` requires a clean tracked worktree and only fast-forwards.
- Diverged history blocks the edit instead of merging automatically.
- `sync-after` refuses pre-staged changes.
- No `git add .`; only the files passed to `sync-after` are committed.
- Non-LaTeX untracked files are ignored by `sync-before`, but supported LaTeX files are protected.

## Claude Code

The installer adds a Claude Code hook to `~/.claude/settings.json`. The hook runs before Claude reads or edits supported LaTeX files. If Git state is unsafe, it exits non-zero and blocks the tool call.

To install without the hook:

```bash
python3 scripts/install.py --all --no-claude-hook
```

## Codex

The Codex plugin provides the `overleaf-git-sync` skill. In Codex, ask for it explicitly:

```text
Use overleaf-git-sync: sync this Overleaf Git project, edit main.tex, then commit and push the changed files back to Overleaf.
```

Codex hook support varies by version and platform, so the skill is the primary enforcement path. A hook entrypoint is still available:

```bash
overleaf-git-sync hook-config
```

## Commands

```text
overleaf-git-sync init [path] [--remote overleaf] [--branch master] [--lock-timeout 60]
overleaf-git-sync sync-before [path] [--force] [--allow-dirty] [--lock-timeout 60]
overleaf-git-sync sync-after [paths...] -m "message" [--no-push] [--all-latex] [--lock-timeout 60]
overleaf-git-sync watch [path] [--interval 5] [--require-clean] [--lock-timeout 60]
overleaf-git-sync watch-supervisor {start|stop|restart|status|logs} [path] [--interval 5]
overleaf-git-sync watch-health [path] [--restart-missing] [--interval 5]
overleaf-git-sync reconcile [path] [--lock-timeout 60]
overleaf-git-sync status [path] [--fetch] [--lock-timeout 60]
overleaf-git-sync hook [--lock-timeout 60]
overleaf-git-sync hook-config
```

## Dependencies

Core commands such as `init`, `sync-before`, `sync-after`, `watch`, `status`, and `reconcile` require:

- Python 3.8+
- Git
- Overleaf Git integration enabled for the project
- Git credentials configured for `https://git.overleaf.com/<project_id>`

Supervised background polling additionally requires:

- `tmux` for `watch-supervisor` and `watch-health`

Agent integrations are optional:

- Claude Code, if you want the installed Claude skill and PreToolUse hook
- Codex CLI, if you want the installer to register the local Codex plugin marketplace

## License

MIT
