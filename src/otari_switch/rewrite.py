"""Rewrite code so AI calls go through Otari routing policies.

Edits are exact text spans taken from the AST, so formatting and comments are kept. Running the
rewrite twice changes nothing the second time.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from pathlib import Path

from otari_switch.scan import CLIENT_CLASSES, scan

GUARD_MODULE = "otari_guard"

GUARD_SOURCE = '''"""Runtime guard added by Otari Switch.

If an open-weight model's answer fails a format check (for example invalid JSON), the same request
is retried once on the frontier model. Delete this file and the wrapper to remove the guard.
"""

import inspect
import json


def _text(resp):
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        pass
    try:
        return "".join(getattr(b, "text", "") for b in resp.content)
    except (AttributeError, TypeError):
        pass
    return getattr(resp, "output_text", "") or ""


def _ok(resp, check):
    if check != "json":
        return True
    text = _text(resp).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def checked(fn, *args, _otari_fallback=None, _otari_check="json", **kwargs):
    """Call fn(**kwargs); if the answer fails the check, retry once with model=_otari_fallback."""
    if inspect.iscoroutinefunction(fn):
        async def run():
            resp = await fn(*args, **kwargs)
            if _otari_fallback and not kwargs.get("stream") and not _ok(resp, _otari_check):
                resp = await fn(*args, **{**kwargs, "model": _otari_fallback})
            return resp
        return run()
    resp = fn(*args, **kwargs)
    if _otari_fallback and not kwargs.get("stream") and not _ok(resp, _otari_check):
        resp = fn(*args, **{**kwargs, "model": _otari_fallback})
    return resp
'''


@dataclass
class Edit:
    start: tuple[int, int]  # (line, byte col) 1-based line
    end: tuple[int, int]
    text: str


@dataclass
class FilePlan:
    path: Path
    original: str
    edits: list[Edit] = field(default_factory=list)
    add_import_os: bool = False
    add_guard_import: str | None = None

    def result(self) -> str:
        lines = self.original.splitlines(keepends=True)
        starts, acc = [], 0
        for ln in lines:
            starts.append(acc)
            acc += len(ln)

        def off(pos):
            line, bcol = pos
            ln = lines[line - 1]
            return starts[line - 1] + len(ln.encode("utf-8")[:bcol].decode("utf-8", "ignore"))

        text = self.original
        for e in sorted(self.edits, key=lambda e: off(e.start), reverse=True):
            text = text[:off(e.start)] + e.text + text[off(e.end):]
        imports = []
        if self.add_import_os:
            imports.append("import os\n")
        if self.add_guard_import:
            imports.append(self.add_guard_import + "\n")
        if imports:
            text = _insert_imports(text, "".join(imports))
        return text


def _insert_imports(text: str, block: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return block + text
    insert_line = 0
    for node in tree.body:
        is_doc = isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
            and isinstance(node.value.value, str)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if (is_doc and insert_line == 0 and node is tree.body[0]) or is_future:
            insert_line = node.end_lineno
            continue
        break
    lines = text.splitlines(keepends=True)
    return "".join(lines[:insert_line]) + block + "".join(lines[insert_line:])


def _has_import_os(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "os" and not a.asname for a in node.names):
            return True
    return False


def _client_edits(tree: ast.AST, src_lines: list[str]) -> list[Edit]:
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        cls = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if cls not in CLIENT_CLASSES:
            continue
        provider = CLIENT_CLASSES[cls]
        base_expr = ('os.environ.get("OTARI_BASE_URL", "https://api.otari.ai/api/v1")' if provider == "openai"
                     else 'os.environ.get("OTARI_ANTHROPIC_BASE_URL", "https://api.otari.ai/api")')
        key_expr = 'os.environ.get("OTARI_API_KEY")'
        kws = {k.arg: k for k in node.keywords if k.arg}
        if "base_url" in kws:
            v = kws["base_url"].value
            if "OTARI" in ast.unparse(v):
                continue  # already rewritten
            edits.append(Edit((v.lineno, v.col_offset), (v.end_lineno, v.end_col_offset), base_expr))
        if "api_key" in kws:
            v = kws["api_key"].value
            if "OTARI" not in ast.unparse(v):
                edits.append(Edit((v.lineno, v.col_offset), (v.end_lineno, v.end_col_offset), key_expr))
        missing = []
        if "base_url" not in kws:
            missing.append(f"base_url={base_expr}")
        if "api_key" not in kws:
            missing.append(f"api_key={key_expr}")
        if missing:
            # insert right after the opening parenthesis
            line, col = f.end_lineno, f.end_col_offset
            src = src_lines[line - 1].encode("utf-8")
            paren = src.index(b"(", col)
            has_args = bool(node.args or node.keywords)
            txt = ", ".join(missing) + (", " if has_args else "")
            edits.append(Edit((line, paren + 1), (line, paren + 1), txt))
    return edits


def plan(root: str | Path, policies: list[dict], frontier_by_site: dict[str, str | None],
         stage: str = "all", only_site: str | None = None) -> dict[str, FilePlan]:
    """stage: 'clients' (point clients at Otari), 'sites' (models -> policies), or 'all'."""
    root = Path(root).resolve()
    res = scan(root)
    by_id = {p["site_id"]: p for p in policies}
    files: dict[str, FilePlan] = {}

    def fp(rel: str) -> FilePlan:
        if rel not in files:
            p = root / rel
            files[rel] = FilePlan(p, p.read_text(encoding="utf-8"))
        return files[rel]

    if stage in {"clients", "all"}:
        rels = sorted({c.file for c in res.clients})
        for rel in rels:
            f = fp(rel)
            tree = ast.parse(f.original)
            edits = _client_edits(tree, f.original.splitlines())
            if edits:
                f.edits.extend(edits)
                if not _has_import_os(tree):
                    f.add_import_os = True

    if stage in {"sites", "all"}:
        for s in res.sites:
            if s.id not in by_id or (only_site and s.id != only_site):
                continue
            if s.model_env and s.model_env.startswith("OTARI_POLICY_"):
                continue  # already rewritten
            if not s.model_span:
                continue
            p = by_id[s.id]
            f = fp(s.file)
            l1, c1, l2, c2 = s.model_span
            f.edits.append(Edit((l1, c1), (l2, c2), f'os.environ.get("{p["env_var"]}", "{p["name"]}")'))
            tree = ast.parse(f.original)
            if not _has_import_os(tree):
                f.add_import_os = True
            if p.get("runtime_guard") and frontier_by_site.get(s.id):
                _guard_call(f, s, frontier_by_site[s.id], root)
    return {k: v for k, v in files.items() if v.edits}


def _guard_call(f: FilePlan, site, fallback_model: str, root: Path) -> None:
    lines = f.original.splitlines(keepends=True)
    tree = ast.parse(f.original)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == site.line and node.col_offset == site.col:
            func_src = ast.get_source_segment(f.original, node.func)
            if not func_src or "_otari_guard" in (ast.get_source_segment(f.original, node) or ""):
                return
            # wrap: client.x.create(ARGS)  ->  _otari_guard.checked(client.x.create, ARGS, _otari_fallback=...)
            func_end = (node.func.end_lineno, node.func.end_col_offset)
            src = lines[func_end[0] - 1].encode("utf-8")
            paren = src.index(b"(", func_end[1])
            has_args = bool(node.args or node.keywords)
            after_paren = src[paren + 1:].decode("utf-8", "ignore")
            sep = ("," if not after_paren.strip() else ", ") if has_args else ""
            f.edits.append(Edit((node.lineno, node.col_offset), (func_end[0], paren + 1),
                                f"_otari_guard.checked({func_src}{sep}"))
            close_line = lines[node.end_lineno - 1]
            close_bytes = close_line.encode("utf-8")
            before_close = close_bytes[:node.end_col_offset - 1].decode("utf-8", "ignore")
            kw = f"_otari_fallback={fallback_model!r}"
            if has_args and not before_close.strip():
                # closing paren on its own line: add the keyword as a new line, indented like the last argument
                last = (node.keywords or node.args)[-1]
                last_line = lines[last.lineno - 1]  # where the last argument starts
                indent = last_line[: len(last_line) - len(last_line.lstrip())]
                seg = (ast.get_source_segment(f.original, node) or "").rstrip()[:-1].rstrip()
                comma = "" if seg.endswith(",") else ","
                if comma:
                    end = (getattr(last, "end_lineno", last.lineno), getattr(last, "end_col_offset", 0))
                    f.edits.append(Edit(end, end, ","))
                f.edits.append(Edit((node.end_lineno, 0), (node.end_lineno, 0), f"{indent}{kw},\n"))
            else:
                pos = (node.end_lineno, node.end_col_offset - 1)
                seg = (ast.get_source_segment(f.original, node) or "").rstrip()[:-1].rstrip()
                f.edits.append(Edit(pos, pos, (" " if seg.endswith(",") else ", ") + kw if has_args else kw))
            pkg = (f.path.parent / "__init__.py").exists()
            f.add_guard_import = (f"from . import {GUARD_MODULE} as _otari_guard" if pkg
                                  else f"import {GUARD_MODULE} as _otari_guard")
            return


def diff(plans: dict[str, FilePlan], root: Path) -> str:
    out = []
    for rel, f in sorted(plans.items()):
        new = f.result()
        out.extend(difflib.unified_diff(f.original.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        if f.add_guard_import:
            gp = (f.path.parent / f"{GUARD_MODULE}.py")
            if not gp.exists():
                grel = gp.relative_to(root).as_posix()
                out.extend(difflib.unified_diff([], GUARD_SOURCE.splitlines(keepends=True),
                                                fromfile="/dev/null", tofile=f"b/{grel}"))
    return "".join(out)


def write(plans: dict[str, FilePlan]) -> list[Path]:
    written = []
    for f in plans.values():
        new = f.result()
        compile(new, str(f.path), "exec")  # never write code that does not parse
        f.path.write_text(new, encoding="utf-8")
        written.append(f.path)
        if f.add_guard_import:
            gp = f.path.parent / f"{GUARD_MODULE}.py"
            if not gp.exists():
                gp.write_text(GUARD_SOURCE, encoding="utf-8")
                written.append(gp)
    return written


def env_example(root: Path, policies: list[dict]) -> Path:
    p = root / ".env.example"
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    add = []
    for line in ["OTARI_API_KEY=", "OTARI_BASE_URL=https://api.otari.ai/api/v1"] + \
            [f"{pol['env_var']}={pol['name']}" for pol in policies]:
        if line.split("=")[0] + "=" not in existing:
            add.append(line)
    if add:
        block = ("\n" if existing and not existing.endswith("\n") else "") + \
            "# Added by Otari Switch\n" + "\n".join(add) + "\n"
        p.write_text(existing + block, encoding="utf-8")
    return p


def badge(root: Path, share: float) -> Path | None:
    p = root / "README.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    if "open--weight" in text:
        return None
    pct = round(share * 100)
    line = (f"\n![{pct}% open-weight via Otari](https://img.shields.io/badge/open--weight%20via%20Otari-"
            f"{pct}%25-30D158)\n")
    p.write_text(text.rstrip("\n") + "\n" + line, encoding="utf-8")
    return p
