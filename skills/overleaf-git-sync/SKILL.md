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
overleaf-git-sync hook-config
```

Use `watch` only when the user explicitly asks for Dropbox-like polling. It is pull-only and should be described as safe auto-pull, not as full bidirectional background sync. By default it allows non-overlapping remote updates while local files are dirty and lets Git refuse updates that would overwrite local work; use `--require-clean` only when the user wants stricter behavior.

`hook` is provided as a PreToolUse entrypoint for environments that support wiring Codex hooks. The skill remains the primary enforcement path inside Codex because hook coverage differs by platform and Codex version.
