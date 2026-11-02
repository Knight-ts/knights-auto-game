# -*- coding: utf-8 -*-
"""源码定位：把处理函数名 / pipeline 节点名解析成「文件:行」，并能在编辑器里打开。

为什么要单独做：
    - 场景处理表里写的是 handler（``handle_mercenary_join`` / ``SceneGuardTask``），
      未知界面记录里写的是调用栈（``agent/.../fight_processor.py:168``）；
      排查时两头都要能一键跳到代码，不然只能全局搜索；
    - 不在页面里写死行号：文件一改行号就过期。这里用 AST 现场扫 Python，
      pipeline 节点用正则扫 JSON 的 key，缓存按文件 mtime 失效；
    - 打开编辑器优先用 ``vscode://file/...:行`` 协议（装了 VS Code / Cursor
      就能直接定位行），不行再试 code / cursor 命令行，最后用资源管理器选中。
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
CREATE_NO_WINDOW = 0x08000000
PIPELINE_DIRS = ("resource/base/pipeline",)
SCAN_DIRS = ("agent", "webapp", "tools")
SYMBOL_RE = re.compile(r"^[A-Za-z_][\w.]{0,120}$")
MAX_LOCATIONS = 8
MAX_SNIPPET_LINES = 45

_index_cache: dict = {"stamp": None, "index": {}}


# ---------------------------------------------------------------- 小工具


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _safe_target(rel: str) -> Path | None:
    """只允许项目目录里的文件，避免接口被用来读任意路径。"""
    rel = str(rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    try:
        target = (ROOT / rel).resolve()
        target.relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return None
    return target


def _clean_symbol(symbol: str) -> str:
    symbol = str(symbol or "").strip()
    symbol = symbol.split("(")[0].strip()
    return symbol


def editor_uri(path: Path | str, line: int = 1) -> str:
    """生成 ``vscode://file/...:行``；VS Code / Cursor 都认这个协议。"""
    target = Path(path).resolve()
    full = target.as_posix()
    if not re.match(r"^[A-Za-z]:/", full) and not full.startswith("/"):
        full = "/" + full
    return "vscode://file/" + quote(full, safe="/:") + f":{int(line or 1)}"


# ---------------------------------------------------------------- 索引


def _python_files() -> list[Path]:
    files: list[Path] = []
    for folder in SCAN_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or path.name.startswith("."):
                continue
            files.append(path)
    return files


def _stamp(files: list[Path]) -> tuple:
    total = 0.0
    newest = 0.0
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_mtime
        newest = max(newest, stat.st_mtime)
    return (len(files), round(total, 3), round(newest, 3))


def _walk(node: ast.AST, prefix: str, rel: str, index: dict) -> None:
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        qualname = f"{prefix}.{child.name}" if prefix else child.name
        entry = {
            "path": rel,
            "line": int(child.lineno),
            "end_line": int(getattr(child, "end_lineno", child.lineno) or child.lineno),
            "kind": "class" if isinstance(child, ast.ClassDef) else "def",
            "name": child.name,
            "qualname": qualname,
        }
        index.setdefault(child.name, []).append(entry)
        if qualname != child.name:
            index.setdefault(qualname, []).append(entry)
        _walk(child, qualname, rel, index)


def _build_index() -> dict:
    index: dict[str, list[dict]] = {}
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = _rel(path)
        _walk(tree, "", rel, index)
        index.setdefault(path.stem, []).append(
            {
                "path": rel,
                "line": 1,
                "end_line": 1,
                "kind": "module",
                "name": path.stem,
                "qualname": path.stem,
            }
        )
    return index


def _index() -> dict:
    files = _python_files()
    stamp = _stamp(files)
    if _index_cache["stamp"] != stamp:
        _index_cache["index"] = _build_index()
        _index_cache["stamp"] = stamp
    return _index_cache["index"]


def _priority(path: str) -> int:
    if path.startswith("agent/"):
        return 0
    if path.startswith("webapp/"):
        return 1
    return 2


def _lookup_symbol(symbol: str) -> list[dict]:
    index = _index()
    hits = list(index.get(symbol) or [])
    if not hits and "." in symbol:
        hits = list(index.get(symbol.split(".")[-1]) or [])
    hits.sort(key=lambda item: (_priority(item["path"]), item["line"]))
    return hits


def _find_node(node: str) -> list[dict]:
    name = str(node or "").strip()
    if not name:
        return []
    hits: list[dict] = []
    pattern = re.compile(r'^\s*"' + re.escape(name) + r'"\s*:')
    for folder in PIPELINE_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.glob("*.json")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for number, text in enumerate(lines, 1):
                if pattern.match(text):
                    hits.append(
                        {
                            "path": _rel(path),
                            "line": number,
                            "end_line": number,
                            "kind": "node",
                            "name": name,
                            "qualname": name,
                        }
                    )
                    break
    return hits


