# -*- coding: utf-8 -*-
"""刷取任务记录：每次接取任务后记录 城市 / 任务名 / 花费回合数，并支持一键拉黑。

数据文件（都在 ``table/`` 下）：

- ``task_records.jsonl``  明细，一行一条
- ``task_records.json``   汇总，按 城市 → 任务 分组，给网页按城市展示
- ``task_blacklist.json`` 黑名单（和任务板识别器 ``TaskBlacklist`` 共用同一个文件）
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

try:  # Agent 进程里用项目统一的 logger；网页版单独加载时退回标准 logging
    from utils import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("task_records")

_pending: Optional[dict] = None


# ---------------------------------------------------------------- 路径


def _table(name: str) -> str:
    return os.path.join(os.getcwd(), "table", name)


def records_path() -> str:
    return _table("task_records.jsonl")


def summary_path() -> str:
    return _table("task_records.json")


def blacklist_path() -> str:
    return _table("task_blacklist.json")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 {path} 失败：{exc}")
        return {}


# ---------------------------------------------------------------- 当前任务


def set_pending(
    city: str,
    task_name: str,
    task_type: str = "",
    task_level: str = "",
) -> None:
    """接取任务后登记“当前正在打的任务”，等战斗结束再写记录。"""
    global _pending
    _pending = {
        "city": (city or "").strip(),
        "task": (task_name or "").strip(),
        "type": (task_type or "").strip(),
        "level": (task_level or "").strip(),
        "started": _now(),
    }
    logger.info(
        f"任务记录：本次接取「{_pending['task'] or '未知任务'}」"
        f"（城市 {_pending['city'] or '未知'}）"
    )


def get_pending() -> Optional[dict]:
    return dict(_pending) if _pending else None


def clear_pending() -> None:
    global _pending
    _pending = None


def finish(
    rounds: int, result: str = "胜利", task_name: Optional[str] = None
) -> Optional[dict]:
    """战斗结束：把当前任务连同回合数落盘，并更新按城市分组的汇总。

    ``task_name`` 用于补名：接取时没读到任务名（例如续跑已接取任务），
    战斗结束后回大地图从左下角 HUD 读到再传进来。
    """
    global _pending
    if not _pending:
        return None

    record = {
        "time": _now(),
        "city": _pending["city"],
        "task": (task_name or _pending["task"] or "").strip(),
        "type": _pending["type"],
        "level": _pending["level"],
        "rounds": max(0, int(rounds or 0)),
        "result": result,
    }
    _pending = None

    try:
        os.makedirs(os.path.dirname(records_path()), exist_ok=True)
        with open(records_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"写任务明细失败：{exc}")
        return record

    _update_summary(record)
    logger.info(
        f"任务记录：{record['city'] or '未知城市'}「{record['task'] or '未知任务'}」"
        f"{record['result']}，{record['rounds']} 回合"
    )
    return record


# ---------------------------------------------------------------- 汇总


def load_summary() -> dict:
    data = _read_json(summary_path())
    data.setdefault("cities", {})
    return data


def _update_summary(record: dict) -> None:
    data = load_summary()
    city = record["city"] or "未知城市"
    task = record["task"] or "未知任务"

    per_city = data["cities"].setdefault(city, {})
    item = per_city.setdefault(task, {"count": 0, "rounds_total": 0})
    item["count"] = int(item.get("count", 0)) + 1
    item["rounds_total"] = int(item.get("rounds_total", 0)) + record["rounds"]
    item["rounds_avg"] = round(item["rounds_total"] / item["count"], 1)
    item["last_rounds"] = record["rounds"]
    item["last"] = record["time"]
    item["result"] = record["result"]
    if record.get("type"):
        item["type"] = record["type"]
    if record.get("level"):
        item["level"] = record["level"]
    data["updated"] = record["time"]

    try:
        _atomic_write(
            summary_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"写任务汇总失败：{exc}")


# ---------------------------------------------------------------- 黑名单


def load_blacklist() -> list:
    data = _read_json(blacklist_path())
    names = data.get("blacklist") or []
    if not isinstance(names, list):
        return []
    return [str(name) for name in names if str(name).strip()]


def _save_blacklist(names: list) -> None:
    _atomic_write(
        blacklist_path(),
        json.dumps({"blacklist": names}, ensure_ascii=False, indent=4) + "\n",
    )


def add_blacklist(task_name: str) -> bool:
    name = (task_name or "").strip()
    if not name:
        return False
    names = load_blacklist()
    if name in names:
        return True
    names.append(name)
    _save_blacklist(names)
    logger.info(f"任务黑名单：加入「{name}」（共 {len(names)} 个）")
    return True


def remove_blacklist(task_name: str) -> bool:
    name = (task_name or "").strip()
    if not name:
        return False
    names = load_blacklist()
    if name not in names:
        return True
    names = [item for item in names if item != name]
    _save_blacklist(names)
    logger.info(f"任务黑名单：移除「{name}」（剩 {len(names)} 个）")
    return True
