"""
pytest configuration — vLLM Latency Metrics Proxy
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: fast, no-network unit tests")
    config.addinivalue_line("markers", "integration: FastAPI integration tests (mock network)")
    config.addinivalue_line("markers", "e2e: requires running vLLM instance (set VLLM_E2E_URL)")
    config.addinivalue_line("markers", "benchmark: performance/throughput tests")
    config.addinivalue_line("markers", "regression: backward-compatibility regression tests")
