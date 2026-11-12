"""A one-function sanity test that the suite can collect.

This file was a scratch script (`calculate([1, 2, 3, 4, 5])` then `print`) with
no test in it, so `pytest tests/test_calc.py` exited 5 -- "no tests collected"
-- and golden_arrow, which runs every file in that directory, recorded a
permanently red file. The function is kept and asserted rather than deleted:
the cheapest honest repair is to make the file state what it means.
"""


def calculate(items):
    total = 0
    for item in items:
        total = total + item
    return total


def test_calculate_sums_its_input():
    assert calculate([1, 2, 3, 4, 5]) == 15


def test_calculate_of_nothing_is_zero():
    assert calculate([]) == 0
