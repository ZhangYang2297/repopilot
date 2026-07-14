"""Tests for stats.TodoStats."""
import pytest
from store import TodoStore
from stats import TodoStats


class TestTodoStats:
    def _make_store(self):
        """Helper: build a store with a known mix of todos."""
        s = TodoStore()
        # 2 high priority, 1 overdue
        s.add_todo("urgent task", priority="high", due_date="2000-01-01T00:00:00", tags=["work", "urgent"])
        s.add_todo("important task", priority="high", tags=["work"])
        # 1 medium priority, completed
        t = s.add_todo("normal task", priority="medium", tags=["work", "home"])
        s.complete_todo(t.id)
        # 1 low priority
        s.add_todo("chill task", priority="low", tags=["home"])
        return s

    # ---------- count() ----------

    def test_count_empty_store(self):
        s = TodoStore()
        stats = TodoStats(s)
        assert stats.count() == {"total": 0, "pending": 0, "completed": 0, "overdue": 0}

    def test_count_mixed(self):
        s = self._make_store()
        stats = TodoStats(s)
        result = stats.count()
        assert result["total"] == 4
        assert result["completed"] == 1
        assert result["pending"] == 3
        assert result["overdue"] == 1

    def test_count_all_completed(self):
        s = TodoStore()
        t1 = s.add_todo("a")
        t2 = s.add_todo("b")
        s.complete_todo(t1.id)
        s.complete_todo(t2.id)
        stats = TodoStats(s)
        result = stats.count()
        assert result == {"total": 2, "pending": 0, "completed": 2, "overdue": 0}

    def test_count_keys_present(self):
        s = self._make_store()
        stats = TodoStats(s)
        result = stats.count()
        assert set(result.keys()) == {"total", "pending", "completed", "overdue"}

    # ---------- priority_breakdown() ----------

    def test_priority_breakdown_empty(self):
        s = TodoStore()
        stats = TodoStats(s)
        assert stats.priority_breakdown() == {"low": 0, "medium": 0, "high": 0}

    def test_priority_breakdown_mixed(self):
        s = self._make_store()
        stats = TodoStats(s)
        result = stats.priority_breakdown()
        assert result == {"low": 1, "medium": 1, "high": 2}

    def test_priority_breakdown_sums_to_total(self):
        s = self._make_store()
        stats = TodoStats(s)
        breakdown = stats.priority_breakdown()
        assert sum(breakdown.values()) == stats.total_count()

    def test_priority_breakdown_all_low(self):
        s = TodoStore()
        s.add_todo("a", priority="low")
        s.add_todo("b", priority="low")
        stats = TodoStats(s)
        assert stats.priority_breakdown() == {"low": 2, "medium": 0, "high": 0}

    def test_priority_breakdown_has_all_three_keys(self):
        s = TodoStore()
        s.add_todo("a", priority="high")
        stats = TodoStats(s)
        result = stats.priority_breakdown()
        assert set(result.keys()) == {"low", "medium", "high"}

    # ---------- tag_cloud() ----------

    def test_tag_cloud_empty(self):
        s = TodoStore()
        stats = TodoStats(s)
        assert stats.tag_cloud() == {}

    def test_tag_cloud_no_tags(self):
        s = TodoStore()
        s.add_todo("no tags here")
        stats = TodoStats(s)
        assert stats.tag_cloud() == {}

    def test_tag_cloud_counts(self):
        s = self._make_store()
        stats = TodoStats(s)
        result = stats.tag_cloud()
        # work appears on 3 todos, home on 2, urgent on 1
        assert result["work"] == 3
        assert result["home"] == 2
        assert result["urgent"] == 1

    def test_tag_cloud_sorted_descending(self):
        s = self._make_store()
        stats = TodoStats(s)
        result = stats.tag_cloud()
        values = list(result.values())
        assert values == sorted(values, reverse=True)

    def test_tag_cloud_order_with_ties(self):
        s = TodoStore()
        s.add_todo("a", tags=["x", "y"])
        s.add_todo("b", tags=["x", "y"])
        stats = TodoStats(s)
        result = stats.tag_cloud()
        # both have count 2; both should be present
        assert result["x"] == 2
        assert result["y"] == 2
        assert list(result.values()) == [2, 2]
