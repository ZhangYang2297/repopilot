"""Setup: a todo item module that needs CLI interface added."""
from pathlib import Path
def setup(repo: Path):
    (repo / "todo.py").write_text('''
import json
import sys

class TodoManager:
    def __init__(self, path="todos.json"):
        self.path = path
        self.todos = []
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self.todos = json.load(f)
        except FileNotFoundError:
            self.todos = []

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.todos, f, indent=2)

    def add(self, text):
        todo = {"id": len(self.todos)+1, "text": text, "done": False}
        self.todos.append(todo)
        self._save()
        return todo

    def list(self):
        return self.todos

    def done(self, todo_id):
        for t in self.todos:
            if t["id"] == todo_id:
                t["done"] = True
                self._save()
                return t
        return None

if __name__ == "__main__":
    pass  # TODO: implement CLI
''', encoding="utf-8")
    (repo / "test_todo_cli.py").write_text('''
import subprocess, sys, os, json

TODO_PY = "todo.py"

def test_add_todo():
    # Clean up
    if os.path.exists("todos.json"):
        os.remove("todos.json")
    r = subprocess.run([sys.executable, TODO_PY, "add", "buy milk"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert os.path.exists("todos.json")
    with open("todos.json") as f:
        todos = json.load(f)
    assert len(todos) == 1
    assert todos[0]["text"] == "buy milk"
    assert todos[0]["done"] is False
    os.remove("todos.json")

def test_list_todos():
    if os.path.exists("todos.json"):
        os.remove("todos.json")
    subprocess.run([sys.executable, TODO_PY, "add", "task1"], capture_output=True)
    subprocess.run([sys.executable, TODO_PY, "add", "task2"], capture_output=True)
    r = subprocess.run([sys.executable, TODO_PY, "list"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert "task1" in r.stdout
    assert "task2" in r.stdout
    os.remove("todos.json")

def test_done():
    if os.path.exists("todos.json"):
        os.remove("todos.json")
    subprocess.run([sys.executable, TODO_PY, "add", "finish me"], capture_output=True)
    r = subprocess.run([sys.executable, TODO_PY, "done", "1"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    with open("todos.json") as f:
        todos = json.load(f)
    assert todos[0]["done"] is True
    os.remove("todos.json")
''', encoding="utf-8")
