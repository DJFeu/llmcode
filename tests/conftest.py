"""Top-level test configuration.

Exists so that ``tests`` is treated as a package root by pytest's
rootdir discovery and shared fixtures under ``tests/fixtures/`` can be
imported by absolute module path (``from tests.fixtures.runtime import
make_conv_runtime``) from every test file.
"""
