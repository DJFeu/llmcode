"""``llmcode profiles`` click CLI group (v2.10.0).

Provides subcommands for managing built-in model profiles bundled
with the wheel:

* ``llmcode profiles list``   — show what's bundled vs. installed.
* ``llmcode profiles diff``   — unified diff between bundled and user copy.
* ``llmcode profiles update`` — copy bundled → user dir with safety rails.
* ``llmcode profiles validate`` — parse profiles and check provider/parser refs.

Wired into the top-level ``llmcode`` group via ``cli/main.py``'s
``_register_subcommands()`` indirection so this module's import doesn't
block the main entry point on a bad install.

Spec: ``docs/superpowers/specs/2026-04-28-llm-code-v2-10-profiles-cli-design.md``
"""
from __future__ import annotations

import datetime as _dt
import difflib
import shutil
import sys
import tomllib
from pathlib import Path

import click

from llm_code.api.provider_registry import get_registry
from llm_code.profiles.builtins import (
    builtin_profile_dir,
    builtin_profile_path,
    list_builtin_profile_paths,
    strip_numeric_prefix,
)
from llm_code.runtime.model_profile import _profile_from_dict

__all__ = ["profiles"]


# ── Constants ─────────────────────────────────────────────────────────


def _user_profile_dir() -> Path:
    """Return ``~/.llmcode/model_profiles``. Centralised for testability."""
    return Path.home() / ".llmcode" / "model_profiles"


def _user_path_for(builtin: Path) -> Path:
    """Resolve where ``builtin`` lives in the user dir.

    The user-side filename strips the bundle's numeric prefix so users
    can refer to the profile by its slug (``glm-5.1.toml``) rather
    than the bundle's sort key (``65-glm-5.1.toml``). This matches the
    documentation in ``examples/model_profiles/65-glm-5.1.toml``:
    "copy this file to ~/.llmcode/model_profiles/glm-5.1.toml".
    """
    slug = strip_numeric_prefix(builtin.stem)
    return _user_profile_dir() / f"{slug}.toml"


def _profile_status(builtin: Path) -> tuple[str, Path]:
    """Compute the install status of a single bundled profile.

    Returns ``(status, user_path)`` where status is one of:

    * ``"installed"``  — user copy exists and is byte-identical to bundled.
    * ``"diverged"``   — user copy exists but content differs.
    * ``"missing"``    — user copy does not exist.
    """
    user_path = _user_path_for(builtin)
    if not user_path.is_file():
        return ("missing", user_path)
    try:
        builtin_bytes = builtin.read_bytes()
        user_bytes = user_path.read_bytes()
    except OSError:
        # Treat unreadable user copy as "diverged" so the caller surfaces
        # a recoverable signal rather than crashing the whole list.
        return ("diverged", user_path)
    if builtin_bytes == user_bytes:
        return ("installed", user_path)
    return ("diverged", user_path)


# ── Group ─────────────────────────────────────────────────────────────


@click.group(name="profiles")
def profiles() -> None:
    """Manage llmcode model profiles (v2.10.0).

    Built-in profiles ship inside the wheel under
    ``llm_code/_builtins/profiles/``. This group lists them, diffs
    them against your local copies in ``~/.llmcode/model_profiles/``,
    and refreshes user copies on demand.
    """


# ── list ──────────────────────────────────────────────────────────────


@profiles.command("list")
def list_command() -> None:
    """Show every bundled profile and its install status."""
    bundled = list_builtin_profile_paths()
    if not bundled:
        click.echo(
            "No built-in profiles bundled with this install (unexpected).",
            err=True,
        )
        sys.exit(1)

    version = _resolve_version()
    click.echo(f"Built-in profiles bundled with llmcode-cli {version}:")
    click.echo("")

    name_width = max(
        len(strip_numeric_prefix(p.stem)) for p in bundled
    )
    name_width = max(name_width, 16)

    for path in bundled:
        slug = strip_numeric_prefix(path.stem)
        status, _ = _profile_status(path)
        if status == "installed":
            line = f"  {slug.ljust(name_width)}  installed (matches built-in)"
        elif status == "diverged":
            line = (
                f"  {slug.ljust(name_width)}  installed (DIVERGED — run "
                f"`llmcode profiles diff {slug}`)"
            )
        else:
            line = f"  {slug.ljust(name_width)}  not installed"
        click.echo(line)

    click.echo("")
    click.echo(f"User profile dir:     {_user_profile_dir()}")
    click.echo(f"Built-in profile dir: {builtin_profile_dir()}")


# ── diff ──────────────────────────────────────────────────────────────


