"""Install per-user MCP configuration and the bundled skill."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass(frozen=True)
class Agent:
    executable: str
    config: Path
    skill: Path
    mcp_key: str = "mcpServers"


def agents(home):
    codex = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    opencode = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode"
    return {
        "claude": Agent("claude", home / ".claude.json", home / ".claude/skills"),
        "codex": Agent("codex", codex / "config.toml", home / ".agents/skills"),
        "cursor": Agent("cursor", home / ".cursor/mcp.json", home / ".cursor/skills"),
        "gemini": Agent("gemini", home / ".gemini/settings.json", home / ".gemini/skills"),
        "opencode": Agent("opencode", opencode / "opencode.json", opencode / "skills", "mcp"),
    }


def change_file(path, before, after, backup):
    if before == after:
        return
    if backup and before is not None:
        backup_path = path.with_name(path.name + ".carpool.bak")
        try:
            with backup_path.open("xb") as stream:
                os.chmod(stream.fileno(), 0o600)
                stream.write(before)
        except FileExistsError:
            pass
    if after is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            if before is not None:
                os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
            stream.write(after)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def read_config(path):
    if path.is_symlink():
        raise ValueError(f"{path} is a symlink; edit it through its primary location")
    return path.read_bytes() if path.exists() else None


def json_config(before, key, entry, remove):
    document = json.loads(before) if before is not None else {}
    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    servers = document.get(key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{key} must be a JSON object")
    if "carpool" in servers:
        if servers["carpool"] != entry:
            raise ValueError("the existing carpool MCP entry has custom settings")
        if not remove:
            return before
        del servers["carpool"]
    elif remove:
        return before
    else:
        document[key] = servers
        servers["carpool"] = entry
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def codex_config(before, command, remove):
    text = (before or b"").decode()
    servers = tomllib.loads(text).get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp_servers must be a TOML table")
    start = "# carpool setup: start\n"
    end = "# carpool setup: end\n"
    expected = start + f"[mcp_servers.carpool]\ncommand = {json.dumps(command, ensure_ascii=False)}\n" + end
    if "carpool" in servers:
        if text.count(expected) != 1 or servers["carpool"] != {"command": command}:
            raise ValueError("the existing carpool MCP entry has custom settings")
        if not remove:
            return before
        result = text.replace(expected, "", 1)
    elif remove:
        return before
    else:
        separator = "\n" if text and not text.endswith("\n") else ""
        result = text + separator + expected
    tomllib.loads(result)
    return result.encode()


def configure_agent(name, agent, command, skill, remove, backup):
    path = agent.config
    if name == "opencode" and path.with_suffix(".jsonc").exists():
        raise ValueError("opencode.jsonc exists; register carpool in that file manually")
    before = read_config(path)
    if name == "codex":
        after = codex_config(before, command, remove)
    else:
        entry = {"command": command}
        if name == "opencode":
            entry = {"type": "local", "command": [command], "enabled": True}
        after = json_config(before, agent.mcp_key, entry, remove)

    skill_path = agent.skill / "carpool" / "SKILL.md"
    installed = read_config(skill_path)
    if installed not in (None, skill):
        raise ValueError(f"{skill_path} already contains a different skill")
    change_file(path, before, after, backup)
    change_file(skill_path, installed, None if remove else skill, backup)


def setup_agents(selected, remove=False, backup=True):
    known = agents(Path.home())
    names = selected or [
        name for name, agent in known.items()
        if shutil.which(agent.executable) or agent.config.exists()
        or (name == "claude" and agent.skill.parent.is_dir())
        or (name != "claude" and agent.config.parent.is_dir())
        or (remove and (agent.skill / "carpool/SKILL.md").exists())
    ]
    if not names:
        raise ValueError("no supported agents found; specify one with --agent")
    executable_name = "carpool-mcp.exe" if os.name == "nt" else "carpool-mcp"
    executable = Path(sys.executable).with_name(executable_name)
    if not executable.is_file():
        found = shutil.which("carpool-mcp")
        if found is None:
            raise ValueError("carpool-mcp is not installed")
        executable = Path(found)
    skill_file = files("carpool").joinpath("_skill", "SKILL.md")
    if not skill_file.is_file():
        # Editable installs keep the plugin's skill in its source directory.
        skill_file = Path(__file__).resolve().parents[1] / "skills/carpool/SKILL.md"
    skill = skill_file.read_bytes()
    failed = False
    for name in dict.fromkeys(names):
        try:
            configure_agent(name, known[name], str(executable.absolute()), skill, remove, backup)
        except (OSError, ValueError) as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failed = True
        else:
            print(f"{name}: {'removed carpool' if remove else 'configured carpool'}")
    return int(failed)
