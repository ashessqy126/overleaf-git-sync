---
name: overleaf-git-sync
description: Safely work on Overleaf projects through Overleaf Git integration. Use when a user asks Codex to edit, sync, commit, push, or set up a LaTeX paper/project whose Git remote is Overleaf (`git.overleaf.com`), or when a repository has `.overleaf-git-sync.json`. Before editing supported Overleaf text files (`.tex`, `.bib`, `.cls`, `.sty`, `.bst`), run the bundled `overleaf_git_sync.py sync-before`; after editing, run `sync-after` with exactly the files Codex changed so the edits are committed and pushed to Overleaf without staging unrelated local work.
---

# Overleaf Git Sync

Use the `overleaf-git-sync` CLI when it is on `PATH`. If the command is not available and this skill is loaded from a Codex plugin checkout, use the bundled script at `scripts/overleaf_git_sync.py` relative to the plugin root; this skill lives at `skills/overleaf-git-sync/SKILL.md`, so the script is `../../scripts/overleaf_git_sync.py`.

## Safety Model

- Act only on repositories with an Overleaf remote URL containing `git.overleaf.com`, or repositories explicitly opted in with `.overleaf-git-sync.json`.
- Before editing `.tex`, `.bib`, `.cls`, `.sty`, or `.bst`, run `sync-before` on the file or project folder.
- `sync-before` requires a clean tracked worktree, fetches the Overleaf remote, and only fast-forwards. If the repo diverged or has local changes, stop and ask the user to resolve Git state.
- After editing, run `sync-after` with the exact files changed in this turn. It refuses to commit pre-staged changes, stages only those files, commits, and pushes `HEAD` to the Overleaf branch.
- `sync-before`, `sync-after`, `reconcile`, and `watch` share a per-worktree lock under the Git directory. If a helper agent is polling while the main agent commits or pulls, one side waits or reports that another sync is running instead of touching the worktree concurrently.
- Never use `git add .` for this workflow.

## Setup

If the repo already has an Overleaf remote, initialize the marker:

```bash
overleaf-git-sync init
```

If the remote has another name or branch:

```bash
overleaf-git-sync init --remote overleaf --branch master
```

If no remote exists, have the user add the Overleaf Git remote first:

```bash
git remote add overleaf https://git.overleaf.com/<project_id>
```

## Before Editing

Run this before reading for substantive reasoning or before editing supported files:

```bash
overleaf-git-sync sync-before <path-to-file-or-project>
```

If it errors, do not edit. Report the Git state and the safest next action.

## After Editing

Pass only the files Codex actually changed:

```bash
overleaf-git-sync sync-after paper.tex refs.bib -m "Update paper"
```

Use `--no-push` only if the user explicitly wants a local commit without updating Overleaf.

## Diagnostics

```bash
overleaf-git-sync status .
overleaf-git-sync status . --fetch
overleaf-git-sync watch . --interval 5
overleaf-git-sync watch . --interval 5 --once
overleaf-git-sync watch-supervisor start . --interval 5
overleaf-git-sync watch-health . --restart-missing --interval 5
overleaf-git-sync reconcile .
overleaf-git-sync hook-config
```

Use `watch` only when the user explicitly asks for Dropbox-like polling. It is pull-only and should be described as safe auto-pull, not as full bidirectional background sync. By default it allows non-overlapping remote updates while local files are dirty and lets Git refuse updates that would overwrite local work; use `--require-clean` only when the user wants stricter behavior.

For a long-running background watcher, prefer `watch-supervisor start` over a dedicated sync subagent. The supervisor uses `tmux` to keep the normal `watch` command running after the current agent turn. Supervised watchers disable interactive Git password prompts; if Overleaf Git credentials are not available non-interactively, report the authentication error and ask the user to configure credentials rather than waiting for input. Use `watch-supervisor status`, `watch-supervisor logs`, `watch-supervisor restart`, and `watch-supervisor stop` to inspect or manage it.

## Codex Automation Choice

If the user asks for scheduled reminders, current-thread follow-ups, periodic status updates, or
lightweight polling from the current conversation, create or update a current-thread heartbeat
(`kind=heartbeat`, `destination=thread`). Do not create a cron/workspace automation for this case.

