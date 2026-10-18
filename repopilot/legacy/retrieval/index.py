# 安全遍历与 AST 提取
from pathlib import Path
import ast
import os
from repopilot.legacy.retrieval.schema import Symbol, RepositoryIndex, SymbolKind
from repopilot.legacy.tools.search import IGNORED_DIR_NAMES


AST_SYMBOL_TYPES = {ast.FunctionDef: SymbolKind.FUNCTION,
                   ast.ClassDef: SymbolKind.CLASS,
                   ast.AsyncFunctionDef: SymbolKind.ASYNC_FUNCTION}


def extract_symbols(tree: ast.Module, relative_path: str) -> list[Symbol]:
    symbols = []
    def visit(nodes: list[ast.stmt], parents: list[str]):
        for node in nodes:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.ClassDef) or isinstance(node, ast.AsyncFunctionDef):
                start_line = node.lineno
                if node.decorator_list:
                    start_line = min(start_line, *(d.lineno for d in node.decorator_list))
                symbols.append(Symbol(
                    path=Path(relative_path).as_posix(),
                    qualified_name=".".join(parents + [node.name]),
                    kind=AST_SYMBOL_TYPES[type(node)],
                    start_line=start_line,
                    end_line=node.end_lineno
                ))
                visit(node.body, parents + [node.name])
    visit(tree.body, [])
    return symbols


def build_index(workspace: Path) -> RepositoryIndex:
    # 收集.py文件
    py_files = []
    root = workspace.resolve()
    for current_dir, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIR_NAMES)  # 防止进入忽略目录
        for filename in sorted(files):
            if filename.endswith(".py"):
                try:
                    (Path(current_dir) / filename).resolve().relative_to(root)  # 检查是否在工作空间内
                except ValueError:
                    continue
                py_files.append(Path(current_dir) / filename)

    # 读取
    symbols = []
    warnings = []
    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.append(f"UnicodeDecodeError: {py_file.relative_to(root)}")
            continue
        except OSError:
            warnings.append(f"OSError: {py_file.relative_to(root)}")
            continue
        try:
            module = ast.parse(content)
        except SyntaxError:
            warnings.append(f"SyntaxError: {py_file.relative_to(root)}")
            continue
        symbols.extend(extract_symbols(module, str(py_file.relative_to(root))))

    return RepositoryIndex(
        symbols=symbols,
        files=[py_file.relative_to(root).as_posix() for py_file in py_files],
        warnings=warnings,
        truncated=False
    )
