"""Setup: a package with import errors that the agent must resolve."""
from pathlib import Path
def setup(repo: Path):
    (repo / "config.py").write_text('''
DATABASE_URL = "sqlite:///app.db"
DEBUG = True
MAX_CONNECTIONS = 10
TIMEOUT = 30
''', encoding="utf-8")
    (repo / "database.py").write_text('''
from config import DATABASE_URL, MAX_CONNECTIONS, TIMEOUT  # BUG: imports DEBUG which doesn't exist in some call paths

def get_connection():
    return {"url": DATABASE_URL, "max_conn": MAX_CONNECTIONS, "timeout": TIMEOUT}

def execute_query(query):
    conn = get_connection()
    return f"Executing on {conn['url']}: {query}"
''', encoding="utf-8")
    (repo / "api.py").write_text('''
from database import execute_query
# BUG: imports from non-existent module
from auth import authenticate
from utils import format_response

def handle_request(endpoint, data=None):
    user = authenticate(data.get("token") if data else None)
    if endpoint == "/users":
        result = execute_query("SELECT * FROM users")
        return format_response(result)
    return format_response({"error": "not found"})
''', encoding="utf-8")
    (repo / "test_api.py").write_text('''
import sys
import os

# Need auth.py and utils.py stubs to exist for import to work
def test_imports_resolve():
    """All modules should be importable without errors."""
    import config
    import database
    assert database.get_connection()["url"] == "sqlite:///app.db"

def test_missing_modules_created():
    """auth.py and utils.py should exist and provide the needed functions."""
    assert os.path.exists("auth.py"), "auth.py must be created"
    assert os.path.exists("utils.py"), "utils.py must be created"
    import auth
    import utils
    assert hasattr(auth, "authenticate"), "auth.py must have authenticate()"
    assert hasattr(utils, "format_response"), "utils.py must have format_response()"

def test_handle_request():
    """api.handle_request should work end-to-end after fixes."""
    from api import handle_request
    resp = handle_request("/users", {"token": "abc"})
    assert resp is not None
''', encoding="utf-8")
