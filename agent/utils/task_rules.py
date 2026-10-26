# -*- coding: utf-8 -*-
"""任务接取规则：按城市配置等级区间 + 名称黑名单，并保存最近一次任务板快照。

数据文件（项目根 ``table/`` 下）：

- ``task_accept_rules.json``  城市规则（等级区间 + 城市黑名单），网页「任务接取」面板编辑
- ``task_blacklist.json``     全局黑名单（沿用旧格式 ``{"blacklist": [...]}``，所有城市都生效）
- ``task_pool.json``          最近一次识别到的任务板（城市 / 时间 / 每条任务是否可接及原因）

城市名来自界面选项「选择刷取任务国家」，对应 pipeline 节点 ``EnterCity`` 的
``expected``。规则优先级：城市规则 > 默认规则 > 内置默认（0~120 级）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # Agent 进程用统一 logger；网页版单独加载时退回标准 logging
    from utils import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("task_rules")

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_MODULE_DIR))
TABLE_DIR = os.path.join(ROOT_DIR, "table")

RULES_NAME = "task_accept_rules.json"
POOL_NAME = "task_pool.json"
BLACKLIST_NAME = "task_blacklist.json"

DEFAULT_MIN_LEVEL = 0
DEFAULT_MAX_LEVEL = 120

_rules_cache: Optional[dict] = None
_rules_mtime: float = -1.0
_pool_cache: Optional[dict] = None
_pool_mtime: float = -1.0


# ------------------------------------------------------------------ 路径 / IO


def rules_path() -> str:
    return os.path.join(TABLE_DIR, RULES_NAME)


def pool_path() -> str:
    return os.path.join(TABLE_DIR, POOL_NAME)


def blacklist_path() -> str:
    return os.path.join(TABLE_DIR, BLACKLIST_NAME)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return -1.0


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 {path} 失败：{exc}")
        return None


def _atomic_write(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    if os.path.exists(path):
        try:
            os.replace(path, f"{path}.bak")
        except OSError:
            pass
    os.replace(tmp, path)


# ------------------------------------------------------------------ 规则读写


def _clean_int(value: Any, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _clean_blacklist(names: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for name in names or []:
        text = str(name).strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_rules(data: Any) -> dict:
    data = data if isinstance(data, dict) else {}
    defaults_raw = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    defaults = {
        "min_level": _clean_int(
            defaults_raw.get("min_level"), DEFAULT_MIN_LEVEL
        ),
        "max_level": _clean_int(
            defaults_raw.get("max_level"), DEFAULT_MAX_LEVEL
        ),
    }
    if defaults["min_level"] > defaults["max_level"]:
        defaults["min_level"], defaults["max_level"] = (
            defaults["max_level"],
            defaults["min_level"],
        )

    cities: Dict[str, dict] = {}
    for city, raw in (data.get("cities") or {}).items():
        name = str(city).strip()
        if not name or not isinstance(raw, dict):
            continue
        entry: Dict[str, Any] = {}
        if "min_level" in raw:
            entry["min_level"] = _clean_int(raw.get("min_level"), defaults["min_level"])
        if "max_level" in raw:
            entry["max_level"] = _clean_int(raw.get("max_level"), defaults["max_level"])
        entry["blacklist"] = _clean_blacklist(raw.get("blacklist") or [])
        cities[name] = entry
    return {"defaults": defaults, "cities": cities}


def load_rules(force: bool = False) -> dict:
    """读取规则文件（按 mtime 缓存，网页改完 Agent 不用重启）。"""
    global _rules_cache, _rules_mtime
    mtime = _mtime(rules_path())
    if not force and _rules_cache is not None and mtime == _rules_mtime:
        return _rules_cache
    data = _read_json(rules_path())
    if data is None and not os.path.exists(rules_path()):
        data = {"defaults": {"min_level": DEFAULT_MIN_LEVEL, "max_level": DEFAULT_MAX_LEVEL}, "cities": {}}
    _rules_cache = _normalize_rules(data)
    _rules_mtime = mtime
    return _rules_cache


def save_rules(data: dict) -> dict:
    global _rules_cache, _rules_mtime
    normalized = _normalize_rules(data)
    payload = {
        "_说明": (
            "按城市配置任务接取规则。cities 里的 min_level/max_level 覆盖 defaults；"
            "blacklist 只在该城市生效。全局黑名单在 task_blacklist.json。"
        ),
        "defaults": normalized["defaults"],
        "cities": normalized["cities"],
    }
    _atomic_write(rules_path(), payload)
    _rules_cache = normalized
    _rules_mtime = _mtime(rules_path())
    return normalized


def city_rule(city: str, fallback: Optional[dict] = None) -> dict:
    """返回某城市实际生效的规则（含城市黑名单列表）。

    ``fallback`` 用于界面/命令行临时下限，优先级低于规则文件里的 defaults。
    """
    rules = load_rules()
    base_min = rules["defaults"]["min_level"]
    base_max = rules["defaults"]["max_level"]
    if fallback:
        base_min = _clean_int(fallback.get("min_level"), base_min)
        base_max = _clean_int(fallback.get("max_level"), base_max)
    entry = rules["cities"].get((city or "").strip()) or {}

    min_level = _clean_int(entry.get("min_level"), base_min)
    max_level = _clean_int(entry.get("max_level"), base_max)
    if min_level > max_level:
        min_level, max_level = max_level, min_level
    return {
        "city": (city or "").strip(),
        "min_level": min_level,
        "max_level": max_level,
        "blacklist": _clean_blacklist(entry.get("blacklist") or []),
        "explicit": bool(entry),
    }


def set_city_levels(
    city: str, min_level: Any, max_level: Any
) -> dict:
    """保存某城市的等级区间；``city`` 为空时改默认区间。"""
    rules = load_rules(force=True)
    min_value = _clean_int(min_level, rules["defaults"]["min_level"])
    max_value = _clean_int(max_level, rules["defaults"]["max_level"])
    if min_value > max_value:
        min_value, max_value = max_value, min_value

    city = (city or "").strip()
    if not city:
        rules["defaults"]["min_level"] = min_value
        rules["defaults"]["max_level"] = max_value
    else:
        entry = rules["cities"].setdefault(city, {"blacklist": []})
        entry["min_level"] = min_value
        entry["max_level"] = max_value
    return save_rules(rules)


def set_city_blacklist(city: str, names: List[str]) -> dict:
    rules = load_rules(force=True)
    city = (city or "").strip()
    cleaned = _clean_blacklist(names)
    if not city:
        # 空城市 = 默认展示，不写城市黑名单，避免误把全局名单塞进默认规则
        return save_rules(rules)
    entry = rules["cities"].setdefault(city, {"blacklist": []})
    entry["blacklist"] = cleaned
    return save_rules(rules)


def add_city_blacklist(city: str, name: str) -> dict:
    city = (city or "").strip()
    name = (name or "").strip()
    if not city or not name:
        return load_rules()
    rule = city_rule(city)
    names = list(rule["blacklist"])
    if name not in names:
        names.append(name)
    return set_city_blacklist(city, names)


def remove_city_blacklist(city: str, name: str) -> dict:
    city = (city or "").strip()
    name = (name or "").strip()
    if not city or not name:
        return load_rules()
    rule = city_rule(city)
    return set_city_blacklist(
        city, [item for item in rule["blacklist"] if item != name]
    )


# ------------------------------------------------------------------ 全局黑名单


def global_blacklist() -> List[str]:
    data = _read_json(blacklist_path())
    names = data.get("blacklist") if isinstance(data, dict) else data
    if not isinstance(names, list):
        return []
    return _clean_blacklist(names)


def save_global_blacklist(names: List[str]) -> List[str]:
    cleaned = _clean_blacklist(names)
    _atomic_write(blacklist_path(), {"blacklist": cleaned})
    return cleaned


def add_global_blacklist(name: str) -> List[str]:
    name = (name or "").strip()
    names = global_blacklist()
    if name and name not in names:
        names.append(name)
        save_global_blacklist(names)
    return names


def remove_global_blacklist(name: str) -> List[str]:
    name = (name or "").strip()
    names = [item for item in global_blacklist() if item != name]
    save_global_blacklist(names)
    return names


def effective_blacklist(city: str) -> List[str]:
    """全局黑名单 + 城市黑名单（去重、排序）。"""
    names = set(global_blacklist())
    names.update(city_rule(city)["blacklist"])
    return sorted(names)


def is_blacklisted(name: str, city: str = "") -> bool:
    name = (name or "").strip()
    if not name:
        return False
    return name in effective_blacklist(city)


# ------------------------------------------------------------------ 任务板快照


def load_pool() -> dict:
    global _pool_cache, _pool_mtime
    mtime = _mtime(pool_path())
    if _pool_cache is not None and mtime == _pool_mtime:
        return _pool_cache
    data = _read_json(pool_path())
    _pool_cache = data if isinstance(data, dict) else {}
    _pool_mtime = mtime
    return _pool_cache


def save_pool(city: str, items: List[dict], *, source: str = "hud") -> dict:
    """保存最近一次任务板快照；内容没变就不重复写盘。"""
    global _pool_cache, _pool_mtime
    payload = {
        "city": (city or "").strip(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "tasks": [
            {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
                "level": str(item.get("level") or ""),
                "description": str(item.get("description") or ""),
                "eligible": bool(item.get("eligible")),
                "reason": str(item.get("reason") or ""),
            }
            for item in items
        ],
    }
    previous = load_pool()
    if (
        previous.get("city") == payload["city"]
        and previous.get("tasks") == payload["tasks"]
    ):
        return previous

    _atomic_write(pool_path(), payload)
    _pool_cache = payload
    _pool_mtime = _mtime(pool_path())
    logger.info(
        f"任务板快照：{payload['city'] or '未知城市'} "
        f"{len(payload['tasks'])} 条（可接 "
        f"{sum(1 for item in payload['tasks'] if item['eligible'])} 条）"
    )
    return payload


def city_names() -> List[str]:
    """规则 + 任务板 + 记录里出现过的城市，给网页下拉框用。"""
    names = set(load_rules()["cities"])
    pool_city = (load_pool().get("city") or "").strip()
    if pool_city:
        names.add(pool_city)
    return sorted(name for name in names if name)
