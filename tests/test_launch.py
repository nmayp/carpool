import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

from carpool.cli import main
from carpool.setup import agents, tomllib

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/squad48-holdout-claude-lean.json"


def test_card_renders_logged_totals(tmp_path):
    output = tmp_path / "card.png"
    assert main(["stats", str(RESULT), "--card", str(output)]) == 0
    with Image.open(output) as image:
        assert image.size == (1200, 630)
        assert image.getpixel((100, 5)) == (24, 64, 255)
        assert image.getpixel((0, 100)) == (255, 255, 255)
        assert image.info["Description"].splitlines()[1:] == [
            "Model calls: 48 to 3",
            "Total cost: $0.8169 to $0.1954",
            "Accepted answers: 42/48 to 46/48",
            "Carpool fallbacks: 2",
            "github.com/nmayp/carpool",
        ]


@pytest.fixture
def temporary_agent_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_path = bin_dir / ("carpool-mcp.exe" if os.name == "nt" else "carpool-mcp")
    command_path.touch()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.defpath]))
    return agents(tmp_path), str(command_path)


@pytest.mark.parametrize("name", ["claude", "codex", "cursor", "gemini", "opencode"])
def test_setup_preserves_other_settings_and_is_repeatable(temporary_agent_home, name):
    clients, command = temporary_agent_home
    agent = clients[name]
    agent.config.parent.mkdir(parents=True, exist_ok=True)
    if name == "codex":
        before = b'model = "custom"\n# keep this comment\n[mcp_servers.other]\ncommand = "other"\n'
        load = lambda: tomllib.loads(agent.config.read_text())
        key = "mcp_servers"
    else:
        key = agent.mcp_key
        before = json.dumps({"setting": True, key: {"other": {"command": "other"}}}).encode()
        load = lambda: json.loads(agent.config.read_bytes())
    agent.config.write_bytes(before)
    backup = agent.config.with_name(agent.config.name + ".carpool.bak")
    skill = agent.skill / "carpool/SKILL.md"

    # The existing config detects the client without an explicit --agent.
    assert main(["setup"]) == 0
    expected = ({"type": "local", "command": [command], "enabled": True}
                if name == "opencode" else {"command": command})
    assert load()[key] == {"carpool": expected, "other": {"command": "other"}}
    assert backup.read_bytes() == before
    assert skill.read_bytes() == (ROOT / "skills/carpool/SKILL.md").read_bytes()
    configured = agent.config.read_bytes()
    time = agent.config.stat().st_mtime_ns
    assert main(["setup", "--agent", name]) == 0
    assert agent.config.read_bytes() == configured
    assert agent.config.stat().st_mtime_ns == time

    assert main(["setup", "--remove", "--agent", name]) == 0
    assert load()[key] == {"other": {"command": "other"}}
    assert backup.read_bytes() == before
    assert not skill.exists()
    if name == "codex":
        assert agent.config.read_bytes() == before


def test_setup_no_backup_and_conflicts(temporary_agent_home):
    clients, _ = temporary_agent_home
    config = clients["claude"].config
    config.write_text('{"someSetting": true}')
    assert main(["setup", "--agent", "claude", "--no-backup"]) == 0
    assert not config.with_name(config.name + ".carpool.bak").exists()
    custom = '{"mcpServers":{"carpool":{"command":"custom"}}}'
    config.write_text(custom)
    assert main(["setup", "--agent", "claude", "--remove"]) == 1
    assert config.read_text() == custom
