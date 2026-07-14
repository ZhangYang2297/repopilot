"""Setup: multi-file project with cross-file dependencies."""
from pathlib import Path
def setup(repo: Path):
    (repo / "user.py").write_text('''
class User:
    def __init__(self, user_id, name, email):
        self.id = user_id
        self.name = name
        self.email = email

    def display_name(self):
        return self.name.upper()  # BUG: should be self.name.title()
''', encoding="utf-8")
    (repo / "user_store.py").write_text('''
from user import User

class UserStore:
    def __init__(self):
        self._users = {}

    def add_user(self, name, email):
        user_id = len(self._users) + 1
        user = User(user_id, name, email)
        self._users[user_id] = user
        return user

    def find_by_email(self, email):
        for u in self._users.values():
            if u.email == email:  # BUG: case-sensitive, should be case-insensitive
                return u
        return None

    def list_all(self):
        return self._users.values()
''', encoding="utf-8")
    (repo / "test_users.py").write_text('''
import pytest
from user import User
from user_store import UserStore

def test_user_display_name_title_case():
    u = User(1, "john doe", "john@example.com")
    assert u.display_name() == "John Doe"

def test_user_store_add_and_find():
    store = UserStore()
    u = store.add_user("Alice", "alice@example.com")
    assert u.id == 1
    assert store.find_by_email("alice@example.com").name == "Alice"

def test_find_by_email_case_insensitive():
    store = UserStore()
    store.add_user("Bob", "Bob@Example.COM")
    assert store.find_by_email("bob@example.com") is not None
    assert store.find_by_email("BOB@EXAMPLE.COM") is not None

def test_list_all():
    store = UserStore()
    store.add_user("A", "a@b.com")
    store.add_user("B", "c@d.com")
    assert len(list(store.list_all())) == 2
''', encoding="utf-8")
