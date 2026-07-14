The validators.py module has 4 buggy validation functions that don't match the test expectations. Fix all regex/validation logic:

1. is_valid_email: Must require exactly one @, proper domain with dot. Reject: "notanemail", "@nodomain.com", "noat.com", "missing@.com"
2. is_valid_phone: Must accept 10-digit numbers AND formatted numbers like "123-456-7890" and "(123) 456-7890". Reject letters and short strings.
3. is_valid_url: Must require http:// or https:// prefix. Reject "example.com" without prefix.
4. is_valid_password: Must be at least 8 chars AND contain at least: one uppercase, one lowercase, one digit, one special character (!@#$%^&* etc.).

Run python -m pytest test_validators.py -v to verify all tests pass.
