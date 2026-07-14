"""Todo storage — in-memory + JSON persistence.

KNOWN BUGS (to be fixed by the agent):
  1. save() writes to wrong file: uses self.file_path + ".bak" instead of self.file_path
  2. load() does not parse tags correctly (forgets to convert them back to list)
  3. delete_todo silently succeeds even if id does not exist; should raise KeyError
  4. get_overdue returns completed todos that are past due (should exclude completed)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from models import Todo


class TodoStore:
    def __init__(self, file_path: str = "todos.json"):
        self.file_path = Path(file_path)
        self._todos: dict[int, Todo] = {}
        self._next_id = 1

    def add_todo(self, title: str, priority: str = "medium", due_date: Optional[str] = None, tags: list[str] | None = None) -> Todo:
        todo = Todo(
            id=self._next_id,
            title=title,
            priority=priority,
            due_date=due_date,
            tags=tags or [],
        )
        self._todos[todo.id] = todo
        self._next_id += 1
        return todo

    def get_todo(self, todo_id: int) -> Todo:
        return self._todos[todo_id]

    def complete_todo(self, todo_id: int) -> Todo:
        todo = self.get_todo(todo_id)
        todo.mark_complete()
        return todo

    def update_todo(self, todo_id: int, **kwargs) -> Todo:
        todo = self.get_todo(todo_id)  # raises KeyError if not found
        todo.update(**kwargs)
        return todo

    def delete_todo(self, todo_id: int) -> None:
        if todo_id not in self._todos:
            raise KeyError(todo_id)
        del self._todos[todo_id]

    def list_todos(self, completed: Optional[bool] = None, tag: Optional[str] = None) -> list[Todo]:
        result = list(self._todos.values())
        if completed is not None:
            result = [t for t in result if t.completed == completed]
        if tag is not None:
            result = [t for t in result if t.matches_tag(tag)]
        return result

    def get_overdue(self) -> list[Todo]:
        return [t for t in self._todos.values() if t.is_overdue()]

    def search_by_title(self, keyword: str) -> list[Todo]:
        kw = keyword.lower()
        return [t for t in self._todos.values() if kw in t.title.lower()]

    def save(self) -> None:
        data = []
        for todo in self._todos.values():
            data.append({
                "id": todo.id,
                "title": todo.title,
                "completed": todo.completed,
                "priority": todo.priority,
                "created_at": todo.created_at,
                "due_date": todo.due_date,
                "tags": todo.tags,
            })
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load(self) -> None:
        if not self.file_path.exists():
            return
        with open(self.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._todos.clear()
        self._next_id = 1
        for item in data:
            todo = Todo(
                id=item["id"],
                title=item["title"],
                completed=item["completed"],
                priority=item["priority"],
                created_at=item["created_at"],
                due_date=item.get("due_date"),
                tags=item.get("tags", []),
            )
            self._todos[todo.id] = todo
            if todo.id >= self._next_id:
                self._next_id = todo.id + 1
