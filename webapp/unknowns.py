# -*- coding: utf-8 -*-
"""网页版「未知界面」：记录列表、截图、备注、返回方式。

真正的存储实现在 ``agent/utils/unknown_store.py``（agent 与网页共用同一份
JSON + 进程间文件锁），这里只做 HTTP 层需要的整理：去掉很长的画面指纹、
补图片地址、校验「返回方式」和备注。这样 agent 写记录、网页改路线不会
各写一份、互相覆盖。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_module = None
_store = None
STEPS_LIMIT = 8


def _load_module():
    global _module
    if _module is None:
        path = ROOT / "agent" / "utils" / "unknown_store.py"
        spec = importlib.util.spec_from_file_location("unknown_store", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        _module = module
    return _module


def store():
    global _store
    if _store is None:
        _store = _load_module().Store(ROOT)
    return _store


def routes() -> dict:
    """返回路线表（含默认路线、动作原语、各场景路线）。"""
    return store().routes()


def _source_label(item: dict) -> str:
    trace = item.get("trace") or []
    first = trace[0] if trace and isinstance(trace[0], dict) else {}
    if first.get("file"):
        return f"{first['file']}:{first.get('line') or 0} {first.get('function') or ''}".strip()
    return str(item.get("source") or "")


def _public(item: dict) -> dict:
    row = dict(item)
    row.pop("signature", None)
    record_id = row.get("id") or ""
    row["has_image"] = bool(row.get("has_image"))
    row["image_url"] = f"/api/unknowns/image?id={record_id}" if row["has_image"] else ""
    row["image_path"] = (
        f"debug/unknown/snapshots/{record_id}/screen.png" if row["has_image"] else ""
    )
    trace = row.get("trace") or []
    first = trace[0] if trace and isinstance(trace[0], dict) else {}
    row["source_path"] = str(first.get("file") or "")
    row["source_line"] = int(first.get("line") or 0)
    row["source_function"] = str(first.get("function") or "")
    row["source_label"] = _source_label(row)
    return row


def state(limit: int = 120) -> dict:
    service = store()
    table = service.routes()
    presets = [
        {
            "id": route["id"],
            "label": route["label"],
            "steps": route["steps"],
            "note": route.get("note") or "",
            "scene": route.get("scene") or "",
        }
        for route in table["routes"]
        if not route["id"].startswith("unknown:")
    ]
    items = [_public(item) for item in service.items(limit)]
    return {
        "items": items,
        "stats": service.stats(),
        "default": table["default"],
        "presets": presets,
        "settings": table["settings"],
        "probeNodes": table["probeNodes"],
    }


def detail(record_id: str) -> dict | None:
    item = store().get(record_id)
    if item is None:
        return None
    row = _public(item)
    row["route"] = None
    if item.get("key"):
        route = routes()["by_id"].get(f"unknown:{item['key']}")
        if route:
            row["route"] = route
    return row


def image_bytes(record_id: str) -> bytes | None:
    record_id = str(record_id or "").strip()
    if not record_id or "/" in record_id or "\\" in record_id:
        return None
    path = ROOT / "debug" / "unknown" / "snapshots" / record_id / "screen.png"
    try:
        return path.read_bytes()
    except OSError:
        return None


def save_route(record_id: str, route_id: str) -> dict | None:
    """给一条记录选返回方式；空 route_id 表示改回默认路线。

    选中的路线会以 ``unknown:<key>`` 写进 table/return_routes.json，
    agent 下次遇到同一画面（指纹匹配）就直接照这几步执行。
    """
    service = store()
    item = service.get(record_id)
    if item is None:
        return None
    route_id = str(route_id or "").strip()
    key = item.get("key") or ""
    dynamic_id = f"unknown:{key}" if key else ""

    if not route_id:
        if dynamic_id:
            service.remove_route(dynamic_id)
        updated = service.touch(
            record_id,
            route_id="",
            route_steps=[],
            route_label="",
            route_saved=False,
        )
        return _public(updated) if updated else None

    table = service.routes()
    preset = table["by_id"].get(route_id)
    if preset is None or route_id.startswith("unknown:"):
        raise ValueError("没有这个返回方式")
    steps = list(preset["steps"])[:STEPS_LIMIT]
    if dynamic_id:
        service.save_route(
            dynamic_id,
            steps,
            label=preset["label"],
            note=f"记录 {record_id} 选的返回方式",
            symbol="handle_unknown",
        )
    updated = service.touch(
        record_id,
        # 记录里存「选的预设 id」方便页面回显；agent 实际用的是按画面
        # 指纹命名的 unknown:<key> 路线（上面 save_route 写的那条）。
        route_id=route_id,
        route_steps=steps,
        route_label=preset["label"],
        route_saved=bool(dynamic_id),
    )
    return _public(updated) if updated else None


def note(record_id: str, text: str) -> dict | None:
    updated = store().touch(record_id, note=str(text or ""))
    return _public(updated) if updated else None


def remove(record_id: str) -> bool:
    return store().remove(record_id)


def clear() -> dict:
    service = store()
    result = service.clear()
    # 记录清掉后，按画面指纹保存的返回路线也没有记录可对应了，一起清掉，
    # 免得 table/return_routes.json 里越积越多 unknown:* 死条目。
    for route in list(service.routes().get("routes") or []):
        if str(route.get("id") or "").startswith("unknown:"):
            service.remove_route(route["id"])
    return result
