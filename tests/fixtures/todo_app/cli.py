"""CLI interface for todo app.

BUG: the --priority flag is not passed to add_todo() so all todos are medium priority.
"""
from __future__ import annotations
import argparse
import sys
from store import TodoStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Todo CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a todo")
    add_p.add_argument("title", help="Todo title")
    add_p.add_argument("--priority", default="medium", choices=["low", "medium", "high"])
    add_p.add_argument("--due", default=None, help="Due date ISO format")
    add_p.add_argument("--tag", action="append", default=[], dest="tags")

    list_p = sub.add_parser("list", help="List todos")
    list_p.add_argument("--completed", action="store_true", default=None)
    list_p.add_argument("--pending", action="store_true")
    list_p.add_argument("--tag", default=None)

    done_p = sub.add_parser("done", help="Mark todo complete")
    done_p.add_argument("id", type=int)

    rm_p = sub.add_parser("rm", help="Delete todo")
    rm_p.add_argument("id", type=int)

    update_p = sub.add_parser("update", help="Update an existing todo")
    update_p.add_argument("id", type=int)
    update_p.add_argument("--title", default=None, help="New title")
    update_p.add_argument("--priority", default=None, choices=["low", "medium", "high"], help="New priority")
    update_p.add_argument("--due", default=None, dest="due_date", help="New due date ISO format")
    update_p.add_argument("--tag", action="append", default=None, dest="tags", help="New tags (replaces existing)")

    edit_p = sub.add_parser("edit", help="Edit an existing todo")
    edit_p.add_argument("id", type=int)
    edit_p.add_argument("--title", default=None, help="New title")
    edit_p.add_argument("--priority", default=None, choices=["low", "medium", "high"], help="New priority")
    edit_p.add_argument("--due", default=None, dest="due_date", help="New due date ISO format")

    overdue_p = sub.add_parser("overdue", help="List overdue todos")

    search_p = sub.add_parser("search", help="Search todos by title keyword")
    search_p.add_argument("keyword", help="Keyword to search for (case-insensitive substring)")

    save_p = sub.add_parser("save", help="Save to JSON")

    args = parser.parse_args(argv)
    store = TodoStore()

    if args.command == "add":
        todo = store.add_todo(args.title, priority=args.priority, due_date=args.due, tags=args.tags)
        print(f"Added #{todo.id}: {todo.title} [{todo.priority}]")

    elif args.command == "list":
        completed = None
        if args.pending:
            completed = False
        elif args.completed:
            completed = True
        todos = store.list_todos(completed=completed, tag=args.tag)
        if not todos:
            print("(empty)")
        for t in todos:
            status = "x" if t.completed else " "
            print(f"[{status}] #{t.id} {t.title} ({t.priority})")

    elif args.command == "done":
        store.complete_todo(args.id)
        print(f"Completed #{args.id}")

    elif args.command == "rm":
        store.delete_todo(args.id)
        print(f"Deleted #{args.id}")

    elif args.command == "update":
        todo = store.update_todo(
            args.id,
            title=args.title,
            priority=args.priority,
            due_date=args.due_date,
            tags=args.tags,
        )
        print(f"Updated #{todo.id}: {todo.title} [{todo.priority}]")

    elif args.command == "edit":
        kwargs = {}
        if args.title is not None:
            kwargs["title"] = args.title
        if args.priority is not None:
            kwargs["priority"] = args.priority
        if args.due_date is not None:
            kwargs["due_date"] = args.due_date
        if not kwargs:
            print("Error: no fields to edit. Provide at least one of --title, --priority, --due", file=sys.stderr)
            sys.exit(1)
        todo = store.update_todo(args.id, **kwargs)
        print(f"Edited #{todo.id}: {todo.title} [{todo.priority}]")

    elif args.command == "overdue":
        for t in store.get_overdue():
            print(f"! #{t.id} {t.title} (due: {t.due_date})")

    elif args.command == "search":
        todos = store.search_by_title(args.keyword)
        if not todos:
            print("(no matches)")
        for t in todos:
            status = "x" if t.completed else " "
            print(f"[{status}] #{t.id} {t.title} ({t.priority})")

    elif args.command == "save":
        store.save()
        print(f"Saved to {store.file_path}")


if __name__ == "__main__":
    main()
