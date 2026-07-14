"""Todo model — represents a single todo item."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Todo:
    id: int
    title: str
    completed: bool = False
    priority: str = "medium"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    due_date: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def mark_complete(self) -> None:
        self.completed = True

    def is_overdue(self) -> bool:
        if self.due_date is None:
            return False
        due = datetime.fromisoformat(self.due_date)
        return due < datetime.now() and not self.completed

    def add_tag(self, tag: str) -> None:
        if tag not in self.tags:
            self.tags.append(tag)

    def matches_tag(self, tag: str) -> bool:
        return tag in self.tags

    def rename(self, new_title: str) -> None:
        self.title = new_title

    def set_priority(self, priority: str) -> None:
        if priority not in ("low", "medium", "high"):
            raise ValueError(f"Invalid priority '{priority}'; must be one of: low, medium, high")
        self.priority = priority

    def update(
        self,
        title: Optional[str] = None,
        priority: Optional[str] = None,
        completed: Optional[bool] = None,
        due_date: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        if title is not None:
            self.rename(title)
        if priority is not None:
            self.set_priority(priority)
        if completed is not None:
            self.completed = completed
        if due_date is not None:
            self.due_date = due_date
        if tags is not None:
            self.tags = tags
