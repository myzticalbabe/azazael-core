"""AOA conftest — pytest configuration.

The test_cross_arm.py and test_benchmark_workflow.py files are standalone
integration scripts (not pytest tests). They are run directly with:
    python Desktop/AOA/test_cross_arm.py
    python Desktop/AOA/test_benchmark_workflow.py

This conftest prevents pytest from collecting them.
"""
collect_ignore = ["test_cross_arm.py", "test_benchmark_workflow.py"]
