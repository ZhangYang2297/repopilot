from __future__ import annotations
import json
import os
import time
from pathlib import Path

import pytest

from repopilot.session import SessionStore, Session, VALID_EVENT_TYPES


@pytest.fixture
def store(tmp_path):
    """Create a SessionStore in a temp directory."""
    sessions_dir = tmp_path / "sessions"
    db_path = tmp_path / "state.sqlite"
    return SessionStore(sessions_dir=sessions_dir, db_path=db_path)


class TestSessionCreate:
    def test_create_returns_session(self, store):
        s = store.create(title="my session", cwd="/code", model="gpt-4")
        assert isinstance(s, Session)
        assert len(s.id) == 12
        assert s.title == "my session"
        assert s.cwd == "/code"
        assert s.model == "gpt-4"
        assert s.last_event_id == 0
        assert s.tokens_used == 0

    def test_create_persists_rollout_file(self, store):
        s = store.create()
        assert Path(s.rollout_path).exists()
        # Path should be under sessions/YYYY/MM/DD/
        parts = Path(s.rollout_path).parts
        assert "sessions" in parts

    def test_create_default_title(self, store):
        s = store.create()
        assert s.title.startswith("Session ")

    def test_get_existing(self, store):
        s = store.create(title="hello")
        loaded = store.get(s.id)
        assert loaded is not None
        assert loaded.id == s.id
        assert loaded.title == "hello"

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("no-such-id") is None

    def test_create_multiple_sessions(self, store):
        s1 = store.create(title="first")
        s2 = store.create(title="second")
        assert s1.id != s2.id
        assert len(store.list()) == 2


