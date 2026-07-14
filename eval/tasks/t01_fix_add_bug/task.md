Fix the bug in mathlib.py. The add function currently returns a - b but should return a + b. After fixing, verify by running the tests: python -m pytest test_math.py -v
