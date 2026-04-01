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
