"""Tests for compression strategies."""

import os
import pytest

from openclaw_compressor.session import ContentBlock, Message, Session
from openclaw_compressor.strategies import (
    CompactionConfig,
    CompactionStrategy,
    LocalStrategy,
    LlmStrategy,
    SmartLocalStrategy,
    get_strategy,
    resolve_model,
    _infer_provider,
    truncate,
    extract_file_paths,
)


def _make_session(n: int = 20, text_len: int = 500) -> Session:
    msgs = []
    for i in range(n):
        if i % 3 == 0:
            msgs.append(Message(role="user", blocks=[
                ContentBlock(type="text", data={"text": f"Request {i}: please fix " + "x" * text_len}),
            ]))
        elif i % 3 == 1:
            msgs.append(Message(role="assistant", blocks=[
                ContentBlock(type="text", data={"text": f"Let me check."}),
                ContentBlock(type="tool_use", data={"id": f"t{i}", "name": "Read", "input": f'{{"path":"src/file{i}.ts"}}'}),
            ]))
        else:
            msgs.append(Message(role="tool", blocks=[
                ContentBlock(type="tool_result", data={
                    "tool_use_id": f"t{i-1}", "tool_name": "Read",
                    "output": f"export function foo{i}() " + "y" * text_len,
                    "is_error": False,
                }),
            ]))
    return Session(version=1, messages=msgs)


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        result = truncate("a" * 200, 50)
        assert len(result) == 51  # 50 chars + ellipsis
        assert result.endswith("\u2026")

    def test_exact_limit(self):
        assert truncate("abcde", 5) == "abcde"


class TestExtractFilePaths:
    def test_extracts_paths(self):
        text = "Update src/auth/login.ts and src/utils/helpers.py next."
        paths = extract_file_paths(text)
        assert "src/auth/login.ts" in paths
        assert "src/utils/helpers.py" in paths

    def test_ignores_non_paths(self):
        text = "hello world no paths here"
        assert extract_file_paths(text) == []

    def test_strips_punctuation(self):
        text = 'Check "src/main.rs", and (src/lib.rs).'
        paths = extract_file_paths(text)
        assert "src/main.rs" in paths
        assert "src/lib.rs" in paths

    def test_ignores_unknown_extensions(self):
        text = "src/data.bin src/image.png"
        assert extract_file_paths(text) == []


class TestCompactionStrategy:
    def test_should_compact_below_threshold(self):
        session = Session(messages=[
            Message(role="user", blocks=[ContentBlock(type="text", data={"text": "hi"})]),
        ])
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=10_000)
        assert not CompactionStrategy.should_compact(session, config)

    def test_should_compact_above_threshold(self):
        session = _make_session(20, 500)
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=100)
        assert CompactionStrategy.should_compact(session, config)

    def test_skips_when_below_threshold(self):
        session = Session(messages=[
            Message(role="user", blocks=[ContentBlock(type="text", data={"text": "hi"})]),
        ])
        strategy = LocalStrategy()
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=10_000)
        result = strategy.compact(session, config)
        assert result.removed_count == 0
        assert result.compacted_session is session


class TestLocalStrategy:
    def test_compact_produces_system_summary(self):
        session = _make_session(20, 500)
        strategy = LocalStrategy()
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=100)
        result = strategy.compact(session, config)

        assert result.removed_count == 16
        assert result.preserved_count == 4
        assert len(result.compacted_session.messages) == 5  # 1 system + 4 preserved
        assert result.compacted_session.messages[0].role == "system"
        assert "Conversation summary" in result.summary
        assert "Scope:" in result.summary
        assert result.tokens_after < result.tokens_before

    def test_summary_contains_tool_names(self):
        session = _make_session(10, 500)
        strategy = LocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "Read" in result.summary

    def test_summary_contains_timeline(self):
        session = _make_session(10, 100)
        strategy = LocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "Key timeline:" in result.summary
        assert "user:" in result.summary
        assert "assistant:" in result.summary


