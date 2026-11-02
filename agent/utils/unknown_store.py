# -*- coding: utf-8 -*-
"""未知界面记录 + 返回路线（agent 与网页版共用，只依赖标准库）。

为什么单独一个模块：
    - 「认不出来的画面」不是普通报错：光看日志只有一行文字，必须同时留下
      截图、点过哪些位置、画面有没有变化、是从哪一行代码走进去的，才能判断
      是识别节点缺失、遮罩没处理，还是流程走错了；
    - 记录不只要给人看，还要给 agent 下次直接用：同一个画面（按 32×32 灰度
      指纹匹配）再次出现时，套用网页里保存的返回步骤，而不是每次都重新试；
    - agent 和 webapp 是两个进程，可能同时写记录 / 改返回方式，这里用同一份
      JSON + 文件锁（Windows 用 msvcrt，其他平台用 fcntl），避免互相覆盖。

目录结构（debug/ 默认被 .gitignore 忽略）：
    debug/unknown/index.json              记录列表（不含截图二进制）
    debug/unknown/snapshots/<id>/         screen.png + meta.json
    debug/unknown/REPORT.md               自动生成的复盘报告（给人和 Harness）
    debug/unknown/.lock                   进程间写锁

返回路线表（table/return_routes.json）：
    settings    点击次数 / 等待 / 变化阈值 / 匹配阈值 / 记录上限；
    primitives  动作原语：click / key / task / wait；
    default     认不出的画面先怎么点；
    routes      已知场景 + 记录过的未知画面怎么回大地图（id 为 ``unknown:<key>``）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterator

# agent 侧：agent/utils/unknown_store.py -> 项目根目录
ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SETTINGS = {
    "enabled": True,
    "maxClicks": 3,
    "clickGapSeconds": 0.7,
    "changeThreshold": 2.0,
    "matchThreshold": 3.0,
    "recordLimit": 80,
    "probeOnRecord": True,
}

# 文件缺失时的最小可用配置，保证 agent 不会因为少一张表就崩溃。
FALLBACK_PRIMITIVES: dict[str, dict] = {
    "click_bottom": {"label": "点屏幕下方（确定/继续常见位置）", "kind": "click", "x": 360, "y": 1010},
    "click_center": {"label": "点屏幕中心", "kind": "click", "x": 360, "y": 640},
    "back_key": {"label": "安卓返回键（keycode 4）", "kind": "key", "keycode": 4},
    "back_button": {"label": "点游戏右上角返回箭头", "kind": "task", "task": "BackButton_500ms"},
    "popup": {"label": "点提示弹窗「确定」", "kind": "task", "task": "PopUpWindowTip"},
    "wait": {"label": "等待 0.8 秒", "kind": "wait", "seconds": 0.8},
}

FALLBACK_DEFAULT = {
    "id": "",
    "label": "默认：下方 → 返回键 → 中心",
    "steps": ["click_bottom", "back_key", "click_center"],
    "note": "点完每一步都重新截图比对，画面一变就停。",
}

FALLBACK_ROUTES = [
    {
        "id": "popup_tip",
        "scene": "popup_tip",
        "label": "提示弹窗：点「确定」关闭",
        "steps": ["popup"],
        "symbol": "handle_interrupt_scene",
        "note": "",
    },
    {
        "id": "annual_closing",
        "scene": "annual_closing",
        "label": "年度结算：点「确定」",
        "steps": ["task:Event_HarvestFestivalAnnuaClosing"],
        "symbol": "_recover_yearly_to_bigmap",
        "note": "",
    },
    {
        "id": "travel_dialog",
        "scene": "travel_dialog",
        "label": "旅行确认：步行兜底",
        "steps": ["task:TravelDialog_ChooseSlow"],
        "symbol": "_recover_yearly_to_bigmap",
        "note": "",
    },
]

FALLBACK_PROBES = [
    "UI_MainWindows",
    "BackButton_500ms",
    "PopUpWindowTip",
    "TravelDialog",
    "FightStart",
    "FightVictory",
    "FightFail",
    "Event_HarvestFestivalPrePay",
    "Event_HarvestFestivalAnnuaClosing",
    "Event_ConfessionFail",
    "Event_MercenaryJoin",
    "Event_MercenaryBaby",
]

# 记录里允许网页改的字段；hits / 时间戳这些只有 agent 写。
EDITABLE_FIELDS = {
    "note",
    "route_id",
    "route_steps",
    "route_label",
    "route_saved",
    "reason",
    "task",
    "entry",
}
_ITEM_LIST_FIELDS = ("attempts", "trace", "probes", "route_steps")


def project_root() -> Path:
    return ROOT


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    """原子写：先写 .tmp 再替换，避免进程被杀留下半个 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def signature_diff(a_hex: str, b_hex: str) -> float:
    """两张 32×32 灰度指纹的平均绝对差（0~255），读不出来时返回 255。"""
    try:
        a = bytes.fromhex(str(a_hex))
        b = bytes.fromhex(str(b_hex))
    except (ValueError, TypeError):
        return 255.0
    if not a or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


