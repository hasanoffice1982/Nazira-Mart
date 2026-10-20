import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The pages cache small lookups (categories, footer pages…); every test starts with an empty cache."""
    cache.clear()
    yield
    cache.clear()
