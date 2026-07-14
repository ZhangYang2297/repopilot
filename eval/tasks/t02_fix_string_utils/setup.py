"""Setup: string utility module with multiple bugs."""
from pathlib import Path
def setup(repo: Path):
    (repo / "strutils.py").write_text('''
def reverse_words(s: str) -> str:
    """Reverse each word in a string but keep word order.
    E.g. 'hello world' -> 'olleh dlrow'
    """
    words = s.split()
    return " ".join(w[::-1] for w in words) + " "  # BUG: trailing space

def is_palindrome(s: str) -> bool:
    """Check if string is a palindrome (case-insensitive, ignoring spaces)."""
    cleaned = s.replace(" ", "").lower()
    return cleaned == reversed(cleaned)  # BUG: reversed() returns iterator, not string

def count_vowels(s: str) -> int:
    """Count vowels in string (case-insensitive)."""
    vowels = "aeiou"
    return sum(1 for c in s if c in vowels)  # BUG: not case-insensitive

def truncate(s: str, max_len: int = 10, suffix: str = "...") -> str:
    """Truncate string to max_len characters (including suffix)."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + suffix  # BUG: suffix adds beyond max_len
''', encoding="utf-8")
    (repo / "test_strutils.py").write_text('''
from strutils import reverse_words, is_palindrome, count_vowels, truncate

def test_reverse_words():
    assert reverse_words("hello world") == "olleh dlrow"
    assert reverse_words("a") == "a"
    assert reverse_words("") == ""

def test_is_palindrome():
    assert is_palindrome("racecar") is True
    assert is_palindrome("A man a plan a canal Panama") is True
    assert is_palindrome("hello") is False

def test_count_vowels():
    assert count_vowels("hello") == 2
    assert count_vowels("HELLO") == 2
    assert count_vowels("xyz") == 0

def test_truncate():
    assert truncate("hello world", 8) == "hello..."
    assert truncate("hello", 10) == "hello"
    assert len(truncate("abcdefghijklmnop", 10)) == 10
''', encoding="utf-8")
