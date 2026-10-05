import pytest

from config.default_world import build_default_world


@pytest.fixture
def world():
    return build_default_world()