class Store:
    """未知界面记录与返回路线的读写。线程安全 + 跨进程文件锁。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else ROOT
        self.base = self.root / "debug" / "unknown"
        self.snapshots = self.base / "snapshots"
        self.index_path = self.base / "index.json"
        self.report_path = self.base / "REPORT.md"
        self.lock_path = self.base / ".lock"
        self.routes_path = self.root / "table" / "return_routes.json"
        self._thread_lock = threading.RLock()

    # ------------------------------------------------------------ 目录 / 锁

    def _ensure_dirs(self) -> None:
        self.snapshots.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """进程内 RLock + 进程间文件锁；锁拿不到就退化成无锁，不阻塞流程。"""
        self._ensure_dirs()
        with self._thread_lock:
            handle = None
            try:
                handle = open(self.lock_path, "a+b")
                if os.path.getsize(self.lock_path) == 0:
                    handle.write(b"\0")
                    handle.flush()
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                if handle is not None:
                    handle.close()
                    handle = None
            try:
                yield
            finally:
                if handle is not None:
                    try:
                        if os.name == "nt":
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    finally:
                        handle.close()

    # ------------------------------------------------------------ 返回路线

    @staticmethod
    def _normalize_settings(raw: Any) -> dict:
        settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            for key in DEFAULT_SETTINGS:
                if key in raw and raw[key] is not None:
                    settings[key] = raw[key]
        settings["enabled"] = bool(settings["enabled"])
        settings["probeOnRecord"] = bool(settings["probeOnRecord"])
        settings["maxClicks"] = int(max(1, min(8, _num(settings["maxClicks"], 3))))
        settings["clickGapSeconds"] = round(
            max(0.2, min(5.0, _num(settings["clickGapSeconds"], 0.7))), 2
        )
        settings["changeThreshold"] = round(
            max(0.2, min(60.0, _num(settings["changeThreshold"], 2.0))), 2
        )
        settings["matchThreshold"] = round(
            max(0.5, min(60.0, _num(settings["matchThreshold"], 3.0))), 2
        )
        settings["recordLimit"] = int(max(5, min(500, _num(settings["recordLimit"], 80))))
        return settings

    @staticmethod
    def _normalize_route(entry: dict) -> dict | None:
        if not isinstance(entry, dict):
            return None
        route_id = str(entry.get("id") or "").strip()
        steps = [str(step).strip() for step in (entry.get("steps") or []) if str(step).strip()]
        if not route_id or not steps:
            return None
        return {
            "id": route_id,
            "scene": str(entry.get("scene") or "").strip(),
            "label": str(entry.get("label") or route_id).strip(),
            "steps": steps,
            "symbol": str(entry.get("symbol") or "").strip(),
            "note": str(entry.get("note") or "").strip(),
            "updated": str(entry.get("updated") or "").strip(),
        }

    def routes(self) -> dict:
        """读返回路线表；缺文件/坏行时回退到内置值，保证 agent 能继续跑。"""
        payload = _read_json(self.routes_path)
        if not isinstance(payload, dict):
            payload = {}

        primitives = {name: dict(value) for name, value in FALLBACK_PRIMITIVES.items()}
        raw_primitives = payload.get("primitives")
        if isinstance(raw_primitives, dict):
            for name, value in raw_primitives.items():
                if isinstance(value, dict):
                    primitives[str(name)] = dict(value)

        default = dict(FALLBACK_DEFAULT)
        raw_default = payload.get("default")
        if isinstance(raw_default, dict) and raw_default.get("steps"):
            default = {
                "id": str(raw_default.get("id") or ""),
                "label": str(raw_default.get("label") or FALLBACK_DEFAULT["label"]),
                "steps": [str(step).strip() for step in raw_default["steps"] if str(step).strip()],
                "note": str(raw_default.get("note") or ""),
            }
            if not default["steps"]:
                default = dict(FALLBACK_DEFAULT)

        routes: list[dict] = []
        raw_routes = payload.get("routes")
        if isinstance(raw_routes, list):
            for entry in raw_routes:
                route = self._normalize_route(entry)
                if route is not None:
                    routes.append(route)
        if not routes:
            routes = [self._normalize_route(entry) for entry in FALLBACK_ROUTES]  # type: ignore[list-item]
            routes = [route for route in routes if route]

        by_id = {route["id"]: route for route in routes}
        probes = payload.get("probeNodes")
        if not isinstance(probes, list) or not probes:
            probes = list(FALLBACK_PROBES)

        return {
            "version": int(_num(payload.get("version"), 1)),
            "updated": str(payload.get("updated") or ""),
            "note": str(payload.get("note") or ""),
            "settings": self._normalize_settings(payload.get("settings")),
            "primitives": primitives,
            "default": default,
            "routes": routes,
            "by_id": by_id,
            "probeNodes": [_text(node, 80) for node in probes if str(node).strip()][:24],
        }

    def save_route(
        self,
        route_id: str,
        steps: list[str],
        *,
        label: str = "",
        note: str = "",
        scene: str = "",
        symbol: str = "",
    ) -> dict:
        """新增/覆盖一条返回路线；网页给未知画面选返回方式时写这里。"""
        route_id = str(route_id or "").strip()
        steps = [str(step).strip() for step in (steps or []) if str(step).strip()]
        if not route_id:
            raise ValueError("缺少路线 id")
        if not steps:
            raise ValueError("返回路线至少要有一步")

        with self._locked():
            payload = _read_json(self.routes_path)
            if not isinstance(payload, dict):
                payload = {}
            raw_routes = payload.get("routes")
            routes = [
                entry
                for entry in (raw_routes if isinstance(raw_routes, list) else [])
                if isinstance(entry, dict)
            ]
            entry = {
                "id": route_id,
                "scene": scene,
                "label": label or route_id,
                "steps": steps,
                "symbol": symbol,
                "note": note,
                "updated": time.strftime("%Y-%m-%d"),
            }
            replaced = False
            for index, existing in enumerate(routes):
                if str(existing.get("id") or "").strip() == route_id:
                    routes[index] = entry
                    replaced = True
                    break
            if not replaced:
                routes.append(entry)
            payload["routes"] = routes
            payload["updated"] = time.strftime("%Y-%m-%d")
            payload.setdefault("version", 1)
            _write_json(self.routes_path, payload)
        return self._normalize_route(entry) or entry

    def remove_route(self, route_id: str) -> bool:
        route_id = str(route_id or "").strip()
        if not route_id:
            return False
        with self._locked():
            payload = _read_json(self.routes_path)
            if not isinstance(payload, dict):
                return False
            raw_routes = payload.get("routes")
            routes = raw_routes if isinstance(raw_routes, list) else []
            kept = [
                entry
                for entry in routes
                if not (isinstance(entry, dict) and str(entry.get("id") or "").strip() == route_id)
            ]
            if len(kept) == len(routes):
                return False
            payload["routes"] = kept
            payload["updated"] = time.strftime("%Y-%m-%d")
            _write_json(self.routes_path, payload)
        return True

    def route_for_key(self, key: str) -> dict | None:
        return self.routes()["by_id"].get(f"unknown:{key}")

    # ------------------------------------------------------------ 记录读写

    def _folder(self, record_id: str) -> Path:
        return self.snapshots / record_id

    def _normalize_item(self, raw: dict) -> dict:
        record_id = str(raw.get("id") or "")
        ts = _num(raw.get("ts"), 0.0) or time.time()
        last_seen_ts = _num(raw.get("last_seen_ts"), ts) or ts
        item = {
            "id": record_id,
            "key": _text(raw.get("key"), 32),
            "created_at": _text(raw.get("created_at") or _fmt_ts(ts), 40),
            "ts": round(ts, 3),
            "last_seen": _text(raw.get("last_seen") or _fmt_ts(last_seen_ts), 40),
            "last_seen_ts": round(last_seen_ts, 3),
            "hits": int(max(1, _num(raw.get("hits"), 1))),
            "signature": _text(raw.get("signature"), 4096),
            "width": int(_num(raw.get("width"), 0)),
            "height": int(_num(raw.get("height"), 0)),
            "task": _text(raw.get("task"), 120),
            "entry": _text(raw.get("entry"), 120),
            "source": _text(raw.get("source"), 200),
            "reason": _text(raw.get("reason"), 500),
            "note": _text(raw.get("note"), 1000),
            "route_id": _text(raw.get("route_id"), 80),
            "route_label": _text(raw.get("route_label"), 120),
            "route_saved": bool(raw.get("route_saved")),
            "size": int(_num(raw.get("size"), 0)),
        }
        for field in _ITEM_LIST_FIELDS:
            value = raw.get(field)
            if not isinstance(value, list):
                value = []
            item[field] = [entry for entry in value if isinstance(entry, dict)][:24]
        item["has_image"] = (self._folder(record_id) / "screen.png").exists()
        return item

    def _load_index_locked(self) -> list[dict]:
        data = _read_json(self.index_path)
        raw_items = data.get("items") if isinstance(data, dict) else data
        items: list[dict] = []
        seen: set[str] = set()
        if isinstance(raw_items, list):
            for raw in raw_items:
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                item = self._normalize_item(raw)
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                items.append(item)
        if items:
            return items
        # 索引丢了/损坏也能从磁盘把记录捞回来
        if not self.snapshots.exists():
            return []
        for meta_path in sorted(self.snapshots.glob("*/meta.json")):
            raw = _read_json(meta_path)
            if isinstance(raw, dict) and raw.get("id") and raw["id"] not in seen:
                seen.add(raw["id"])
                items.append(self._normalize_item(raw))
        return items

    def _write_index_locked(self, items: list[dict]) -> None:
        ordered = sorted(
            items, key=lambda item: item.get("last_seen_ts") or item.get("ts") or 0, reverse=True
        )
        _write_json(
            self.index_path,
            {
                "version": 1,
                "updated_at": _fmt_ts(time.time()),
                "items": ordered,
            },
        )

    def _write_meta_locked(self, item: dict) -> None:
        folder = self._folder(item["id"])
        if not folder.exists():
            return
        try:
            _write_json(folder / "meta.json", item)
        except OSError:
            pass

    def current_run(self) -> dict:
        """网页启动任务时写的 temp/current_task.json；读不到就返回空。"""
        data = _read_json(self.root / "temp" / "current_task.json")
        if not isinstance(data, dict):
            return {"name": "", "entry": ""}
        return {"name": _text(data.get("name"), 120), "entry": _text(data.get("entry"), 120)}

    def items(self, limit: int | None = None) -> list[dict]:
        with self._locked():
            items = self._load_index_locked()
        if not items:
            # 空索引时用磁盘扫描兜底（第一次使用、索引被删）
            items = self._scan_items()
        items.sort(key=lambda item: item.get("last_seen_ts") or item.get("ts") or 0, reverse=True)
        return items[: int(limit)] if limit else items

    def _scan_items(self) -> list[dict]:
        items: list[dict] = []
        if not self.snapshots.exists():
            return items
        for meta_path in sorted(self.snapshots.glob("*/meta.json")):
            raw = _read_json(meta_path)
            if isinstance(raw, dict) and raw.get("id"):
                items.append(self._normalize_item(raw))
        return items

    def get(self, record_id: str) -> dict | None:
        record_id = str(record_id or "").strip()
        if not record_id or "/" in record_id or "\\" in record_id or record_id.startswith("."):
            return None
        with self._locked():
            for item in self._load_index_locked():
                if item["id"] == record_id:
                    return item
        # 索引里没有，但目录还在（例如 index 刚被清掉）
        raw = _read_json(self._folder(record_id) / "meta.json")
        return self._normalize_item(raw) if isinstance(raw, dict) and raw.get("id") else None

    def find(self, signature: str, threshold: float = 3.0) -> dict | None:
        """按画面指纹找工作记录：差得最少且低于阈值的那条。"""
        if not signature:
            return None
        with self._locked():
            items = self._load_index_locked()
        best: dict | None = None
        best_diff = float(threshold)
        for item in items:
            diff = signature_diff(signature, item.get("signature", ""))
            if diff <= best_diff:
                best, best_diff = item, diff
        if best is not None:
            best = dict(best)
            best["match_diff"] = round(best_diff, 2)
        return best

    def record(self, signature: str, image: bytes | None = None, meta: dict | None = None) -> dict:
        """存一条未知画面；同一画面只留一条，重复遇到就累加 hits。"""
        meta = dict(meta or {})
        signature = str(signature or "")
        if not signature:
            raise ValueError("缺少画面指纹")
        try:
            key = hashlib.sha1(bytes.fromhex(signature)).hexdigest()[:16]
        except ValueError:
            key = hashlib.sha1(signature.encode("utf-8", "replace")).hexdigest()[:16]

        now = time.time()
        with self._locked():
            table = self.routes()
            settings = table["settings"]
            items = self._load_index_locked()
            # 记录被删过、但「返回方式」还留在路线表里：重新遇到同一画面时
            # 自动把学到的路线接回来，不用再选一次。
            learned = table["by_id"].get(f"unknown:{key}")

            existing = next((item for item in items if item.get("key") == key), None)
            if existing is not None:
                existing["hits"] += 1
                existing["last_seen_ts"] = round(now, 3)
                existing["last_seen"] = _fmt_ts(now)
                for field in ("task", "entry", "source", "reason"):
                    if meta.get(field):
                        existing[field] = _text(meta.get(field), 200)
                for field in ("attempts", "trace", "probes"):
                    if isinstance(meta.get(field), list) and meta[field]:
                        existing[field] = [entry for entry in meta[field] if isinstance(entry, dict)][:24]
                if learned and not existing.get("route_saved"):
                    self._adopt_route(existing, table, learned)
                folder = self._folder(existing["id"])
                if image and not (folder / "screen.png").exists():
                    self._write_screen(folder, image)
                existing["has_image"] = (folder / "screen.png").exists()
                existing["width"] = int(_num(meta.get("width"), existing["width"]))
                existing["height"] = int(_num(meta.get("height"), existing["height"]))
                self._write_meta_locked(existing)
                self._write_index_locked(items)
                self._write_report_locked(items)
                return dict(existing)

            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            record_id = f"{stamp}-{key[:6]}"
            folder = self._folder(record_id)
            folder.mkdir(parents=True, exist_ok=True)
            item = self._normalize_item(
                {
                    "id": record_id,
                    "key": key,
                    "created_at": _fmt_ts(now),
                    "ts": now,
                    "last_seen": _fmt_ts(now),
                    "last_seen_ts": now,
                    "hits": 1,
                    "signature": signature,
                    "width": int(_num(meta.get("width"), 0)),
                    "height": int(_num(meta.get("height"), 0)),
                    "task": meta.get("task"),
                    "entry": meta.get("entry"),
                    "source": meta.get("source"),
                    "reason": meta.get("reason"),
                    "note": meta.get("note"),
                    "attempts": meta.get("attempts"),
                    "trace": meta.get("trace"),
                    "probes": meta.get("probes"),
                }
            )
            if learned:
                self._adopt_route(item, table, learned)
            if image:
                self._write_screen(folder, image)
                item["size"] = len(image)
            item["has_image"] = (folder / "screen.png").exists()
            self._write_meta_locked(item)
            items.append(item)
            items = self._prune_locked(items, settings)
            self._write_index_locked(items)
            self._write_report_locked(items)
            return dict(item)

    def _adopt_route(self, item: dict, table: dict, route: dict) -> None:
        """把路线表里已保存的 unknown:<key> 路线接回记录，页面能正确回显。"""
        item["route_saved"] = True
        item["route_steps"] = [str(step) for step in route.get("steps") or []][:8]
        item["route_label"] = _text(route.get("label"), 120)
        item["route_id"] = self._preset_id_for_steps(table, item["route_steps"])

    @staticmethod
    def _preset_id_for_steps(table: dict, steps: list) -> str:
        for route in table.get("routes") or []:
            if str(route.get("id") or "").startswith("unknown:"):
                continue
            if route.get("steps") == steps:
                return str(route["id"])
        return ""

    @staticmethod
    def _write_screen(folder: Path, image: bytes) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "screen.png").write_bytes(image)
        except OSError:
            pass

    def _prune_locked(self, items: list[dict], settings: dict) -> list[dict]:
        """超出上限时先丢最旧的普通记录，有返回方式的记录尽量保留。"""
        limit = int(settings.get("recordLimit") or 80)
        if len(items) <= limit:
            return items
        ordered = sorted(
            items, key=lambda item: item.get("last_seen_ts") or item.get("ts") or 0
        )
        protected = [item for item in ordered if item.get("route_saved")]
        plain = [item for item in ordered if not item.get("route_saved")]
        victims: list[dict] = []
        excess = len(items) - limit
        while excess > 0 and plain:
            victims.append(plain.pop(0))
            excess -= 1
        # 带返回方式的也不能无限增长
        hard_limit = limit * 2
        while len(protected) + len(plain) > hard_limit and protected:
            victims.append(protected.pop(0))
            excess -= 1
        for victim in victims:
            shutil.rmtree(self._folder(victim["id"]), ignore_errors=True)
        removed = {victim["id"] for victim in victims}
        return [item for item in items if item["id"] not in removed]

    def touch(self, record_id: str, **fields: Any) -> dict | None:
        """只改允许的字段（备注 / 返回方式），不碰 agent 写的统计。"""
        with self._locked():
            items = self._load_index_locked()
            item = next((entry for entry in items if entry["id"] == record_id), None)
            if item is None:
                return None
            self._apply_fields(item, fields)
            self._write_meta_locked(item)
            self._write_index_locked(items)
            self._write_report_locked(items)
            return dict(item)

    def bump(
        self,
        record_id: str,
        *,
        attempts: list | None = None,
        reason: str = "",
        source: str = "",
    ) -> dict | None:
        with self._locked():
            items = self._load_index_locked()
            item = next((entry for entry in items if entry["id"] == record_id), None)
            if item is None:
                return None
            item["hits"] += 1
            item["last_seen_ts"] = round(time.time(), 3)
            item["last_seen"] = _fmt_ts(item["last_seen_ts"])
            if attempts:
                item["attempts"] = [entry for entry in attempts if isinstance(entry, dict)][:24]
            if reason:
                item["reason"] = _text(reason, 500)
            if source:
                item["source"] = _text(source, 200)
            self._write_meta_locked(item)
            self._write_index_locked(items)
            self._write_report_locked(items)
            return dict(item)

    @staticmethod
    def _apply_fields(item: dict, fields: dict) -> None:
        for key, value in fields.items():
            if key not in EDITABLE_FIELDS:
                continue
            if key == "note":
                item["note"] = _text(value, 1000)
            elif key == "reason":
                item["reason"] = _text(value, 500)
            elif key == "task":
                item["task"] = _text(value, 120)
            elif key == "entry":
                item["entry"] = _text(value, 120)
            elif key == "route_id":
                item["route_id"] = _text(value, 80)
            elif key == "route_label":
                item["route_label"] = _text(value, 120)
            elif key == "route_saved":
                item["route_saved"] = bool(value)
            elif key == "route_steps":
                item["route_steps"] = [
                    _text(step, 80) for step in (value or []) if str(step).strip()
                ][:8]

    def remove(self, record_id: str) -> bool:
        record_id = str(record_id or "").strip()
        if not record_id:
            return False
        with self._locked():
            items = self._load_index_locked()
            kept = [item for item in items if item["id"] != record_id]
            if len(kept) == len(items):
                return False
            shutil.rmtree(self._folder(record_id), ignore_errors=True)
            self._write_index_locked(kept)
            self._write_report_locked(kept)
        return True

    def clear(self) -> dict:
        with self._locked():
            removed = len(self._load_index_locked())
            for folder in list(self.snapshots.glob("*")) if self.snapshots.exists() else []:
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
            self._write_index_locked([])
            self._write_report_locked([])
            return {"removed": removed}

    def stats(self) -> dict:
        items = self.items()
        total_bytes = 0
        for item in items:
            folder = self._folder(item["id"])
            try:
                total_bytes += sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
            except OSError:
                pass
        return {
            "total": len(items),
            "hits": sum(int(item.get("hits") or 1) for item in items),
            "routed": sum(1 for item in items if item.get("route_saved")),
            "bytes": total_bytes,
            "mb": round(total_bytes / 1048576, 2),
            "dir": str(self.base),
            "report": str(self.report_path),
        }

    # ------------------------------------------------------------ 报告

    def report(self) -> str:
        with self._locked():
            items = self._load_index_locked()
            self._write_report_locked(items)
        try:
            return self.report_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _write_report_locked(self, items: list[dict]) -> None:
        ordered = sorted(
            items, key=lambda item: item.get("last_seen_ts") or item.get("ts") or 0, reverse=True
        )
        stats = {
            "total": len(ordered),
            "routed": sum(1 for item in ordered if item.get("route_saved")),
            "hits": sum(int(item.get("hits") or 1) for item in ordered),
        }
        lines = [
            "# 未知界面记录（自动生成）",
            "",
            "> agent 遇到认不出的画面、点了几次都没有变化时写在这里。"
            "网页「主页 → 未知界面」可以看截图、选返回方式；"
            "结构化数据见同目录 `index.json`，截图在 `snapshots/<id>/screen.png`。",
            "",
            f"- 生成时间：{_fmt_ts(time.time())}",
            f"- 统计：{stats['total']} 条记录 · 累计遇到 {stats['hits']} 次 · "
            f"{stats['routed']} 条已选返回方式",
            f"- 目录：`{self.base}`",
            "",
            "> 约定：`来源` 是出现未知画面的代码位置（文件:行 函数）；"
            "`返回方式` 是 agent 下次遇到同一画面时执行的步骤。",
            "",
        ]
        if not ordered:
            lines.append("_还没有记录。_")
            lines.append("")
        for index, item in enumerate(ordered, 1):
            trace = (item.get("trace") or [{}])[0]
            source = (
                f"{trace.get('file')}:{trace.get('line')} {trace.get('function')}"
                if trace.get("file")
                else item.get("source") or "未知"
            )
            lines.extend(
                [
                    f"## {index}. {item.get('created_at') or item.get('last_seen')} · "
                    f"{item.get('task') or item.get('entry') or '未知任务'}（命中 {item.get('hits')} 次）",
                    f"- 来源：`{source}`",
                    f"- 原因：{item.get('reason') or '—'}",
                    "- 试过：" + self._attempts_text(item.get("attempts") or []),
                    f"- 返回方式：{item.get('route_label') or '未选择（用默认）'}"
                    + (f" `{' → '.join(item.get('route_steps') or [])}`" if item.get("route_steps") else ""),
                ]
            )
            if item.get("probes"):
                hits = [
                    f"{probe.get('node')}={probe.get('score')}"
                    for probe in item["probes"]
                    if probe.get("hit") or _num(probe.get("score"), 0) >= 0.6
                ]
                if hits:
                    lines.append("- 可疑识别：" + "、".join(hits[:10]))
            if item.get("note"):
                lines.append(f"- 备注：{item['note']}")
            if item.get("has_image"):
                lines.append(f"- 截图：`snapshots/{item['id']}/screen.png`")
            lines.append("")
        try:
            self._ensure_dirs()
            self.report_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _attempts_text(attempts: list[dict]) -> str:
        if not attempts:
            return "—"
        parts = []
        for attempt in attempts:
            step = attempt.get("step") or "?"
            if attempt.get("changed") is True:
                parts.append(f"{step}（画面变了）")
            elif attempt.get("changed") is False:
                parts.append(f"{step}（没变）")
            else:
                parts.append(str(step))
        return " → ".join(parts)
