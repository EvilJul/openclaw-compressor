"""Tests for session module."""

import json
import tempfile
from pathlib import Path

from openclaw_compressor.session import ContentBlock, Message, Session


class TestContentBlock:
    def test_text_block(self):
        block = ContentBlock(type="text", data={"text": "hello world"})
        assert block.text == "hello world"
        assert block.char_count == 11
        assert block.estimated_tokens == 11 // 4 + 1

    def test_tool_use_block(self):
        block = ContentBlock(type="tool_use", data={"id": "1", "name": "Bash", "input": '{"cmd":"ls"}'})
        assert "Bash" in block.text
        assert block.estimated_tokens > 0

    def test_tool_result_block(self):
        block = ContentBlock(type="tool_result", data={
            "tool_use_id": "1", "tool_name": "Bash", "output": "file.txt", "is_error": False,
        })
        assert block.text == "file.txt"

    def test_roundtrip(self):
        block = ContentBlock(type="text", data={"text": "hello"})
        restored = ContentBlock.from_dict(block.to_dict())
        assert restored.type == block.type
        assert restored.text == block.text


class TestMessage:
    def test_first_text(self):
        msg = Message(role="user", blocks=[
            ContentBlock(type="text", data={"text": "  "}),
            ContentBlock(type="text", data={"text": "real content"}),
        ])
        assert msg.first_text == "real content"

    def test_first_text_empty(self):
        msg = Message(role="tool", blocks=[
            ContentBlock(type="tool_result", data={
                "tool_use_id": "1", "tool_name": "Bash", "output": "ok", "is_error": False,
            }),
        ])
        assert msg.first_text == ""

    def test_tool_names(self):
        msg = Message(role="assistant", blocks=[
            ContentBlock(type="text", data={"text": "Let me check."}),
            ContentBlock(type="tool_use", data={"id": "1", "name": "Read", "input": "{}"}),
            ContentBlock(type="tool_use", data={"id": "2", "name": "Bash", "input": "{}"}),
        ])
        assert msg.tool_names == ["Read", "Bash"]

    def test_estimated_tokens(self):
        msg = Message(role="user", blocks=[
            ContentBlock(type="text", data={"text": "a" * 100}),
        ])
        assert msg.estimated_tokens == 100 // 4 + 1

    def test_roundtrip(self):
        msg = Message(role="assistant", blocks=[
            ContentBlock(type="text", data={"text": "hello"}),
        ], usage={"input_tokens": 10, "output_tokens": 5})
        restored = Message.from_dict(msg.to_dict())
        assert restored.role == "assistant"
        assert restored.blocks[0].text == "hello"
        assert restored.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_roundtrip_no_usage(self):
        msg = Message(role="user", blocks=[
            ContentBlock(type="text", data={"text": "hi"}),
        ])
        d = msg.to_dict()
        assert "usage" not in d
        restored = Message.from_dict(d)
        assert restored.usage is None