class TestEventAppend:
    def test_append_event_assigns_incrementing_ids(self, store):
        s = store.create()
        e1 = store.append_event(s.id, "user_msg", {"content": "hi"})
        e2 = store.append_event(s.id, "assistant_msg", {"content": "hello"})
        e3 = store.append_event(s.id, "tool_call", {"tool": "bash"})
        assert e1["id"] == 1
        assert e2["id"] == 2
        assert e3["id"] == 3

    def test_append_event_has_ts(self, store):
        s = store.create()
        e = store.append_event(s.id, "user_msg", {"q": "test"})
        assert "ts" in e
        assert "T" in e["ts"]  # ISO8601

    def test_append_event_updates_last_event_id(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {})
        store.append_event(s.id, "assistant_msg", {})
        loaded = store.get(s.id)
        assert loaded.last_event_id == 2

    def test_append_invalid_type_raises(self, store):
        s = store.create()
        with pytest.raises(ValueError, match="Invalid event type"):
            store.append_event(s.id, "bogus_type", {})

    def test_append_to_nonexistent_session_raises(self, store):
        with pytest.raises(KeyError):
            store.append_event("no-such-id", "user_msg", {})

    def test_append_writes_jsonl_line(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {"content": "hello world"})
        with open(s.rollout_path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        evt = json.loads(line)
        assert evt["type"] == "user_msg"
        assert evt["payload"]["content"] == "hello world"
        assert evt["id"] == 1

    def test_all_valid_event_types_accepted(self, store):
        s = store.create()
        for etype in VALID_EVENT_TYPES:
            store.append_event(s.id, etype, {"x": 1})
        events = store.read_events(s.id)
        types = [e["type"] for e in events]
        for etype in VALID_EVENT_TYPES:
            assert etype in types

    def test_tokens_used_accumulated_from_assistant_msg(self, store):
        s = store.create()
        store.append_event(s.id, "assistant_msg", {"usage": {"total_tokens": 100}})
        store.append_event(s.id, "assistant_msg", {"usage": {"total_tokens": 200}})
        loaded = store.get(s.id)
        assert loaded.tokens_used == 300

    def test_empty_payload_default(self, store):
        s = store.create()
        e = store.append_event(s.id, "finish")
        assert e["payload"] == {}


class TestReadEvents:
    def test_read_all_events(self, store):
        s = store.create()
        for i in range(5):
            store.append_event(s.id, "user_msg" if i % 2 == 0 else "assistant_msg", {"i": i})
        events = store.read_events(s.id)
        assert len(events) == 5

    def test_read_after_id(self, store):
        s = store.create()
        for i in range(5):
            store.append_event(s.id, "user_msg", {"i": i})
        events = store.read_events(s.id, after_id=3)
        assert len(events) == 2
        assert events[0]["id"] == 4
        assert events[1]["id"] == 5

    def test_read_after_id_zero_returns_all(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {})
        store.append_event(s.id, "user_msg", {})
        events = store.read_events(s.id, after_id=0)
        assert len(events) == 2

    def test_read_nonexistent_raises(self, store):
        with pytest.raises(KeyError):
            store.read_events("missing-id")


class TestSessionList:
    def test_list_empty(self, store):
        assert store.list() == []

    def test_list_returns_meta(self, store):
        s = store.create(title="t1", cwd="/a", model="m1")
        meta = store.list()
        assert len(meta) == 1
        assert meta[0].id == s.id
        assert meta[0].title == "t1"
        assert meta[0].cwd == "/a"
        assert meta[0].model == "m1"
        assert meta[0].event_count == 0

    def test_list_ordered_by_updated_desc(self, store):
        s1 = store.create(title="first")
        s2 = store.create(title="second")
        s3 = store.create(title="third")
        # Append to s1 so it becomes most recently updated
        store.append_event(s1.id, "user_msg", {})
        meta = store.list()
        assert meta[0].id == s1.id

    def test_list_respects_limit(self, store):
        for i in range(10):
            store.create(title=f"s{i}")
        assert len(store.list(limit=3)) == 3


class TestRewind:
    def test_rewind_truncates_events(self, store):
        s = store.create()
        for i in range(5):
            store.append_event(s.id, "user_msg", {"i": i})
        kept = store.rewind(s.id, step=3)
        assert len(kept) == 3
        events = store.read_events(s.id)
        assert len(events) == 3
        assert events[-1]["id"] == 3

    def test_rewind_to_zero_clears_all(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {})
        store.append_event(s.id, "assistant_msg", {})
        kept = store.rewind(s.id, step=0)
        assert kept == []
        events = store.read_events(s.id)
        assert len(events) == 0

    def test_rewind_updates_last_event_id(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {})
        store.append_event(s.id, "assistant_msg", {})
        store.append_event(s.id, "tool_call", {})
        store.rewind(s.id, step=1)
        loaded = store.get(s.id)
        assert loaded.last_event_id == 1

    def test_rewind_then_append_continues(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {"q": "first"})
        store.append_event(s.id, "assistant_msg", {"a": "bad"})
        store.rewind(s.id, step=1)
        e = store.append_event(s.id, "assistant_msg", {"a": "better"})
        assert e["id"] == 2
        events = store.read_events(s.id)
        assert len(events) == 2
        assert events[1]["payload"]["a"] == "better"

    def test_rewind_negative_raises(self, store):
        s = store.create()
        with pytest.raises(ValueError):
            store.rewind(s.id, step=-1)

    def test_rewind_nonexistent_raises(self, store):
        with pytest.raises(KeyError):
            store.rewind("nope", step=1)

    def test_rewind_preserves_prefix_content(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {"content": "keep this"})
        store.append_event(s.id, "assistant_msg", {"content": "trash"})
        store.append_event(s.id, "tool_call", {"tool": "bash"})
        store.rewind(s.id, step=1)
        events = store.read_events(s.id)
        assert events[0]["payload"]["content"] == "keep this"


class TestDelete:
    def test_delete_session(self, store):
        s = store.create()
        store.append_event(s.id, "user_msg", {})
        assert store.delete(s.id)
        assert store.get(s.id) is None
        assert not Path(s.rollout_path).exists()

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("no-such") is False


class TestUpdateTitle:
    def test_update_title(self, store):
        s = store.create(title="old")
        store.update_title(s.id, "new title")
        loaded = store.get(s.id)
        assert loaded.title == "new title"


class TestEventTypes:
    def test_valid_event_types_set(self):
        expected = {"user_msg", "assistant_msg", "tool_call", "tool_result",
                    "plan", "replan", "finish", "error", "slash"}
        for t in expected:
            assert t in VALID_EVENT_TYPES
