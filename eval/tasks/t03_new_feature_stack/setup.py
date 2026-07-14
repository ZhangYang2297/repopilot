"""Setup: empty repo, just ask agent to create a stack data structure."""
from pathlib import Path
def setup(repo: Path):
    # Write test file first (test-driven task)
    (repo / "test_stack.py").write_text('''
import pytest
from stack import Stack

class TestStack:
    def test_push_pop(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert s.pop() == 2
        assert s.pop() == 1

    def test_empty_pop_raises(self):
        s = Stack()
        with pytest.raises(IndexError):
            s.pop()

    def test_peek(self):
        s = Stack()
        s.push("a")
        assert s.peek() == "a"
        assert s.size() == 1

    def test_size_and_len(self):
        s = Stack()
        assert s.size() == 0
        assert len(s) == 0
        s.push(1)
        s.push(2)
        assert s.size() == 2
        assert len(s) == 2

    def test_is_empty(self):
        s = Stack()
        assert s.is_empty() is True
        s.push(1)
        assert s.is_empty() is False

    def test_iter(self):
        s = Stack()
        for i in [1, 2, 3]:
            s.push(i)
        assert list(s) == [3, 2, 1]  # LIFO order

    def test_str(self):
        s = Stack()
        s.push(1)
        s.push(2)
        assert "2" in str(s)
        assert "1" in str(s)
''', encoding="utf-8")