@profiles.command("diff")
@click.argument("name")
def diff_command(name: str) -> None:
    """Show a unified diff between the bundled profile and the user copy."""
    builtin = builtin_profile_path(name)
    if builtin is None:
        click.echo(
            f"Error: no built-in profile matches '{name}'. "
            f"Run `llmcode profiles list` to see available profiles.",
            err=True,
        )
        sys.exit(1)

    user_path = _user_path_for(builtin)
    if not user_path.is_file():
        slug = strip_numeric_prefix(builtin.stem)
        click.echo(
            f"Profile '{slug}' is not installed. "
            f"Run `llmcode profiles update {slug}` to copy it from the "
            f"built-in.",
        )
        return

    builtin_text = builtin.read_text(encoding="utf-8")
    user_text = user_path.read_text(encoding="utf-8")
    if builtin_text == user_text:
        # Empty diff = identical; print nothing so the command is
        # script-friendly. ``echo $?`` is still 0.
        return

    diff = difflib.unified_diff(
        builtin_text.splitlines(keepends=True),
        user_text.splitlines(keepends=True),
        fromfile=f"built-in/{builtin.name}",
        tofile=f"user/{user_path.name}",
        n=3,
    )
    for line in diff:
        click.echo(line, nl=False)


# ── update ────────────────────────────────────────────────────────────


@profiles.command("update")
@click.argument("name", required=False)
@click.option(
    "--all",
    "update_all",
    is_flag=True,
    default=False,
    help="Update every built-in profile.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite without prompting even when user copy diverges.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would change without writing.",
)
@click.option(
    "--no-backup",
    "no_backup",
    is_flag=True,
    default=False,
    help="Skip the .bak file (default: backup if user copy exists).",
)
@click.option(
    "--backup-suffix",
    "backup_suffix",
    default=None,
    help="Custom suffix; defaults to .bak.YYYYMMDD-HHMMSS.",
)
def update_command(
    name: str | None,
    update_all: bool,
    force: bool,
    dry_run: bool,
    no_backup: bool,
    backup_suffix: str | None,
) -> None:
    """Copy a built-in profile into ``~/.llmcode/model_profiles/``.

    Default behaviour:

    * Missing user copy → copy bundled file in.
    * Identical user copy → skip ("already up to date").
    * Diverged user copy → show summary diff and prompt ``[y/N]``;
      on confirm, back up the user copy then overwrite.

    See the option table for ``--force`` / ``--dry-run`` / ``--no-backup``
    / ``--all`` / ``--backup-suffix``.
    """
    if update_all and name:
        raise click.UsageError(
            "Pass either a profile name or --all, not both."
        )
    if not update_all and not name:
        raise click.UsageError(
            "Pass a profile name or --all. "
            "Run `llmcode profiles list` to see available profiles."
        )

    if update_all:
        targets = list_builtin_profile_paths()
        if not targets:
            click.echo("No built-in profiles bundled.", err=True)
            sys.exit(1)
    else:
        assert name is not None  # narrowed by the guard above
        target = builtin_profile_path(name)
        if target is None:
            click.echo(
                f"Error: no built-in profile matches '{name}'. "
                f"Run `llmcode profiles list` to see available profiles.",
                err=True,
            )
            sys.exit(1)
        targets = [target]

    user_dir = _user_profile_dir()
    if not dry_run:
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            click.echo(
                f"Error: cannot create user profile dir {user_dir}: {exc}",
                err=True,
            )
            sys.exit(1)

    overall_ok = True
    for builtin in targets:
        ok = _update_one(
            builtin=builtin,
            force=force,
            dry_run=dry_run,
            no_backup=no_backup,
            backup_suffix=backup_suffix,
        )
        overall_ok = overall_ok and ok

    if not overall_ok:
        sys.exit(1)


# ── validate ──────────────────────────────────────────────────────────


@profiles.command("validate")
@click.argument("name", required=False)
@click.option(
    "--builtins",
    "validate_builtins",
    is_flag=True,
    default=False,
    help="Validate every bundled built-in profile.",
)
def validate_command(name: str | None, validate_builtins: bool) -> None:
    """Validate model profile TOML files without installing them."""
    if validate_builtins and name:
        raise click.UsageError("Pass either a profile name or --builtins, not both.")

    label = "user profiles"
    if validate_builtins:
        targets = list_builtin_profile_paths()
        label = "built-in profiles"
    elif name:
        builtin = builtin_profile_path(name)
        if builtin is not None:
            targets = [builtin]
            label = "profile"
        else:
            candidate = Path(name).expanduser()
            if not candidate.suffix:
                candidate = _user_profile_dir() / f"{name}.toml"
            targets = [candidate]
            label = "profile"
    else:
        user_dir = _user_profile_dir()
        targets = sorted(user_dir.glob("*.toml")) if user_dir.is_dir() else []

    if not targets:
        click.echo(f"No {label} found.")
        return

    failures: list[str] = []
    for path in targets:
        failures.extend(_validate_profile_file(path))

    if failures:
        click.echo("Profile validation failed:", err=True)
        for failure in failures:
            click.echo(f"  {failure}", err=True)
        sys.exit(1)

    click.echo(f"Validated {len(targets)} {label}: OK")


# ── Internal helpers ──────────────────────────────────────────────────


