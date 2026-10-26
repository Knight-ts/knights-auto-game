# -*- coding: utf-8 -*-
"""佣兵入队条件：复用「好娃提醒」的组合（特性 + 属性门槛）判断。

table/child_alert_conditions.json 里的组合本来就是「必需特性 + 属性门槛（含 or_attributes）」，
佣兵入队直接拿来用：选中几个组合，满足其中任意一个就收。

单独配置的 ``features`` / ``potential_min`` 也走同一套比较逻辑，
保证和「好娃提醒」判定口径一致（同一份等级区间）。
"""

from __future__ import annotations

import json
from pathlib import Path

ATTRIBUTE_ORDER = ("力量", "体质", "技巧", "感知", "敏捷", "意志")
ATTRIBUTE_RANK = {"SS": 7, "S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
# 和 role_utils.get_potential_grade / child.evaluate 保持一致
GRADE_BOUNDS = (
    ("E", 0.10),
    ("D", 0.20),
    ("C", 0.35),
    ("B", 0.55),
    ("A", 0.74),
    ("S", 0.93),
    ("SS", float("inf")),
)

ROOT = Path(__file__).resolve().parents[2]
CONDITIONS_PATH = ROOT / "table" / "child_alert_conditions.json"

_cache: list[dict] | None = None
_cache_mtime: float = -1.0


def grade_of(value) -> str:
    """潜力数值 -> 等级（E/D/C/B/A/S/SS）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "E"
    for grade, upper in GRADE_BOUNDS:
        if number < upper:
            return grade
    return "SS"


def grades_of(values: dict) -> dict:
    return {attr: grade_of(values.get(attr, 0)) for attr in ATTRIBUTE_ORDER}


def grades_text(values: dict) -> str:
    """压成一行给记录用：力S 体A 技SS 感B 敏C 意A。"""
    grades = grades_of(values or {})
    return " ".join(f"{attr[0]}{grades[attr]}" for attr in ATTRIBUTE_ORDER)


def load_conditions(path: Path | None = None, force: bool = False) -> list[dict]:
    """读取「好娃提醒」组合表（按 mtime 缓存）。"""
    global _cache, _cache_mtime
    path = path or CONDITIONS_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return list(_cache or [])
    if not force and _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(_cache or [])
    conditions = [item for item in (data.get("conditions") or []) if isinstance(item, dict)]
    _cache = conditions
    _cache_mtime = mtime
    return conditions


def condition_choices() -> list[dict]:
    """给网页下拉/勾选用：id + 名称 + 说明。"""
    return [
        {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or ""),
            "description": str(item.get("description") or ""),
            "features": [str(name) for name in (item.get("features") or [])],
        }
        for item in load_conditions()
        if item.get("id") or item.get("name")
    ]


def find_condition(key: str, conditions: list[dict] | None = None) -> dict | None:
    """按 id 或名称找组合。"""
    key = (key or "").strip()
    if not key:
        return None
    for item in conditions if conditions is not None else load_conditions():
        if key in (str(item.get("id") or ""), str(item.get("name") or "")):
            return item
    return None


def check_potential(values: dict, condition: dict) -> tuple[bool, dict]:
    """属性门槛（含 or_attributes），和「好娃提醒」同口径。"""
    config = condition.get("potential") or {}
    if not config:
        return True, {}

    hit: dict = {}
    or_attributes = None
    for attr, requirement in config.items():
        if attr == "or_attributes":
            or_attributes = requirement
            continue
        if not isinstance(requirement, dict):
            continue
        value = float(values.get(attr, 0) or 0)
        grade = grade_of(value)
        required_grade = requirement.get("grade")
        if required_grade and ATTRIBUTE_RANK.get(grade, 0) < ATTRIBUTE_RANK.get(str(required_grade), 0):
            return False, {}
        min_value = requirement.get("min_value")
        if min_value is not None and value < float(min_value):
            return False, {}
        hit[attr] = grade

    if or_attributes:
        satisfied = False
        for group in or_attributes:
            if not isinstance(group, dict):
                continue
            group_ok = True
            group_hit = {}
            for attr, requirement in group.items():
                value = float(values.get(attr, 0) or 0)
                min_value = (requirement or {}).get("min_value")
                if min_value is not None and value < float(min_value):
                    group_ok = False
                    break
                group_hit[attr] = grade_of(value)
            if group_ok:
                hit.update(group_hit)
                satisfied = True
                break
        if not satisfied:
            return False, {}
    return True, hit


def check_features(feature_names: list[str], condition: dict) -> tuple[bool, list[str]]:
    """必需特性（全部命中，包含关系即可）。"""
    required = [str(name) for name in (condition.get("features") or []) if str(name).strip()]
    if not required:
        return True, []
    found: list[str] = []
    for want in required:
        for name in feature_names or []:
            if want in name:
                found.append(name)
                break
    return len(found) >= len(required), found


def match_combos(
    selected: list[str],
    values: dict,
    feature_names: list[str],
    conditions: list[dict] | None = None,
) -> tuple[bool | None, str, str]:
    """选中的组合逐个比，满足任一个就通过。

    返回 (结果, 命中的组合名, 说明)；selected 为空时结果是 None（条件未启用）。
    """
    selected = [str(name).strip() for name in (selected or []) if str(name).strip()]
    if not selected:
        return None, "", ""
    pool = conditions if conditions is not None else load_conditions()
    tried = []
    for key in selected:
        condition = find_condition(key, pool)
        if condition is None:
            tried.append(f"{key}(未找到)")
            continue
        potential_ok, _ = check_potential(values, condition)
        feature_ok, found = check_features(feature_names, condition)
        name = str(condition.get("name") or condition.get("id") or key)
        if potential_ok and feature_ok:
            detail = "、".join(found) if found else "不看特性"
            return True, name, detail
        missing = []
        if not feature_ok:
            missing.append("缺特性")
        if not potential_ok:
            missing.append("属性不够")
        tried.append(f"{name}({'/'.join(missing)})")
    return False, "", "；".join(tried)


def check_potential_min(values: dict, minimum: dict) -> tuple[bool, str]:
    """单独配置的属性门槛：{"技巧": "SS", "力量": "S"}。"""
    minimum = {str(k): str(v) for k, v in (minimum or {}).items() if str(v).strip()}
    if not minimum:
        return True, ""
    grades = grades_of(values or {})
    parts = []
    for attr, want in minimum.items():
        got = grades.get(attr, "E")
        ok = ATTRIBUTE_RANK.get(got, 0) >= ATTRIBUTE_RANK.get(want, 0)
        parts.append(f"{attr}{got}≥{want}" if ok else f"{attr}{got}<{want}")
        if not ok:
            return False, "；".join(parts)
    return True, "；".join(parts)


def check_feature_list(feature_names: list[str], required: list[str]) -> tuple[bool, str]:
    """单独配置的必需特性（全部命中）。"""
    required = [str(name).strip() for name in (required or []) if str(name).strip()]
    if not required:
        return True, ""
    names = list(feature_names or [])
    missing = [want for want in required if not any(want in name for name in names)]
    if missing:
        return False, f"缺 {'、'.join(missing)}"
    return True, "、".join(required)
