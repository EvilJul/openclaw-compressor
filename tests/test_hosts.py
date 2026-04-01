"""Tests for multi-host session path discovery and auto-configuration."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openclaw_compressor.hosts import (
    KNOWN_HOSTS,
    HostInfo,
    DetectedHost,
    detect_hosts,
    generate_mcp_config,
    get_session_search_dirs,
    register_in_config,
    resolve_session_path,
)


class TestHostInfo:
    def test_find_session_dir_exists(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        host = HostInfo(
            name="Test",
            session_dirs=[session_dir],
            config_paths=[tmp_path / "settings.json"],
            config_key="mcpServers",
        )
        assert host.find_session_dir() == session_dir

    def test_find_session_dir_missing(self, tmp_path):
        host = HostInfo(
            name="Test",
            session_dirs=[tmp_path / "nonexistent"],
            config_paths=[tmp_path / "settings.json"],
            config_key="mcpServers",
        )
        assert host.find_session_dir() is None

    def test_find_config_path_exists(self, tmp_path):
        config = tmp_path / "settings.json"
        config.write_text("{}")
        host = HostInfo(
            name="Test",
            session_dirs=[tmp_path / "sessions"],
            config_paths=[config],
            config_key="mcpServers",
        )
        assert host.find_config_path() == config

    def test_find_config_path_missing(self, tmp_path):
        host = HostInfo(
            name="Test",
            session_dirs=[tmp_path / "sessions"],
            config_paths=[tmp_path / "nonexistent.json"],
            config_key="mcpServers",
        )
        assert host.find_config_path() is None

    def test_default_config_path(self, tmp_path):
        first = tmp_path / "first.json"
        second = tmp_path / "second.json"
        host = HostInfo(
            name="Test",
            session_dirs=[],
            config_paths=[first, second],
            config_key="mcpServers",
        )
        assert host.default_config_path() == first


class TestGetSessionSearchDirs:
    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_SESSION_DIR", str(custom_dir))
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", []):
            dirs = get_session_search_dirs()
        assert dirs[0] == custom_dir

    def test_known_hosts_included(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_SESSION_DIR", raising=False)
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        fake_host = HostInfo(
            name="Fake",
            session_dirs=[session_dir],
            config_paths=[],
            config_key="mcpServers",
        )
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [fake_host]):
            dirs = get_session_search_dirs()
        assert session_dir in dirs

    def test_nonexistent_dirs_excluded(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_SESSION_DIR", raising=False)
        fake_host = HostInfo(
            name="Fake",
            session_dirs=[tmp_path / "nonexistent"],
            config_paths=[],
            config_key="mcpServers",
        )
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [fake_host]):
            dirs = get_session_search_dirs()
        assert len(dirs) == 0

    def test_no_duplicates(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_SESSION_DIR", str(session_dir))
        fake_host = HostInfo(
            name="Fake",
            session_dirs=[session_dir],
            config_paths=[],
            config_key="mcpServers",
        )
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [fake_host]):
            dirs = get_session_search_dirs()
        assert dirs.count(session_dir) == 1


class TestResolveSessionPath:
    def test_absolute_path(self, tmp_path):
        session_file = tmp_path / "test.json"
        session_file.write_text('{"version": 1, "messages": []}')
        result = resolve_session_path(str(session_file))
        assert result == session_file

    def test_session_id_found(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        session_file = session_dir / "abc123.json"
        session_file.write_text('{"version": 1, "messages": []}')
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_SESSION_DIR", str(session_dir))
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", []):
            result = resolve_session_path("abc123")
        assert result == session_file

    def test_session_id_not_found(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_SESSION_DIR", str(session_dir))
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", []):
            with pytest.raises(FileNotFoundError, match="Session not found"):
                resolve_session_path("nonexistent")

    def test_no_dirs_gives_helpful_error(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_SESSION_DIR", raising=False)
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", []):
            with pytest.raises(FileNotFoundError, match="No known session directories"):
                resolve_session_path("anything")

    def test_searches_multiple_hosts(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_SESSION_DIR", raising=False)
        dir_a = tmp_path / "host_a" / "sessions"
        dir_b = tmp_path / "host_b" / "sessions"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        # File only in second host
        session_file = dir_b / "mysession.json"
        session_file.write_text('{"version": 1}')
        host_a = HostInfo(name="A", session_dirs=[dir_a], config_paths=[], config_key="mcpServers")
        host_b = HostInfo(name="B", session_dirs=[dir_b], config_paths=[], config_key="mcpServers")
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [host_a, host_b]):
            result = resolve_session_path("mysession")
        assert result == session_file

    def test_env_var_dir_searched_first(self, tmp_path, monkeypatch):
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        host_dir = tmp_path / "host" / "sessions"
        host_dir.mkdir(parents=True)
        # Same session ID in both dirs
        custom_file = custom_dir / "dup.json"
        custom_file.write_text('{"source": "custom"}')
        host_file = host_dir / "dup.json"
        host_file.write_text('{"source": "host"}')
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_SESSION_DIR", str(custom_dir))
        host = HostInfo(name="H", session_dirs=[host_dir], config_paths=[], config_key="mcpServers")
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [host]):
            result = resolve_session_path("dup")
        assert result == custom_file


class TestDetectHosts:
    def test_detects_existing_host(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        fake_host = HostInfo(
            name="TestHost",
            session_dirs=[session_dir],
            config_paths=[tmp_path / "settings.json"],
            config_key="mcpServers",
        )
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [fake_host]):
            detected = detect_hosts()
        assert len(detected) == 1
        assert detected[0].host.name == "TestHost"
        assert detected[0].session_dir == session_dir

    def test_skips_missing_host(self, tmp_path):
        fake_host = HostInfo(
            name="Missing",
            session_dirs=[tmp_path / "nonexistent"],
            config_paths=[],
            config_key="mcpServers",
        )
        with patch("openclaw_compressor.hosts.KNOWN_HOSTS", [fake_host]):
            detected = detect_hosts()
        assert len(detected) == 0


class TestGenerateMcpConfig:
    def test_basic_config(self):
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            config = generate_mcp_config()
        assert config["command"] == "openclaw-compressor"
        assert "args" not in config
        assert "env" not in config

    def test_python_fallback(self):
        with patch("openclaw_compressor.hosts._get_server_command",
                   return_value=["/usr/bin/python3", "-m", "openclaw_compressor.server"]):
            config = generate_mcp_config()
        assert config["command"] == "/usr/bin/python3"
        assert config["args"] == ["-m", "openclaw_compressor.server"]

    def test_with_env_vars(self):
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            config = generate_mcp_config(env_vars={
                "ANTHROPIC_API_KEY": "sk-test",
                "EMPTY_VAR": "",
            })
        assert config["env"] == {"ANTHROPIC_API_KEY": "sk-test"}

    def test_empty_env_vars_excluded(self):
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            config = generate_mcp_config(env_vars={"EMPTY": ""})
        assert "env" not in config or config.get("env") == {}


class TestRegisterInConfig:
    def test_creates_new_config(self, tmp_path):
        config_path = tmp_path / "settings.json"
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            result = register_in_config(config_path)
        assert "Registered" in result
        data = json.loads(config_path.read_text())
        assert "context-compressor" in data["mcpServers"]
        assert data["mcpServers"]["context-compressor"]["command"] == "openclaw-compressor"

    def test_updates_existing_config(self, tmp_path):
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({
            "mcpServers": {
                "context-compressor": {"command": "old-command"},
                "other-server": {"command": "keep-me"},
            }
        }))
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            result = register_in_config(config_path)
        assert "Updated" in result
        data = json.loads(config_path.read_text())
        assert data["mcpServers"]["context-compressor"]["command"] == "openclaw-compressor"
        assert data["mcpServers"]["other-server"]["command"] == "keep-me"

    def test_preserves_existing_keys(self, tmp_path):
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({
            "someOtherSetting": True,
            "mcpServers": {},
        }))
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            register_in_config(config_path)
        data = json.loads(config_path.read_text())
        assert data["someOtherSetting"] is True

    def test_creates_parent_dirs(self, tmp_path):
        config_path = tmp_path / "deep" / "nested" / "settings.json"
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            register_in_config(config_path)
        assert config_path.exists()

    def test_custom_server_name(self, tmp_path):
        config_path = tmp_path / "settings.json"
        with patch("openclaw_compressor.hosts._get_server_command", return_value=["openclaw-compressor"]):
            register_in_config(config_path, server_name="my-compressor")
        data = json.loads(config_path.read_text())
        assert "my-compressor" in data["mcpServers"]


class TestKnownHosts:
    def test_known_hosts_defined(self):
        names = [h.name for h in KNOWN_HOSTS]
        assert "Claude Code" in names
        assert "OpenClaw" in names
        assert "Cline" in names

    def test_all_hosts_have_session_dirs(self):
        for host in KNOWN_HOSTS:
            assert len(host.session_dirs) > 0

    def test_all_hosts_have_config_paths(self):
        for host in KNOWN_HOSTS:
            assert len(host.config_paths) > 0
