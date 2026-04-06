"""Tree-sitter based symbol extraction -- optional dependency."""
from __future__ import annotations

import logging
from pathlib import Path

from llm_code.runtime.repo_map import ClassSymbol, FileSymbols

logger = logging.getLogger(__name__)

# Language detection by file extension
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
}

# Node types that represent classes/structs per language
_CLASS_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"class_definition"}),
    "javascript": frozenset({"class_declaration"}),
    "typescript": frozenset({"class_declaration"}),
    "tsx": frozenset({"class_declaration"}),
    "go": frozenset(),  # handled by _extract_go_type_decl
    "rust": frozenset({"struct_item"}),
    "java": frozenset({"class_declaration"}),
    "c": frozenset({"struct_specifier"}),
    "cpp": frozenset({"struct_specifier", "class_specifier"}),
    "ruby": frozenset({"class"}),
    "swift": frozenset({"class_declaration"}),
    "kotlin": frozenset({"class_declaration"}),
}

# Node types that represent functions per language
_FUNC_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition"}),
    "javascript": frozenset({"function_declaration"}),
    "typescript": frozenset({"function_declaration"}),
    "tsx": frozenset({"function_declaration"}),
    "go": frozenset({"function_declaration", "method_declaration"}),
    "rust": frozenset({"function_item"}),
    "java": frozenset({"method_declaration"}),
    "c": frozenset({"function_definition"}),
    "cpp": frozenset({"function_definition"}),
    "ruby": frozenset({"method"}),
    "swift": frozenset({"function_declaration"}),
    "kotlin": frozenset({"function_declaration"}),
}

# Node types that represent methods inside classes per language
_METHOD_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition"}),
    "javascript": frozenset({"method_definition"}),
    "typescript": frozenset({"method_definition"}),
    "tsx": frozenset({"method_definition"}),
    "go": frozenset({"method_declaration"}),
    "rust": frozenset({"function_item"}),
    "java": frozenset({"method_declaration"}),
    "c": frozenset(),
    "cpp": frozenset({"function_definition"}),
    "ruby": frozenset({"method"}),
    "swift": frozenset({"function_declaration"}),
    "kotlin": frozenset({"function_declaration"}),
}


def is_available() -> bool:
    """Check if tree-sitter language pack is installed."""
    try:
        import tree_sitter_language_pack  # noqa: F401

        return True
    except ImportError:
        return False


def parse_file(path: Path, rel_path: str) -> FileSymbols | None:
    """Parse a file with tree-sitter and extract class/function symbols.

    Returns None if language not supported or tree-sitter not available.
    """
    lang_name = _EXT_TO_LANG.get(path.suffix.lower())
    if not lang_name:
        return None

    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None

    try:
        source = path.read_bytes()
        parser = get_parser(lang_name)
        tree = parser.parse(source)
    except Exception:
        logger.debug("tree-sitter parse failed for %s", rel_path)
        return None

    classes: list[ClassSymbol] = []
    functions: list[str] = []

    _extract_symbols(tree.root_node, lang_name, classes, functions)

    return FileSymbols(
        path=rel_path,
        classes=tuple(classes),
        functions=tuple(functions),
    )


def _extract_symbols(
    node: object,
    lang: str,
    classes: list[ClassSymbol],
    functions: list[str],
) -> None:
    """Walk top-level children of *node* and collect symbols."""
    class_types = _CLASS_NODE_TYPES.get(lang, frozenset())
    func_types = _FUNC_NODE_TYPES.get(lang, frozenset())

    for child in node.children:  # type: ignore[attr-defined]
        # Handle export wrappers (JS/TS: export class ..., export function ...)
        if child.type == "export_statement":
            _extract_symbols(child, lang, classes, functions)
            continue

        if child.type in class_types:
            cls = _extract_class(child, lang)
            if cls is not None:
                classes.append(cls)
        elif child.type in func_types:
            name = _get_node_name(child)
            if name and not name.startswith("_"):
                functions.append(name)
        elif lang == "rust" and child.type == "impl_item":
            _extract_rust_impl(child, classes)
        elif lang == "go" and child.type == "type_declaration":
            _extract_go_type_decl(child, classes)


def _get_node_name(node: object) -> str | None:
    """Extract the identifier name from a node via its 'name' field child."""
    # Try common field names used by tree-sitter grammars
    for field_name in ("name", "identifier"):
        child = getattr(node, "child_by_field_name", lambda _: None)(field_name)
        if child is not None:
            text = getattr(child, "text", None)
            if text is not None:
                return text.decode("utf-8") if isinstance(text, bytes) else text
    # Fallback: look for first identifier child
    for child in getattr(node, "children", []):
        if getattr(child, "type", None) in ("identifier", "name", "type_identifier"):
            text = getattr(child, "text", None)
            if text is not None:
                return text.decode("utf-8") if isinstance(text, bytes) else text
    return None


def _extract_class(node: object, lang: str) -> ClassSymbol | None:
    """Extract a ClassSymbol from a class/struct node."""
    name = _get_node_name(node)
    if not name:
        return None

    method_types = _METHOD_NODE_TYPES.get(lang, frozenset())
    methods: list[str] = []

    _collect_methods(node, method_types, methods)

    return ClassSymbol(name=name, methods=tuple(methods))


# Node types that act as class body containers
_BODY_TYPES = frozenset({
    "block", "body", "class_body", "declaration_list",
    "field_declaration_list", "enum_body",
})


def _collect_methods(
    node: object,
    method_types: frozenset[str],
    methods: list[str],
) -> None:
    """Collect method names from a class body node."""
    for child in getattr(node, "children", []):
        child_type = getattr(child, "type", "")
        if child_type in _BODY_TYPES or child_type.endswith("_body"):
            _collect_methods(child, method_types, methods)
            continue
        if child_type in method_types:
            method_name = _get_node_name(child)
            if method_name and not method_name.startswith("_"):
                methods.append(method_name)


def _extract_rust_impl(impl_node: object, classes: list[ClassSymbol]) -> None:
    """Extract methods from a Rust impl block into a ClassSymbol."""
    name = _get_node_name(impl_node)
    if not name:
        # Try type child for impl Type { ... }
        type_child = getattr(impl_node, "child_by_field_name", lambda _: None)("type")
        if type_child is not None:
            text = getattr(type_child, "text", None)
            if text is not None:
                name = text.decode("utf-8") if isinstance(text, bytes) else text
    if not name:
        return

    method_types = frozenset({"function_item"})
    methods: list[str] = []
    _collect_methods(impl_node, method_types, methods)

    # Merge with existing class of same name
    for i, existing in enumerate(classes):
        if existing.name == name:
            merged = ClassSymbol(
                name=name,
                methods=existing.methods + tuple(methods),
            )
            classes[i] = merged
            return

    classes.append(ClassSymbol(name=name, methods=tuple(methods)))


def _extract_go_type_decl(type_decl_node: object, classes: list[ClassSymbol]) -> None:
    """Extract struct names from Go type declarations."""
    for child in getattr(type_decl_node, "children", []):
        if getattr(child, "type", None) == "type_spec":
            name = _get_node_name(child)
            if name:
                classes.append(ClassSymbol(name=name))
