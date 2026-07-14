The strutils.py module has 4 bugs:
1. reverse_words adds a trailing space
2. is_palindrome uses reversed() incorrectly (returns iterator, not reversed string)
3. count_vowels doesn't handle uppercase letters
4. truncate adds suffix on top of max_len instead of replacing the end

Fix all bugs so that all tests in test_strutils.py pass. Run the tests to verify.
