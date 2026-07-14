The data_proc.py module works but is missing a function that the tests expect: `group_by(rows, key)` which should group a list of dicts by a key, returning a dict mapping key values to lists of rows. 

For example: group_by([{"city":"NYC","name":"A"},{"city":"LA","name":"B"},{"city":"NYC","name":"C"}], "city") should return {"NYC": [{"city":"NYC","name":"A"},{"city":"NYC","name":"C"}], "LA": [{"city":"LA","name":"B"}]}

Add the `group_by` function to data_proc.py. Do NOT modify the test file. Then run: python -m pytest test_data_proc.py -v
