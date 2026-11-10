"""Find every OpenAI and Anthropic SDK call in a Python repo. Reads code; never runs it."""

from __future__ import annotations

import ast
import fnmatch
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from otari_switch.models import CallSite, ClientSite

SKIP_DIRS = {
    ".git", ".hg", ".svn", "venv", ".venv", "env", ".env", "node_modules", "site-packages",
    "__pycache__", "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".otari-switch",
    ".ruff_cache", ".idea", ".vscode", "eggs", ".eggs",
}

CLIENT_CLASSES = {
    "OpenAI": "openai", "AsyncOpenAI": "openai", "AzureOpenAI": "openai", "AsyncAzureOpenAI": "openai",
    "Anthropic": "anthropic", "AsyncAnthropic": "anthropic",
}

# attribute-chain suffix -> (endpoint, provider or None for "depends on client")
CALL_SUFFIXES = [
    (("chat", "completions", "create"), "chat", "openai"),
    (("beta", "chat", "completions", "parse"), "chat", "openai"),
    (("chat", "completions", "parse"), "chat", "openai"),
    (("responses", "create"), "responses", "openai"),
    (("responses", "parse"), "responses", "openai"),
    (("messages", "create"), "messages", "anthropic"),
    (("beta", "messages", "create"), "messages", "anthropic"),
    (("embeddings", "create"), "embeddings", "openai"),
    (("images", "generate"), "images", "openai"),
    (("audio", "transcriptions", "create"), "audio", "openai"),
]

EVALUABLE = {"chat", "responses", "messages"}


@dataclass
class ScanResult:
    root: str
    sites: list[CallSite] = field(default_factory=list)
    clients: list[ClientSite] = field(default_factory=list)
    files_scanned: int = 0
    parse_errors: list[str] = field(default_factory=list)


def _gitignore_patterns(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    pats = []
    for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("!"):
            pats.append(line.rstrip("/"))
    return pats


def iter_py_files(root: Path):
    pats = _gitignore_patterns(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
            and not any(fnmatch.fnmatch(d, p) or fnmatch.fnmatch(os.path.normpath(os.path.join(rel_dir, d)), p)
                        for p in pats)
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                rel = os.path.normpath(os.path.join(rel_dir, fn))
                if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fn, p) for p in pats):
                    continue
                yield root / rel


