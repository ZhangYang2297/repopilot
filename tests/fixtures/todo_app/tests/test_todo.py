"""Tests for todo_app. These tests currently FAIL due to planted bugs — the agent must fix them."""
import pytest
import os
import tempfile
from models import Todo
from store import TodoStore


class TestTodoModel:
    def test_mark_complete(self):
        t = Todo(id=1, title="test")
        assert not t.completed
        t.mark_complete()
        assert t.completed

    def test_add_tag_no_duplicates(self):
        t = Todo(id=1, title="test")
        t.add_tag("work")
        t.add_tag("work")
        assert t.tags.count("work") == 1

    def test_is_overdue_future(self):
        t = Todo(id=1, title="test", due_date="2099-01-01T00:00:00")
        assert not t.is_overdue()


class TestTodoStore:
    def test_add_and_get(self):
        s = TodoStore()
        t = s.add_todo("buy milk")
        assert s.get_todo(t.id).title == "buy milk"

    def test_delete_nonexistent_raises(self):
        s = TodoStore()
        with pytest.raises(KeyError):
            s.delete_todo(999)

    def test_get_overdue_excludes_completed(self):
        s = TodoStore()
        t1 = s.add_todo("past due", due_date="2000-01-01T00:00:00")
        t2 = s.add_todo("past done", due_date="2000-01-01T00:00:00")
        s.complete_todo(t2.id)
        overdue = s.get_overdue()
        assert t1 in overdue
        assert t2 not in overdue

    def test_save_and_load_preserves_tags(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "todos.json")
            s1 = TodoStore(path)
            s1.add_todo("test", tags=["urgent", "work"])
            s1.save()
            # Verify the file was written to the correct path (not .bak)
            assert os.path.exists(path)
            assert not os.path.exists(path + ".bak")

            s2 = TodoStore(path)
            s2.load()
            loaded = s2.list_todos()
            assert len(loaded) == 1
            assert "urgent" in loaded[0].tags
            assert "work" in loaded[0].tags


class TestCLI:
    def test_add_priority_flag(self):
        """CLI add --priority high should actually set high priority."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "cli.py", "add", "urgent task", "--priority", "high"],
            capture_output=True, text=True, cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert "[high]" in result.stdout, f"Expected [high] in output, got: {result.stdout}"
