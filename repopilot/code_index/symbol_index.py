"""Symbol extraction from source files using tree-sitter.

Extracts class/function/method definitions with docstrings and line numbers
from Python source code. Other languages return empty results gracefully.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Symbol:
    kind: str          # "class" | "function" | "method"
    name: str
    line: int          # 1-based line number
    docstring: str = ""
    parent: str = ""   # enclosing class name for methods


@dataclass
class FileSymbols:
    path: str
    imports: list[str]
    symbols: list[Symbol]


def _get_parser():
    import tree_sitter_python
    from tree_sitter import Language, Parser
    lang = Language(tree_sitter_python.language(), "python")
    parser = Parser()
    parser.set_language(lang)
    return parser


_parser = None


def _name_of(node) -> str:
    for c in node.children:
        if c.type == "identifier":
            return c.text.decode("utf-8", errors="replace")
    return ""


def _doc_of(node) -> str:
    """Extract docstring from a class/function body (block).

    Looks for the first expression_statement containing a string literal
    in the block of the definition.
    """
    body = None
    for c in node.children:
        if c.type == "block":
            body = c
            break
    if body is None:
        return ""
    for bc in body.children:
        if bc.type in ("comment", "decorator"):
            continue
        if bc.type == "decorated_definition":
            return _doc_of(bc)  # decorated def
        if bc.type == "expression_statement":
            for ec in bc.children:
                if ec.type == "string":
                    text = ec.text.decode("utf-8", errors="replace").strip()
                    for q in ('"""', "'''", '"', "'"):
                        if text.startswith(q) and text.endswith(q):
                            text = text[len(q):-len(q)]
                            break
                    return text.split("\n")[0].strip()[:120]
            return ""
        return ""  # first real statement isn't a docstring
    return ""


def _walk(node, symbols: list, imports: list, parent: str = ""):
    """Recursively walk AST collecting definitions."""
    for child in node.children:
        t = child.type
        if t == "import_statement" or t == "import_from_statement":
            text = child.text.decode("utf-8", errors="replace").strip()
            if len(text) < 80:
                imports.append(text)
            continue
        if t == "class_definition":
            name = _name_of(child)
            doc = _doc_of(child)
            line = child.start_point[0] + 1
            symbols.append(Symbol(kind="class", name=name, line=line,
                                 docstring=doc, parent=parent))
            _walk(child, symbols, imports, parent=name)  # recurse into body for methods
            continue
        if t == "function_definition":
            name = _name_of(child)
            doc = _doc_of(child)
            line = child.start_point[0] + 1
            symbols.append(Symbol(
                kind="method" if parent else "function",
                name=name, line=line, docstring=doc, parent=parent,
            ))
            continue
        if t == "decorated_definition":
            for dc in child.children:
                if dc.type == "class_definition":
                    name = _name_of(dc)
                    doc = _doc_of(dc)
                    line = dc.start_point[0] + 1
                    symbols.append(Symbol(kind="class", name=name, line=line,
                                         docstring=doc, parent=parent))
                    _walk(dc, symbols, imports, parent=name)
                    break
                if dc.type == "function_definition":
                    name = _name_of(dc)
                    doc = _doc_of(dc)
                    line = dc.start_point[0] + 1
                    symbols.append(Symbol(
                        kind="method" if parent else "function",
                        name=name, line=line, docstring=doc, parent=parent,
                    ))
                    break
            continue
        # Recurse into other containers (block, module, if_statement, etc.)
        if child.child_count > 0 and t not in ("comment", "string"):
            _walk(child, symbols, imports, parent)


def index_python(source: str, rel_path: str) -> FileSymbols:
    """Extract symbols from Python source."""
    global _parser
    if _parser is None:
        try:
            _parser = _get_parser()
        except Exception:
            return FileSymbols(path=rel_path, imports=[], symbols=[])
    tree = _parser.parse(source.encode("utf-8"))
    symbols: list = []
    imports: list = []
    _walk(tree.root_node, symbols, imports)
    return FileSymbols(path=rel_path, imports=imports, symbols=symbols)


def index_file(path: Path, repo_root: Path) -> Optional[FileSymbols]:
    """Index a single file by extension."""
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        return None
    ext = path.suffix.lower()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if ext == ".py":
        return index_python(source, rel)
    return None


def format_file_symbols(fs: FileSymbols) -> str:
    """Format FileSymbols into a compact Repo Map string."""
    if not fs.symbols and not fs.imports:
        return f"{fs.path}"
    lines = [f"{fs.path}"]
    for imp in fs.imports[:5]:
        lines.append(f"  {imp}")
    for sym in fs.symbols[:30]:
        if sym.kind == "class":
            sig = f"  class {sym.name}"
        elif sym.kind == "method":
            sig = f"    .{sym.name}()"
        else:
            sig = f"  def {sym.name}()"
        doc = f" — {sym.docstring}" if sym.docstring else ""
        lines.append(f"  L{sym.line}{sig}{doc}")
    return "\n".join(lines)