class TestSession:
    def _make_session(self, n_messages: int = 10, text_len: int = 200) -> Session:
        msgs = []
        for i in range(n_messages):
            role = ["user", "assistant", "tool"][i % 3]
            blocks = [ContentBlock(type="text", data={"text": f"msg{i} " + "x" * text_len})]
            msgs.append(Message(role=role, blocks=blocks))
        return Session(version=1, messages=msgs)

    def test_estimated_tokens(self):
        session = self._make_session(5, 100)
        assert session.estimated_tokens > 0
        assert session.estimated_tokens == sum(m.estimated_tokens for m in session.messages)

    def test_role_counts(self):
        session = self._make_session(9)
        counts = session.role_counts()
        assert counts["user"] == 3
        assert counts["assistant"] == 3
        assert counts["tool"] == 3

    def test_all_tool_names(self):
        session = Session(messages=[
            Message(role="assistant", blocks=[
                ContentBlock(type="tool_use", data={"id": "1", "name": "Read", "input": "{}"}),
            ]),
            Message(role="tool", blocks=[
                ContentBlock(type="tool_result", data={
                    "tool_use_id": "1", "tool_name": "Read", "output": "ok", "is_error": False,
                }),
            ]),
            Message(role="assistant", blocks=[
                ContentBlock(type="tool_use", data={"id": "2", "name": "Bash", "input": "{}"}),
                ContentBlock(type="tool_use", data={"id": "3", "name": "Read", "input": "{}"}),
            ]),
        ])
        names = session.all_tool_names()
        assert names == ["Read", "Bash"]  # deduped, order preserved

    def test_save_and_load(self):
        session = self._make_session(3, 50)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            session.save(path)
            loaded = Session.load(path)
            assert loaded.version == session.version
            assert len(loaded.messages) == len(session.messages)
            for orig, rest in zip(session.messages, loaded.messages):
                assert orig.role == rest.role
                assert orig.first_text == rest.first_text
        finally:
            path.unlink()

    # ---- JSONL format tests ----

    def test_load_jsonl_basic(self):
        """Load a basic OpenClaw JSONL session."""
        content = "\n".join([
            '{"type":"session","id":"s1","cwd":"/tmp","timestamp":"2026-03-13T00:00:00Z"}',
            '{"type":"message","message":{"role":"user","content":"hello"}}',
            '{"type":"message","message":{"role":"assistant","content":"hi there"}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(content)
            path = Path(f.name)
        try:
            session = Session.load(path)
            assert session._source_format == "jsonl"
            assert session._jsonl_header is not None
            assert session._jsonl_header["id"] == "s1"
            assert len(session.messages) == 2
            assert session.messages[0].role == "user"
            assert session.messages[0].first_text == "hello"
            assert session.messages[1].role == "assistant"
            assert session.messages[1].first_text == "hi there"
        finally:
            path.unlink()

    def test_load_jsonl_with_content_blocks(self):
        """Load JSONL with list-style content blocks."""
        content = "\n".join([
            '{"type":"session","id":"s2"}',
            '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"thinking"},{"type":"tool_use","id":"t1","name":"Bash","input":"ls"}]}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(content)
            path = Path(f.name)
        try:
            session = Session.load(path)
            assert len(session.messages) == 1
            msg = session.messages[0]
            assert len(msg.blocks) == 2
            assert msg.blocks[0].type == "text"
            assert msg.blocks[0].text == "thinking"
            assert msg.blocks[1].type == "tool_use"
            assert msg.blocks[1].data["name"] == "Bash"
        finally:
            path.unlink()

    def test_load_jsonl_with_compaction(self):
        """Compaction entries should be loaded as system messages with _source_type preserved."""
        content = "\n".join([
            '{"type":"session","id":"s3"}',
            '{"type":"message","message":{"role":"user","content":"do something"}}',
            '{"type":"compaction","summary":"User asked to do something. Assistant did it."}',
            '{"type":"message","message":{"role":"user","content":"next question"}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(content)
            path = Path(f.name)
        try:
            session = Session.load(path)
            assert len(session.messages) == 3
            assert session.messages[0].role == "user"
            assert session.messages[1].role == "system"
            assert session.messages[1]._source_type == "compaction"
            assert "User asked" in session.messages[1].first_text
            assert session.messages[2].role == "user"
        finally:
            path.unlink()

    def test_jsonl_roundtrip_preserves_compaction(self):
        """Compaction entries must survive a load -> save -> load cycle."""
        original = "\n".join([
            '{"type":"session","id":"s4","cwd":"/home"}',
            '{"type":"message","message":{"role":"user","content":"hello"}}',
            '{"type":"compaction","summary":"Summary of conversation so far."}',
            '{"type":"message","message":{"role":"user","content":"continue"}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(original)
            path = Path(f.name)
        try:
            session = Session.load(path)
            session.save(path)

            # Re-read raw lines to verify compaction type is preserved
            raw_lines = [
                json.loads(line)
                for line in path.read_text().strip().splitlines()
                if line.strip()
            ]
            types = [entry.get("type") for entry in raw_lines]
            assert types == ["session", "message", "compaction", "message"]
            # Verify compaction content
            compaction_entry = raw_lines[2]
            assert compaction_entry["summary"] == "Summary of conversation so far."

            # Also verify re-load produces the same structure
            reloaded = Session.load(path)
            assert len(reloaded.messages) == len(session.messages)
            assert reloaded.messages[1]._source_type == "compaction"
            assert reloaded.messages[1].first_text == session.messages[1].first_text
        finally:
            path.unlink()

    def test_jsonl_roundtrip_preserves_header(self):
        """Session header should survive round-trip."""
        content = "\n".join([
            '{"type":"session","id":"abc","cwd":"/proj","timestamp":"2026-03-13"}',
            '{"type":"message","message":{"role":"user","content":"hi"}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(content)
            path = Path(f.name)
        try:
            session = Session.load(path)
            session.save(path)
            reloaded = Session.load(path)
            assert reloaded._jsonl_header["id"] == "abc"
            assert reloaded._jsonl_header["cwd"] == "/proj"
        finally:
            path.unlink()

    def test_jsonl_autodetect_without_extension(self):
        """A file without .jsonl extension but with JSONL content should be parsed as JSONL."""
        content = "\n".join([
            '{"type":"session","id":"s5"}',
            '{"type":"message","message":{"role":"user","content":"test"}}',
        ])
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write(content)
            path = Path(f.name)
        try:
            session = Session.load(path)
            assert session._source_format == "jsonl"
            assert len(session.messages) == 1
        finally:
            path.unlink()

    def test_load_json_missing_messages_key_raises(self):
        """A JSON file with valid JSON but no 'messages' key should raise ValueError."""
        data = {"version": 1, "something_else": []}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            import pytest
            with pytest.raises(ValueError, match="missing 'messages' key"):
                Session.load(path)
        finally:
            path.unlink()

    def test_session_equality_ignores_format_metadata(self):
        """Two sessions with same messages but different source formats should be equal."""
        msgs = [Message(role="user", blocks=[ContentBlock(type="text", data={"text": "hi"})])]
        s1 = Session(version=1, messages=msgs)
        s2 = Session(version=1, messages=msgs)
        s2._source_format = "jsonl"
        s2._jsonl_header = {"type": "session", "id": "x"}
        assert s1 == s2

    def test_json_format_matches_rust(self):
        """Verify the JSON structure matches what Rust session.rs produces."""
        session = Session(version=1, messages=[
            Message(role="user", blocks=[
                ContentBlock(type="text", data={"text": "hello"}),
            ]),
            Message(role="assistant", blocks=[
                ContentBlock(type="text", data={"text": "thinking"}),
                ContentBlock(type="tool_use", data={"id": "t1", "name": "bash", "input": "echo hi"}),
            ], usage={"input_tokens": 10, "output_tokens": 4,
                      "cache_creation_input_tokens": 1, "cache_read_input_tokens": 2}),
            Message(role="tool", blocks=[
                ContentBlock(type="tool_result", data={
             "tool_use_id": "t1", "tool_name": "bash", "output": "hi", "is_error": False,
                }),
            ]),
        ])
        d = session.to_dict()
        assert d["version"] == 1
        assert len(d["messages"]) == 3
        assert d["messages"][0]["role"] == "user"
        assert d["messages"][0]["blocks"][0]["type"] == "text"
        assert d["messages"][1]["blocks"][1]["type"] == "tool_use"
        assert d["messages"][1]["blocks"][1]["name"] == "bash"
        assert d["messages"][1]["usage"]["input_tokens"] == 10
        assert d["messages"][2]["blocks"][0]["type"] == "tool_result"
        assert d["messages"][2]["blocks"][0]["is_error"] is False
