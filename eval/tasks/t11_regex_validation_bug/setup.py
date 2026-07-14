"""Setup: validator module with regex bugs and missing validation functions."""
from pathlib import Path
def setup(repo: Path):
    (repo / "validators.py").write_text('''
import re

def is_valid_email(email):
    # BUG: regex is wrong - allows emails without @ sign or domain
    pattern = r"^[a-zA-Z0-9_.+-]+@?[a-zA-Z0-9-]+\\.?[a-zA-Z0-9]*\\.?[a-zA-Z]*$"
    return bool(re.match(pattern, email))

def is_valid_phone(phone):
    # BUG: only accepts exactly 10 digits, no separators allowed
    return bool(re.match(r"^\\d{10}$", phone))

def is_valid_url(url):
    # BUG: doesn't require http:// or https:// prefix and matches too broadly
    return bool(re.match(r"^[a-z]+\\.[a-z]+", url))

def is_valid_password(password):
    # BUG: only checks length >= 6, missing other requirements
    return len(password) >= 6
''', encoding="utf-8")
    (repo / "test_validators.py").write_text('''
import pytest
from validators import is_valid_email, is_valid_phone, is_valid_url, is_valid_password

class TestEmail:
    def test_valid_emails(self):
        assert is_valid_email("user@example.com") is True
        assert is_valid_email("a@b.co") is True
        assert is_valid_email("test.user+tag@domain.org") is True

    def test_invalid_emails(self):
        assert is_valid_email("notanemail") is False
        assert is_valid_email("@nodomain.com") is False
        assert is_valid_email("noat.com") is False
        assert is_valid_email("missing@.com") is False

class TestPhone:
    def test_valid_phones(self):
        assert is_valid_phone("1234567890") is True
        assert is_valid_phone("123-456-7890") is True
        assert is_valid_phone("(123) 456-7890") is True

    def test_invalid_phones(self):
        assert is_valid_phone("12345") is False
        assert is_valid_phone("abcdefghij") is False
        assert is_valid_phone("") is False

class TestUrl:
    def test_valid_urls(self):
        assert is_valid_url("http://example.com") is True
        assert is_valid_url("https://example.com/path?q=1") is True

    def test_invalid_urls(self):
        assert is_valid_url("example.com") is False
        assert is_valid_url("not a url") is False
        assert is_valid_url("") is False

class TestPassword:
    def test_valid_passwords(self):
        assert is_valid_password("Abc123!@") is True
        assert is_valid_password("Test1ng!") is True

    def test_invalid_passwords(self):
        assert is_valid_password("short") is False  # too short
        assert is_valid_password("alllowercase1!") is False  # no uppercase
        assert is_valid_password("ALLUPPERCASE1!") is False  # no lowercase
        assert is_valid_password("NoDigits!!") is False  # no digit
        assert is_valid_password("NoSpecial1") is False  # no special char
''', encoding="utf-8")
