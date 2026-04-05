"""Python AST-based code analysis rules."""
from __future__ import annotations

import ast
from pathlib import PurePosixPath

from llm_code.analysis.rules import Rule, RuleRegistry, Violation


def check_bare_except(
    file_path: str, content: str, tree: ast.Module | None = None
) -> list[Violation]:
    """Detect bare except clauses (except without a type)."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                Violation(
                    rule_key="bare-except",
                    severity="high",
                    file_path=file_path,
                    line=node.lineno,
                    message="Bare except clause",
                )
            )
    return violations


def check_empty_except(
    file_path: str, content: str, tree: ast.Module | None = None
) -> list[Violation]:
    """Detect except blocks with only pass or ellipsis."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = node.body
            if len(body) == 1:
                stmt = body[0]
                is_pass = isinstance(stmt, ast.Pass)
                is_ellipsis = (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is ...
                )
                if is_pass or is_ellipsis:
                    violations.append(
                        Violation(
                            rule_key="empty-except",
                            severity="medium",
                            file_path=file_path,
                            line=node.lineno,
                            message="Empty except block",
                        )
                    )
    return violations


def check_unused_import(
    file_path: str, content: str, tree: ast.Module | None = None
) -> list[Violation]:
    """Detect imported names that are never referenced in the file."""
    if tree is None:
        return []
    # Skip __init__.py files (re-exports)
    if PurePosixPath(file_path).name == "__init__.py":
        return []

    # Collect imported names -> line numbers
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno

    if not imported:
        return []

    # Collect all Name references
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)

    violations: list[Violation] = []
    for name, lineno in sorted(imported.items(), key=lambda x: x[1]):
        if name not in used_names:
            violations.append(
                Violation(
                    rule_key="unused-import",
                    severity="low",
                    file_path=file_path,
                    line=lineno,
                    message=f"Unused import: {name}",
                )
            )
    return violations


def check_star_import(
    file_path: str, content: str, tree: ast.Module | None = None
) -> list[Violation]:
    """Detect wildcard imports (from x import *)."""
    if tree is None:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.names:
            if node.names[0].name == "*":
                module = node.module or ""
                violations.append(
                    Violation(
                        rule_key="star-import",
                        severity="low",
                        file_path=file_path,
                        line=node.lineno,
                        message=f"Wildcard import: from {module} import *",
                    )
                )
    return violations


def check_print_in_prod(
    file_path: str, content: str, tree: ast.Module | None = None
) -> list[Violation]:
    """Detect print() calls in non-test files."""
    if tree is None:
        return []
    # Skip test files
    parts = PurePosixPath(file_path).parts
    if any(p in ("tests", "test") for p in parts):
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            violations.append(
                Violation(
                    rule_key="print-in-prod",
                    severity="low",
                    file_path=file_path,
                    line=node.lineno,
                    message="print() in production code",
                )
            )
    return violations


def check_circular_import(files: dict[str, str]) -> list[Violation]:
    """Detect circular import chains across multiple Python files.

    Args:
        files: Mapping of relative file paths to their source content.

    Returns:
        A list of Violation for each detected cycle.
    """
    # Build module name -> set of imported module names
    graph: dict[str, set[str]] = {}
    file_modules: set[str] = set()

    for file_path, content in files.items():
        module_name = PurePosixPath(file_path).stem
        file_modules.add(module_name)
        graph.setdefault(module_name, set())

        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dep = alias.name.split(".")[0]
                    graph[module_name].add(dep)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    dep = node.module.split(".")[0]
                    graph[module_name].add(dep)

    # Filter graph to only include project-internal modules
    for mod in graph:
        graph[mod] = graph[mod] & file_modules

    # DFS cycle detection
    visited: set[str] = set()
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        on_stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                _dfs(neighbor, path)
            elif neighbor in on_stack:
                # Found a cycle: extract it
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        path.pop()
        on_stack.discard(node)

    for mod in sorted(graph):
        if mod not in visited:
            _dfs(mod, [])

    # Deduplicate cycles by their canonical form (sorted rotation)
    seen_cycles: set[tuple[str, ...]] = set()
    violations: list[Violation] = []

    for cycle in cycles:
        # Normalize: rotate so smallest element is first
        min_idx = cycle[:-1].index(min(cycle[:-1]))
        canonical = tuple(cycle[min_idx:-1]) + (cycle[min_idx],)
        if canonical in seen_cycles:
            continue
        seen_cycles.add(canonical)

        chain = " → ".join(canonical)
        # Report on the first module in the cycle
        first_mod = canonical[0]
        first_file = next(
            (fp for fp in files if PurePosixPath(fp).stem == first_mod),
            f"{first_mod}.py",
        )
        violations.append(
            Violation(
                rule_key="circular-import",
                severity="high",
                file_path=first_file,
                line=0,
                message=f"Circular import: {chain}",
            )
        )

    return violations


def register_python_rules(registry: RuleRegistry) -> None:
    """Register all Python rules with the given registry."""
    registry.register(
        Rule(
            key="bare-except",
            name="Bare except clause",
            severity="high",
            languages=("python",),
            check=check_bare_except,
        )
    )
    registry.register(
        Rule(
            key="empty-except",
            name="Empty except block",
            severity="medium",
            languages=("python",),
            check=check_empty_except,
        )
    )
    registry.register(
        Rule(
            key="unused-import",
            name="Unused import",
            severity="low",
            languages=("python",),
            check=check_unused_import,
        )
    )
    registry.register(
        Rule(
            key="star-import",
            name="Wildcard import",
            severity="low",
            languages=("python",),
            check=check_star_import,
        )
    )
    registry.register(
        Rule(
            key="print-in-prod",
            name="print() in production code",
            severity="low",
            languages=("python",),
            check=check_print_in_prod,
        )
    )
    registry.register(
        Rule(
            key="circular-import",
            name="Circular import chain",
            severity="high",
            languages=("python",),
            check=check_circular_import,  # type: ignore[arg-type]  # cross-file signature
        )
    )
