# -*- coding: utf-8 -*-
"""场景处理表：月度循环遇到的画面 → 识别条件 → 处理逻辑。

数据放在 ``table/scene_rules.json``，网页「主页 → 场景处理」直接展示；
真正的识别与处理仍在 agent / pipeline 里，这张表是给人看的总览与索引。

用法::

    from webapp import scenes
    data = scenes.load()          # {"version": 1, "scenes": [...], "updated": ...}
    items = scenes.items()        # 只取场景列表
    scenes.guard()                # 最近一次 SceneGuard 处理结果
    scenes.routes()               # table/return_routes.json：每个场景怎么回大地图
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "table" / "scene_rules.json"
GUARD_PATH = ROOT / "temp" / "scene_guard.json"

# 文件缺失时至少保证页面不空白；内容与 table/scene_rules.json 保持一致的最小子集。
FALLBACK: dict[str, Any] = {
    "version": 0,
    "updated": "",
    "source": "月度循环（Auto_YearlyTask）",
    "note": "没有找到 table/scene_rules.json，先显示内置的最小列表。",
    "scenes": [
        {
            "id": "ration_prepay",
            "name": "口粮预交",
            "group": "节日",
            "when": "识别到「口粮预交」面板，并且面板出现「粮食充足」",
            "then": "点击「确定」；没有检测到粮食充足则不点确定，暂停并提醒补粮",
            "nodes": ["Event_HarvestFestivalPrePay", "Event_RationPrepayEnough"],
            "handler": "handle_ration_prepay",
            "task": "Event_HarvestFestivalDealWith",
            "auto": True,
            "new": True,
        }
    ],
}

REQUIRED = ("id", "name", "when", "then")


def _normalize(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if any(not str(item.get(key, "")).strip() for key in REQUIRED):
        return None
    nodes = item.get("nodes")
    if isinstance(nodes, str):
        nodes = [nodes]
    if not isinstance(nodes, list):
        nodes = []
    return {
        "id": str(item["id"]).strip(),
        "name": str(item["name"]).strip(),
        "group": str(item.get("group") or "其他").strip(),
        "when": str(item["when"]).strip(),
        "then": str(item["then"]).strip(),
        "nodes": [str(node).strip() for node in nodes if str(node).strip()],
        "handler": str(item.get("handler") or "").strip(),
        "task": str(item.get("task") or "").strip(),
        "auto": bool(item.get("auto", True)),
        "new": bool(item.get("new", False)),
        "note": str(item.get("note") or "").strip(),
    }


def load() -> dict[str, Any]:
    """读取场景表；坏行会跳过，文件缺失/损坏时回退到内置列表。"""
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except FileNotFoundError:
        data = dict(FALLBACK)
    except (OSError, json.JSONDecodeError):
        data = dict(FALLBACK)

    raw_items = data.get("scenes")
    if not isinstance(raw_items, list):
        raw_items = FALLBACK["scenes"]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = _normalize(raw)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)

    # 返回路线表：每个已知场景「回大地图」走哪几步；没有单独配的场景
    # 由它自己的 handler 负责返回，页面显示「由处理器返回」。
    route_table = routes()
    routes_by_scene: dict[str, dict[str, Any]] = {}
    for route in route_table.get("routes") or []:
        scene_id = str(route.get("scene") or "").strip()
        if scene_id:
            routes_by_scene[scene_id] = route
    default_route = route_table.get("default") or {}
    for item in items:
        route = routes_by_scene.get(item["id"])
        if (
            not route
            and item.get("handler") == "handle_unknown"
            and isinstance(default_route.get("steps"), list)
            and default_route["steps"]
        ):
            # 「未知界面」场景没有单独路线，就是把 default 当成它的返回方式：
            # 这样表格里也能看到默认点哪几步，并点「实现」跳到 handle_unknown()。
            route = {
                "id": "default",
                "label": str(default_route.get("label") or "默认路线"),
                "steps": list(default_route["steps"]),
                "note": str(default_route.get("note") or ""),
                "symbol": "handle_unknown",
            }
        item["route"] = (
            {
                "id": route["id"],
                "label": route["label"],
                "steps": list(route["steps"]),
                "note": route.get("note") or "",
                "symbol": route.get("symbol") or "",
            }
            if route
            else None
        )

    groups: list[str] = []
    for item in items:
        if item["group"] not in groups:
            groups.append(item["group"])

    return {
        "version": data.get("version", 0),
        "updated": str(data.get("updated") or ""),
        "source": str(data.get("source") or "月度循环（Auto_YearlyTask）"),
        "note": str(data.get("note") or ""),
        "groups": groups,
        "count": len(items),
        "scenes": items,
        "default_route": route_table.get("default") or {},
        "routes_updated": str(route_table.get("updated") or ""),
    }


def items() -> list[dict[str, Any]]:
    return load()["scenes"]


def routes() -> dict[str, Any]:
    """返回路线表；网页版没装好 / 文件坏掉时返回空表，页面照样能显示场景。"""
    try:
        from webapp import unknowns

        return unknowns.routes()
    except Exception:  # noqa: BLE001
        return {"routes": [], "default": {}, "by_id": {}, "probeNodes": []}


def guard() -> dict[str, Any]:
    """最近一次「场景确认」（SceneGuard）处理了哪些短事件。"""
    try:
        data = json.loads(GUARD_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": [], "time": ""}
    events = data.get("events")
    return {
        "events": [str(item) for item in events] if isinstance(events, list) else [],
        "time": str(data.get("time") or ""),
    }
