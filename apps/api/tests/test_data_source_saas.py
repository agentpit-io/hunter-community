"""DATA_SOURCE_PROVIDER=saas with no key must signal HunterKeyRequired, not a bare
RuntimeError, or the isinstance check in finance_data_client silently swallows it to None
instead of letting the FastAPI hunter_key_required handler answer the caller (issue #39).

跑法(容器里):
    python -m pytest tests/test_data_source_saas.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.data_source.hunter_tools import HunterKeyRequired  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """get_data_source() caches its result in a module-level singleton; without a reset
    a provider built by an earlier test would be returned as-is, and the saas branch under
    test would never run."""
    import app.providers.data_source as data_source
    monkeypatch.setattr(data_source, "_INSTANCE", None)
    monkeypatch.setenv("DATA_SOURCE_PROVIDER", "saas")
    monkeypatch.setattr("app.services.finance_data_auth.data_token", lambda: "")
    yield
    monkeypatch.setattr(data_source, "_INSTANCE", None)


def test_saas_missing_key_raises_hunter_key_required():
    from app.providers.data_source import get_data_source

    with pytest.raises(HunterKeyRequired) as exc_info:
        get_data_source()
    assert exc_info.value.apply_url


def test_saas_missing_key_reaches_caller_not_swallowed_to_none():
    """The actual consumer: finance_data_client re-raises only HunterKeyRequired and folds
    every other exception to None. A bare RuntimeError here means a caller sees an empty
    quote instead of the key-required signal, which is the bug in #39."""
    from app.services.finance_data_client import _provider_get_quote_sync

    with pytest.raises(HunterKeyRequired):
        _provider_get_quote_sync("AAPL")