def _chain(node: ast.AST) -> list[str]:
    """a.b.c.d -> ['a','b','c','d']; returns [] when the base is not a simple name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append("<call>")
    elif isinstance(node, ast.Subscript):
        parts.append("<item>")
    else:
        parts.append("<expr>")
    return list(reversed(parts))


class _FileVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, client_names: set[str]):
        self.rel = rel
        self.client_names = client_names  # repo-wide variable names bound to SDK clients
        self.imports_sdk: set[str] = set()  # 'openai' / 'anthropic'
        self.module_alias: dict[str, str] = {}  # local name -> sdk module
        self.class_alias: dict[str, str] = {}  # local name -> CLIENT_CLASSES key
        self.constants: dict[str, str] = {}
        self.stack: list[str] = []
        self.sites: list[CallSite] = []
        self.clients: list[ClientSite] = []
        self.assigned_clients: dict[str, str] = {}  # var -> provider
        self.per_func_index: dict[str, int] = defaultdict(int)

    # imports
    def visit_Import(self, node):
        for a in node.names:
            top = a.name.split(".")[0]
            if top in {"openai", "anthropic"}:
                self.imports_sdk.add(top)
                self.module_alias[a.asname or top] = top
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = (node.module or "").split(".")[0]
        if mod in {"openai", "anthropic"}:
            self.imports_sdk.add(mod)
            for a in node.names:
                if a.name in CLIENT_CLASSES:
                    self.class_alias[a.asname or a.name] = a.name
        self.generic_visit(node)

    # scopes
    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Assign(self, node):
        if not self.stack and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.constants[t.id] = node.value.value
        if isinstance(node.value, ast.Call):
            prov = self._client_ctor(node.value)
            if prov:
                for t in node.targets:
                    name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
                    if name:
                        self.assigned_clients[name] = prov
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if (not self.stack and isinstance(node.target, ast.Name) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            self.constants[node.target.id] = node.value.value
        self.generic_visit(node)

    def _client_ctor(self, call: ast.Call) -> str | None:
        f = call.func
        cls = None
        if isinstance(f, ast.Name) and f.id in self.class_alias:
            cls = self.class_alias[f.id]
        elif isinstance(f, ast.Attribute) and f.attr in CLIENT_CLASSES:
            ch = _chain(f)
            if ch and ch[0] in self.module_alias:
                cls = f.attr
        return CLIENT_CLASSES.get(cls) if cls else None

    def visit_Call(self, node: ast.Call):
        prov = self._client_ctor(node)
        if prov:
            kws = {k.arg for k in node.keywords if k.arg}
            cls = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            self.clients.append(ClientSite(
                file=self.rel, line=node.lineno, col=node.col_offset, end_line=node.end_lineno,
                end_col=node.end_col_offset, provider=prov, cls=self.class_alias.get(cls, cls),
                has_base_url="base_url" in kws, has_api_key="api_key" in kws))
        elif _is_guard_call(node):
            # a call Otari Switch already wrapped: _otari_guard.checked(client.x.create, ..., model=...)
            self._maybe_site(node, node.args[0])
        elif isinstance(node.func, ast.Attribute):
            self._maybe_site(node)
        self.generic_visit(node)

    def _maybe_site(self, node: ast.Call, func: ast.AST | None = None):
        ch = _chain(func if func is not None else node.func)
        for suffix, endpoint, provider in CALL_SUFFIXES:
            n = len(suffix)
            if len(ch) > n and tuple(ch[-n:]) == suffix:
                base = ch[0]
                receiver = ch[-n - 1]
                plausible = (
                    bool(self.imports_sdk)
                    or base in self.client_names or receiver in self.client_names
                    or "client" in receiver.lower() or "llm" in receiver.lower()
                )
                if not plausible:
                    return
                if receiver in self.assigned_clients:
                    provider = self.assigned_clients[receiver]
                elif base in self.module_alias:
                    provider = self.module_alias[base]
                self._add_site(node, endpoint, provider)
                return

    def _add_site(self, node: ast.Call, endpoint: str, provider: str):
        func = ".".join(self.stack) if self.stack else "<module>"
        idx = self.per_func_index[func]
        self.per_func_index[func] += 1
        site_id = f"{self.rel}:{func}:{idx}"
        kw = {k.arg: k.value for k in node.keywords if k.arg}

        model, source, span, env = None, "missing", None, None
        mv = kw.get("model")
        if mv is not None:
            span = [mv.lineno, mv.col_offset, mv.end_lineno, mv.end_col_offset]
            if isinstance(mv, ast.Constant) and isinstance(mv.value, str):
                model, source = mv.value, "literal"
            elif isinstance(mv, ast.Name) and mv.id in self.constants:
                model, source = self.constants[mv.id], "constant"
            elif isinstance(mv, ast.Call):
                env, default = _env_lookup(mv)
                if env:
                    source, model = "env", default
                else:
                    source = "dynamic"
            elif isinstance(mv, ast.Subscript) and _chain(mv.value)[-2:] == ["os", "environ"]:
                sl = mv.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    env, source = sl.value, "env"
                else:
                    source = "dynamic"
            else:
                source = "dynamic"

        flags = []
        rf = kw.get("response_format")
        if rf is not None:
            flags.append("json")
            if _has_json_schema(rf) or (isinstance(rf, ast.Name) and not isinstance(rf, ast.Constant)):
                flags.append("schema")
        txt = kw.get("text")
        if endpoint == "responses" and txt is not None and "format" in ast.unparse(txt):
            flags.append("json")
        if "tools" in kw or "functions" in kw:
            flags.append("tools")
        st = kw.get("stream")
        if isinstance(st, ast.Constant) and st.value is True:
            flags.append("stream")

        evaluable = endpoint in EVALUABLE
        note = "" if evaluable else f"{endpoint} calls are listed but not evaluated yet"
        if evaluable and source == "dynamic":
            note = "Model is chosen at run time; the rewrite will wrap it with an env override."

        self.sites.append(CallSite(
            id=site_id, file=self.rel, line=node.lineno, col=node.col_offset,
            end_line=node.end_lineno, end_col=node.end_col_offset, function=func,
            provider=provider, endpoint=endpoint, model=model, model_source=source,
            model_span=span, model_env=env, flags=sorted(set(flags)), evaluable=evaluable, note=note))


def _is_guard_call(node: ast.Call) -> bool:
    f = node.func
    named = (isinstance(f, ast.Attribute) and f.attr == "checked" and _chain(f)[:1] == ["_otari_guard"]) or         (isinstance(f, ast.Name) and f.id == "checked")
    return named and bool(node.args) and isinstance(node.args[0], ast.Attribute)


def _env_lookup(call: ast.Call) -> tuple[str | None, str | None]:
    ch = _chain(call.func)
    if ch[-2:] == ["os", "getenv"] or ch[-1:] == ["getenv"] or ch[-3:] == ["os", "environ", "get"]:
        args = call.args
        name = args[0].value if args and isinstance(args[0], ast.Constant) else None
        default = None
        if len(args) > 1 and isinstance(args[1], ast.Constant):
            default = args[1].value
        for k in call.keywords:
            if k.arg == "default" and isinstance(k.value, ast.Constant):
                default = k.value.value
        return (name if isinstance(name, str) else None), (default if isinstance(default, str) else None)
    return None, None


def _has_json_schema(node: ast.AST) -> bool:
    try:
        return "json_schema" in ast.unparse(node)
    except Exception:  # noqa: BLE001
        return False


def _collect_client_names(trees: dict[str, ast.AST]) -> set[str]:
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                f = node.value.func
                cls = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                if cls in CLIENT_CLASSES:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
                        elif isinstance(t, ast.Attribute):
                            names.add(t.attr)
    return names


def scan(root: str | os.PathLike) -> ScanResult:
    root = Path(root).resolve()
    res = ScanResult(root=str(root))
    trees: dict[str, ast.AST] = {}
    for path in iter_py_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            src = path.read_text(encoding="utf-8")
            trees[rel] = ast.parse(src, filename=rel)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            res.parse_errors.append(f"{rel}: {e}")
        res.files_scanned += 1
    names = _collect_client_names(trees)
    for rel in sorted(trees):
        v = _FileVisitor(rel, names)
        v.visit(trees[rel])
        res.sites.extend(v.sites)
        res.clients.extend(v.clients)
    return res