# ---------------------------------------------------------------- 代码片段


def snippet(path: str | Path, line: int = 1, end_line: int | None = None) -> dict | None:
    target = _safe_target(str(path)) if not isinstance(path, Path) else path
    if target is None or not target.is_file():
        return None
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    total = len(lines)
    start = max(1, int(line or 1) - 2)
    stop = end_line if end_line and end_line >= start else start + 20
    stop = min(total, max(start, min(int(stop), start + MAX_SNIPPET_LINES)))
    body = "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(start, stop + 1))
    return {
        "path": _rel(target),
        "line": int(line or 1),
        "start_line": start,
        "end_line": stop,
        "total_lines": total,
        "text": body,
        "uri": editor_uri(target, line),
    }


# ---------------------------------------------------------------- 对外


def lookup(symbol: str = "", node: str = "", path: str = "", line: int = 0) -> dict:
    """按函数名 / 节点名 / 直接文件+行 查源码位置，返回第一条的代码片段。"""
    locations: list[dict] = []
    if path:
        target = _safe_target(path)
        if target is None:
            return {"ok": False, "error": "路径不在项目目录里", "locations": []}
        locations.append(
            {
                "path": _rel(target),
                "line": int(line or 1),
                "end_line": 0,
                "kind": "file",
                "name": target.name,
                "qualname": target.name,
            }
        )

    symbol = _clean_symbol(symbol)
    if symbol:
        if not SYMBOL_RE.match(symbol):
            return {"ok": False, "error": "函数名不合法", "locations": []}
        locations.extend(_lookup_symbol(symbol))
    if node:
        locations.extend(_find_node(node))

    seen: set[tuple] = set()
    unique: list[dict] = []
    for location in locations:
        marker = (location["path"], location["line"], location["kind"])
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(location)

    code = None
    if unique:
        first = unique[0]
        code = snippet(first["path"], first["line"], first.get("end_line") or None)
    return {
        "ok": bool(unique),
        "locations": unique[:MAX_LOCATIONS],
        "code": code,
        "uri": (code or {}).get("uri", ""),
    }


def open_in_editor(path: str, line: int = 1) -> dict:
    """在编辑器里打开文件并定位行；返回用了哪种方式 / 失败的 uri 备用。"""
    target = _safe_target(path)
    if target is None:
        return {"ok": False, "error": "路径不在项目目录里"}
    if not target.is_file():
        return {"ok": False, "error": f"文件不存在：{_rel(target)}"}
    uri = editor_uri(target, line)
    errors: list[str] = []

    # 1) vscode:// 协议：装了 VS Code / Cursor 时最直接，还能定位到行
    try:
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(
                ["xdg-open", uri],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return {"ok": True, "opened": "vscode", "uri": uri, "path": _rel(target), "line": int(line)}
    except OSError as exc:
        errors.append(f"vscode 协议：{exc}")

    # 2) 命令行（code / code-insiders / cursor）
    for exe in ("code", "code-insiders", "cursor"):
        resolved = shutil.which(exe)
        if not resolved:
            continue
        try:
            if resolved.lower().endswith((".cmd", ".bat")):
                command = f'"{resolved}" -g "{target}:{int(line)}"'
                argv = [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/s",
                    "/c",
                    command,
                ]
            else:
                argv = [resolved, "-g", f"{target}:{int(line)}"]
            subprocess.Popen(argv, cwd=str(ROOT), creationflags=CREATE_NO_WINDOW)
            return {
                "ok": True,
                "opened": exe,
                "uri": uri,
                "path": _rel(target),
                "line": int(line),
            }
        except OSError as exc:
            errors.append(f"{exe}：{exc}")

    # 3) 兜底：资源管理器选中文件（定位不到行，但至少能打开目录）
    if os.name == "nt":
        try:
            subprocess.Popen(["explorer", "/select,", str(target)], cwd=str(ROOT))
            return {
                "ok": True,
                "opened": "explorer",
                "uri": uri,
                "path": _rel(target),
                "line": int(line),
                "note": "只选中了文件，没有定位到行；可以复制 vscode 链接打开",
            }
        except OSError as exc:
            errors.append(f"explorer：{exc}")

    return {
        "ok": False,
        "error": "；".join(errors) or "没有找到 VS Code / Cursor / 资源管理器",
        "uri": uri,
        "path": _rel(target),
        "line": int(line),
    }
