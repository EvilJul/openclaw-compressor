"""
OpenClaw Context Compressor - MCP Server.

Exposes three tools via MCP stdio protocol:
- analyze_context: report token usage and compression recommendations
- compress_session: compress a session file using a chosen strategy
- preview_compression: dry-run compression, return summary without modifying files
"""

from __future__ import annotations

from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import run_stdio
from mcp.types import Tool, TextContent

from .hosts import resolve_session_path, setup_interactive
from .session import Session
from .strategies import CompactionConfig, get_strategy

server = Server("openclaw-compressor")


def _parse_config(args: dict) -> CompactionConfig:
    return CompactionConfig(
        preserve_recent_messages=args.get("preserve_recent_messages", 4),
        max_estimated_tokens=args.get("max_estimated_tokens", 10_000),
        strategy=args.get("strategy", "smart_local"),
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="analyze_context",
            description=(
                "Analyze a session's context usage. Returns message count, "
                "estimated token count, role breakdown, tools used, and whether "
                "compression is recommended. Use this to decide when to compress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_path": {
                        "type": "string",
                        "description": "Path to session JSON file, or session ID",
                    },
                    "max_estimated_tokens": {
                        "type": "integer",
                        "description": "Token threshold for compression recommendation (default: 10000)",
                        "default": 10000,
                    },
                },
                "required": ["session_path"],
            },
        ),
        Tool(
            name="compress_session",
            description=(
                "Compress a session file to reduce context size. "
                "Replaces older messages with a structured summary while preserving recent messages. "
                "Writes the compressed session back to disk. "
                "Strategies: 'local' (mirrors built-in), 'smart_local' (enhanced heuristics), 'llm' (AI-powered)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_path": {
                        "type": "string",
                        "description": "Path to session JSON file, or session ID",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["local", "smart_local", "llm"],
                        "description": "Compression strategy (default: smart_local)",
                        "default": "smart_local",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "LLM model ID for 'llm' strategy (e.g. 'claude-sonnet-4-20250514', 'gpt-4o'). "
                            "Can also be set via OPENCLAW_COMPRESSOR_MODEL env var (env var takes priority)."
                        ),
                    },
                    "preserve_recent_messages": {
                        "type": "integer",
                        "description": "Number of recent messages to keep verbatim (default: 4)",
                        "default": 4,
                    },
                    "max_estimated_tokens": {
                        "type": "integer",
                        "description": "Token threshold to trigger compression (default: 10000)",
                        "default": 10000,
                    },
                },
                "required": ["session_path"],
            },
        ),
        Tool(
            name="preview_compression",
            description=(
                "Dry-run compression: returns the summary that would be generated "
                "and compression stats, without modifying the session file."
         ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_path": {
                        "type": "string",
                        "description": "Path to session JSON file, or session ID",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["local", "smart_local", "llm"],
                        "description": "Compression strategy (default: smart_local)",
                        "default": "smart_local",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "LLM model ID for 'llm' strategy (e.g. 'claude-sonnet-4-20250514', 'gpt-4o'). "
                            "Can also be set via OPENCLAW_COMPRESSOR_MODEL env var (env var takes priority)."
                        ),
                    },
                    "preserve_recent_messages": {
                        "type": "integer",
                        "description": "Number of recent messages to keep verbatim (default: 4)",
                        "default": 4,
                    },
                },
                "required": ["session_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "analyze_context":
            return _handle_analyze(arguments)
        elif name == "compress_session":
            return _handle_compress(arguments)
        elif name == "preview_compression":
            return _handle_preview(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except FileNotFoundError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


def _handle_analyze(args: dict) -> list[TextContent]:
    path = resolve_session_path(args["session_path"])
    session = Session.load(path)
    threshold = args.get("max_estimated_tokens", 10_000)

    role_counts = session.role_counts()
    tool_names = session.all_tool_names()
    tokens = session.estimated_tokens
    should_compress = len(session.messages) > 4 and tokens >= threshold

    lines = [
        f"Session: {path.name}",
        f"Messages: {len(session.messages)}",
        f"Estimated tokens: {tokens:,}",
        f"Roles: {', '.join(f'{k}={v}' for k, v in role_counts.items() if v > 0)}",
    ]
    if tool_names:
        lines.append(f"Tools: {', '.join(tool_names[:15])}")
    if should_compress:
        lines.append(f"RECOMMENDATION: Compress now (tokens {tokens:,} >= threshold {threshold:,})")
    else:
        headroom = round((1 - tokens / max(threshold, 1)) * 100, 1)
        lines.append(f"Status: OK ({headroom}% headroom remaining)")

    return [TextContent(type="text", text="\n".join(lines))]


def _handle_compress(args: dict) -> list[TextContent]:
    path = resolve_session_path(args["session_path"])
    session = Session.load(path)
    config = _parse_config(args)
    strategy_kwargs: dict = {}
    if model := args.get("model"):
        strategy_kwargs["model"] = model
    strategy = get_strategy(config.strategy, **strategy_kwargs)

    result = strategy.compact(session, config)

    if result.removed_count == 0:
        return [TextContent(
            type="text",
            text=(
                f"Skipped: session is below compression threshold "
                f"({session.estimated_tokens:,} tokens, threshold {config.max_estimated_tokens:,})."
            ),
        )]

    result.compacted_session.save(path)

    lines = [
        f"Compressed session: {path.name}",
        f"Strategy: {config.strategy}",
        f"Messages: {result.removed_count + result.preserved_count} -> {len(result.compacted_session.messages)}",
        f"Removed: {result.removed_count} messages",
        f"Preserved: {result.preserved_count} recent messages",
        f"Tokens: {result.tokens_before:,} -> {result.tokens_after:,} ({result.compression_ratio:.0%} reduction)",
    ]

    return [TextContent(type="text", text="\n".join(lines))]


def _handle_preview(args: dict) -> list[TextContent]:
    path = resolve_session_path(args["session_path"])
    session = Session.load(path)
    config = _parse_config(args)
    strategy_kwargs: dict = {}
    if model := args.get("model"):
        strategy_kwargs["model"] = model
    strategy = get_strategy(config.strategy, **strategy_kwargs)

    result = strategy.compact(session, config)

    if result.removed_count == 0:
        return [TextContent(
            type="text",
            text=f"Nothing to compress ({session.estimated_tokens:,} tokens, threshold {config.max_estimated_tokens:,}).",
        )]

    lines = [
        "=== Compression Preview (dry run) ===",
        f"Strategy: {config.strategy}",
        f"Messages: {result.removed_count + result.preserved_count} -> {len(result.compacted_session.messages)}",
        f"Tokens: {result.tokens_before:,} -> {result.tokens_after:,} ({result.compression_ratio:.0%} reduction)",
        "",
        "=== Generated Summary ===",
        result.summary,
    ]

    return [TextContent(type="text", text="\n".join(lines))]


def main():
    """Entry point for the MCP server. Supports 'setup' subcommand."""
    import asyncio
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_interactive()
    else:
        asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