def _validate_profile_file(path: Path) -> list[str]:
    """Return validation failures for a single profile TOML file."""
    if not path.is_file():
        return [f"{path}: file not found"]

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return [f"{path}: invalid TOML: {exc}"]
    except OSError as exc:
        return [f"{path}: cannot read: {exc}"]

    try:
        profile = _profile_from_dict(data)
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot build ModelProfile: {exc}"]

    failures: list[str] = []
    if profile.provider_type and get_registry().get(profile.provider_type) is None:
        failures.append(
            f"{path}: unknown provider_type {profile.provider_type!r}"
        )

    template = (profile.prompt_template or "").strip()
    if template:
        name = template
        if name.startswith("models/"):
            name = name[len("models/"):]
        if name.endswith(".j2"):
            name = name[: -len(".j2")]
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "engine"
            / "prompts"
            / "models"
            / f"{name}.j2"
        )
        if not prompt_path.is_file():
            failures.append(
                f"{path}: prompt template {template!r} not found"
            )

    if profile.parser_variants:
        from llm_code.tools.parser_variants import UnknownVariantError, get_variant

        for variant in profile.parser_variants:
            if ":" in variant:
                continue
            try:
                get_variant(variant)
            except UnknownVariantError:
                failures.append(
                    f"{path}: unknown parser variant {variant!r}"
                )

    return failures


def _update_one(
    *,
    builtin: Path,
    force: bool,
    dry_run: bool,
    no_backup: bool,
    backup_suffix: str | None,
) -> bool:
    """Apply the update flow to a single bundled profile.

    Returns ``True`` on success / clean skip, ``False`` on a recoverable
    error that should bubble up to a non-zero exit code.
    """
    user_path = _user_path_for(builtin)
    slug = strip_numeric_prefix(builtin.stem)
    status, _ = _profile_status(builtin)

    if status == "installed":
        click.echo(f"  {slug}: already up to date")
        return True

    if status == "missing":
        if dry_run:
            click.echo(f"  {slug}: would create {user_path}")
            return True
        try:
            shutil.copy2(builtin, user_path)
        except OSError as exc:
            click.echo(
                f"  {slug}: error writing {user_path}: {exc}",
                err=True,
            )
            return False
        click.echo(f"  {slug}: installed → {user_path}")
        return True

    # status == "diverged"
    if not force:
        # Print a short diff summary so the user can decide.
        click.echo(
            f"  {slug}: user copy at {user_path} has diverged from the "
            f"built-in:"
        )
        try:
            builtin_text = builtin.read_text(encoding="utf-8")
            user_text = user_path.read_text(encoding="utf-8")
            summary = "".join(
                difflib.unified_diff(
                    builtin_text.splitlines(keepends=True),
                    user_text.splitlines(keepends=True),
                    fromfile=f"built-in/{builtin.name}",
                    tofile=f"user/{user_path.name}",
                    n=1,
                )
            )
            click.echo(summary)
        except OSError as exc:
            click.echo(f"  {slug}: cannot read user copy: {exc}", err=True)
            return False

        if dry_run:
            click.echo(f"  {slug}: would overwrite (run without --dry-run)")
            return True

        if not click.confirm(
            f"Overwrite {user_path}?", default=False
        ):
            click.echo(f"  {slug}: skipped (user declined)")
            return True

    if dry_run:
        click.echo(f"  {slug}: would overwrite {user_path}")
        return True

    if not no_backup:
        backup_path = _backup_path(user_path, backup_suffix)
        try:
            shutil.copy2(user_path, backup_path)
        except OSError as exc:
            click.echo(
                f"  {slug}: error writing backup {backup_path}: {exc}",
                err=True,
            )
            return False
        click.echo(f"  {slug}: backed up → {backup_path}")

    try:
        shutil.copy2(builtin, user_path)
    except OSError as exc:
        click.echo(
            f"  {slug}: error overwriting {user_path}: {exc}",
            err=True,
        )
        return False

    click.echo(f"  {slug}: overwritten → {user_path}")
    return True


def _backup_path(user_path: Path, custom_suffix: str | None) -> Path:
    """Return the backup destination for a user profile copy.

    Default suffix: ``.bak.YYYYMMDD-HHMMSS`` so multiple backups inside
    the same minute don't collide. Custom suffixes are appended verbatim
    (the dot is the caller's responsibility).
    """
    if custom_suffix:
        return user_path.with_suffix(user_path.suffix + custom_suffix)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return user_path.with_suffix(user_path.suffix + f".bak.{stamp}")


def _resolve_version() -> str:
    """Best-effort version lookup.

    Mirrors :func:`llm_code.cli.main._resolve_version` so the CLI prints
    a useful version string in editable checkouts and installed wheels
    alike. Falls back to ``"unknown"`` rather than raising — the
    ``profiles list`` UX must not crash on a metadata read error.
    """
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("llmcode-cli")
    except Exception:
        pass
    try:
        import tomllib

        pyproject = (
            Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        )
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data.get("project", {}).get("version", "unknown"))
    except Exception:
        pass
    return "unknown"
