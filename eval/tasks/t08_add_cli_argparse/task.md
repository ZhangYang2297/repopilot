The todo.py file has a TodoManager class but the __main__ block is empty. Add argparse-based CLI to support three subcommands:
1. `python todo.py add "text"` - add a todo
2. `python todo.py list` - list all todos (print them with id and done status)
3. `python todo.py done <id>` - mark a todo as done by id

The CLI should print output for each command (e.g. "Added: buy milk" for add, formatted list for list, "Marked #1 as done" for done).
Do NOT modify the TodoManager class itself (it works correctly), just add the CLI in the __main__ block.
After implementing, run: python -m pytest test_todo_cli.py -v
