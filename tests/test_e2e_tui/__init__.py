"""End-to-end TUI scenarios driven by Textual's pilot API.

Unlike the unit tests in ``tests/test_tui/`` (which mock widgets or
import individual classes), these tests boot a minimal
:class:`llm_code.tui.app.LLMCodeTUI` instance inside
``App.run_test()`` and drive it with real keystrokes. The goal is to
catch the class of bugs that pytest-green unit tests miss: anything
that only manifests as user-visible behavior at runtime —
autocomplete drop-downs, modal scroll, focus chains, keybinding
dispatch, status-bar updates, voice flow end-to-end.

Each scenario mocks the heavy external dependencies (runtime init,
STT engine, tool registry, LLM provider) so the tests run in
milliseconds and don't require network / mic / LLM credentials, but
everything else — Textual widget mount / reactive updates / key
routing — runs for real.
"""
