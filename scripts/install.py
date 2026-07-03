#!/usr/bin/env python3
"""Install Overleaf Git Sync for Claude Code and Codex."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time


PLUGIN_NAME = "overleaf-git-sync"
HOOK_MATCHER = "Read|Edit|Write|MultiEdit"


def plugin_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def home() -> pathlib.Path:
    return pathlib.Path.home()


def write_text(path: pathlib.Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{int(time.time())}")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None and os.name == "posix":
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def copytree_clean(src: pathlib.Path, dst: pathlib.Path) -> None:
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".DS_Store", "*.pyc")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def install_cli() -> pathlib.Path:
    bin_dir = home() / ".local" / "bin"
    script = plugin_root() / "scripts" / "overleaf_git_sync.py"
    target = bin_dir / "overleaf-git-sync"
    wrapper = f"""#!/bin/sh
exec python3 {str(script)!r} "$@"
"""
    write_text(target, wrapper, mode=0o755)
    return target


def install_claude_skill(cli_path: pathlib.Path) -> pathlib.Path:
    source = plugin_root() / "skills" / PLUGIN_NAME / "SKILL.md"
    target_dir = home() / ".claude" / "skills" / PLUGIN_NAME
    text = source.read_text(encoding="utf-8")
    text += (
        "\n\n## Local Install\n\n"
        f"This skill was installed from `{plugin_root()}`. Prefer this CLI:\n\n"
        f"```bash\n{cli_path}\n```\n"
    )
    write_text(target_dir / "SKILL.md", text)
    return target_dir


def is_our_hook(command: str) -> bool:
    return "overleaf-git-sync" in command and " hook" in f" {command} "


def install_claude_hook(cli_path: pathlib.Path) -> pathlib.Path:
    settings_path = home() / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            backup = settings_path.with_suffix(f".json.broken-{int(time.time())}")
            shutil.copyfile(settings_path, backup)
            raise SystemExit(
                f"{settings_path} is not valid JSON ({exc}). Backed up to {backup}; "
                "fix it and rerun install."
            )
    hooks = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
    cleaned = []
    for entry in hooks:
        if not isinstance(entry, dict):
            continue
        entry_hooks = entry.get("hooks", [])
        if isinstance(entry_hooks, list):
            entry["hooks"] = [
                hook for hook in entry_hooks
                if not (isinstance(hook, dict) and is_our_hook(str(hook.get("command", ""))))
            ]
        if entry.get("hooks"):
            cleaned.append(entry)
    cleaned.append({
        "matcher": HOOK_MATCHER,
        "hooks": [{
            "type": "command",
            "command": f'"{cli_path}" hook',
        }],
    })
    settings["hooks"]["PreToolUse"] = cleaned
    write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    return settings_path


def load_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {
            "name": "personal",
            "interface": {"displayName": "Personal"},
            "plugins": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def install_codex_plugin(codex_root: pathlib.Path) -> pathlib.Path:
    target = codex_root / "plugins" / PLUGIN_NAME
    if plugin_root().resolve() != target.resolve():
        copytree_clean(plugin_root(), target)
    marketplace = codex_root / ".agents" / "plugins" / "marketplace.json"
    payload = load_json(marketplace)
    plugins = payload.setdefault("plugins", [])
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    for index, item in enumerate(plugins):
        if isinstance(item, dict) and item.get("name") == PLUGIN_NAME:
            plugins[index] = entry
            break
    else:
        plugins.append(entry)
    write_text(marketplace, json.dumps(payload, indent=2) + "\n")
    try:
        subprocess.run(["codex", "plugin", "marketplace", "add", str(codex_root)], check=False)
    except FileNotFoundError:
        pass
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="install CLI, Claude skill/hook, and Codex plugin")
    parser.add_argument("--no-claude-hook", action="store_true", help="install Claude skill without the PreToolUse hook")
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--codex-root", default=str(home() / "codex-plugins"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cli = install_cli()
    print(f"CLI installed: {cli}")
    if not args.skip_claude:
        target = install_claude_skill(cli)
        print(f"Claude skill installed: {target}")
        if not args.no_claude_hook:
            settings = install_claude_hook(cli)
            print(f"Claude PreToolUse hook updated: {settings}")
    if not args.skip_codex:
        target = install_codex_plugin(pathlib.Path(args.codex_root).expanduser())
        print(f"Codex plugin installed: {target}")
    print()
    print("Restart Claude Code and start a new Codex thread so skills/hooks are reloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
