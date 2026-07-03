#!/usr/bin/env python3
"""Guarded Git synchronization for Overleaf projects around AI edits."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Iterable


SUPPORTED_EXTS = (".tex", ".bib", ".cls", ".sty", ".bst")
MARKER = ".overleaf-git-sync.json"
DEFAULT_BRANCH = "master"
DEFAULT_DEBOUNCE_SECONDS = 30
OVERLEAF_REMOTE_HOST = "git.overleaf.com"


class SyncError(RuntimeError):
    """A safety condition failed and the caller should stop."""


class NoProject(RuntimeError):
    """The path is not an opted-in Overleaf Git project."""


def data_dir() -> pathlib.Path:
    raw = os.environ.get("OVERLEAF_GIT_SYNC_DATA_DIR")
    return pathlib.Path(raw).expanduser() if raw else pathlib.Path.home() / ".overleaf-git-sync"


def state_file() -> pathlib.Path:
    return data_dir() / "state.json"


def git(repo: pathlib.Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and proc.returncode != 0:
        cmd = "git " + " ".join(args)
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SyncError(f"{cmd} failed: {detail}")
    return proc


def existing_dir_for(path: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute():
        p = pathlib.Path.cwd() / p
    p = p.resolve(strict=False)
    if p.exists() and p.is_dir():
        return p
    cur = p.parent if p.suffix or not p.exists() else p
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    if cur.exists() and cur.is_dir():
        return cur
    return pathlib.Path.cwd()


def repo_root_for(path: str | pathlib.Path) -> pathlib.Path:
    base = existing_dir_for(path)
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise NoProject(f"{path} is not inside a Git repository")
    return pathlib.Path(proc.stdout.strip()).resolve()


def load_marker(repo: pathlib.Path) -> dict:
    marker = repo / MARKER
    if not marker.is_file():
        return {}
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"{marker} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SyncError(f"{marker} must contain a JSON object")
    return payload


def is_overleaf_url(url: str) -> bool:
    return OVERLEAF_REMOTE_HOST in (url or "").lower()


def remote_urls(repo: pathlib.Path) -> dict[str, list[str]]:
    proc = git(repo, ["remote", "-v"])
    remotes: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], []).append(parts[1])
    return remotes


def current_branch(repo: pathlib.Path) -> str | None:
    proc = git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    branch = proc.stdout.strip()
    return branch or None


def resolve_project(
    path: str | pathlib.Path,
    *,
    remote_override: str | None = None,
    branch_override: str | None = None,
    require_opt_in: bool = True,
) -> tuple[pathlib.Path, str, str, str]:
    repo = repo_root_for(path)
    marker = load_marker(repo)
    if marker.get("enabled") is False:
        raise NoProject(f"{repo} has {MARKER} with enabled=false")

    remotes = remote_urls(repo)
    remote = remote_override or marker.get("remote")
    opt_in_source = "marker" if marker else ""

    if remote and remote not in remotes:
        raise SyncError(f"remote {remote!r} does not exist in {repo}")

    if not remote:
        for name, urls in remotes.items():
            if any(is_overleaf_url(url) for url in urls):
                remote = name
                opt_in_source = "overleaf-remote"
                break

    if not remote:
        if require_opt_in:
            raise NoProject(
                f"{repo} is not opted in: add an Overleaf remote ({OVERLEAF_REMOTE_HOST}) "
                f"or run `overleaf-git-sync init`"
            )
        raise SyncError("no remote selected")

    remote_has_overleaf_url = any(is_overleaf_url(url) for url in remotes.get(remote, []))
    if require_opt_in and not marker and not remote_has_overleaf_url:
        raise NoProject(
            f"{repo} remote {remote!r} is not an Overleaf remote and no {MARKER} marker exists"
        )

    branch = branch_override or marker.get("branch") or current_branch(repo) or DEFAULT_BRANCH
    return repo, str(remote), str(branch), opt_in_source or "explicit"


def supported_path(path: str | pathlib.Path) -> bool:
    return pathlib.Path(path).suffix.lower() in SUPPORTED_EXTS


def rel_to_repo(repo: pathlib.Path, raw: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(raw).expanduser()
    if not p.is_absolute():
        p = pathlib.Path.cwd() / p
    resolved = p.resolve(strict=False)
    try:
        return resolved.relative_to(repo.resolve())
    except ValueError as exc:
        raise SyncError(f"{raw} is outside repository {repo}") from exc


def parse_porcelain(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((status, path))
    return entries


def dirty_entries(repo: pathlib.Path) -> list[tuple[str, str]]:
    return parse_porcelain(git(repo, ["status", "--porcelain"]).stdout)


def ensure_safe_to_pull(repo: pathlib.Path, *, allow_dirty: bool = False) -> None:
    if allow_dirty:
        return
    blocking = []
    for status, path in dirty_entries(repo):
        untracked = status == "??"
        if untracked and not supported_path(path):
            continue
        if untracked or status.strip():
            blocking.append(f"{status} {path}")
    if blocking:
        sample = "\n  ".join(blocking[:12])
        raise SyncError(
            "worktree has local changes that could be overwritten by a pull. "
            "Commit, stash, or run sync-after first:\n  " + sample
        )


def rev(repo: pathlib.Path, ref: str) -> str:
    return git(repo, ["rev-parse", "--verify", ref]).stdout.strip()


def is_ancestor(repo: pathlib.Path, older: str, newer: str) -> bool:
    return git(repo, ["merge-base", "--is-ancestor", older, newer], check=False).returncode == 0


def load_state() -> dict:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        if os.name == "posix":
            os.chmod(tmp, 0o600)
            os.chmod(path.parent, 0o700)
    except OSError:
        pass
    os.replace(tmp, path)


def state_key(repo: pathlib.Path, remote: str, branch: str) -> str:
    return f"{repo.resolve()}::{remote}::{branch}"


def is_debounced(repo: pathlib.Path, remote: str, branch: str, seconds: int) -> bool:
    if seconds <= 0:
        return False
    ts = load_state().get(state_key(repo, remote, branch), 0)
    return (time.time() - float(ts or 0)) < seconds


def mark_synced(repo: pathlib.Path, remote: str, branch: str) -> None:
    state = load_state()
    state[state_key(repo, remote, branch)] = time.time()
    save_state(state)


def sync_before(
    path: str | pathlib.Path,
    *,
    remote_override: str | None = None,
    branch_override: str | None = None,
    force: bool = False,
    allow_dirty: bool = False,
    debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
) -> str:
    repo, remote, branch, source = resolve_project(
        path, remote_override=remote_override, branch_override=branch_override
    )
    if not force and is_debounced(repo, remote, branch, debounce_seconds):
        return f"debounced: {repo} ({remote}/{branch})"

    ensure_safe_to_pull(repo, allow_dirty=allow_dirty)
    git(repo, ["fetch", "--prune", remote, branch])
    head = rev(repo, "HEAD")
    fetched = rev(repo, "FETCH_HEAD")

    if head == fetched:
        mark_synced(repo, remote, branch)
        return f"up to date: {repo} ({remote}/{branch}, {source})"
    if is_ancestor(repo, head, "FETCH_HEAD"):
        git(repo, ["merge", "--ff-only", "FETCH_HEAD"])
        mark_synced(repo, remote, branch)
        return f"fast-forwarded: {repo} to {remote}/{branch}"
    if is_ancestor(repo, "FETCH_HEAD", "HEAD"):
        mark_synced(repo, remote, branch)
        return f"local ahead of Overleaf: {repo} ({remote}/{branch})"

    raise SyncError(
        f"{repo} has diverged from {remote}/{branch}. Resolve with Git before AI edits."
    )


def ensure_clean_index(repo: pathlib.Path) -> None:
    staged = git(repo, ["diff", "--cached", "--name-only"]).stdout.splitlines()
    if staged:
        sample = "\n  ".join(staged[:12])
        raise SyncError(
            "index already has staged changes. Unstage or commit them before sync-after:\n  "
            + sample
        )


def changed_supported_files(repo: pathlib.Path, roots: Iterable[str]) -> list[pathlib.Path]:
    targets: set[pathlib.Path] = set()
    for raw in roots:
        rel = rel_to_repo(repo, raw)
        full = repo / rel
        if full.is_dir():
            prefix = rel.as_posix().rstrip("/")
            status_args = ["status", "--porcelain", "--", prefix]
            entries = parse_porcelain(git(repo, status_args).stdout)
            for _, path in entries:
                if supported_path(path):
                    targets.add(pathlib.Path(path))
        else:
            if not supported_path(rel):
                raise SyncError(f"{rel} is not a supported Overleaf text file")
            status = git(repo, ["status", "--porcelain", "--", rel.as_posix()]).stdout
            if status.strip():
                targets.add(rel)
    return sorted(targets, key=lambda p: p.as_posix())


def all_changed_supported_files(repo: pathlib.Path) -> list[pathlib.Path]:
    targets = [pathlib.Path(path) for _, path in dirty_entries(repo) if supported_path(path)]
    return sorted(set(targets), key=lambda p: p.as_posix())


def remote_is_not_ahead(repo: pathlib.Path, remote: str, branch: str) -> None:
    git(repo, ["fetch", "--prune", remote, branch])
    if not is_ancestor(repo, "FETCH_HEAD", "HEAD"):
        raise SyncError(
            f"{remote}/{branch} has new commits. Run sync-before on a clean worktree, "
            "resolve any conflicts, then retry sync-after."
        )


def sync_after(
    paths: list[str],
    *,
    remote_override: str | None = None,
    branch_override: str | None = None,
    message: str,
    push: bool = True,
    all_latex: bool = False,
) -> str:
    base_path = paths[0] if paths else "."
    repo, remote, branch, _ = resolve_project(
        base_path, remote_override=remote_override, branch_override=branch_override
    )
    remote_is_not_ahead(repo, remote, branch)
    ensure_clean_index(repo)

    targets = all_changed_supported_files(repo) if all_latex else changed_supported_files(repo, paths)
    if not targets:
        return f"no supported file changes to commit in {repo}"

    for rel in targets:
        git(repo, ["add", "--", rel.as_posix()])

    staged = [pathlib.Path(p) for p in git(repo, ["diff", "--cached", "--name-only"]).stdout.splitlines()]
    target_set = {p.as_posix() for p in targets}
    stray = [p.as_posix() for p in staged if p.as_posix() not in target_set]
    if stray:
        git(repo, ["reset", "--mixed", "--", *[p.as_posix() for p in staged]], check=False)
        raise SyncError("refusing to commit files outside requested target set: " + ", ".join(stray))

    if not staged:
        return f"no staged changes in {repo}"

    git(repo, ["commit", "-m", message])
    commit = rev(repo, "HEAD")[:12]
    if push:
        git(repo, ["push", remote, f"HEAD:{branch}"])
        return f"committed {commit} and pushed to {remote}/{branch}: " + ", ".join(target_set)
    return f"committed {commit} without push: " + ", ".join(target_set)


def local_tracked_patch(repo: pathlib.Path) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary", "HEAD", "--"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise SyncError(proc.stderr.decode("utf-8", "replace").strip() or "git diff failed")
    return proc.stdout


def commit_all_for_probe(repo: pathlib.Path) -> None:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "overleaf-git-sync",
        "GIT_AUTHOR_EMAIL": "overleaf-git-sync@example.invalid",
        "GIT_COMMITTER_NAME": "overleaf-git-sync",
        "GIT_COMMITTER_EMAIL": "overleaf-git-sync@example.invalid",
    })
    git(repo, ["add", "-A"])
    proc = subprocess.run(
        ["git", "-C", str(repo), "commit", "--no-gpg-sign", "-m", "overleaf-git-sync conflict probe"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SyncError(f"conflict probe commit failed: {detail}")


def conflict_marker_ranges(root: pathlib.Path, paths: Iterable[str]) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    for raw in paths:
        path = root / raw
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = None
        for idx, line in enumerate(lines, start=1):
            if line.startswith("<<<<<<< "):
                start = idx
            elif line.startswith(">>>>>>> ") and start is not None:
                ranges.append((raw, start, idx))
                start = None
    return ranges


def format_conflict_ranges(ranges: list[tuple[str, int, int]], *, limit: int = 8) -> str:
    if not ranges:
        return "same-position conflict detected, but no conflict markers were found"
    items = [f"{path}:{start}-{end}" for path, start, end in ranges[:limit]]
    extra = f" (+{len(ranges) - limit} more)" if len(ranges) > limit else ""
    return "same-position conflict at " + ", ".join(items) + extra


def dry_run_conflict_report(repo: pathlib.Path, fetched_ref: str = "FETCH_HEAD") -> tuple[str, str]:
    """Return (status, detail) for applying local dirty changes over fetched_ref.

    status is one of: clean, conflict, no-local-patch, inconclusive.
    This uses a temporary linked worktree and never touches the user's real
    working tree.
    """
    patch = local_tracked_patch(repo)
    if not patch.strip():
        return "no-local-patch", "no tracked local patch to test"
    fetched = rev(repo, fetched_ref)
    tmp_parent = pathlib.Path(tempfile.mkdtemp(prefix="overleaf-git-sync-"))
    tmp = tmp_parent / "probe"
    try:
        git(repo, ["worktree", "add", "--detach", "--quiet", str(tmp), "HEAD"])
        apply_proc = subprocess.run(
            ["git", "-C", str(tmp), "apply", "--3way"],
            input=patch,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if apply_proc.returncode != 0:
            detail = apply_proc.stderr.decode("utf-8", "replace").strip()
            return "inconclusive", detail or "could not replay local patch in probe worktree"
        commit_all_for_probe(tmp)
        merge_proc = subprocess.run(
            ["git", "-C", str(tmp), "merge", "--no-commit", "--no-ff", fetched],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if merge_proc.returncode == 0:
            return "clean", "same-file remote update appears mergeable"
        conflicted = git(tmp, ["diff", "--name-only", "--diff-filter=U"], check=False).stdout.splitlines()
        ranges = conflict_marker_ranges(tmp, conflicted)
        return "conflict", format_conflict_ranges(ranges)
    finally:
        git(repo, ["worktree", "remove", "--force", str(tmp)], check=False)
        shutil.rmtree(tmp_parent, ignore_errors=True)


def reconcile(
    path: str | pathlib.Path,
    *,
    remote_override: str | None = None,
    branch_override: str | None = None,
) -> str:
    repo, remote, branch, _ = resolve_project(
        path, remote_override=remote_override, branch_override=branch_override
    )
    git(repo, ["fetch", "--prune", remote, branch])
    head = rev(repo, "HEAD")
    fetched = rev(repo, "FETCH_HEAD")
    if head == fetched:
        return f"up to date: {repo} ({remote}/{branch})"
    if not is_ancestor(repo, head, "FETCH_HEAD"):
        raise SyncError(f"{repo} has diverged from {remote}/{branch}; resolve committed history first")

    dirty = bool(dirty_entries(repo))
    stashed = False
    if dirty:
        proc = git(repo, ["stash", "push", "-u", "-m", "overleaf-git-sync reconcile"], check=False)
        if "No local changes to save" not in (proc.stdout + proc.stderr):
            if proc.returncode != 0:
                raise SyncError((proc.stderr or proc.stdout).strip() or "git stash failed")
            stashed = True
    git(repo, ["merge", "--ff-only", "FETCH_HEAD"])
    if not stashed:
        return f"fast-forwarded: {repo} to {remote}/{branch}"

    pop = git(repo, ["stash", "pop"], check=False)
    if pop.returncode == 0:
        return f"reconciled: fast-forwarded {repo} and reapplied local changes cleanly"
    conflicted = git(repo, ["diff", "--name-only", "--diff-filter=U"], check=False).stdout.splitlines()
    ranges = conflict_marker_ranges(repo, conflicted)
    raise SyncError(format_conflict_ranges(ranges) + "; resolve conflict markers, then continue")


def write_marker(repo: pathlib.Path, remote: str, branch: str) -> None:
    payload = {
        "enabled": True,
        "remote": remote,
        "branch": branch,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "overleaf-git-sync init",
    }
    path = repo / MARKER
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> None:
    repo = repo_root_for(args.path)
    remotes = remote_urls(repo)
    remote = args.remote
    if not remote:
        for name, urls in remotes.items():
            if any(is_overleaf_url(url) for url in urls):
                remote = name
                break
    if not remote:
        raise SyncError(
            f"No Overleaf remote found. Add one first, for example: "
            f"git remote add overleaf https://{OVERLEAF_REMOTE_HOST}/<project_id>"
        )
    if remote not in remotes:
        raise SyncError(f"remote {remote!r} does not exist")
    branch = args.branch or current_branch(repo) or DEFAULT_BRANCH
    write_marker(repo, remote, branch)
    print(f"initialized {repo} -> {remote}/{branch} ({MARKER})")


def cmd_status(args: argparse.Namespace) -> None:
    repo, remote, branch, source = resolve_project(
        args.path,
        remote_override=args.remote,
        branch_override=args.branch,
        require_opt_in=False,
    )
    print(f"Repo:    {repo}")
    print(f"Remote:  {remote}")
    print(f"Branch:  {branch}")
    print(f"Source:  {source}")
    print(f"Marker:  {(repo / MARKER) if (repo / MARKER).exists() else 'none'}")
    blocking = []
    for status, path in dirty_entries(repo):
        if status != "??" or supported_path(path):
            blocking.append(f"{status} {path}")
    print(f"Dirty:   {'yes' if blocking else 'no'}")
    if blocking:
        for item in blocking[:12]:
            print(f"  {item}")
    if args.fetch:
        print(sync_before(args.path, remote_override=remote, branch_override=branch, force=True))


def cmd_reconcile(args: argparse.Namespace) -> None:
    print(reconcile(args.path, remote_override=args.remote, branch_override=args.branch))


def hook_paths(data: dict) -> list[str]:
    tool_name = str(data.get("tool_name") or data.get("tool") or "")
    tool_input = data.get("tool_input") or data.get("input") or {}
    cwd = pathlib.Path(data.get("cwd") or os.getcwd())
    raw_paths: list[str] = []

    if isinstance(tool_input, dict):
        for key in ("file_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str):
                raw_paths.append(value)
        value = tool_input.get("paths")
        if isinstance(value, list):
            raw_paths.extend(str(item) for item in value)
        text = "\n".join(str(v) for v in tool_input.values())
    else:
        text = str(tool_input)

    if tool_name.lower() == "apply_patch" or "*** Begin Patch" in text:
        for match in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", text, re.MULTILINE):
            raw_paths.append(match.group(1).strip())

    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("cmd") or tool_input.get("command") or "")
    if command:
        try:
            tokens = shlex.split(command, posix=(os.name != "nt"))
        except ValueError:
            tokens = command.split()
        raw_paths.extend(token for token in tokens if supported_path(token))

    resolved: list[str] = []
    for raw in raw_paths:
        cleaned = raw.strip().strip("\"'")
        if not cleaned or not supported_path(cleaned):
            continue
        p = pathlib.Path(cleaned)
        if not p.is_absolute():
            p = cwd / p
        resolved.append(str(p))
    return list(dict.fromkeys(resolved))


def cmd_hook(args: argparse.Namespace) -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"[overleaf-git-sync] hook input is not JSON: {exc}", file=sys.stderr)
        return
    paths = hook_paths(data if isinstance(data, dict) else {})
    for path in paths:
        try:
            message = sync_before(path, force=args.force)
            print(f"[overleaf-git-sync] {message}", file=sys.stderr)
        except NoProject:
            continue
        except SyncError as exc:
            print(f"[overleaf-git-sync] blocked: {exc}", file=sys.stderr)
            raise SystemExit(2)


def cmd_hook_config(args: argparse.Namespace) -> None:
    script = pathlib.Path(__file__).resolve()
    print("# Example Codex PreToolUse command for apply_patch/Bash guardrails.")
    print("# Hook schema differs by Codex version; use this command as the hook body:")
    print(f"python3 {shlex.quote(str(script))} hook")


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def cmd_watch(args: argparse.Namespace) -> None:
    if args.interval < 1:
        raise SyncError("--interval must be at least 1 second")
    print(
        f"[overleaf-git-sync] watching {args.path} every {args.interval}s "
        "(pull-only, fast-forward only; Ctrl-C to stop)"
    )
    while True:
        try:
            message = sync_before(
                args.path,
                remote_override=args.remote,
                branch_override=args.branch,
                force=True,
                allow_dirty=not args.require_clean,
                debounce_seconds=0,
            )
            print(f"[{timestamp()}] {message}", flush=True)
        except NoProject as exc:
            print(f"[{timestamp()}] noop: {exc}", flush=True)
            if args.once:
                return
        except SyncError as exc:
            text = str(exc)
            if text.startswith("worktree has local changes"):
                print(
                    f"[{timestamp()}] skipped: worktree has local changes; "
                    "commit/stash or run sync-after to resume auto-pull",
                    flush=True,
                )
            elif "would be overwritten" in text or "Please commit your changes" in text:
                try:
                    repo, _, _, _ = resolve_project(
                        args.path,
                        remote_override=args.remote,
                        branch_override=args.branch,
                    )
                    status, detail = dry_run_conflict_report(repo)
                    if status == "clean":
                        print(
                            f"[{timestamp()}] pending: same-file update appears mergeable; "
                            f"run `overleaf-git-sync reconcile {args.path}` to apply it",
                            flush=True,
                        )
                    elif status == "conflict":
                        print(f"[{timestamp()}] pending: {detail}", flush=True)
                    else:
                        print(f"[{timestamp()}] pending: {detail}", flush=True)
                except Exception as report_exc:
                    print(
                        f"[{timestamp()}] skipped: remote update would overwrite local changes; "
                        f"conflict probe failed: {report_exc}",
                        flush=True,
                    )
            else:
                print(f"[{timestamp()}] blocked: {exc}", flush=True)
                if args.stop_on_error:
                    raise
        if args.once:
            return
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[overleaf-git-sync] watch stopped")
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="overleaf-git-sync")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write an opt-in marker for this Git repo")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--remote")
    init.add_argument("--branch")
    init.set_defaults(func=cmd_init)

    before = sub.add_parser("sync-before", help="fast-forward from Overleaf before AI edits")
    before.add_argument("path", nargs="?", default=".")
    before.add_argument("--remote")
    before.add_argument("--branch")
    before.add_argument("--force", action="store_true")
    before.add_argument("--allow-dirty", action="store_true")
    before.add_argument("--debounce-seconds", type=int, default=DEFAULT_DEBOUNCE_SECONDS)
    before.set_defaults(
        func=lambda args: print(
            sync_before(
                args.path,
                remote_override=args.remote,
                branch_override=args.branch,
                force=args.force,
                allow_dirty=args.allow_dirty,
                debounce_seconds=args.debounce_seconds,
            )
        )
    )

    after = sub.add_parser("sync-after", help="commit selected files and push to Overleaf")
    after.add_argument("paths", nargs="*")
    after.add_argument("--remote")
    after.add_argument("--branch")
    after.add_argument("--message", "-m", default="Update Overleaf project")
    after.add_argument("--no-push", action="store_true")
    after.add_argument("--all-latex", action="store_true")
    after.set_defaults(
        func=lambda args: print(
            sync_after(
                args.paths,
                remote_override=args.remote,
                branch_override=args.branch,
                message=args.message,
                push=not args.no_push,
                all_latex=args.all_latex,
            )
        )
    )

    status = sub.add_parser("status", help="show project resolution and dirty state")
    status.add_argument("path", nargs="?", default=".")
    status.add_argument("--remote")
    status.add_argument("--branch")
    status.add_argument("--fetch", action="store_true")
    status.set_defaults(func=cmd_status)

    reconcile_parser = sub.add_parser("reconcile", help="explicitly merge Overleaf updates with local dirty changes")
    reconcile_parser.add_argument("path", nargs="?", default=".")
    reconcile_parser.add_argument("--remote")
    reconcile_parser.add_argument("--branch")
    reconcile_parser.set_defaults(func=cmd_reconcile)

    hook = sub.add_parser("hook", help="PreToolUse hook entrypoint; reads JSON on stdin")
    hook.add_argument("--force", action="store_true")
    hook.set_defaults(func=cmd_hook)

    hook_config = sub.add_parser("hook-config", help="print the hook command to wire into Codex")
    hook_config.set_defaults(func=cmd_hook_config)

    watch = sub.add_parser("watch", help="poll Overleaf and fast-forward when safe")
    watch.add_argument("path", nargs="?", default=".")
    watch.add_argument("--remote")
    watch.add_argument("--branch")
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--once", action="store_true", help="run one polling iteration and exit")
    watch.add_argument("--require-clean", action="store_true", help="skip whenever the worktree has local changes")
    watch.add_argument("--stop-on-error", action="store_true")
    watch.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except NoProject as exc:
        print(f"NOOP: {exc}", file=sys.stderr)
        return 0
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
