"""PromptBuilder — Jinja2-backed prompt template renderer.

v12 M1.1. Replaces ad-hoc ``str.format`` prompt assembly in
``runtime/prompt*.py`` with declarative Jinja2 templates. See
``docs/superpowers/plans/2026-04-21-llm-code-v12-prompt-template.md``.

Borrowed shape: ``haystack/components/builders/prompt_builder.py``.
Not borrowed: Component graph socket declarations (that lands in M2) — a
PromptAssemblerComponent thin wrapper will subclass / compose this class
when the Component decorator exists.

Design:

- Accepts exactly one of ``template`` (inline string) or ``template_path``
  (resolved through the Jinja2 ``FileSystemLoader`` rooted at
  ``templates_dir``).
- Uses ``StrictUndefined`` — referencing an undefined variable at render
  time raises ``jinja2.UndefinedError`` rather than silently rendering
  empty. This catches template/state mismatches in CI.
- ``autoescape=False`` because prompts are plain text, not HTML. User
  content that might contain ``{{`` is expected to use the ``| e``
  filter inside templates; documented in the template-author guide.
- ``keep_trailing_newline``/``trim_blocks``/``lstrip_blocks`` tuned so
  Jinja2 block tags do not introduce surprise whitespace.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    meta,
)

_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "prompts"


class PromptBuilder:
    """Render a prompt from a Jinja2 template and runtime inputs."""

    def __init__(
        self,
        template: str | None = None,
        *,
        template_path: str | None = None,
        required_variables: Iterable[str] = (),
        templates_dir: Path | None = None,
    ) -> None:
        if (template is None) == (template_path is None):
            raise ValueError(
                "PromptBuilder requires exactly one of `template` "
                "(inline string) or `template_path` (filesystem-relative)"
            )

        self._templates_dir = Path(templates_dir or _DEFAULT_TEMPLATES_DIR)
        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        if template_path is not None:
            source, _, _ = self._env.loader.get_source(  # type: ignore[union-attr]
                self._env, template_path
            )
            self._source = source
            self._template_name = template_path
            self._template = self._env.get_template(template_path)
        else:
            assert template is not None
            self._source = template
            self._template_name = None
            self._template = self._env.from_string(template)

        self._required = frozenset(required_variables)

    @property
    def templates_dir(self) -> Path:
        return self._templates_dir

    @property
    def template_name(self) -> str | None:
        return self._template_name

    @property
    def required_variables(self) -> frozenset[str]:
        return self._required

    @property
    def declared_variables(self) -> frozenset[str]:
        """Return the set of variable names referenced by the template
        (via Jinja2 AST analysis). Useful for static checks + docs."""
        ast = self._env.parse(self._source)
        return frozenset(meta.find_undeclared_variables(ast))

    def run(self, **inputs: Any) -> dict[str, str]:
        """Render the template with ``inputs``.

        Returns ``{"prompt": str}`` — dict shape mirrors Haystack
        Component output convention so this class can be wrapped as a
        Component in M2 without signature churn.

        Raises:
            ValueError: if any declared ``required_variables`` key is
                missing from ``inputs``.
            jinja2.UndefinedError: if the template references a variable
                not supplied in ``inputs`` (StrictUndefined).
        """
        missing = self._required - set(inputs)
        if missing:
            raise ValueError(
                f"PromptBuilder missing required variables: {sorted(missing)}"
            )
        rendered = self._template.render(**inputs)
        return {"prompt": rendered}


def render_template_file(
    name: str, /, *, templates_dir: Path | None = None, **inputs: Any
) -> str:
    """Convenience: load ``name`` from ``templates_dir`` and render.

    Equivalent to ``PromptBuilder(template_path=name, templates_dir=dir).run(**inputs)["prompt"]``.
    """
    builder = PromptBuilder(template_path=name, templates_dir=templates_dir)
    return builder.run(**inputs)["prompt"]
