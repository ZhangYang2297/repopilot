This project has bugs spanning two files:
1. In user.py: display_name() uses .upper() but should use .title() so "john doe" becomes "John Doe"
2. In user_store.py: find_by_email() does case-sensitive comparison but should be case-insensitive

Read the tests first, then fix both files. Run python -m pytest test_users.py -v to verify all 4 tests pass.
