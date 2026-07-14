The calculator.py module has runtime bugs. Run the tests (python -m pytest test_calculator.py -v) to see the failures. Fix ALL bugs in calculator.py:
1. average() has a bug where total + n doesn't assign back to total (should be total += n)
2. format_result() uses .toString() which is not Python (should use str() or round() properly)
Do NOT modify the test file. Make all tests pass.
