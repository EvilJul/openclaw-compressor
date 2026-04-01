"""
OpenClaw session file reader/writer.

Session JSON format (mirrors rust/crates/runtime/src/session.rs):
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
      "usage": {"input_tokens": N, "output_tokens": N, "cache_creation_input_tokens": N, "cache_read_input_tokens": N}
    }
  ]
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
        content = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(content))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
