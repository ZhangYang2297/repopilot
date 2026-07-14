Refactor timing.py to use a decorator pattern:
1. Create a `@timed` decorator that prints the execution time of any function it wraps (matching the existing output format: "func_name took 0.0001s")
2. Apply the @timed decorator to slow_function, fibonacci, and sleep_and_return
3. Remove the duplicated start/elapsed timing code from each function body while preserving the actual logic
4. All functions must return the same values as before

Run: python -m pytest test_timing.py -v to verify.