class TestSmartLocalStrategy:
    def test_compact_produces_richer_summary(self):
        session = _make_session(20, 500)
        strategy = SmartLocalStrategy()
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=100)
        result = strategy.compact(session, config)

        assert result.removed_count == 16
        assert "Conversation summary" in result.summary
        assert "Tools used:" in result.summary
        assert result.tokens_after < result.tokens_before

    def test_detects_pending_work(self):
        msgs = [
            Message(role="user", blocks=[ContentBlock(type="text", data={"text": "fix the bug"})]),
            Message(role="assistant", blocks=[ContentBlock(type="text", data={"text": "Done. TODO: add tests next."})]),
        ] + [Message(role="user", blocks=[ContentBlock(type="text", data={"text": "x" * 500})])] * 5
        session = Session(messages=msgs)
        strategy = SmartLocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "Pending work:" in result.summary

    def test_detects_errors(self):
        msgs = [
            Message(role="assistant", blocks=[
                ContentBlock(type="tool_use", data={"id": "1", "name": "Bash", "input": "bad"}),
            ]),
            Message(role="tool", blocks=[
                ContentBlock(type="tool_result", data={
                    "tool_use_id": "1", "tool_name": "Bash",
                    "output": "command not found", "is_error": True,
                }),
            ]),
        ] + [Message(role="user", blocks=[ContentBlock(type="text", data={"text": "x" * 500})])] * 5
        session = Session(messages=msgs)
        strategy = SmartLocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "Errors encountered:" in result.summary
        assert "Bash" in result.summary

    def test_extracts_key_files(self):
        msgs = [
            Message(role="user", blocks=[
                ContentBlock(type="text", data={"text": "Update src/auth/login.ts and src/utils/helpers.py"}),
            ]),
        ] + [Message(role="user", blocks=[ContentBlock(type="text", data={"text": "x" * 500})])] * 5
        session = Session(messages=msgs)
        strategy = SmartLocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "Key files referenced:" in result.summary
        assert "src/auth/login.ts" in result.summary

    def test_groups_tool_chains(self):
        msgs = [
            Message(role="assistant", blocks=[
                ContentBlock(type="text", data={"text": "Let me read it."}),
                ContentBlock(type="tool_use", data={"id": "1", "name": "Read", "input": "{}"}),
            ]),
            Message(role="tool", blocks=[
                ContentBlock(type="tool_result", data={
                    "tool_use_id": "1", "tool_name": "Read",
                    "output": "file content here", "is_error": False,
                }),
            ]),
        ] + [Message(role="user", blocks=[ContentBlock(type="text", data={"text": "x" * 500})])] * 5
        session = Session(messages=msgs)
        strategy = SmartLocalStrategy()
        result = strategy.compact(session, CompactionConfig(
            preserve_recent_messages=2, max_estimated_tokens=1,
        ))
        assert "call(Read)" in result.summary
        assert "[ok]" in result.summary


class TestGetStrategy:
    def test_local(self):
        assert isinstance(get_strategy("local"), LocalStrategy)

    def test_smart_local(self):
        assert isinstance(get_strategy("smart_local"), SmartLocalStrategy)

    def test_llm_with_model(self):
        strategy = get_strategy("llm", model="claude-haiku-4-5-20251001")
        assert isinstance(strategy, LlmStrategy)
        assert strategy.model == "claude-haiku-4-5-20251001"

    def test_llm_without_model_raises(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_MODEL", raising=False)
        with pytest.raises(ValueError, match="No model specified"):
            get_strategy("llm")

    def test_llm_env_var_overrides_param(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_MODEL", "claude-sonnet-4-20250514")
        strategy = get_strategy("llm", model="claude-haiku-4-5-20251001")
        assert strategy.model == "claude-sonnet-4-20250514"

    def test_unknown_raises(self):
        try:
            get_strategy("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "nonexistent" in str(e)


class TestResolveModel:
    def test_param_only(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_MODEL", raising=False)
        assert resolve_model("gpt-4o") == "gpt-4o"

    def test_env_only(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_MODEL", "claude-sonnet-4-20250514")
        assert resolve_model(None) == "claude-sonnet-4-20250514"

    def test_env_takes_priority(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_COMPRESSOR_MODEL", "claude-opus-4-20250514")
        assert resolve_model("gpt-4o") == "claude-opus-4-20250514"

    def test_neither_raises(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_COMPRESSOR_MODEL", raising=False)
        with pytest.raises(ValueError, match="No model specified"):
            resolve_model(None)


class TestInferProvider:
    def test_claude_models(self):
        assert _infer_provider("claude-haiku-4-5-20251001") == "anthropic"
        assert _infer_provider("claude-sonnet-4-20250514") == "anthropic"
        assert _infer_provider("claude-opus-4-20250514") == "anthropic"

    def test_openai_models(self):
        assert _infer_provider("gpt-4o") == "openai"
        assert _infer_provider("gpt-4o-mini") == "openai"
        assert _infer_provider("o3-mini") == "openai"
        assert _infer_provider("o4-mini") == "openai"
        assert _infer_provider("chatgpt-4o-latest") == "openai"

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Cannot infer provider"):
            _infer_provider("llama-3-70b")


class TestCompressionRatio:
    def test_ratio_calculation(self):
        session = _make_session(20, 500)
        strategy = SmartLocalStrategy()
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=100)
        result = strategy.compact(session, config)
        assert 0 < result.compression_ratio < 1
        assert result.compression_ratio == 1.0 - result.tokens_after / result.tokens_before

    def test_no_compression_ratio_zero(self):
        session = Session(messages=[
            Message(role="user", blocks=[ContentBlock(type="text", data={"text": "hi"})]),
        ])
        strategy = SmartLocalStrategy()
        result = strategy.compact(session, CompactionConfig())
        assert result.compression_ratio == 0.0
