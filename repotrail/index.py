"""Build a bounded Python snapshot and conservative, syntactic call candidates.

Nothing from the target repository is imported or executed. A matched AST edge
is static evidence. Python dispatch and prose semantics still need review.
"""
from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import tokenize
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import Edge, Flow, RepositoryIndex, Symbol


SKIP = {".git", ".venv", "venv", "env", "node_modules", "__pycache__",
        ".repotrail", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache"}


@dataclass(frozen=True)
class Limits:
    max_files: int = 300
    max_file_bytes: int = 200_000
    max_total_bytes: int = 4_000_000


def module_name(file: str) -> str:
    parts = list(Path(file).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _git(root: Path, *args: str) -> str | None:
    try:
        output = subprocess.check_output(
            ["git", "-c", f"safe.directory={root.as_posix()}", "-c", "core.fsmonitor=false",
             "-C", str(root), *args], stderr=subprocess.DEVNULL,
            timeout=10, text=True, encoding="utf-8", errors="replace",
        )
        return output if "-z" in args else output.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _files(root: Path, suffix: str = ".py") -> list[Path]:
    top = _git(root, "rev-parse", "--show-toplevel")
    if top and Path(top).resolve() == root:
        tracked = _git(root, "ls-files", "-z", "--cached", "--others",
                       "--exclude-standard", "--", "*" + suffix)
        if tracked is not None:
            return sorted({root / name for name in tracked.split("\0") if name})
    result = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs
                         if name not in SKIP and not (Path(folder) / name).is_symlink())
        result.extend(Path(folder) / name for name in sorted(files) if name.endswith(suffix))
    return sorted(result)


def _own_nodes(node: ast.AST):
    """Traverse a body without including nested definition bodies."""
    children = node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ast.iter_child_nodes(node)
    for child in children:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _own_nodes(child)


def _aliases(tree: ast.AST, module: str, is_package: bool) -> dict[str, str]:
    aliases: dict[str, str] = {}
    nodes = _own_nodes(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = (
                    item.name if item.asname else item.name.split(".")[0])
        if isinstance(node, ast.ImportFrom):
            base = module.split(".") if is_package else module.split(".")[:-1]
            if node.level:
                keep = len(base) - node.level + 1
                prefix = base[:max(keep, 0)]
                if node.module:
                    prefix.extend(node.module.split("."))
                source = ".".join(prefix)
            else:
                source = node.module or ""
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = f"{source}.{item.name}".strip(".")
    return aliases


def _definitions(tree: ast.AST, prefix: str = ""):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}.{node.name}".strip(".")
            yield name, node
            yield from _definitions(node, name)
        elif not isinstance(node, ast.Lambda):
            yield from _definitions(node, prefix)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def build_index(directory: str | Path, limits: Limits | None = None) -> RepositoryIndex:
    limits = limits or Limits()
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("仓库目录不存在")
    candidates = _files(root)
    candidates = [path for path in candidates
                  if not set(path.relative_to(root).parts) & SKIP]
    if len(candidates) > limits.max_files:
        raise ValueError(f"Python 文件数 {len(candidates)} 超出首版上限 {limits.max_files}")
    files: dict[str, str] = {}
    trees: dict[str, ast.Module] = {}
    symbols: dict[str, Symbol] = {}
    nodes: dict[str, ast.AST] = {}
    by_qualified: dict[str, list[str]] = {}
    warnings = []
    total = 0
    hasher = hashlib.sha256()
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            warnings.append(f"跳过符号链接或越界文件 {relative}")
            continue
        if any(parent.is_symlink() for parent in path.parents if parent != root):
            warnings.append(f"跳过含符号链接父目录的文件 {relative}")
            continue
        size = path.stat().st_size
        if size > limits.max_file_bytes:
            raise ValueError(f"文件 {relative} 超出单文件上限 {limits.max_file_bytes} 字节")
        total += size
        if total > limits.max_total_bytes:
            raise ValueError(f"源码总大小超出首版上限 {limits.max_total_bytes} 字节")
        try:
            with tokenize.open(path) as handle:
                content = handle.read()
            tree = ast.parse(content, filename=relative)
        except (SyntaxError, UnicodeError, LookupError) as exc:
            warnings.append(f"无法解析 {relative}，{type(exc).__name__}")
            continue
        files[relative] = content
        trees[relative] = tree
        hasher.update(relative.encode() + b"\0" + content.encode("utf-8") + b"\0")
        module = module_name(relative)
        lines = content.splitlines()
        for name, node in _definitions(tree):
            symbol_id = f"{relative}::{name}"
            symbol = Symbol(symbol_id, relative, module, name,
                            "class" if isinstance(node, ast.ClassDef) else "function",
                            node.lineno, node.end_lineno or node.lineno,
                            lines[node.lineno - 1].strip(), (ast.get_docstring(node) or "")[:400])
            symbols[symbol_id] = symbol
            nodes[symbol_id] = node
            qualified = f"{module}.{name}".strip(".")
            matches = by_qualified.setdefault(qualified, [])
            if symbol_id not in matches:
                matches.append(symbol_id)
    if not files:
        raise ValueError("目录中没有可解析的 Python 文件")

    def resolve(expression: str, symbol: Symbol, aliases: dict[str, str],
                shadowed: set[str]) -> str | None:
        first, _, tail = expression.partition(".")
        if first in shadowed:
            return None
        if first in {"self", "cls"} and "." in symbol.name:
            names = [f"{symbol.module}.{symbol.name.rsplit('.', 1)[0]}.{tail}"]
        elif first in aliases:
            names = [aliases[first] + (f".{tail}" if tail else "")]
        else:
            scope = symbol.name.split(".")[:-1]
            names = [".".join([symbol.module, *scope[:depth], expression]).strip(".")
                     for depth in range(len(scope), -1, -1)]
        for name in names:
            matches = by_qualified.get(name, [])
            if len(matches) == 1:
                return matches[0]
        return None

    edges = []
    file_aliases = {file: _aliases(tree, module_name(file), file.endswith("__init__.py"))
                    for file, tree in trees.items()}
    for symbol_id, symbol in symbols.items():
        node = nodes[symbol_id]
        if isinstance(node, ast.ClassDef):
            continue
        aliases = dict(file_aliases[symbol.file])
        aliases.update(_aliases(node, symbol.module, symbol.file.endswith("__init__.py")))
        args = node.args
        shadowed = {arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                    if arg.arg not in {"self", "cls"}}
        if args.vararg:
            shadowed.add(args.vararg.arg)
        if args.kwarg:
            shadowed.add(args.kwarg.arg)
        for child in _own_nodes(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                shadowed.add(child.id)
        for call in _own_nodes(node):
            if not isinstance(call, ast.Call):
                continue
            expression = _dotted(call.func)
            target = resolve(expression, symbol, aliases, shadowed) if expression else None
            if target:
                edges.append(Edge(symbol_id, target, symbol.file, call.lineno, expression))

    flows: list[Flow] = []
    config = root / "pyproject.toml"
    if config.is_file() and not config.is_symlink() and config.stat().st_size < 200_000:
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8"))
            scripts = data.get("project", {}).get("scripts", {})
            for title, target in scripts.items():
                if isinstance(target, str) and ":" in target:
                    matches = by_qualified.get(target.replace(":", "."), [])
                    if len(matches) == 1:
                        flows.append(Flow(matches[0], f"{title} 命令启动", matches[0],
                                          "来自 pyproject.toml 的 project.scripts"))
        except (tomllib.TOMLDecodeError, UnicodeError):
            warnings.append("pyproject.toml 无法解析")
    for file, tree in trees.items():
        dummy = Symbol("", file, module_name(file), "", "module", 1, 1, "")
        for statement in tree.body:
            if isinstance(statement, ast.If) and ast.unparse(statement.test) in {
                "__name__ == '__main__'", "'__main__' == __name__"
            }:
                for child in _own_nodes(statement):
                    if isinstance(child, ast.Call):
                        target = resolve(_dotted(child.func), dummy, file_aliases[file], set())
                        if target:
                            flows.append(Flow(target, "程序启动流程", target,
                                              f"来自 {file} 的 __main__ 入口"))
        for symbol_id, symbol in symbols.items():
            if symbol.file != file or symbol.kind != "function":
                continue
            node = nodes[symbol_id]
            decorators = [ast.unparse(item) for item in node.decorator_list]
            if any(".route(" in item or ".get(" in item or ".post(" in item
                   for item in decorators):
                flows.append(Flow(symbol_id, f"{symbol.name} 请求处理", symbol_id,
                                  "根据函数装饰器识别的请求入口"))
    def entry_rank(symbol: Symbol):
        leaf = symbol.name.split(".")[-1]
        preferred = {"request": 2, "create_app": 3, "run": 4, "get": 5, "post": 6}
        priority = preferred.get(leaf, 20)
        if leaf == "main":
            priority = 0 if symbol.file.endswith(("cli.py", "__main__.py", "main.py")) else 9
        if leaf == "__call__":
            priority = 1 if symbol.file.endswith(("app.py", "server.py")) else 12
        auxiliary = bool(set(Path(symbol.file).parts) & {"tests", "test", "examples", "docs"})
        return auxiliary, priority, not symbol.file.startswith("src/"), symbol.file, symbol.start

    if symbols:
        ranking = sorted((s for s in symbols.values() if s.kind == "function"),
                         key=entry_rank)
        existing = {flow.id for flow in flows}
        for symbol in ranking[:10]:
            if symbol.id in existing:
                continue
            flows.append(Flow(symbol.id, f"从 {symbol.name} 开始阅读", symbol.id,
                              "按名称与文件顺序推荐的阅读起点"))
    unique = {flow.id: flow for flow in reversed(flows)}
    flows = sorted(unique.values(), key=lambda flow: (
        0 if "project.scripts" in flow.reason else 1,
        entry_rank(symbols[flow.entry]),
    ))
    readme = ""
    for name in ["README.md", "README.rst", "README.txt"]:
        path = root / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 300_000:
            readme = path.read_text(encoding="utf-8", errors="replace")[:6000]
            hasher.update(name.encode() + b"\0" + readme.encode())
            break
    if config.is_file() and not config.is_symlink() and config.stat().st_size < 200_000:
        hasher.update(config.read_bytes())
    top = _git(root, "rev-parse", "--show-toplevel")
    revision = _git(root, "rev-parse", "HEAD") if top and Path(top).resolve() == root else None
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=normal")) if revision else False
    return RepositoryIndex(str(root), root.name, revision,
                           hasher.hexdigest(), files, symbols, edges, flows, readme, warnings, dirty)

