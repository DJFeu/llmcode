"""``llmcode memory`` click CLI group (v12 M7 Task 7.9).

Defines the ``memory`` group with a single ``migrate`` subcommand. The
group is **not wired** into the main llmcode entry point yet; that hook
lands in a later milestone once the rest of M7 (Components + default
pipeline) is in place.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

from pathlib import Path

import click

from llm_code.memory.migrate import migrate_index

__all__ = ["memory"]


@click.group(name="memory")
def memory() -> None:
    """v12 memory subsystem utilities."""


@memory.command("migrate")
@click.option(
    "--from",
    "src_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to the legacy HIDA index to migrate.",
)
@click.option(
    "--to",
    "dst_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Destination path for the v12 index.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute counts and warnings without writing the destination.",
)
def migrate_command(
    src_path: Path,
    dst_path: Path,
    dry_run: bool,
) -> None:
    """Migrate a v10/v11 HIDA index to the v12 schema."""
    try:
        report = migrate_index(src_path, dst_path, dry_run=dry_run)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"entries_read={report.entries_read} "
        f"entries_written={report.entries_written} "
        f"schema_from={report.schema_from} "
        f"schema_to={report.schema_to} "
        f"duration_s={report.duration_s:.4f}",
    )
    if report.warnings:
        click.echo(f"warnings ({len(report.warnings)}):", err=True)
        for w in report.warnings:
            click.echo(f"  - {w}", err=True)
    if dry_run:
        click.echo("(dry-run — no files written)")
