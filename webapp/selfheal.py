# -*- coding: utf-8 -*-
"""问题自纠：把报错瞬间的截图、任务信息和日志尾巴存下来，供用户筛选、供 Harness 复盘。

为什么单独一个模块：
    - 报错往往只闪一下，日志里只有一行文字；截图能直接看到当时游戏停在哪；
    - 截图不能无限堆：用户要能判断“这张有价值 / 这张没用”，保留或删除；
    - DeepSeek Harness 需要机器可读的入口：index.json（结构化）+ REPORT.md（人/模型都好读）。

目录结构（全部在 debug/errors/ 下，随 debug/ 一起被 .gitignore 忽略）：
    index.json              所有记录（状态、错误、备注、截图相对路径）
    REPORT.md               自动生成的复盘报告（只写“待判定 + 保留”，已删除的单独列出）
    snapshots/<id>/         正常记录：screen.png + meta.json + logs.txt
    trash/<id>/             用户删除的记录（进回收站，可恢复；超过 trashDays 自动清掉）

状态：
    pending  待判定（默认）
    kept     用户确认“有价值，留着修”
    deleted  用户认为没价值（移入 trash/）

自动规则：
    - 只在 debug/errors 里写文件，不碰游戏数据；
    - 同一类报错在 dedupeSeconds 内只存一张（避免疯狂刷屏）；
    - pending 超过 pendingDays 自动进回收站，回收站超过 trashDays 自动彻底删除
      （两者都设 0 表示不自动清理，只按条数/体积上限兜底）；
    - 条目数 / 体积超限时，优先丢最旧的 pending，再丢最旧的 deleted，**永远不动 kept**。
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

STATUSES = ("pending", "kept", "deleted")
SOURCE_LABELS = {
    "task": "任务",
    "agent": "Agent",
    "api": "接口",
    "manual": "手动",
    "combo": "组合",
    "frontend": "页面",
}

DEFAULTS = {
    "enabled": True,
    "autoCapture": True,
    "maxItems": 200,
    "maxMB": 400,
    "pendingDays": 7,
    "trashDays": 3,
    "dedupeSeconds": 15,
}

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_size(data: bytes) -> tuple[int, int]:
    """不引入 cv2，直接从 PNG 的 IHDR 读宽高。"""
    if len(data) >= 24 and data[:8] == _PNG_SIG:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return 0, 0


def _text(value, limit: int) -> str:
    text = str(value or "")
    return text[:limit]


class ErrorStore:
    """报错现场的存取与整理。线程安全，不依赖 MaaFramework / HTTP。"""

    def __init__(self, root: Path, config_provider=None) -> None:
        self.root = Path(root)
        self.base = self.root / "debug" / "errors"
        self.snapshots = self.base / "snapshots"
        self.trash = self.base / "trash"
        self.index_path = self.base / "index.json"
        self.report_path = self.base / "REPORT.md"
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._dedupe: dict[str, float] = {}
        self._config_provider = config_provider
        self._ensure_dirs()
        with self._lock:
            self._load_locked()
            if not self.report_path.exists():
                self._write_report_locked()

    # ------------------------------------------------------------ 配置

    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        if self._config_provider is not None:
            try:
                live = self._config_provider() or {}
                section = live.get("selfheal") or {}
                if isinstance(section, dict):
                    for key in DEFAULTS:
                        if key in section and section[key] is not None:
                            cfg[key] = section[key]
            except Exception:  # noqa: BLE001
                pass
        cfg["enabled"] = bool(cfg.get("enabled", True))
        cfg["autoCapture"] = bool(cfg.get("autoCapture", True))
        cfg["maxItems"] = max(10, int(_number(cfg.get("maxItems"), 200)))
        cfg["maxMB"] = max(10, int(_number(cfg.get("maxMB"), 400)))
        cfg["pendingDays"] = max(0, int(_number(cfg.get("pendingDays"), 7)))
        cfg["trashDays"] = max(0, int(_number(cfg.get("trashDays"), 3)))
        cfg["dedupeSeconds"] = max(0, int(_number(cfg.get("dedupeSeconds"), 15)))
        return cfg

    # ------------------------------------------------------------ 目录 / 索引

    def _ensure_dirs(self) -> None:
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.trash.mkdir(parents=True, exist_ok=True)

    def _load_locked(self) -> None:
        raw = None
        try:
            if self.index_path.exists():
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                raw = data.get("items") if isinstance(data, dict) else data
        except Exception:  # noqa: BLE001
            raw = None

        items = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and entry.get("id"):
                    items.append(self._normalize_record(entry))
        self._items = items

        # 索引丢了/损毁了也能从磁盘把记录捞回来
        known = {item["id"] for item in items}
        for folder in sorted(self.snapshots.glob("*/meta.json")):
            sid = folder.parent.name
            if sid in known or not ID_RE.fullmatch(sid):
                continue
            record = self._read_meta(folder.parent)
            if record:
                self._items.append(record)
                known.add(sid)
        for folder in sorted(self.trash.glob("*/meta.json")):
            sid = folder.parent.name
            if sid in known or not ID_RE.fullmatch(sid):
                continue
            record = self._read_meta(folder.parent)
            if record:
                record["status"] = "deleted"
                self._items.append(record)
                known.add(sid)

        self._refresh_image_flags_locked()
        self._sort_locked()

    def _read_meta(self, folder: Path) -> dict | None:
        try:
            data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return self._normalize_record(data)

    def _normalize_record(self, data: dict) -> dict:
        sid = str(data.get("id") or "")
        status = data.get("status") if data.get("status") in STATUSES else "pending"
        ts = _number(data.get("ts"), 0.0)
        if not ts:
            ts = time.time()
        record = {
            "id": sid,
            "created_at": _text(data.get("created_at") or _fmt_ts(ts), 40),
            "ts": round(ts, 3),
            "source": data.get("source") if data.get("source") in SOURCE_LABELS else "task",
            "title": _text(data.get("title") or "未命名报错", 160),
            "error": _text(data.get("error"), 4000),
            "entry": _text(data.get("entry"), 120),
            "task_name": _text(data.get("task_name"), 120),
            "state": _text(data.get("state"), 40),
            "status": status,
            "note": _text(data.get("note"), 1000),
            "has_image": bool(data.get("has_image")),
            "width": int(_number(data.get("width"), 0)),
            "height": int(_number(data.get("height"), 0)),
            "logs": int(_number(data.get("logs"), 0)),
            "extra": data.get("extra") if isinstance(data.get("extra"), dict) else {},
            "decided_at": _text(data.get("decided_at"), 40),
            "decided_ts": round(_number(data.get("decided_ts"), 0.0), 3),
        }
        return record

    def _refresh_image_flags_locked(self) -> None:
        for item in self._items:
            folder = self._folder_locked(item["id"], item["status"])
            item["has_image"] = bool(folder and (folder / "screen.png").exists())

    def _folder_locked(self, sid: str, status: str | None = None) -> Path | None:
        """按状态优先找目录；被用户删掉/移动过的记录也能找到图。"""
        candidates = [self.snapshots / sid]
        if status == "deleted":
            candidates = [self.trash / sid, self.snapshots / sid]
        for folder in candidates:
            if folder.exists():
                return folder
        for folder in (self.trash / sid, self.snapshots / sid):
            if folder.exists():
                return folder
        return None

    def _sort_locked(self) -> None:
        self._items.sort(key=lambda item: item.get("ts") or 0, reverse=True)

    def _write_index_locked(self) -> None:
        self._ensure_dirs()
        payload = {
            "version": 1,
            "updated_at": _fmt_ts(time.time()),
            "items": self._items,
        }
        tmp = self.index_path.with_name(self.index_path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp.replace(self.index_path)

    # ------------------------------------------------------------ 采集

    def capture(
        self,
        source: str = "task",
        title: str = "",
        *,
        error: str = "",
        entry: str = "",
        task_name: str = "",
        state: str = "",
        logs=None,
        image: bytes | None = None,
        extra: dict | None = None,
        note: str = "",
        dedupe_key: str | None = None,
        force: bool = False,
    ) -> dict | None:
        """存一条报错现场。返回记录；被开关/去重挡下时返回 None。"""
        cfg = self.config()
        if not force and (not cfg["enabled"] or not cfg["autoCapture"]):
            return None

        now = time.time()
        key = dedupe_key or f"{source}|{entry}|{title}|{error[:160]}"
        with self._lock:
            if not force and cfg["dedupeSeconds"] > 0:
                last = self._dedupe.get(key)
                if last and now - last < cfg["dedupeSeconds"]:
                    return None
            self._dedupe[key] = now
            for stale in [k for k, v in self._dedupe.items() if now - v > 900]:
                self._dedupe.pop(stale, None)

        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        sid = f"{stamp}-{uuid.uuid4().hex[:6]}"
        folder = self.snapshots / sid
        folder.mkdir(parents=True, exist_ok=True)

        has_image = False
        width = height = 0
        size = 0
        if image:
            try:
                (folder / "screen.png").write_bytes(image)
                has_image = True
                width, height = _png_size(image)
                size += len(image)
            except Exception:  # noqa: BLE001
                has_image = False
        log_lines = [str(line) for line in (logs or []) if str(line).strip()]
        if log_lines:
            try:
                text = "\n".join(log_lines)
                (folder / "logs.txt").write_text(text, encoding="utf-8")
                size += len(text.encode("utf-8"))
            except Exception:  # noqa: BLE001
                pass

        record = {
            "id": sid,
            "created_at": _fmt_ts(now),
            "ts": round(now, 3),
            "source": source if source in SOURCE_LABELS else "task",
            "title": _text(title or "未命名报错", 160),
            "error": _text(error, 4000),
            "entry": _text(entry, 120),
            "task_name": _text(task_name, 120),
            "state": _text(state, 40),
            "status": "pending",
            "note": _text(note, 1000),
            "has_image": has_image,
            "width": width,
            "height": height,
            "logs": len(log_lines),
            "extra": _clean_extra(extra),
            "decided_at": "",
            "decided_ts": 0,
            "size": size,
        }
        try:
            (folder / "meta.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

        with self._lock:
            self._items.append(record)
            self._sort_locked()
            self._cleanup_locked(cfg)
            self._write_index_locked()
            self._write_report_locked()
        return dict(record)

    def capture_event(self, event: dict) -> dict | None:
        """给 runtime 的 error_sink 用：参数直接是 dict，方便以后扩展。"""
        event = event or {}
        return self.capture(
            source=event.get("source") or "task",
            title=event.get("title") or "",
            error=event.get("error") or "",
            entry=event.get("entry") or "",
            task_name=event.get("task_name") or "",
            state=event.get("state") or "",
            logs=event.get("logs") or [],
            image=event.get("image"),
            extra=event.get("extra") or {},
            note=event.get("note") or "",
            dedupe_key=event.get("dedupe_key"),
            force=bool(event.get("force")),
        )

    # ------------------------------------------------------------ 查询

    def list(self, status: str | None = None, limit: int = 300) -> list[dict]:
        with self._lock:
            items = list(self._items)
        if status in STATUSES:
            items = [item for item in items if item["status"] == status]
        result = []
        for item in items[: max(1, int(limit or 300))]:
            row = dict(item)
            row["source_label"] = SOURCE_LABELS.get(row["source"], row["source"])
            row["image_url"] = (
                f"/api/selfheal/image?id={row['id']}" if row["has_image"] else ""
            )
            folder = self._folder_locked(row["id"], row["status"])
            row["path"] = _display_path(folder, self.root) if folder else ""
            row["image_path"] = (
                _display_path(folder / "screen.png", self.root)
                if folder and (folder / "screen.png").exists()
                else ""
            )
            result.append(row)
        return result

    def detail(self, sid: str) -> dict | None:
        record = self.get(sid)
        if record is None:
            return None
        record["source_label"] = SOURCE_LABELS.get(record["source"], record["source"])
        folder = self._folder_locked(sid, record["status"])
        record["image_url"] = f"/api/selfheal/image?id={sid}" if record["has_image"] else ""
        record["image_path"] = (
            _display_path(folder / "screen.png", self.root)
            if folder and (folder / "screen.png").exists()
            else ""
        )
        record["log_text"] = ""
        if folder is not None:
            log_file = folder / "logs.txt"
            try:
                record["log_text"] = log_file.read_text(encoding="utf-8")[-20000:]
            except OSError:
                pass
            record["size"] = _folder_size(folder)
            record["path"] = _display_path(folder, self.root)
        return record

    def get(self, sid: str) -> dict | None:
        sid = str(sid or "")
        if not ID_RE.fullmatch(sid):
            return None
        with self._lock:
            for item in self._items:
                if item["id"] == sid:
                    return dict(item)
        return None

    def image_bytes(self, sid: str) -> bytes | None:
        sid = str(sid or "")
        if not ID_RE.fullmatch(sid):
            return None
        folder = self._folder_locked(sid)
        if folder is None:
            return None
        image = folder / "screen.png"
        if not image.exists():
            return None
        try:
            return image.read_bytes()
        except OSError:
            return None

    def stats(self) -> dict:
        with self._lock:
            items = list(self._items)
        counts = {status: 0 for status in STATUSES}
        total_bytes = 0
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            folder = self._folder_locked(item["id"], item["status"])
            if folder is not None:
                total_bytes += _folder_size(folder)
        return {
            "pending": counts["pending"],
            "kept": counts["kept"],
            "deleted": counts["deleted"],
            "total": len(items),
            "bytes": total_bytes,
            "mb": round(total_bytes / 1048576, 2),
            "dir": _display_path(self.base, self.root),
            "report": _display_path(self.report_path, self.root),
        }

    def state(self, status: str | None = None, limit: int = 300) -> dict:
        return {
            "items": self.list(status=status, limit=limit),
            "stats": self.stats(),
            "config": self.config(),
        }

    # ------------------------------------------------------------ 用户整理

    def mark(self, sid: str, status: str, note: str | None = None) -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"不认识的状态：{status}")
        with self._lock:
            record = next((item for item in self._items if item["id"] == sid), None)
            if record is None:
                return None
            old = record["status"]
            if status == "deleted" and old != "deleted":
                self._move_to_trash_locked(sid)
            elif status != "deleted" and old == "deleted":
                self._move_from_trash_locked(sid)
            if note is not None:
                record["note"] = _text(note, 1000)
            record["status"] = status
            record["decided_ts"] = round(time.time(), 3) if status != "pending" else 0
            record["decided_at"] = _fmt_ts(record["decided_ts"]) if record["decided_ts"] else ""
            self._refresh_image_flags_locked()
            self._write_index_locked()
            self._write_report_locked()
            return dict(record)

    def note(self, sid: str, note: str) -> dict | None:
        with self._lock:
            record = next((item for item in self._items if item["id"] == sid), None)
            if record is None:
                return None
            record["note"] = _text(note, 1000)
            self._write_index_locked()
            self._write_report_locked()
            return dict(record)

    def purge(self, sid: str | None = None) -> int:
        """彻底删除：给了 id 删一条（不管状态），否则清空所有 deleted。"""
        with self._lock:
            if sid:
                record = next((item for item in self._items if item["id"] == sid), None)
                if record is None:
                    return 0
                self._purge_item_locked(record)
                removed = 1
            else:
                removed = 0
                for record in list(self._items):
                    if record["status"] == "deleted":
                        self._purge_item_locked(record)
                        removed += 1
            self._write_index_locked()
            self._write_report_locked()
            return removed

    def cleanup(self) -> dict:
        cfg = self.config()
        with self._lock:
            trashed, purged = self._cleanup_locked(cfg)
            self._write_index_locked()
            self._write_report_locked()
            self._refresh_image_flags_locked()
        return {
            "removed": purged,
            "trashed": trashed,
            "stats": self.stats(),
        }

    # ------------------------------------------------------------ 自动整理

    def _cleanup_locked(self, cfg: dict) -> tuple[int, int]:
        now = time.time()
        pending_days = cfg["pendingDays"]
        trash_days = cfg["trashDays"]
        trashed = 0
        purged = 0

        for record in list(self._items):
            try:
                age = now - float(record.get("ts") or 0)
                if record["status"] == "pending" and pending_days > 0 and age > pending_days * 86400:
                    self._move_to_trash_locked(record["id"])
                    trashed += 1
                    continue
                if record["status"] == "deleted":
                    since = float(record.get("decided_ts") or record.get("ts") or 0)
                    if trash_days > 0 and now - since > trash_days * 86400:
                        self._purge_item_locked(record)
                        purged += 1
            except Exception:  # noqa: BLE001
                continue

        # 条目数上限：先丢最旧的 pending，再丢最旧的 deleted，kept 不动
        guard = 0
        while len(self._items) > cfg["maxItems"] and guard < 10000:
            guard += 1
            victim = self._oldest_locked(("pending",)) or self._oldest_locked(("deleted",))
            if victim is None:
                break
            if victim["status"] == "pending":
                self._move_to_trash_locked(victim["id"])
                trashed += 1
            else:
                self._purge_item_locked(victim)
                purged += 1

        # 体积上限：同上
        guard = 0
        while self._total_size_locked() > cfg["maxMB"] * 1048576 and guard < 10000:
            guard += 1
            victim = self._oldest_locked(("pending",)) or self._oldest_locked(("deleted",))
            if victim is None:
                break
            if victim["status"] == "pending":
                self._move_to_trash_locked(victim["id"])
                trashed += 1
            else:
                self._purge_item_locked(victim)
                purged += 1

        return trashed, purged

    def _oldest_locked(self, statuses: tuple[str, ...]) -> dict | None:
        candidates = [item for item in self._items if item["status"] in statuses]
        if not candidates:
            return None
        return min(candidates, key=lambda item: item.get("ts") or 0)

    def _total_size_locked(self) -> int:
        total = 0
        for item in self._items:
            folder = self._folder_locked(item["id"], item["status"])
            if folder is not None:
                total += _folder_size(folder)
        return total

    def _move_to_trash_locked(self, sid: str) -> None:
        src = self.snapshots / sid
        dst = self.trash / sid
        self.trash.mkdir(parents=True, exist_ok=True)
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))
        record = next((item for item in self._items if item["id"] == sid), None)
        if record is not None:
            record["status"] = "deleted"
            record["decided_ts"] = round(time.time(), 3)
            record["decided_at"] = _fmt_ts(record["decided_ts"])

    def _move_from_trash_locked(self, sid: str) -> None:
        src = self.trash / sid
        dst = self.snapshots / sid
        if src.exists():
            self.snapshots.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))

    def _purge_item_locked(self, record: dict) -> None:
        sid = record["id"]
        for folder in (self.snapshots / sid, self.trash / sid):
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
        try:
            self._items.remove(record)
        except ValueError:
            pass

    # ------------------------------------------------------------ 报告

    def report(self) -> str:
        with self._lock:
            self._write_report_locked()
            try:
                return self.report_path.read_text(encoding="utf-8")
            except OSError:
                return ""

    def _write_report_locked(self) -> None:
        stats = {
            "pending": sum(1 for item in self._items if item["status"] == "pending"),
            "kept": sum(1 for item in self._items if item["status"] == "kept"),
            "deleted": sum(1 for item in self._items if item["status"] == "deleted"),
        }
        total_mb = round(self._total_size_locked() / 1048576, 2)
        lines = [
            "# 问题自纠报告（自动生成）",
            "",
            "> 网页版「问题自纠」模块生成。Harness 可直接读本文件，"
            "结构化数据见同目录 `index.json`，截图在 `snapshots/<id>/screen.png`。",
            "",
            f"- 生成时间：{_fmt_ts(time.time())}",
            f"- 统计：待判定 {stats['pending']} · 保留 {stats['kept']} · 已删除 {stats['deleted']}"
            f" · 占用 {total_mb} MB",
            f"- 目录：`{_display_path(self.base, self.root)}`",
            "",
            "> 约定：**保留** = 用户确认值得修；**待判定** = 还没看；"
            "**已删除** = 用户认为没价值，不要据此改代码。",
            "",
        ]

        for status, heading in (("kept", "保留（用户确认值得修）"), ("pending", "待判定")):
            records = [item for item in self._items if item["status"] == status]
            lines.append(f"## {heading} · {len(records)} 条")
            lines.append("")
            if not records:
                lines.append("_暂无_")
                lines.append("")
                continue
            for index, item in enumerate(records, 1):
                lines.extend(self._report_item_locked(index, item))
                lines.append("")

        deleted = [item for item in self._items if item["status"] == "deleted"]
        lines.append(f"## 已删除（{len(deleted)}，用户认为没价值）")
        lines.append("")
        if deleted:
            for item in deleted[:60]:
                lines.append(f"- {item['created_at']} · {item['title']}")
        else:
            lines.append("_暂无_")
        lines.append("")

        self._ensure_dirs()
        try:
            self.report_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    def _report_item_locked(self, index: int, item: dict) -> list[str]:
        source = SOURCE_LABELS.get(item["source"], item["source"])
        folder = self._folder_locked(item["id"], item["status"])
        image_rel = ""
        if folder is not None and (folder / "screen.png").exists():
            image_rel = _display_path(folder / "screen.png", self.base)
            if item.get("width") and item.get("height"):
                image_rel += f"（{item['width']}×{item['height']}）"
        lines = [
            f"### {index}. {item['title']}",
            f"- 时间：{item['created_at']}",
            f"- 来源：{source}"
            + (f" · 任务：{item['task_name'] or item['entry']}" if (item["task_name"] or item["entry"]) else "")
            + (f" · 状态：{item['state']}" if item["state"] else ""),
        ]
        if image_rel:
            lines.append(f"- 截图：`{image_rel}`")
        else:
            lines.append("- 截图：_没有存到（采集时设备未连接）_")
        if item.get("error"):
            lines.append("- 错误：")
            lines.append("")
            lines.append("```text")
            lines.extend(str(item["error"]).splitlines()[:40])
            lines.append("```")
        if item.get("note"):
            lines.append(f"- 用户备注：{item['note']}")
        if folder is not None:
            try:
                log_text = (folder / "logs.txt").read_text(encoding="utf-8")
            except OSError:
                log_text = ""
            if log_text:
                lines.append("- 最近日志（尾部）：")
                lines.append("")
                lines.append("```text")
                lines.extend(log_text.splitlines()[-40:])
                lines.append("```")
        return lines


# ---------------------------------------------------------------- 小工具


def _number(value, default) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _folder_size(folder: Path) -> int:
    total = 0
    try:
        for path in folder.iterdir():
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _clean_extra(extra: dict | None) -> dict:
    if not isinstance(extra, dict):
        return {}
    cleaned = {}
    for key, value in list(extra.items())[:30]:
        cleaned[_text(key, 60)] = _json_safe(value)
    return cleaned


def _json_safe(value):
    """保证 extra 一定能写进 JSON：不认识的类型转成字符串。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _text(value, 500) if isinstance(value, str) else value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in list(value)[:40]]
    if isinstance(value, dict):
        return {_text(key, 60): _json_safe(item) for key, item in list(value.items())[:40]}
    return _text(value, 200)
