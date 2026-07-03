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
Install Overleaf Git Sync from https://github.com/ashessqy126/overleaf-git-sync: clone it, run `python3 scripts/install.py --all`, then restart the agent and use the `overleaf-git-sync` skill before editing Overleaf Git projects.
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

## Optional: Poll Like Dropbox

Git does not update your local working tree in the background by itself. If you want a Dropbox-like local auto-pull loop, run:

```bash
overleaf-git-sync watch . --interval 5
```

The watcher is deliberately pull-only:

- it runs `sync-before` every interval;
- it only fast-forwards from Overleaf;
- it lets Git pull non-overlapping remote changes even when other local files are dirty;
- when the same dirty file is also updated remotely, it performs a temporary dry-run merge and reports whether the update is line-mergeable or a same-position conflict;
- it never commits or pushes automatically.

Use this when you want Overleaf web edits to appear locally while you are mostly reading or waiting. If you want the older stricter behavior, pass `--require-clean` so the watcher skips whenever the worktree has local changes.

If watch reports that a same-file update appears mergeable, apply it explicitly:

```bash
overleaf-git-sync reconcile .
```

`reconcile` stashes local changes, fast-forwards to Overleaf, then pops the stash. If Git detects a true same-position conflict, it leaves conflict markers in the affected files and reports line ranges such as `section1.tex:143-158`.

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
overleaf-git-sync init [path] [--remote overleaf] [--branch master]
overleaf-git-sync sync-before [path] [--force] [--allow-dirty]
overleaf-git-sync sync-after [paths...] -m "message" [--no-push] [--all-latex]
overleaf-git-sync watch [path] [--interval 5] [--require-clean]
overleaf-git-sync reconcile [path]
overleaf-git-sync status [path] [--fetch]
overleaf-git-sync hook
overleaf-git-sync hook-config
```

## Requirements

- Python 3.8+
- Git
- Overleaf Git integration enabled for the project
- Git credentials configured for `https://git.overleaf.com/<project_id>`

## License

MIT
