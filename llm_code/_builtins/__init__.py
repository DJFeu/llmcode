"""Wheel-bundled assets for llmcode.

Submodules under this package ship inside the installed wheel and are
discovered via :mod:`importlib.resources` rather than the on-disk
``examples/`` directory (which is intentionally excluded from the
wheel by ``[tool.hatch.build.targets.wheel] packages = ["llm_code"]``).
"""
