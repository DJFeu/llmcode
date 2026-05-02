"""Standalone click group for ``llmcode migrate`` subcommands.

This module intentionally does **not** wire itself into the main
``llmcode`` CLI (``llm_code/cli/main.py``). Wiring is deferred to a
later round of M8 work. For now, ``migrate_cli`` can be imported and
invoked via ``click.testing.CliRunner`` or exposed as its own entry
point.

Exit codes (per plan §Task 8.a.1 Step 2):

* ``0`` — success (changes applied or none needed).
* ``1`` — unsupported patterns found; ``--report`` path printed.
* ``2`` — runtime error (e.g. missing path, unknown rewriter).

The ``v12`` subcommand accepts positional ``PATH`` (plugin source
root), and flags:

* ``--dry-run`` — print unified diff and write nothing.
* ``--rewriters NAMES`` — comma-separated subset; defaults to all.
* ``--report FILE`` — write diagnostics JSON to ``FILE``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click


_REWRITER_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    (
        "tool_pipeline_subclass",
        "ToolExecutionPipeline subclass -> @component Component",
    ),
    (
        "prompt_mode_import",
        "legacy prompt-mode imports -> PromptBuilder(template_path=...)",
    ),
    (
        "prompt_format_call",
        "prompt.format(**kw) -> PromptBuilder(template=prompt).run(**kw)['prompt']",
    ),
    (
        "pyproject_constraint",
        "bump llmcode dep constraint to >=2.0,<3.0 across poetry/PEP 621/hatch",
    ),
)
ALL_REWRITERS: tuple[str, ...] = tuple(name for name, _ in _REWRITER_DESCRIPTIONS)


def describe_rewriters() -> list[tuple[str, str]]:
    """Return rewriter descriptions without importing optional libcst."""
    return list(_REWRITER_DESCRIPTIONS)


@click.group(name="migrate")
def migrate_cli() -> None:
    """llmcode migration codemods (standalone click group)."""


@migrate_cli.command(
    "v12",
    epilog=(
        "Rewriter catalogue:\n"
        + "\n".join(
            f"  {name} — {desc}" for name, desc in describe_rewriters()
        )
        + "\n\nSee docs/plugin_migration_guide.md for before/after examples."
    ),
)
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, resolve_path=True),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print unified diff without writing changes.",
)
@click.option(
    "--rewriters",
    "rewriter_names",
    default=None,
    metavar="NAMES",
    help=(
        "Comma-separated subset of rewriters. Default: all. "
        "Available: " + ", ".join(ALL_REWRITERS) + "."
    ),
)
@click.option(
    "--report",
    "report_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    help="Write diagnostics JSON to this path.",
)
def v12_cmd(
    path: str,
    dry_run: bool,
    rewriter_names: str | None,
    report_path: str | None,
) -> None:
    """Migrate a plugin source tree to llmcode v12.

    Runs the libcst-based codemod against every ``.py`` file and the
    ``pyproject.toml`` under PATH. See ``docs/plugin_migration_guide.md``
    for per-rewriter details.
    """
    try:
        from llm_code.migrate.v12 import runner as v12_runner

        rewriters = _parse_rewriter_list(rewriter_names)
        result = v12_runner.run(
            Path(path),
            rewriters=rewriters,
            dry_run=dry_run,
        )
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # pragma: no cover — truly unexpected
        click.echo(f"runtime error: {exc!r}", err=True)
        sys.exit(2)

    _emit_run_summary(result, dry_run=dry_run)

    if report_path is not None:
        result.diagnostics.write_json(report_path)
        click.echo(f"diagnostics report written to: {report_path}")

    if result.diagnostics.any():
        click.echo(result.diagnostics.render_text(), err=True)
        sys.exit(1)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rewriter_help_text() -> str:
    lines = []
    for name, description in describe_rewriters():
        lines.append(f"  {name} — {description}")
    lines.append("")
    lines.append("See docs/plugin_migration_guide.md for details.")
    return "\n".join(lines)


def _parse_rewriter_list(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    names = tuple(name.strip() for name in raw.split(",") if name.strip())
    if not names:
        return None
    unknown = [n for n in names if n not in ALL_REWRITERS]
    if unknown:
        raise ValueError(
            f"unknown rewriter(s): {unknown!r}; known: {list(ALL_REWRITERS)!r}"
        )
    return names


def _emit_run_summary(result: Any, *, dry_run: bool) -> None:
    click.echo(
        f"files scanned: {result.files_seen}, changed: {result.files_changed}, "
        f"dry_run={dry_run}"
    )
    if result.changes:
        if dry_run:
            click.echo(result.unified_diff())
        else:
            click.echo("rewritten files:")
            for change in result.changes:
                click.echo(
                    f"  {change.path}  [{', '.join(change.rewriters_applied)}]"
                )


# ── v2.6.0 (v16 M10) — JSON checkpoints → SQLite state DB ─────────────


@migrate_cli.group(
    name="v2.6",
    help="v2.6 migrations (e.g. JSON checkpoints → SQLite state DB).",
)
def v26_group() -> None:
    """Subgroup for v2.6 migration commands."""


try:
    from llm_code.cli.migrate_v26_state_db import state_db_command

    v26_group.add_command(state_db_command, name="state-db")
except Exception:  # pragma: no cover — defensive on partial checkouts
    pass


def main(argv: list[str] | None = None) -> Any:
    """Programmatic entry used by tests and any future CLI hookup."""
    return migrate_cli.main(args=argv, standalone_mode=False)


if __name__ == "__main__":  # pragma: no cover
    migrate_cli()
