"""Built-in model profiles bundled with llmcode-cli.

Every ``.toml`` next to this ``__init__.py`` is shipped inside the
wheel. The :mod:`llm_code.profiles.builtins` helper resolves on-disk
paths via :mod:`importlib.resources` so users can copy them into
``~/.llmcode/model_profiles/`` via ``llmcode profiles update``.

Keep this directory in lockstep with ``examples/model_profiles/`` —
the latter is the documentation-friendly source of truth that doesn't
ship in the wheel.
"""
