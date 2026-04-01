"""
Multi-host session path discovery and auto-configuration.

Supports multiple MCP hosts:
- Claude Code (~/.claude/sessions/)
- OpenClaw (~/.openclaw/sessions/)
- Cline (~/.cline/sessions/)
- Custom path via OPENCLAW_COMPRESSOR_SESSION_DIR env var

Also provides setup utilities for auto-detecting the host environment
and generating MCP configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class HostInfo:
    """Describes a known MCP host environment."""
    name: str
    session_dirs: list[Path]
    config_paths: list[Path]
    config_key: str  # key inside the config JSON where mcpServers lives

    def find_session_dir(self) -> Path | None:
        """Return the first existing session directory, or None."""
        for d in self.session_dirs:
            if d.is_dir():
                return d
        return None

    def find_config_path(self) -> Path | None:
        """Return the first existing config file path, or None."""
        for p in self.config_paths:
            if p.exists():
                return p
        return None

    def default_config_path(self) -> Path:
        """Return the preferred config path (first in list)."""
        return self.config_paths[0]


_HOME = Path.home()

KNOWN_HOSTS: list[HostInfo] = [
    HostInfo(
        name="Claude Code",
        session_dirs=[_HOME / ".claude" / "sessions"],
        config_paths=[_HOME / ".claude" / "settings.json"],
        config_key="mcpServers",
    ),
    HostInfo(
        name="OpenClaw",
        session_dirs=[_HOME / ".openclaw" / "sessions"],
        config_paths=[_HOME / ".openclaw" / "settings.json"],
        config_key="mcpServers",
    ),
    HostInfo(
        name="Cline",
        session_dirs=[_HOME / ".cline" / "sessions"],
        config_paths=[_HOME / ".cline" / "settings.json"],
        config_key="mcpServers",
    ),
]


def get_session_search_dirs() -> list[Path]:
    """
    Build the ordered list of directories to search for session files.

    Priority:
    1. OPENCLAW_COMPRESSOR_SESSION_DIR env var (highest)
    2. All known host session directories that exist on disk
    """
    dirs: list[Path] = []

    # Custom env var takes top priority
    env_dir = os.environ.get("OPENCLAW_COMPRESSOR_SESSION_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            dirs.append(p)

    # Then all known hosts
    for host in KNOWN_HOSTS:
        for d in host.session_dirs:
            if d.is_dir() and d not in dirs:
                dirs.append(d)

    return dirs


def resolve_session_path(session_path: str) -> Path:
    """
    Resolve a session path argument to an actual file.

    Accepts:
    - Absolute or relative path to an existing file
    - Session ID (searches all known host directories)

    Raises FileNotFoundError with a helpful message if not found.
    """
    # Direct path
    p = Path(session_path)
    if p.exists():
        return p

    # Search known directories by session ID
    search_dirs = get_session_search_dirs()
    candidates = [
        f"{session_path}.json",
        f"{session_path}.jsonl",
        session_path,
    ]

    for d in search_dirs:
        for candidate in candidates:
            full = d / candidate
            if full.exists():
                return full

    # Build helpful error message
    if search_dirs:
        searched = "\n  ".join(str(d) for d in search_dirs)
        raise FileNotFoundError(
            f"Session not found: {session_path}\n"
            f"Searched directories:\n  {searched}\n"
            f"Tip: pass an absolute path, or set OPENCLAW_COMPRESSOR_SESSION_DIR "
            f"to your host's session directory."
        )
    else:
        raise FileNotFoundError(
            f"Session not found: {session_path}\n"
            f"No known session directories found on this system.\n"
            f"Set OPENCLAW_COMPRESSOR_SESSION_DIR to your host's session directory, "
            f"or pass an absolute file path."
        )


# ---------------------------------------------------------------------------
# Auto-detection & setup
# ---------------------------------------------------------------------------

@dataclass
class DetectedHost:
    """Result of host environment detection."""
    host: HostInfo
    session_dir: Path
    config_path: Path | None


def detect_hosts() -> list[DetectedHost]:
    """Detect which MCP hosts are installed on this system."""
    detected: list[DetectedHost] = []
    for host in KNOWN_HOSTS:
        session_dir = host.find_session_dir()
        if session_dir:
            detected.append(DetectedHost(
                host=host,
                session_dir=session_dir,
                config_path=host.find_config_path(),
            ))
    return detected


def _get_server_command() -> list[str]:
    """Determine the best command to launch this MCP server."""
    if shutil.which("openclaw-compressor"):
        return ["openclaw-compressor"]
    return [sys.executable, "-m", "openclaw_compressor.server"]


def generate_mcp_config(env_vars: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Generate the MCP server config block for registration.

    Returns a dict like:
    {
        "command": "openclaw-compressor",
        "env": { ... }  # only if env_vars provided
    }
    """
    cmd = _get_server_command()
    config: dict[str, Any] = {}

    if len(cmd) == 1:
        config["command"] = cmd[0]
    else:
        config["command"] = cmd[0]
        config["args"] = cmd[1:]

    if env_vars:
        config["env"] = {k: v for k, v in env_vars.items() if v}

    return config


def register_in_config(config_path: Path, server_name: str = "context-compressor",
                       env_vars: dict[str, str] | None = None) -> str:
    """
    Register this MCP server in a host's config file.

    Returns a status message describing what was done.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    mcp_servers = existing.setdefault("mcpServers", {})
    mcp_config = generate_mcp_config(env_vars)

    if server_name in mcp_servers:
        mcp_servers[server_name] = mcp_config
        config_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return f"Updated existing '{server_name}' in {config_path}"
    else:
        mcp_servers[server_name] = mcp_config
        config_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return f"Registered '{server_name}' in {config_path}"


def setup_interactive() -> None:
    """
    Interactive setup: detect hosts, show status, optionally register.
    Called via `openclaw-compressor setup`.
    """
    print("openclaw-compressor setup")
    print("=" * 40)
    print()

    # Detect hosts
    detected = detect_hosts()
    if detected:
        print("Detected MCP hosts:")
        for i, d in enumerate(detected, 1):
            config_status = f"config: {d.config_path}" if d.config_path else "config: not found"
            print(f"  {i}. {d.host.name}")
            print(f"     sessions: {d.session_dir}")
            print(f"     {config_status}")
        print()
    else:
        print("No known MCP hosts detected.")
        print("Supported hosts: Claude Code, OpenClaw, Cline")
        print()
        env_dir = os.environ.get("OPENCLAW_COMPRESSOR_SESSION_DIR")
        if env_dir:
            print(f"Custom session dir (env): {env_dir}")
        else:
            print("Tip: set OPENCLAW_COMPRESSOR_SESSION_DIR to your session directory.")
        print()

    # Show generated config
    mcp_config = generate_mcp_config()
    print("MCP server config to add:")
    print(json.dumps({"context-compressor": mcp_config}, indent=2))
    print()

    # Auto-register if hosts detected
    if detected:
        for d in detected:
            config_path = d.config_path or d.host.default_config_path()
            answer = input(f"Register in {d.host.name} ({config_path})? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                result = register_in_config(config_path)
                print(f"  -> {result}")
            else:
                print("  -> Skipped")
        print()
        print("Done! Restart your AI assistant to activate.")
    else:
        print("Copy the config above into your MCP host's settings file.")
