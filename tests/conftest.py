import pytest

from edgebot.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
