"""Manifest converters for ecosystem interop (v16 M5).

Each converter reads a plugin format from another tool (Claude Code,
Gemini CLI, etc.) and emits an llmcode ``manifest.toml`` text. The
output is parsed by :func:`marketplace.manifest.parse_manifest_text`
+ :func:`marketplace.validator.validate` so the converter never
produces a manifest the installer would reject.

Currently shipping:

* :mod:`claude_plugin` — Claude Code plugin packages
  (``.claude-plugin/plugin.json`` + adjacent files).
"""
