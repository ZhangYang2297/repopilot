The log_parser.py has bugs when handling edge cases:
1. error_rate() will crash with ZeroDivisionError on empty files - fix it to return 0.0
2. filter_by_level() does exact match on level.upper() but the argument may be lowercase like "error" - normalize the input level too

Fix these bugs so all tests pass. The log file server.log has 500 lines (50 errors, 50 warnings, 400 info). Run the tests: python -m pytest test_log_parser.py -v
