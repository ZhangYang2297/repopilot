"""Todo statistics — compute aggregate metrics from a TodoStore."""
from __future__ import annotations
from store import TodoStore


class TodoStats:
    """Compute statistics over a TodoStore's todos."""

    def __init__(self, store: TodoStore):
        self.store = store

    def total_count(self) -> int:
        """Return the total number of todos."""
        return len(self.store.list_todos())

    def completed_count(self) -> int:
        """Return the number of completed todos."""
        return len(self.store.list_todos(completed=True))

    def pending_count(self) -> int:
        """Return the number of pending (incomplete) todos."""
        return len(self.store.list_todos(completed=False))

    def overdue_count(self) -> int:
        """Return the number of overdue todos."""
        return len(self.store.get_overdue())

    def count(self) -> dict[str, int]:
        """Return a dict with total, pending, completed, and overdue counts."""
        return {
            "total": self.total_count(),
            "pending": self.pending_count(),
            "completed": self.completed_count(),
            "overdue": self.overdue_count(),
        }

    def priority_breakdown(self) -> dict[str, int]:
        """Return a dict mapping priority level to count, e.g. {'low': N, 'medium': N, 'high': N}."""
        counts = {"low": 0, "medium": 0, "high": 0}
        for todo in self.store.list_todos():
            counts[todo.priority] = counts.get(todo.priority, 0) + 1
        return counts

    def count_by_tag(self) -> dict[str, int]:
        """Return a dict mapping tag name to the number of todos carrying that tag."""
        counts: dict[str, int] = {}
        for todo in self.store.list_todos():
            for tag in todo.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def tag_cloud(self) -> dict[str, int]:
        """Return a dict of tag -> count across all todos, sorted by count descending."""
        counts = self.count_by_tag()
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))

    def completion_rate(self) -> float:
        """Return the fraction of todos that are completed, as a float between 0.0 and 1.0.

        Returns 0.0 if there are no todos.
        """
        total = self.total_count()
        if total == 0:
            return 0.0
        return self.completed_count() / total

    def summary(self) -> dict:
        """Return a dictionary containing all statistics at once."""
        return {
            "total": self.total_count(),
            "completed": self.completed_count(),
            "pending": self.pending_count(),
            "overdue": self.overdue_count(),
            "by_priority": self.priority_breakdown(),
            "by_tag": self.count_by_tag(),
            "completion_rate": self.completion_rate(),
        }