For lightweight 5-minute sync polling in the current thread, make the heartbeat run one pull-only
polling iteration:

```bash
overleaf-git-sync watch . --interval 5 --once
```

The heartbeat prompt must say not to start a long-running watcher, not to run `watch-health
--restart-missing`, not to run `sync-before`/`sync-after`, and not to use raw Git commands. It
should report only whether the repo is up to date, whether a fast-forward happened, or whether user
action is needed.

Only create a cron/workspace automation when the user explicitly asks for a detached workspace job,
cron job, or project-level monitor that should run outside the current thread. If the user says
"in this thread", "remind me here", "come back to this chat", or otherwise implies conversational
follow-up, use a heartbeat. If the wording is ambiguous between detached workspace automation and
current-thread polling, default to a heartbeat and briefly state that choice.

For supervised background watchers, `watch-health . --restart-missing --interval 5` checks that
the supervised watcher is alive, restarts it if it is missing, and reports attention-needed states
such as pending conflicts, diverged history, stale output, repeated lock skips, or current fetch
errors. It does not run `sync-before` itself, so it should be treated as a watcher health check
rather than a second synchronization loop.

When the user asks to start a persistent background watcher, guarded sync, auto-pull, Dropbox-like
local polling, or a watcher guard for an Overleaf paper, perform the combined setup: start the
supervised tmux-backed watcher with `watch-supervisor start . --interval 5`, then create a
current-thread heartbeat (`kind=heartbeat`, `destination=thread`) that runs
`watch-health . --restart-missing --interval 5 --max-age-seconds 300`. Do not treat "start
background sync" as only starting the watcher, and do not create a detached cron/workspace
automation unless the user explicitly asks for one.

When the user asks to stop background sync, guarded sync, auto-pull, polling, a background watcher, or a watcher guard, stop both parts: stop the supervised tmux watcher with `watch-supervisor stop`, and delete the current-thread heartbeat for that watcher if one exists. Only operate on one part alone when the user explicitly says "only the heartbeat" or "only the tmux watcher".

When creating a Codex automation for an Overleaf paper task, lightweight polling loop, or watcher
health check, prefer a current-thread heartbeat (`kind=heartbeat`, `destination=thread`) so
follow-up status appears in the same paper-editing thread. Do not create a cron/workspace
automation that opens a fresh session every interval unless the user explicitly asks for a
detached workspace monitor or long-term project job. For short intervals such as 5 minutes, use a
heartbeat by default.

When creating or updating an automation for supervised background syncing, write the automation
prompt as a health monitor only. It must run `overleaf-git-sync watch-health . --restart-missing
--interval 5 --max-age-seconds 300`, and it must not run `overleaf-git-sync status . --fetch`,
`sync-before`, raw `git fetch`, raw `git pull`, `git stash`, `git add`, `git commit`, or `git
push`. The supervised watcher is the only component that polls Overleaf; the automation only
verifies and restarts that watcher.

Before starting supervised watcher automation, verify non-interactive Git credentials: `GIT_TERMINAL_PROMPT=0 git ls-remote --heads <remote>`. If it fails, configure the platform credential helper first: macOS uses `osxkeychain` or an absolute Xcode/CommandLineTools helper path for bundled Git, Linux may use `cache`, `store`, or `libsecret`, and Windows should use Git Credential Manager. Without `tmux`, do not use `watch-supervisor` or `watch-health`; the normal `sync-before`/`sync-after` workflow still works.

For first-time Overleaf Git credentials, instruct the user to generate an Overleaf Git authentication token, use username `git`, paste the token as the password during one interactive Git command such as `git ls-remote --heads origin`, then verify unattended access with `GIT_TERMINAL_PROMPT=0`.

When watch reports that a same-file remote update appears mergeable, run `reconcile` only after the user or task explicitly wants to apply it. `reconcile` may create conflict markers when the local and Overleaf changes touch the same position; if that happens, resolve those markers before any `sync-after`.

`hook` is provided as a PreToolUse entrypoint for environments that support wiring Codex hooks. The skill remains the primary enforcement path inside Codex because hook coverage differs by platform and Codex version.
