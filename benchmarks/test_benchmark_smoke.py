"""Smoke benchmarks for critical hot paths."""

import pytest


def test_benchmark_dummy(benchmark):
    """Placeholder to confirm pytest-benchmark is wired."""
    benchmark(lambda: sum(range(100)))