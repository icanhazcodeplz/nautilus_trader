import pytest


@pytest.fixture(autouse=True)
def cleanup_event_loop_tasks():
    """Override parent conftest fixture that requires pytest-asyncio's event_loop."""
    yield
