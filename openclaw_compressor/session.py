"""
OpenClaw session file reader/writer.

Supports two formats:

1. JSON format (Claude Code / Cline style):
{
  "version": 1,
  "messages": [
    {
      "role": "user" | "assistant" | "system" | "tool",
      "blocks": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": "..."},
        {"type": "tool_result", "tool_use_id": "...", "tool_name": "...", "output": "...", "is_error": false}
      ],
      "usage": {"input_tokens": N, "output_tokens": N, ...}
    }
  ]
}

2. JSONL format (OpenClaw style):
Line 1: {"type": "session", "id": "...", "cwd": "...", "timestamp": "..."}
Line N: {"type": "message", "message": {"role": "user"|"assistant", "content": "..." | [...]}}
Line N: {"type": "compaction", "summary": "...", ...}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _content_to_blocks(content: Any) -> list[ContentBlock]:
    """Convert Anthropic API content field to internal ContentBlock list.

    content can be:
    - a plain string -> single text block
    - a list of content block dicts -> mapped to ContentBlock objects
    """
    if isinstance(content, str):
        return [ContentBlock(type="text", data={"text": content})]
    if isinstance(content, list):
        blocks: list[ContentBlock] = []
        for item in content:
            if isinstance(item, str):
                blocks.append(ContentBlock(type="text", data={"text": item}))
            elif isinstance(item, dict):
                blocks.append(ContentBlock.from_dict(item))
        return blocks
    return [ContentBlock(type="text", data={"text": str(content)})]


def _blocks_to_content(blocks: list[ContentBlock]) -> str | list[dict[str, Any]]:
    """Convert internal ContentBlock list back to Anthropic API content field.

    If all blocks are plain text, return a single string.
    Otherwise return a list of content block dicts.
    """
    if len(blocks) == 1 and blocks[0].type == "text":
        return blocks[0].data.get("text", "")
    if all(b.type == "text" for b in blocks):
        return "\n".join(b.data.get("text", "") for b in blocks)
    return [b.to_dict() for b in blocks]


@dataclass
class ContentBlock:
    type: str
    data: dict[str, Any]

    @property
    def text(self) -> str:
        if self.type == "text":
            return self.data.get("text", "")
        if self.type == "tool_use":
            return f'{self.data.get("name", "")}({self.data.get("input", "")})'
        if self.type == "tool_result":
            return self.data.get("output", "")
        return ""

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def estimated_tokens(self) -> int:
        return self.char_count // 4 + 1

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ContentBlock:
        block_type = raw["type"]
        data = {k: v for k, v in raw.items() if k != "type"}
        return cls(type=block_type, data=data)


@dataclass
class Message:
    role: str
    blocks: list[ContentBlock]
    usage: dict[str, Any] | None = None

    @property
    def estimated_tokens(self) -> int:
        return sum(b.estimated_tokens for b in self.blocks)

    @property
    def first_text(self) -> str:
        for block in self.blocks:
            if block.type == "text" and block.text.strip():
                return block.text
        return ""

    @property
    def tool_names(self) -> list[str]:
        names: list[str] = []
        for block in self.blocks:
            if block.type == "tool_use":
                name = block.data.get("name", "")
                if name:
                    names.append(name)
            elif block.type == "tool_result":
                name = block.data.get("tool_name", "")
                if name:
                    names.append(name)
        return names

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role,
            "blocks": [b.to_dict() for b in self.blocks],
        }
        if self.usage is not None:
            d["usage"] = self.usage
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Message:
        return cls(
            role=raw["role"],
            blocks=[ContentBlock.from_dict(b) for b in raw.get("blocks", [])],
            usage=raw.get("usage"),
        )


@dataclass
class Session:
    version: int = 1
    messages: list[Message] = field(default_factory=list)
    _source_format: str = field(default="json", repr=False)
    _jsonl_header: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def estimated_tokens(self) -> int:
        return sum(m.estimated_tokens for m in self.messages)

    def role_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.messages:
            counts[m.role] = counts.get(m.role, 0) + 1
        return counts

    def all_tool_names(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for m in self.messages:
            for name in m.tool_names:
                if name and name not in seen:
                    seen.add(name)
                    result.append(name)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Session:
        return cls(
            version=raw.get("version", 1),
            messages=[Message.from_dict(m) for m in raw.get("messages", [])],
        )

    @classmethod
    def load(cls, path: str | Path) -> Session:
        path = Path(path)
        content = path.read_text(encoding="utf-8")

        # Detect format: JSONL if file ends with .jsonl or first line is a JSON object
        # but the whole file is not a single JSON object
        if path.suffix == ".jsonl":
            return cls._load_jsonl(content)

        # Try JSON first, fall back to JSONL
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "messages" in data:
                return cls.from_dict(data)
        except json.JSONDecodeError:
            pass

        # Fall back to JSONL parsing
        return cls._load_jsonl(content)

    @classmethod
    def _load_jsonl(cls, content: str) -> Session:
        """Parse OpenClaw-style JSONL session transcript."""
        messages: list[Message] = []
        header: dict[str, Any] | None = None

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")

            if entry_type == "session":
                header = entry
                continue

            if entry_type == "message":
                msg_data = entry.get("message", {})
                if not msg_data:
                    continue
                role = msg_data.get("role", "user")
                content_field = msg_data.get("content", "")
                blocks = _content_to_blocks(content_field)
                usage = msg_data.get("usage")
                messages.append(Message(role=role, blocks=blocks, usage=usage))

            elif entry_type == "compaction":
                # Treat existing compaction summaries as system messages
                summary = entry.get("summary", "")
                if summary:
                    blocks = [ContentBlock(type="text", data={"text": summary})]
                    messages.append(Message(role="system", blocks=blocks))

        session = cls(version=1, messages=messages)
        session._source_format = "jsonl"
        session._jsonl_header = header
        return session

    def save(self, path: str | Path) -> None:
        path = Path(path)

        if self._source_format == "jsonl" or path.suffix == ".jsonl":
            self._save_jsonl(path)
        else:
            path.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def _save_jsonl(self, path: Path) -> None:
        """Save back in OpenClaw JSONL format."""
        lines: list[str] = []

        # Write header if we have one
        if self._jsonl_header:
            lines.append(json.dumps(self._jsonl_header, ensure_ascii=False))

        for msg in self.messages:
            content = _blocks_to_content(msg.blocks)
            msg_data: dict[str, Any] = {"role": msg.role, "content": content}
            if msg.usage is not None:
                msg_data["usage"] = msg.usage
            entry = {"type": "message", "message": msg_data}
            lines.append(json.dumps(entry, ensure_ascii=False))

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
