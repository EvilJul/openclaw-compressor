"""
Compression strategies for OpenClaw sessions.

Three strategies available:
- LocalStrategy: mirrors the built-in compact.rs logic (no LLM, deterministic)
- SmartLocalStrategy: enhanced local extraction with better heuristics
- LlmStrategy: calls Claude API for high-quality semantic summarization
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import os

from .session import ContentBlock, Message, Session

PENDING_KEYWORDS = ("todo", "next", "pending", "follow up", "remaining", "fixme", "hack")
FILE_EXTENSIONS = ("rs", "ts", "tsx", "js", "jsx", "json", "md", "py", "go", "java", "toml", "yaml", "yml")
MAX_SUMMARY_CHAR = 160
MAX_CURRENT_WORK_CHAR = 300
MAX_KEY_FILES = 12
MAX_RECENT_REQUESTS = 5
MAX_PENDING_ITEMS = 5


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\u2026"


def extract_file_paths(text: str) -> list[str]:
    """Extract file path candidates from text content."""
    candidates: list[str] = []
    for token in text.split():
        cleaned = token.strip(",.;:()\"'`[]{}|")
        if "/" not in cleaned:
            continue
        ext = cleaned.rsplit(".", 1)[-1].lower() if "." in cleaned else ""
        if ext in FILE_EXTENSIONS:
            candidates.append(cleaned)
    return candidates


@dataclass
class CompactionConfig:
    preserve_recent_messages: int = 4
    max_estimated_tokens: int = 10_000
    strategy: str = "smart_local"


@dataclass
class CompactionResult:
    summary: str
    compacted_session: Session
    removed_count: int
    preserved_count: int
    tokens_before: int
    tokens_after: int

    @property
    def compression_ratio(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return 1.0 - self.tokens_after / self.tokens_before


class CompactionStrategy(ABC):
    """Base class for compression strategies."""

    @abstractmethod
    def summarize(self, messages: list[Message]) -> str:
        """Generate a summary of the given messages."""

    def compact(self, session: Session, config: CompactionConfig) -> CompactionResult:
        """Run the full compaction pipeline."""
        tokens_before = session.estimated_tokens

        if not self.should_compact(session, config):
            return CompactionResult(
                summary="",
                compacted_session=session,
                removed_count=0,
                preserved_count=len(session.messages),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        keep_from = max(0, len(session.messages) - config.preserve_recent_messages)
        removed = session.messages[:keep_from]
        preserved = session.messages[keep_from:]

        summary = self.summarize(removed)
        continuation = self._build_continuation(summary, bool(preserved))

        system_msg = Message(
            role="system",
            blocks=[ContentBlock(type="text", data={"text": continuation})],
        )

        compacted = Session(
            version=session.version,
            messages=[system_msg] + preserved,
        )

        return CompactionResult(
            summary=summary,
            compacted_session=compacted,
            removed_count=len(removed),
            preserved_count=len(preserved),
            tokens_before=tokens_before,
            tokens_after=compacted.estimated_tokens,
        )

    @staticmethod
    def should_compact(session: Session, config: CompactionConfig) -> bool:
        return (
            len(session.messages) > config.preserve_recent_messages
            and session.estimated_tokens >= config.max_estimated_tokens
        )

    @staticmethod
    def _build_continuation(summary: str, has_preserved: bool) -> str:
        parts = [
            "This session is being continued from a previous conversation that ran out of context.",
            "The summary below covers the earlier portion of the conversation.",
            "",
            summary,
        ]
        if has_preserved:
            parts.append("\nRecent messages are preserved verbatim.")
        parts.append(
            "\nContinue the conversation from where it left off without asking the user any further questions. "
            "Resume directly \u2014 do not acknowledge the summary, do not recap what was happening, "
            "and do not preface with continuation text."
        )
        return "\n".join(parts)


class LocalStrategy(CompactionStrategy):
    """
    Mirrors the built-in compact.rs logic exactly.
    Deterministic, no LLM calls, zero cost.
    """

    def summarize(self, messages: list[Message]) -> str:
        role_counts = {"user": 0, "assistant": 0, "tool": 0, "system": 0}
        for m in messages:
            role_counts[m.role] = role_counts.get(m.role, 0) + 1

        tool_names = sorted(set(
            name for m in messages for name in m.tool_names
        ))

        recent_user = [
            truncate(m.first_text, MAX_SUMMARY_CHAR)
            for m in reversed(messages)
            if m.role == "user" and m.first_text
        ][:3]
        recent_user.reverse()

        lines = [
            "Conversation summary:",
            f"- Scope: {len(messages)} earlier messages compacted "
            f"(user={role_counts['user']}, assistant={role_counts['assistant']}, tool={role_counts['tool']}).",
        ]

        if tool_names:
            lines.append(f"- Tools mentioned: {', '.join(tool_names)}.")

        if recent_user:
            lines.append("- Recent user requests:")
            lines.extend(f"  - {r}" for r in recent_user)

        lines.append("- Key timeline:")
        for m in messages:
            block_summaries = []
            for b in m.blocks:
                block_summaries.append(truncate(b.text, MAX_SUMMARY_CHAR))
            content = " | ".join(block_summaries)
            lines.append(f"  - {m.role}: {content}")

        return "\n".join(lines)


class SmartLocalStrategy(CompactionStrategy):
    """
    Enhanced local strategy with better heuristics:
    - Groups tool call chains (use -> result) as single units
    - Extracts more file paths with broader extension support
    - Better pending work detection
    - Separates high-value vs low-value messages for smarter truncation
    """

    def summarize(self, messages: list[Message]) -> str:
        role_counts = {"user": 0, "assistant": 0, "tool": 0, "system": 0}
        for m in messages:
            role_counts[m.role] = role_counts.get(m.role, 0) + 1

        tool_names = sorted(set(
            name for m in messages for name in m.tool_names
        ))

        recent_user = [
            truncate(m.first_text, MAX_SUMMARY_CHAR)
            for m in reversed(messages)
            if m.role == "user" and m.first_text
        ][:MAX_RECENT_REQUESTS]
        recent_user.reverse()

        pending: list[str] = []
        for m in reversed(messages):
            text = m.first_text.lower()
            if any(kw in text for kw in PENDING_KEYWORDS):
                pending.append(truncate(m.first_text, MAX_SUMMARY_CHAR))
                if len(pending) >= MAX_PENDING_ITEMS:
                    break
        pending.reverse()

        all_files: list[str] = []
        seen_files: set[str] = set()
        for m in messages:
            for b in m.blocks:
                for fp in extract_file_paths(b.text):
                    if fp not in seen_files:
                        seen_files.add(fp)
                        all_files.append(fp)
        key_files = all_files[:MAX_KEY_FILES]

        current_work = ""
        for m in reversed(messages):
            if m.first_text.strip():
                current_work = truncate(m.first_text, MAX_CURRENT_WORK_CHAR)
                break

        errors: list[str] = []
        for m in messages:
            for b in m.blocks:
                if b.type == "tool_result" and b.data.get("is_error"):
                    errors.append(truncate(
                        f'{b.data.get("tool_name", "unknown")}: {b.data.get("output", "")}',
                        MAX_SUMMARY_CHAR,
                    ))

        lines = [
            "Conversation summary:",
            f"- Scope: {len(messages)} earlier messages compacted "
            f"(user={role_counts['user']}, assistant={role_counts['assistant']}, tool={role_counts['tool']}).",
        ]

        if tool_names:
            lines.append(f"- Tools used: {', '.join(tool_names)}.")

        if recent_user:
            lines.append("- Recent user requests:")
            lines.extend(f"  - {r}" for r in recent_user)

        if pending:
            lines.append("- Pending work:")
            lines.extend(f"  - {p}" for p in pending)

        if key_files:
            lines.append(f"- Key files referenced: {', '.join(key_files)}.")

        if errors:
            lines.append("- Errors encountered:")
            lines.extend(f"  - {e}" for e in errors[:3])

        if current_work:
            lines.append(f"- Current work: {current_work}")

        lines.append("- Key timeline:")
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.role == "assistant" and m.tool_names:
                tool_call_names = ", ".join(m.tool_names)
                text_part = truncate(m.first_text, 80) if m.first_text else ""
                prefix = f"{text_part} -> " if text_part else ""
                result_summary = ""
                if i + 1 < len(messages) and messages[i + 1].role == "tool":
                    result_msg = messages[i + 1]
                    result_text = result_msg.first_text or ""
                    for b in result_msg.blocks:
                        if b.type == "tool_result":
                            result_text = b.data.get("output", "")
                            break
                    is_err = any(
                        b.data.get("is_error") for b in result_msg.blocks if b.type == "tool_result"
                    )
                    status = "ERROR" if is_err else "ok"
                    result_summary = f" -> [{status}] {truncate(result_text, 80)}"
                    i += 1
                lines.append(f"  - assistant: {prefix}call({tool_call_names}){result_summary}")
            else:
                content = truncate(m.first_text or "(empty)", MAX_SUMMARY_CHAR)
                lines.append(f"  - {m.role}: {content}")
            i += 1

        return "\n".join(lines)


class LlmClient(ABC):
    """Abstract LLM client — one implementation per provider."""

    @abstractmethod
    def chat(self, model: str, max_tokens: int, prompt: str) -> str:
        """Send a single-turn prompt and return the text response."""


class AnthropicClient(LlmClient):
    def __init__(self) -> None:
        try:
            import anthropic
            self._client = anthropic.Anthropic()
        except ImportError:
            raise RuntimeError(
                "Anthropic provider requires the anthropic package. "
                "Install with: pip install openclaw-compressor[anthropic]"
            )

    def chat(self, model: str, max_tokens: int, prompt: str) -> str:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class OpenAIClient(LlmClient):
    def __init__(self) -> None:
        try:
            import openai
            self._client = openai.OpenAI()
        except ImportError:
            raise RuntimeError(
                "OpenAI provider requires the openai package. "
                "Install with: pip install openclaw-compressor[openai]"
            )

    def chat(self, model: str, max_tokens: int, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


# Prefix-based provider detection
_MODEL_PREFIXES: list[tuple[tuple[str, ...], str]] = [
    (("claude",), "anthropic"),
    (("gpt-", "o1-", "o3-", "o4-", "chatgpt-"), "openai"),
]


def _infer_provider(model: str) -> str:
    """Infer provider name from model ID prefix."""
    model_lower = model.lower()
    for prefixes, provider in _MODEL_PREFIXES:
        if any(model_lower.startswith(p) for p in prefixes):
            return provider
    raise ValueError(
        f"Cannot infer provider for model '{model}'. "
        f"Set OPENCLAW_COMPRESSOR_PROVIDER to 'anthropic' or 'openai'."
    )


def get_llm_client(model: str) -> LlmClient:
    """Build an LlmClient for the given model, respecting env overrides."""
    provider = os.environ.get("OPENCLAW_COMPRESSOR_PROVIDER") or _infer_provider(model)
    provider = provider.strip().lower()
    if provider == "anthropic":
        return AnthropicClient()
    elif provider == "openai":
        return OpenAIClient()
    else:
        raise ValueError(f"Unsupported provider: '{provider}'. Use 'anthropic' or 'openai'.")


class LlmStrategy(CompactionStrategy):
    """
    Uses an LLM to generate a high-quality semantic summary.
    Supports any Anthropic or OpenAI(-compatible) model.
    Model must be provided — either via tool parameter or OPENCLAW_COMPRESSOR_MODEL env var.
    """

    def __init__(self, model: str, max_summary_tokens: int = 1024):
        self.model = model
        self.max_summary_tokens = max_summary_tokens
        self._client: LlmClient | None = None

    def _get_client(self) -> LlmClient:
        if self._client is None:
            self._client = get_llm_client(self.model)
        return self._client

    def summarize(self, messages: list[Message]) -> str:
        local = SmartLocalStrategy()
        local_summary = local.summarize(messages)

        conversation_lines: list[str] = []
        for m in messages:
            for b in m.blocks:
                text = truncate(b.text, 500)
                conversation_lines.append(f"[{m.role}] {text}")
        raw_conversation = "\n".join(conversation_lines)

        if len(raw_conversation) > 20_000:
            raw_conversation = raw_conversation[:20_000] + "\n[...truncated...]"

        prompt = (
            "Summarize this coding assistant conversation for context continuation.\n"
            "Focus on: what the user asked for, what was done, what files were changed, what's pending.\n"
            "Be concise but preserve all actionable details. Output plain text, no XML tags.\n\n"
            f"Local extraction (structured):\n{local_summary}\n\n"
            f"Raw conversation:\n{raw_conversation}"
        )

        client = self._get_client()
        llm_summary = client.chat(self.model, self.max_summary_tokens, prompt)

        return f"Conversation summary (AI-generated):\n{llm_summary}"


def resolve_model(model: str | None = None) -> str:
    """Resolve model using priority: env var > tool parameter. Raises if neither set."""
    env_model = os.environ.get("OPENCLAW_COMPRESSOR_MODEL")
    resolved = env_model or model
    if not resolved:
        raise ValueError(
            "No model specified. Either set OPENCLAW_COMPRESSOR_MODEL env var "
            "or pass 'model' in the tool call."
        )
    return resolved


def get_strategy(name: str, **kwargs: Any) -> CompactionStrategy:
    """Factory function to get a strategy by name."""
    strategies: dict[str, type[CompactionStrategy]] = {
        "local": LocalStrategy,
        "smart_local": SmartLocalStrategy,
        "llm": LlmStrategy,
    }
    cls = strategies.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}. Available: {', '.join(strategies)}")
    if cls is LlmStrategy:
        kwargs["model"] = resolve_model(kwargs.get("model"))
    return cls(**kwargs)
