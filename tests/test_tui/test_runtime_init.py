"""Tests for the extracted RuntimeInitializer."""
from unittest.mock import MagicMock


def test_runtime_initializer_exists():
    from llm_code.tui.runtime_init import RuntimeInitializer
    app = MagicMock()
    init = RuntimeInitializer(app)
    assert init._app is app


def test_init_skips_without_config():
    from llm_code.tui.runtime_init import RuntimeInitializer
    app = MagicMock()
    app._config = None
    init = RuntimeInitializer(app)
    init.initialize()
    # Should return early without crashing


def test_initializer_stores_back_reference():
    from llm_code.tui.runtime_init import RuntimeInitializer
    app = MagicMock()
    app._config = None
    init = RuntimeInitializer(app)
    assert init._app is app
    # Calling initialize with no config should be safe
    init.initialize()
