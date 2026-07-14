This project has broken imports. api.py tries to import from "auth" and "utils" modules that don't exist. Do the following:
1. Create auth.py with an authenticate(token) function that returns a user dict (or None if no token)
2. Create utils.py with a format_response(data) function that wraps data in a JSON-serializable dict like {"status": "ok", "data": data}
3. Make sure all modules can be imported without errors
4. Run python -m pytest test_api.py -v and ensure all 3 tests pass

Start by reading the existing files to understand what's needed, then create the missing files.
